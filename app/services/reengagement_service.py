"""
Re-engagement push notifications — estilo "abandono de carrito".

Cuando un usuario busca vuelos y no selecciona ninguno en los siguientes ~20 min,
le enviamos un push recordándole el precio que vio.

Job: process_reengagement_queue() — corre cada 5 min via APScheduler.

Lógica:
  1. Buscar search_result events ocurridos entre 20 y 25 min atrás, no re-engageados aún.
  2. Para cada uno, verificar que NO existe un flight_selected posterior en la misma ruta.
  3. Verificar cap diario (max 1 re-engagement por usuario por día).
  4. Elegir template según país del usuario (tabla notification_templates).
  5. Enviar FCM y registrar en notification_log.
"""
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import SearchEvent, UserProfile, NotificationLog, NotificationTemplate
from app.services import firebase_service
from app.services.airport_resolver import resolve_airports
from app.config import settings

logger = logging.getLogger(__name__)

# Fallback si la BD no tiene templates de reengagement
_FALLBACK_REENGAGEMENT = {
    "title": "¿Todavía pensás en volar a {destination}? 🛫",
    "body":  "El precio más bajo era {price} {currency}.",
}


async def _pick_reengagement_template(
    db: AsyncSession,
    country_code: str | None,
) -> dict[str, str]:
    """
    Selecciona un template de re-engagement aleatorio.

    Prioridad:
      1. País específico del usuario
      2. Argentina ("AR") — fallback regional / mercado principal
      3. Genérico ("*")
      4. Hardcodeado en memoria
    """
    _non_spanish = frozenset({"BR"})

    codes_to_try: list[str] = []
    if country_code:
        codes_to_try.append(country_code)
    if country_code not in _non_spanish and country_code != "AR":
        codes_to_try.append("AR")
    codes_to_try.append("*")

    for code in codes_to_try:
        result = await db.execute(
            select(NotificationTemplate).where(
                and_(
                    NotificationTemplate.country_code == code,
                    NotificationTemplate.drop_level == "reengagement",
                    NotificationTemplate.is_active.is_(True),
                )
            )
        )
        templates = result.scalars().all()
        if templates:
            chosen = random.choice(templates)
            return {"title": chosen.title_template, "body": chosen.body_template}

    return _FALLBACK_REENGAGEMENT


async def process_reengagement_queue() -> None:
    """
    APScheduler job (cada reengagement_check_interval_minutes minutos).
    Busca búsquedas sin compra en la ventana de re-engagement y envía push.
    """
    logger.info("reengagement_service: checking for candidates...")
    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()  # naive UTC — coincide con TIMESTAMP WITHOUT TIME ZONE en DB
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

    # 3. Obtener perfil (FCM token + país para selección de template)
    user_result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == event.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.fcm_token:
        event.notified_reengagement_at = now
        logger.debug(f"Reengagement skip (no FCM token): user={event.user_id}")
        return

    # 4. Seleccionar template según país del usuario
    template = await _pick_reengagement_template(db, user.selected_country)

    price_str = f"${event.best_price:,.0f}" if event.best_price else "un gran precio"
    currency = event.currency or ""

    # Resolver nombres legibles de aeropuertos (ej: "PSS" → "Posadas")
    airport_names    = await resolve_airports(db, [event.origin, event.destination])
    origin_name      = airport_names[event.origin]
    destination_name = airport_names[event.destination]

    fmt = dict(
        origin=origin_name,
        destination=destination_name,
        price=price_str,
        currency=currency,
    )
    title = template["title"].format(**fmt)
    body = template["body"].format(**fmt)

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
            f"Reengagement sent: user={event.user_id} [{user.selected_country or '*'}] "
            f"{event.origin}->{event.destination} price={price_str}"
        )
    else:
        logger.error(
            f"Reengagement FCM failed: user={event.user_id} "
            f"{event.origin}->{event.destination}"
        )
