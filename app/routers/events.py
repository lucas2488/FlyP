import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import APIRouter, Depends

from app.database import get_db
from app.models import PriceSnapshot, PriceWatch, SearchEvent
from app.schemas import PriceSnapshotRequest, SearchResultEvent, FlightSelectedEvent
from app.services import notification_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /events/price-snapshot
# Recibido desde CalendarPricesViewModel cuando el usuario abre el date picker
# ---------------------------------------------------------------------------
@router.post("/events/price-snapshot")
async def receive_price_snapshot(
    data: PriceSnapshotRequest,
    db: AsyncSession = Depends(get_db),
):
    low_prices = [p for p in data.prices if p.group == "low"]
    if not low_prices:
        return {"success": True, "queued": False, "reason": "no_low_prices"}

    # 1. Insertar snapshots (solo días "low" — reduce storage)
    for dp in low_prices:
        db.add(PriceSnapshot(
            user_id=data.user_id,
            origin=data.origin,
            destination=data.destination,
            trip_type=data.trip_type,
            snapshot_date=dp.day,
            price_raw=dp.price,
            price_group=dp.group,
            currency=data.currency,
        ))

    # 2. Obtener o crear price_watch
    watch_result = await db.execute(
        select(PriceWatch).where(
            and_(
                PriceWatch.user_id == data.user_id,
                PriceWatch.origin == data.origin,
                PriceWatch.destination == data.destination,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()
    min_price = min(p.price for p in low_prices)

    if watch is None:
        watch = PriceWatch(
            user_id=data.user_id,
            origin=data.origin,
            destination=data.destination,
            trip_type=data.trip_type,
            is_active=True,
            last_search_best_price=min_price,
            interest_score=0,
            notification_count=0,
        )
        db.add(watch)
        await db.flush()
        queued = False  # primera vez, sin referencia previa para comparar
    else:
        watch.is_active = True
        reference_price = watch.last_search_best_price

        # 3. Evaluar caída de precio si hay referencia
        queued = False
        if reference_price and reference_price > 0:
            queued = await notification_engine.evaluate_price_drop(
                db=db,
                user_id=data.user_id,
                origin=data.origin,
                destination=data.destination,
                new_price=min_price,
                reference_price=reference_price,
                currency=data.currency,
                trip_type=data.trip_type,
            )

        # 4. Actualizar referencia si el nuevo precio es más bajo
        if reference_price is None or min_price < reference_price:
            watch.last_search_best_price = min_price

    await db.commit()

    logger.info(
        f"price-snapshot: {data.user_id} {data.origin}->{data.destination} "
        f"min={min_price:.0f} {data.currency} queued={queued}"
    )
    return {"success": True, "queued": queued, "min_price": min_price}


# ---------------------------------------------------------------------------
# POST /events/search-result
# Recibido cuando el usuario obtiene resultados de búsqueda de vuelos
# ---------------------------------------------------------------------------
@router.post("/events/search-result")
async def receive_search_result(
    data: SearchResultEvent,
    db: AsyncSession = Depends(get_db),
):
    # 1. Registrar evento
    db.add(SearchEvent(
        user_id=data.user_id,
        origin=data.origin,
        destination=data.destination,
        trip_type=data.trip_type,
        best_price=data.best_price,
        currency=data.currency,
        event_type="search_result",
    ))

    # 2. Upsert price_watch: actualizar referencia si es menor, interest_score += 1
    watch_result = await db.execute(
        select(PriceWatch).where(
            and_(
                PriceWatch.user_id == data.user_id,
                PriceWatch.origin == data.origin,
                PriceWatch.destination == data.destination,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()
    if watch is None:
        db.add(PriceWatch(
            user_id=data.user_id,
            origin=data.origin,
            destination=data.destination,
            trip_type=data.trip_type or "ONE_WAY",
            is_active=True,
            last_search_best_price=data.best_price,
            interest_score=1,
            notification_count=0,
        ))
    else:
        watch.is_active = True
        watch.interest_score = (watch.interest_score or 0) + 1
        if watch.last_search_best_price is None or data.best_price < watch.last_search_best_price:
            watch.last_search_best_price = data.best_price

    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# POST /events/flight-selected
# Recibido cuando el usuario toca un vuelo específico
# ---------------------------------------------------------------------------
@router.post("/events/flight-selected")
async def receive_flight_selected(
    data: FlightSelectedEvent,
    db: AsyncSession = Depends(get_db),
):
    # 1. Registrar evento con datos del vuelo en event_data
    event_data = {}
    if data.airline:
        event_data["airline"] = data.airline
    if data.departure_date:
        event_data["departure_date"] = data.departure_date

    db.add(SearchEvent(
        user_id=data.user_id,
        origin=data.origin,
        destination=data.destination,
        trip_type=data.trip_type,
        best_price=data.price,
        currency=data.currency,
        event_type="flight_selected",
        event_data=json.dumps(event_data) if event_data else None,
    ))

    # 2. Upsert price_watch: last_selected_price, interest_score += 2
    watch_result = await db.execute(
        select(PriceWatch).where(
            and_(
                PriceWatch.user_id == data.user_id,
                PriceWatch.origin == data.origin,
                PriceWatch.destination == data.destination,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()
    if watch is None:
        db.add(PriceWatch(
            user_id=data.user_id,
            origin=data.origin,
            destination=data.destination,
            trip_type=data.trip_type or "ONE_WAY",
            is_active=True,
            last_selected_price=data.price,
            interest_score=2,
            notification_count=0,
        ))
    else:
        watch.is_active = True
        watch.last_selected_price = data.price
        watch.interest_score = (watch.interest_score or 0) + 2

    await db.commit()
    return {"success": True}
