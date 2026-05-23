import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models import UserProfile, PriceWatch, NotificationQueue
from app.config import settings

logger = logging.getLogger(__name__)


async def evaluate_price_drop(
    db: AsyncSession,
    user_id: str,
    origin: str,
    destination: str,
    new_price: float,
    reference_price: float,
    currency: str,
    trip_type: str,
) -> bool:
    """
    Verifica condiciones y encola notificación si corresponde.
    Retorna True si se encoló una notificación.

    Condiciones para encolar:
      1. new_price < reference_price * (1 - threshold)
      2. El usuario tiene fcm_token registrado
      3. No fue notificado en las últimas N horas (cooldown)
      4. No hay ya un 'pending' para este user/origin/destination
    """
    # 1. Verificar caída de precio
    if reference_price <= 0:
        return False

    pct_drop = (reference_price - new_price) / reference_price
    if pct_drop < settings.price_drop_threshold:
        return False

    # 2. Verificar FCM token
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.fcm_token:
        logger.debug(f"No FCM token for user {user_id}, skipping notification")
        return False

    # 3. Verificar cooldown (last_notified_at)
    watch_result = await db.execute(
        select(PriceWatch).where(
            and_(
                PriceWatch.user_id == user_id,
                PriceWatch.origin == origin,
                PriceWatch.destination == destination,
            )
        )
    )
    watch = watch_result.scalar_one_or_none()
    if watch and watch.last_notified_at:
        cooldown_limit = datetime.now(timezone.utc) - timedelta(hours=settings.notification_cooldown_hours)
        # last_notified_at puede ser naive (sin tz) desde la DB
        last_notified = watch.last_notified_at
        if last_notified.tzinfo is None:
            last_notified = last_notified.replace(tzinfo=timezone.utc)
        if last_notified > cooldown_limit:
            logger.debug(f"Cooldown active for {user_id} {origin}->{destination}")
            return False

    # 4. Verificar que no haya ya un pending
    existing_result = await db.execute(
        select(NotificationQueue).where(
            and_(
                NotificationQueue.user_id == user_id,
                NotificationQueue.origin == origin,
                NotificationQueue.destination == destination,
                NotificationQueue.status == "pending",
            )
        )
    )
    if existing_result.scalar_one_or_none():
        logger.debug(f"Already pending notification for {user_id} {origin}->{destination}")
        return False

    # Todas las condiciones OK → encolar
    queue_item = NotificationQueue(
        user_id=user_id,
        origin=origin,
        destination=destination,
        price_raw=new_price,
        currency=currency,
        pct_drop=pct_drop,
        reference_price=reference_price,
        trip_type=trip_type,
        status="pending",
    )
    db.add(queue_item)
    await db.flush()

    logger.info(
        f"Queued price-drop notification: {user_id} {origin}->{destination} "
        f"{reference_price:.0f} → {new_price:.0f} ({pct_drop:.1%} drop)"
    )
    return True
