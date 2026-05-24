"""
welcome_service.py — Notificaciones de bienvenida con delay de 24h.

Flujo:
  1. POST /profile detecta usuario nuevo con fcm_token
     → llama a enqueue_welcome_notification(db, user_id)
     → inserta NotificationQueue(notification_type='welcome', scheduled_at=now+24h)

  2. process_welcome_notifications() corre cada 1h (APScheduler).
     → busca entradas welcome con scheduled_at <= now y status='pending'
     → por cada una: elige top ruta del país, elige template 'welcome', envía FCM
     → idempotencia: verifica notification_log antes de enviar (no duplicar)

Notas:
  - Usa datetime.utcnow() (naive) — las columnas son TIMESTAMP WITHOUT TIME ZONE.
  - Si el usuario no tiene FCM token al momento del envío → status='skipped'.
  - Si ya recibió welcome (notification_log.type='welcome') → status='skipped'.
"""

import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import NotificationLog, NotificationQueue, NotificationTemplate, PriceWatch, UserProfile
from app.services import firebase_service

logger = logging.getLogger(__name__)

_WELCOME_DELAY_HOURS = 24

# Fallback si no hay templates en la BD
_FALLBACK_WELCOME = {
    "title": "✈️ ¡Bienvenido a FlyPromociones!",
    "body": "Activamos tus alertas de precio. ¡Te avisamos cuando baje tu vuelo!",
}


# ---------------------------------------------------------------------------
# Job público — llamado desde APScheduler
# ---------------------------------------------------------------------------

async def process_welcome_notifications() -> None:
    """
    APScheduler job (cada 1h).
    Procesa welcomes pendientes cuyo scheduled_at ya llegó.
    """
    logger.info("welcome_service: checking welcome queue...")
    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotificationQueue)
            .where(
                NotificationQueue.notification_type == "welcome",
                NotificationQueue.status == "pending",
                or_(
                    NotificationQueue.scheduled_at.is_(None),
                    NotificationQueue.scheduled_at <= now,
                ),
            )
            .order_by(NotificationQueue.created_at)
            .limit(100)
        )
        items = result.scalars().all()

        if not items:
            logger.info("welcome_service: nothing pending")
            return

        logger.info(f"welcome_service: processing {len(items)} welcome notifications")
        for item in items:
            await _send_welcome(db, item, now)

        await db.commit()

    logger.info("welcome_service: done")


# ---------------------------------------------------------------------------
# Función pública — llamada desde profile.py
# ---------------------------------------------------------------------------

async def enqueue_welcome_notification(db: AsyncSession, user_id: str) -> None:
    """
    Encola una notificación de bienvenida con delay de 24h.
    Idempotente: no crea duplicados si ya existe una entrada para el usuario.
    Debe llamarse dentro de la misma sesión de profile.py (antes del commit).
    """
    # Idempotencia: ¿ya existe entrada welcome para este usuario?
    existing = await db.execute(
        select(NotificationQueue.id)
        .where(
            NotificationQueue.user_id == user_id,
            NotificationQueue.notification_type == "welcome",
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        logger.debug(f"welcome_service: welcome already queued for {user_id}")
        return

    scheduled = datetime.utcnow() + timedelta(hours=_WELCOME_DELAY_HOURS)
    db.add(NotificationQueue(
        user_id=user_id,
        notification_type="welcome",
        drop_level="welcome",
        scheduled_at=scheduled,
        status="pending",
        # Campos requeridos por el modelo — placeholders para tipo welcome
        origin="*",
        destination="*",
        price_raw=0.0,
        currency="",
        pct_drop=0.0,
        reference_price=0.0,
        trip_type="welcome",
    ))
    logger.info(f"welcome_service: enqueued welcome for {user_id}, fires at {scheduled.isoformat()}")


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

async def _send_welcome(db: AsyncSession, item: NotificationQueue, now: datetime) -> None:
    """Intenta enviar la notificación de bienvenida para un item de la queue."""

    # 1. Obtener perfil (FCM token + país)
    user = await db.get(UserProfile, item.user_id)
    if not user or not user.fcm_token:
        item.status = "skipped"
        item.error_msg = "no_fcm_token"
        logger.info(f"welcome_service: skipped {item.id} — no FCM token for {item.user_id}")
        return

    # 2. Idempotencia: ¿ya recibió welcome?
    already = await db.execute(
        select(NotificationLog.id)
        .where(
            NotificationLog.user_id == item.user_id,
            NotificationLog.type == "welcome",
        )
        .limit(1)
    )
    if already.scalar_one_or_none() is not None:
        item.status = "skipped"
        item.error_msg = "already_welcomed"
        logger.info(f"welcome_service: skipped {item.id} — user {item.user_id} already welcomed")
        return

    # 3. Top ruta para el país del usuario
    country = user.selected_country or "AR"
    route = await _get_top_route_for_country(db, country, item.user_id)

    # 4. Template de bienvenida
    template = await _pick_welcome_template(db, country)

    # 5. Payload FCM — título y body del template, ruta como deep-link en data
    title = template["title"]
    body = template["body"]
    data: dict = {
        "link": "https://www.flypromociones.com/",
        "origin": route[0] if route else "",
        "destination": route[1] if route else "",
        "description": "¡Bienvenido!",
        "imageUrl": "",
        "price": "",
        "company": "",
        "notification_queue_id": str(item.id),
    }

    # 6. Enviar
    success = firebase_service.send_notification(user.fcm_token, title, body, data)

    if success:
        item.status = "sent"
        item.sent_at = now
        db.add(NotificationLog(
            user_id=item.user_id,
            type="welcome",
            origin=route[0] if route else None,
            destination=route[1] if route else None,
            price=None,
            delivery_status="sent",
        ))
        logger.info(
            f"welcome_service: sent welcome to {item.user_id} [{country}]"
            + (f" route={route[0]}->{route[1]}" if route else " no-route")
        )
    else:
        item.status = "failed"
        item.error_msg = "firebase_send_failed"
        logger.error(f"welcome_service: FCM send failed for {item.user_id}")


async def _get_top_route_for_country(
    db: AsyncSession,
    country: str,
    user_id: str,
) -> tuple[str, str] | None:
    """
    Devuelve la ruta más popular entre usuarios del mismo país (excluyendo al propio usuario).
    Fallback: top ruta global. Devuelve None si no hay datos.
    Elige aleatoriamente entre el top 3 para dar variedad.
    """
    # Top rutas por país
    country_result = await db.execute(
        select(
            PriceWatch.origin,
            PriceWatch.destination,
            func.count().label("cnt"),
        )
        .join(UserProfile, UserProfile.user_id == PriceWatch.user_id)
        .where(
            UserProfile.selected_country == country,
            PriceWatch.is_active.is_(True),
            PriceWatch.user_id != user_id,
        )
        .group_by(PriceWatch.origin, PriceWatch.destination)
        .order_by(func.count().desc())
        .limit(3)
    )
    rows = country_result.all()
    if rows:
        chosen = random.choice(rows)
        return (chosen.origin, chosen.destination)

    # Fallback: top global
    global_result = await db.execute(
        select(
            PriceWatch.origin,
            PriceWatch.destination,
            func.count().label("cnt"),
        )
        .where(
            PriceWatch.is_active.is_(True),
            PriceWatch.user_id != user_id,
        )
        .group_by(PriceWatch.origin, PriceWatch.destination)
        .order_by(func.count().desc())
        .limit(3)
    )
    global_rows = global_result.all()
    if global_rows:
        chosen = random.choice(global_rows)
        return (chosen.origin, chosen.destination)

    return None


async def _pick_welcome_template(db: AsyncSession, country: str) -> dict[str, str]:
    """
    Selecciona un template welcome al azar para el país dado.
    Cadena de fallback: país → AR (hispanohablantes) → * → hardcoded.
    """
    _non_spanish = frozenset({"BR"})

    codes_to_try = [country]
    if country not in _non_spanish and country != "AR":
        codes_to_try.append("AR")
    codes_to_try.append("*")

    for code in codes_to_try:
        result = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.country_code == code,
                NotificationTemplate.drop_level == "welcome",
                NotificationTemplate.is_active.is_(True),
            )
        )
        templates = result.scalars().all()
        if templates:
            t = random.choice(templates)
            return {"title": t.title_template, "body": t.body_template}

    return _FALLBACK_WELCOME
