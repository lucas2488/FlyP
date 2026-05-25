import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import APIRouter, Depends, HTTPException

from app.database import get_db
from app.models import PriceSnapshot, PriceWatch, PriceMonth, UserProfile
from app.schemas import PriceCalendarRequest, MonthCalendarRequest
from app.services import notification_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /events/price-calendar
# Recibido desde fetchPriceCalendar. Identificador: fcm_token.
# Guarda precios diarios, actualiza price_watch y evalúa bajadas de precio.
# ---------------------------------------------------------------------------
@router.post("/events/price-calendar")
async def receive_price_calendar(
    data: PriceCalendarRequest,
    db: AsyncSession = Depends(get_db),
):
    low_days = [d for d in data.days if d.group == "low"]
    if not low_days:
        return {"success": True, "queued": False, "reason": "no_low_prices"}

    # Buscar usuario por FCM token
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.fcm_token == data.fcm_token)
        .order_by(UserProfile.updated_at.desc())
    )
    user = user_result.scalars().first()
    if not user:
        logger.warning(f"price-calendar: fcm_token no encontrado, guardando igualmente sin user")
        user_id = data.fcm_token  # fallback: usar token como id
    else:
        user_id = user.user_id

    min_price = min(d.price for d in low_days)

    # 1. Insertar snapshots (solo días "low")
    for dp in low_days:
        db.add(PriceSnapshot(
            user_id=user_id,
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
                PriceWatch.user_id == user_id,
                PriceWatch.origin == data.origin_iata,
                PriceWatch.destination == data.destination_iata,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()

    if watch is None:
        watch = PriceWatch(
            user_id=user_id,
            origin=data.origin_iata,
            destination=data.destination_iata,
            is_active=True,
            last_search_best_price=min_price,
            interest_score=0,
            notification_count=0,
        )
        db.add(watch)
        await db.flush()
        queued = False
    else:
        watch.is_active = True
        reference_price = watch.last_search_best_price

        queued = False
        if reference_price and reference_price > 0:
            queued = await notification_engine.evaluate_price_drop(
                db=db,
                user_id=user_id,
                origin=data.origin_iata,
                destination=data.destination_iata,
                new_price=min_price,
                reference_price=reference_price,
                currency=data.currency,
            )

        if reference_price is None or min_price < reference_price:
            watch.last_search_best_price = min_price

    await db.commit()

    logger.info(
        f"price-calendar: {data.origin_iata}->{data.destination_iata} "
        f"min={min_price:.0f} {data.currency} queued={queued}"
    )
    return {"success": True, "queued": queued, "min_price": min_price}


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
