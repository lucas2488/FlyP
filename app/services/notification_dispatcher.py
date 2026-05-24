import logging
import random
from datetime import datetime

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import NotificationQueue, NotificationLog, UserProfile, PriceWatch, NotificationTemplate
from app.services import firebase_service
from app.services.airport_resolver import resolve_airports
from app.config import settings

logger = logging.getLogger(__name__)

# Fallback hardcodeado — usado si la tabla notification_templates está vacía
# o si hubo un error de BD.
_FALLBACK_TEMPLATES: dict[str, dict[str, str]] = {
    "soft": {
        "title": "{origin} → {destination} bajó {pct}% ✈️",
        "body":  "Hoy desde {price} {currency}. ¡Buen momento para volar!",
    },
    "strong": {
        "title": "💸 Gran bajada: {origin} → {destination} bajó {pct}%",
        "body":  "Hoy desde {price} {currency}. No te lo pierdas.",
    },
    "urgent": {
        "title": "🔥 Precio mínimo: {origin} → {destination} bajó {pct}%",
        "body":  "¡Solo {price} {currency}! Esta oportunidad no dura.",
    },
}


async def _pick_template(
    db: AsyncSession,
    country_code: str | None,
    drop_level: str,
) -> dict[str, str]:
    """
    Selecciona un template aleatorio de la BD filtrando por país y nivel.

    Orden de prioridad:
      1. Templates activos para el país específico del usuario (ej: "MX")
      2. Templates en español rioplatense de Argentina ("AR") — fallback regional
         (se omite si ya era AR o si ya se intentó)
      3. Templates genéricos ("*")
      4. Fallback hardcodeado en memoria (si la BD no tiene ninguno)

    Esto asegura que usuarios sin país configurado reciben mensajes en jerga
    argentina, que es el mercado principal de la app.
    """
    # Países no hispanohablantes — no usar AR como fallback intermedio
    _non_spanish = frozenset({"BR"})

    codes_to_try: list[str] = []
    if country_code:
        codes_to_try.append(country_code)
    # Para países hispanohablantes (o sin país), caer a AR (mercado principal)
    # antes del genérico. Para países no hispanohablantes (ej: BR) ir directo a *.
    if country_code not in _non_spanish and country_code != "AR":
        codes_to_try.append("AR")
    codes_to_try.append("*")

    for code in codes_to_try:
        result = await db.execute(
            select(NotificationTemplate).where(
                and_(
                    NotificationTemplate.country_code == code,
                    NotificationTemplate.drop_level == drop_level,
                    NotificationTemplate.is_active.is_(True),
                )
            )
        )
        templates = result.scalars().all()
        if templates:
            chosen = random.choice(templates)
            return {"title": chosen.title_template, "body": chosen.body_template}

    # Fallback a templates hardcodeados en memoria
    return _FALLBACK_TEMPLATES.get(drop_level, _FALLBACK_TEMPLATES["soft"])


def _build_notification_payload(
    item: NotificationQueue,
    template: dict[str, str],
    origin_name: str,
    destination_name: str,
) -> tuple[str, str, dict]:
    """
    Construye title, body y data dict para el FCM message.
    El data dict matchea FlightNotification.kt del Android:
      link, origin, destination, description, imageUrl, price, company,
      notification_queue_id (para el endpoint /notifications/{id}/opened)
    """
    pct = int(item.pct_drop * 100)
    currency_symbol = item.currency
    price_formatted = f"${item.price_raw:,.0f}"

    fmt = dict(
        origin=origin_name,
        destination=destination_name,
        pct=pct,
        price=price_formatted,
        currency=currency_symbol,
    )
    title = template["title"].format(**fmt)
    body = template["body"].format(**fmt)

    data = {
        "link": f"https://www.flypromociones.com/search?origin={item.origin}&destination={item.destination}",
        "origin": item.origin,
        "destination": item.destination,
        "description": f"Bajó {pct}%",
        "imageUrl": "",
        "price": str(int(item.price_raw)),
        "company": "",
        "notification_queue_id": str(item.id),  # Android lo devuelve en POST /notifications/{id}/opened
    }
    return title, body, data


async def process_notification_queue() -> None:
    """
    APScheduler job (cada 30 min).
    Crea su propia sesión DB. Procesa hasta batch_size items pending.
    """
    logger.info("notification_dispatcher: processing queue...")
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotificationQueue)
            .where(
                NotificationQueue.status == "pending",
                # welcomes se procesan en welcome_service, no aquí
                NotificationQueue.notification_type != "welcome",
                # respetar scheduled_at si está seteado (ej: items con delay futuro)
                or_(
                    NotificationQueue.scheduled_at.is_(None),
                    NotificationQueue.scheduled_at <= now,
                ),
            )
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
    """Procesa un item de la queue: obtiene token, elige template, envía y actualiza estado."""
    # Obtener perfil de usuario (FCM token + país para selección de template)
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
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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

    # Seleccionar template según país del usuario y nivel de bajada
    drop_level = item.drop_level or "soft"
    template = await _pick_template(db, user.selected_country, drop_level)

    # Resolver nombres legibles de aeropuertos (ej: "EZE" → "Buenos Aires")
    airport_names    = await resolve_airports(db, [item.origin, item.destination])
    origin_name      = airport_names[item.origin]
    destination_name = airport_names[item.destination]

    # Construir y enviar
    title, body, data = _build_notification_payload(item, template, origin_name, destination_name)
    success = firebase_service.send_notification(user.fcm_token, title, body, data)

    now = datetime.utcnow()
    if success:
        item.status = "sent"
        item.sent_at = now

        # Actualizar price_watch: timestamp por nivel + legacy last_notified_at
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
            if drop_level == "soft":
                watch.last_notified_soft_at = now
            elif drop_level == "strong":
                watch.last_notified_strong_at = now
            elif drop_level == "urgent":
                watch.last_notified_urgent_at = now

        # Registrar en notification_log (para analytics)
        db.add(NotificationLog(
            user_id=item.user_id,
            type=drop_level,
            origin=item.origin,
            destination=item.destination,
            price=item.price_raw,
            delivery_status="sent",
        ))

        logger.info(
            f"Sent [{drop_level}] notification {item.id} "
            f"to user {item.user_id} [{user.selected_country or '*'}]: "
            f"{item.origin}->{item.destination}"
        )
    else:
        item.status = "failed"
        item.error_msg = "firebase_send_failed"
        logger.error(f"Failed notification {item.id} for user {item.user_id}")
