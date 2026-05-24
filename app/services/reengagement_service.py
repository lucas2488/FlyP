"""
Re-engagement push notifications — estilo "abandono de carrito".

Cuando un usuario busca vuelos y no selecciona ninguno en los siguientes ~20 min,
le enviamos un push recordándole el precio que vio.

Job: process_reengagement_queue() — corre cada 5 min via APScheduler.

Lógica:
  1. Buscar search_result events ocurridos entre 20 y 25 min atrás, no re-engageados aún.
  2. Para cada uno, verificar que NO existe un flight_selected posterior en la misma ruta.
  3. Verificar cap diario (max 1 re-engagement por usuario por día).
  4. Enviar FCM y registrar en notification_log.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import SearchEvent, UserProfile, NotificationLog
from app.services import firebase_service
from app.config import settings

logger = logging.getLogger(__name__)


async def process_reengagement_queue() -> None:
    """
    APScheduler job (cada reengagement_check_interval_minutes minutos).
    Busca búsquedas sin compra en la ventana de re-engagement y envía push.
    """
    logger.info("reengagement_service: checking for candidates...")
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        window_max = now - timedelta(minutes=settings.reengagement_window_min_minutes)
        window_min = now - timedelta(minutes=settings.reengagement_window_max_minutes)

        # Buscar search_result events en la ventana, todavía no re-engageados
        candidates_result = await db.execute(
            select(SearchEvent).where(
                and_(
                    SearchEvent.event_type == "search_result",
                    SearchEvent.occurred_at >= window_min,
                    SearchEvent.occurred_at <= window_max,
                    SearchEvent.notified_reengagement_at.is_(None),
                )
            )
        )
        candidates = candidates_result.scalars().all()

        if not candidates:
            logger.info("reengagement_service: no candidates in window")
            return

        logger.info(f"reengagement_service: evaluating {len(candidates)} candidates")
        for event in candidates:
            await _process_candidate(db, event, now)

        await db.commit()
    logger.info("reengagement_service: done")


async def _process_candidate(db: AsyncSession, event: SearchEvent, now: datetime) -> None:
    """Evalúa y (si corresponde) envía el push de re-engagement para un search_result."""

    # 1. Verificar que no existe un flight_selected posterior para la misma ruta
    selected_result = await db.execute(
        select(SearchEvent.id).where(
            and_(
                SearchEvent.user_id == event.user_id,
                SearchEvent.origin == event.origin,
                SearchEvent.destination == event.destination,
                SearchEvent.event_type == "flight_selected",
                SearchEvent.occurred_at > event.occurred_at,
            )
        ).limit(1)
    )
    if selected_result.scalar_one_or_none() is not None:
        # El usuario ya seleccionó un vuelo → no molestar
        event.notified_reengagement_at = now
        logger.debug(
            f"Reengagement skip (flight selected): user={event.user_id} "
            f"{event.origin}->{event.destination}"
        )
        return

    # 2. Verificar cap diario de re-engagements
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cap_result = await db.execute(
        select(sqlfunc.count(NotificationLog.id)).where(
            and_(
                NotificationLog.user_id == event.user_id,
                NotificationLog.type == "reengagement",
                NotificationLog.sent_at >= today_start,
            )
        )
    )
    sent_today = cap_result.scalar_one()
    if sent_today >= settings.max_reengagements_per_user_per_day:
        event.notified_reengagement_at = now  # marcar para no volver a evaluar
        logger.debug(
            f"Reengagement skip (daily cap): user={event.user_id}, sent_today={sent_today}"
        )
        return

    # 3. Obtener FCM token
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == event.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.fcm_token:
        event.notified_reengagement_at = now
        logger.debug(f"Reengagement skip (no FCM token): user={event.user_id}")
        return

    # 4. Construir y enviar FCM
    price_str = f"${event.best_price:,.0f}" if event.best_price else "un gran precio"
    currency = event.currency or ""

    title = f"¿Todavía pensás en volar a {event.destination}? 🛫"
    body = f"El precio más bajo era {price_str} {currency}".strip()
    data = {
        "origin": event.origin,
        "destination": event.destination,
        "type": "reengagement",
        "link": (
            f"https://www.flypromociones.com/search"
            f"?origin={event.origin}&destination={event.destination}"
        ),
    }

    success = firebase_service.send_notification(user.fcm_token, title, body, data)

    # 5. Marcar siempre (éxito o fallo) para no re-intentar
    event.notified_reengagement_at = now

    # 6. Registrar en notification_log
    db.add(NotificationLog(
        user_id=event.user_id,
        type="reengagement",
        origin=event.origin,
        destination=event.destination,
        price=event.best_price,
        delivery_status="sent" if success else "failed",
    ))

    if success:
        logger.info(
            f"Reengagement sent: user={event.user_id} "
            f"{event.origin}->{event.destination} price={price_str}"
        )
    else:
        logger.error(
            f"Reengagement FCM failed: user={event.user_id} "
            f"{event.origin}->{event.destination}"
        )
