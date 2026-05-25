import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import APIRouter, Depends

from app.database import get_db
from app.models import PriceSnapshot, PriceWatch, PriceHistory, PriceMonth
from app.schemas import PriceCalendarRequest, MonthCalendarRequest
from app.services import notification_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /events/price-calendar
# Recibido desde fetchPriceCalendar. Sin identificador de usuario.
# Los precios son datos de RUTA — se comparan contra PriceHistory global
# y se notifica a TODOS los price_watches activos para esa ruta.
# ---------------------------------------------------------------------------
@router.post("/events/price-calendar")
async def receive_price_calendar(
    data: PriceCalendarRequest,
    db: AsyncSession = Depends(get_db),
):
    low_days = [d for d in data.days if d.group == "low"]
    if not low_days:
        return {"success": True, "notified": 0, "reason": "no_low_prices"}

    min_price = min(d.price for d in low_days)

    # 1. Guardar snapshots de los días "low" (sin user_id — dato de ruta)
    for dp in low_days:
        db.add(PriceSnapshot(
            user_id=None,
            origin=data.origin_iata,
            destination=data.destination_iata,
            snapshot_date=dp.day,
            price_raw=dp.price,
            price_group=dp.group,
            currency=data.currency,
        ))

    # 2. Obtener precio de referencia global para esta ruta desde PriceHistory
    history_result = await db.execute(
        select(PriceHistory)
        .where(
            and_(
                PriceHistory.origin == data.origin_iata,
                PriceHistory.destination == data.destination_iata,
                PriceHistory.currency == data.currency,
            )
        )
        .order_by(PriceHistory.checked_at.desc())
        .limit(1)
    )
    history = history_result.scalar_one_or_none()
    reference_price = history.min_price if history else None

    # 3. Si bajó el precio → notificar a TODOS los usuarios con price_watch activo
    notified_count = 0
    if reference_price and reference_price > 0 and min_price < reference_price:
        watches_result = await db.execute(
            select(PriceWatch).where(
                and_(
                    PriceWatch.origin == data.origin_iata,
                    PriceWatch.destination == data.destination_iata,
                    PriceWatch.is_active == True,
                )
            )
        )
        watches = watches_result.scalars().all()

        for watch in watches:
            queued = await notification_engine.evaluate_price_drop(
                db=db,
                user_id=watch.user_id,
                origin=data.origin_iata,
                destination=data.destination_iata,
                new_price=min_price,
                reference_price=reference_price,
                currency=data.currency,
            )
            if queued:
                notified_count += 1

    # 4. Actualizar PriceHistory si el precio bajó (o es el primer registro)
    if reference_price is None or min_price < reference_price:
        db.add(PriceHistory(
            origin=data.origin_iata,
            destination=data.destination_iata,
            min_price=min_price,
            currency=data.currency,
        ))

    await db.commit()

    ref_str = f"{reference_price:.0f}" if reference_price else "none"
    logger.info(
        f"price-calendar: {data.origin_iata}->{data.destination_iata} "
        f"min={min_price:.0f} {data.currency} ref={ref_str} notified={notified_count}"
    )
    return {"success": True, "min_price": min_price, "notified": notified_count}


# ---------------------------------------------------------------------------
# POST /events/month-calendar
# Datos de tendencia mensual por ruta. Sin identificador de usuario.
# ---------------------------------------------------------------------------
@router.post("/events/month-calendar")
async def receive_month_calendar(
    data: MonthCalendarRequest,
    db: AsyncSession = Depends(get_db),
):
    if not data.months:
        return {"success": True, "saved": 0}

    saved = 0
    for m in data.months:
        existing = await db.execute(
            select(PriceMonth).where(
                and_(
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
                user_id=None,
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
        f"month-calendar: {data.origin_iata}->{data.destination_iata} {saved} meses"
    )
    return {"success": True, "saved": saved}
