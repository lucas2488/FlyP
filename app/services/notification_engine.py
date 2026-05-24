import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models import UserProfile, PriceWatch, NotificationQueue
from app.config import settings

logger = logging.getLogger(__name__)

# Mapa nivel → (threshold_setting, cooldown_hours_setting, attr de timestamp en PriceWatch)
_LEVELS = [
    ("urgent", settings.price_drop_threshold_urgent, settings.notification_cooldown_urgent_hours, "last_notified_urgent_at"),
    ("strong", settings.price_drop_threshold_strong, settings.notification_cooldown_strong_hours, "last_notified_strong_at"),
    ("soft",   settings.price_drop_threshold_soft,   settings.notification_cooldown_soft_hours,   "last_notified_soft_at"),
]


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
    Verifica condiciones multi-nivel y encola notificación si corresponde.
    Retorna True si se encoló una notificación.

    Niveles (de mayor a menor):
      - urgent: bajada ≥ price_drop_threshold_urgent (15%), cooldown 6h
      - strong: bajada ≥ price_drop_threshold_strong (10%), cooldown 12h
      - soft:   bajada ≥ price_drop_threshold_soft   (5%),  cooldown 24h

    Condiciones para encolar:
      1. La bajada supera al menos el umbral más bajo (soft)
      2. El usuario tiene fcm_token registrado
      3. No fue notificado en el cooldown del nivel aplicable
      4. No hay ya un 'pending' para este user/origin/destination
    """
    if reference_price <= 0:
        return False

    pct_drop = (reference_price - new_price) / reference_price

    # 1. Determinar el nivel aplicable (el más alto que supera el umbral)
    level = None
    cooldown_hours = 0
    watch_ts_attr = None
    for lvl_name, threshold, cooldown, ts_attr in _LEVELS:
        if pct_drop >= threshold:
            level = lvl_name
            cooldown_hours = cooldown
            watch_ts_attr = ts_attr
            break  # ya encontramos el más alto

    if level is None:
        return False  # por debajo del umbral mínimo (soft)

    # 2. Verificar FCM token
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.fcm_token:
        logger.debug(f"No FCM token for user {user_id}, skipping notification")
        return False

    # 3. Verificar cooldown por nivel
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
    if watch and watch_ts_attr:
        last_ts = getattr(watch, watch_ts_attr, None)
        if last_ts is not None:
            cooldown_limit = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            if last_ts > cooldown_limit:
                logger.debug(
                    f"Cooldown ({level}) active for {user_id} {origin}->{destination}: "
                    f"last={last_ts}, limit={cooldown_limit}"
                )
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
        drop_level=level,
        notification_type="price_drop",
    )
    db.add(queue_item)
    await db.flush()

    logger.info(
        f"Queued price-drop notification [{level}]: {user_id} {origin}->{destination} "
        f"{reference_price:.0f} → {new_price:.0f} ({pct_drop:.1%} drop)"
    )
    return True
