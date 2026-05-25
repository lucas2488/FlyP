import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import APIRouter, Depends

from app.database import get_db
from app.models import PriceSnapshot, PriceWatch, PriceMonth
from app.schemas import PriceCalendarRequest, MonthCalendarRequest
from app.services import notification_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /events/price-calendar
# Recibido desde fetchPriceCalendar (se llama dos veces: origin→dest y dest→origin)
# Almacena precios diarios, actualiza price_watch y evalúa bajadas de precio.
# ---------------------------------------------------------------------------
@router.post("/events/price-calendar")
async def receive_price_calendar(
    data: PriceCalendarRequest,
    db: AsyncSession = Depends(get_db),
):
    low_days = [d for d in data.days if d.group == "low"]
    if not low_days:
        return {"success": True, "queued": False, "reason": "no_low_prices"}

    # 1. Insertar snapshots (solo días "low")
    for dp in low_days:
        db.add(PriceSnapshot(
            user_id=data.user_id,
            origin=data.origin_iata,
            destination=data.destination_iata,
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
                PriceWatch.origin == data.origin_iata,
                PriceWatch.destination == data.destination_iata,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()
    min_price = min(d.price for d in low_days)

    if watch is None:
        watch = PriceWatch(
            user_id=data.user_id,
            origin=data.origin_iata,
            destination=data.destination_iata,
            is_active=True,
            last_search_best_price=min_price,
            interest_score=0,
            notification_count=0,
        )
        db.add(watch)
        await db.flush()
        queued = False  # primera vez, sin referencia previa
    else:
        watch.is_active = True
        reference_price = watch.last_search_best_price

        # 3. Evaluar caída de precio si hay referencia
        queued = False
        if reference_price and reference_price > 0:
            queued = await notification_engine.evaluate_price_drop(
                db=db,
                user_id=data.user_id,
                origin=data.origin_iata,
                destination=data.destination_iata,
                new_price=min_price,
                reference_price=reference_price,
                currency=data.currency,
            )

        # 4. Actualizar referencia si el nuevo precio es más bajo
        if reference_price is None or min_price < reference_price:
            watch.last_search_best_price = min_price

    await db.commit()

    logger.info(
        f"price-calendar: {data.user_id} {data.origin_iata}->{data.destination_iata} "
        f"min={min_price:.0f} {data.currency} queued={queued}"
    )
    return {"success": True, "queued": queued, "min_price": min_price}


# ---------------------------------------------------------------------------
# POST /events/month-calendar
# Recibido desde fetchMonthPriceCalendar (se llama una vez por búsqueda).
# Almacena precios mensuales como contexto de tendencia. No dispara alertas.
# ---------------------------------------------------------------------------
@router.post("/events/month-calendar")
async def receive_month_calendar(
    data: MonthCalendarRequest,
    db: AsyncSession = Depends(get_db),
):
    if not data.months:
        return {"success": True, "saved": 0}

    # Upsert por (user_id, origin, destination, year, month)
    saved = 0
    for m in data.months:
        existing = await db.execute(
            select(PriceMonth).where(
                and_(
                    PriceMonth.user_id == data.user_id,
                    PriceMonth.origin == data.origin_iata,
                    PriceMonth.destination == data.destination_iata,
                    PriceMonth.year == m.year,
                    PriceMonth.month == m.month,
                )
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            db.add(PriceMonth(
                user_id=data.user_id,
                origin=data.origin_iata,
                destination=data.destination_iata,
                year=m.year,
                month=m.month,
                price_raw=m.price,
                price_category=m.price_category,
                currency=data.currency,
            ))
        else:
            row.price_raw = m.price
            row.price_category = m.price_category
            row.received_at = datetime.utcnow()
        saved += 1

    await db.commit()

    logger.info(
        f"month-calendar: {data.user_id} {data.origin_iata}->{data.destination_iata} "
        f"{saved} meses guardados"
    )
    return {"success": True, "saved": saved}
