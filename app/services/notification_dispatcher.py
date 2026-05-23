import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import NotificationQueue, UserProfile, PriceWatch
from app.services import firebase_service
from app.config import settings

logger = logging.getLogger(__name__)


def _build_notification_payload(item: NotificationQueue) -> tuple[str, str, dict]:
    """
    Construye title, body y data dict para el FCM message.
    El data dict matchea FlightNotification.kt del Android:
      link, origin, destination, description, imageUrl, price, company
    """
    pct = int(item.pct_drop * 100)
    currency_symbol = "ARS" if item.currency == "ARS" else item.currency
    price_formatted = f"${item.price_raw:,.0f}"

    title = f"{item.origin} → {item.destination} bajó {pct}%"
    body = f"Hoy desde {price_formatted} {currency_symbol}"

    data = {
        "link": f"https://www.flypromociones.com/search?origin={item.origin}&destination={item.destination}",
        "origin": item.origin,
        "destination": item.destination,
        "description": f"Bajó {pct}%",
        "imageUrl": "",
        "price": str(int(item.price_raw)),
        "company": "",
    }
    return title, body, data


async def process_notification_queue() -> None:
    """
    APScheduler job (cada 30 min).
    Crea su propia sesión DB. Procesa hasta batch_size items pending.
    """
    logger.info("notification_dispatcher: processing queue...")
    async with AsyncSessionLocal() as db:
        # Obtener pending items
        result = await db.execute(
            select(NotificationQueue)
            .where(NotificationQueue.status == "pending")
            .order_by(NotificationQueue.created_at)
            .limit(settings.notification_queue_batch_size)
        )
        queue_items = result.scalars().all()

        if not queue_items:
            logger.info("notification_dispatcher: queue empty, nothing to do")
            return

        logger.info(f"notification_dispatcher: processing {len(queue_items)} items")

        for item in queue_items:
            await _process_single(db, item)

        await db.commit()
    logger.info("notification_dispatcher: done")


async def _process_single(db: AsyncSession, item: NotificationQueue) -> None:
    """Procesa un item de la queue: obtiene token, envía y actualiza estado."""
    # Obtener FCM token
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == item.user_id)
    )
    user = user_result.scalar_one_or_none()

    if not user or not user.fcm_token:
        item.status = "skipped"
        item.error_msg = "no_fcm_token"
        logger.warning(f"Skipped {item.id}: no FCM token for user {item.user_id}")
        return

    # Verificar max notificaciones por día
    from sqlalchemy import func as sqlfunc
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(sqlfunc.count(NotificationQueue.id)).where(
            and_(
                NotificationQueue.user_id == item.user_id,
                NotificationQueue.status == "sent",
                NotificationQueue.sent_at >= today_start,
            )
        )
    )
    sent_today = count_result.scalar_one()
    if sent_today >= settings.max_notifications_per_user_per_day:
        item.status = "skipped"
        item.error_msg = f"daily_limit_reached ({sent_today})"
        logger.info(f"Skipped {item.id}: daily limit for user {item.user_id}")
        return

    # Construir y enviar
    title, body, data = _build_notification_payload(item)
    success = firebase_service.send_notification(user.fcm_token, title, body, data)

    now = datetime.now(timezone.utc)
    if success:
        item.status = "sent"
        item.sent_at = now
        # Actualizar price_watch
        watch_result = await db.execute(
            select(PriceWatch).where(
                and_(
                    PriceWatch.user_id == item.user_id,
                    PriceWatch.origin == item.origin,
                    PriceWatch.destination == item.destination,
                )
            )
        )
        watch = watch_result.scalar_one_or_none()
        if watch:
            watch.last_notified_at = now
            watch.notification_count = (watch.notification_count or 0) + 1
        logger.info(f"Sent notification {item.id} to user {item.user_id}: {item.origin}->{item.destination}")
    else:
        item.status = "failed"
        item.error_msg = "firebase_send_failed"
        logger.error(f"Failed notification {item.id} for user {item.user_id}")
