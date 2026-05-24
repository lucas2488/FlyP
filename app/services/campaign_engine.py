"""
campaign_engine.py — Motor de envío de campañas masivas.

Función pública:
    execute_campaign(campaign_id: int) → None

Lógica de exclusión anti-spam (por usuario):
  • Rutas usadas en campaign_sends de los últimos 14 días
  • Rutas enviadas como price-drop (notification_log) en los últimos 7 días

Selección de ruta:
  • Elegir aleatoriamente entre las top-3 rutas más buscadas del usuario
    que no estén excluidas (rotación para evitar siempre la misma ruta)
  • Fallback a category_tag de la campaña si no quedan rutas válidas
  • Fallback final a las rutas más populares globales del segmento

Idempotencia:
  • Antes de cada envío verifica que no exista ya un campaign_send
    para ese (campaign_id, user_id) con status=sent.
"""

import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    Campaign,
    CampaignSend,
    NotificationLog,
    NotificationTemplate,
    SearchEvent,
    UserProfile,
)
from app.services import firebase_service

logger = logging.getLogger(__name__)

# Días de exclusión por tipo de envío previo
_CAMPAIGN_COOLDOWN_DAYS = 14
_PRICE_DROP_COOLDOWN_DAYS = 7


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------

async def execute_campaign(campaign_id: int) -> None:
    """
    Ejecuta la campaña: carga usuarios del segmento, les envía una
    notificación personalizada y registra cada intento en campaign_sends.
    Actualiza campaign.status al finalizar.
    Crea su propia sesión async para poder correr desde APScheduler.
    """
    logger.info(f"campaign_engine: iniciando campaña {campaign_id}")

    async with AsyncSessionLocal() as db:
        # Cargar campaña
        campaign = await db.get(Campaign, campaign_id)
        if not campaign:
            logger.error(f"campaign_engine: campaña {campaign_id} no encontrada")
            return
        if campaign.status not in ("draft", "scheduled"):
            logger.warning(
                f"campaign_engine: campaña {campaign_id} tiene status={campaign.status}, saltando"
            )
            return

        # Marcar como enviando
        campaign.status = "sending"
        await db.commit()

        try:
            sent, failed, skipped = await _send_to_segment(db, campaign)
            campaign.status = "sent"
            campaign.sent_at = datetime.utcnow()
            await db.commit()
            logger.info(
                f"campaign_engine: campaña {campaign_id} completada — "
                f"sent={sent} failed={failed} skipped={skipped}"
            )
        except Exception as exc:
            campaign.status = "failed"
            await db.commit()
            logger.error(f"campaign_engine: campaña {campaign_id} falló: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# Envío al segmento
# ---------------------------------------------------------------------------

async def _send_to_segment(db: AsyncSession, campaign: Campaign) -> tuple[int, int, int]:
    """Itera sobre los usuarios elegibles y envía la notificación."""
    # Obtener usuarios del segmento con FCM token
    q = select(UserProfile).where(
        UserProfile.fcm_token.isnot(None),
        UserProfile.fcm_token != "",
    )
    if campaign.segment:
        q = q.where(UserProfile.user_segment == campaign.segment)

    result = await db.execute(q)
    users: list[UserProfile] = list(result.scalars().all())
    logger.info(
        f"campaign_engine: campaña {campaign.id} — {len(users)} usuarios en segmento '{campaign.segment}'"
    )

    sent = failed = skipped = 0

    for user in users:
        try:
            outcome = await _send_to_user(db, campaign, user)
            if outcome == "sent":
                sent += 1
            elif outcome == "failed":
                failed += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(
                f"campaign_engine: error enviando a user {user.user_id}: {exc}"
            )
            failed += 1

    return sent, failed, skipped


async def _send_to_user(db: AsyncSession, campaign: Campaign, user: UserProfile) -> str:
    """
    Envía la notificación a un usuario individual.
    Devuelve: 'sent' | 'failed' | 'skipped'
    """
    # Idempotencia: ¿ya fue enviado?
    exists = await db.execute(
        select(CampaignSend).where(
            CampaignSend.campaign_id == campaign.id,
            CampaignSend.user_id == user.user_id,
            CampaignSend.status == "sent",
        )
    )
    if exists.scalar_one_or_none():
        return "skipped"

    # Crear registro en campaign_sends (estado pending)
    cs = CampaignSend(
        campaign_id=campaign.id,
        user_id=user.user_id,
        status="pending",
    )
    db.add(cs)
    await db.flush()  # para obtener cs.id

    # Elegir ruta para este usuario
    origin, destination = await _pick_route(db, user, campaign)
    if not origin or not destination:
        cs.status = "skipped"
        await db.commit()
        return "skipped"

    # Resolver nombres legibles
    from app.services.airport_resolver import resolve_airports
    names = await resolve_airports(db, [origin, destination])
    origin_name = names.get(origin, origin)
    destination_name = names.get(destination, destination)

    # Seleccionar template de campaña
    template = await _pick_template(db, user, campaign)
    if not template:
        cs.status = "skipped"
        await db.commit()
        return "skipped"

    # Formatear mensaje
    fmt = {
        "origin": origin_name,
        "destination": destination_name,
        "origin_iata": origin,
        "destination_iata": destination,
    }
    try:
        title = template.title_template.format(**fmt)
        body = template.body_template.format(**fmt)
    except KeyError:
        # Template tiene variables que no están disponibles para campaigns
        title = template.title_template
        body = template.body_template

    # Enviar FCM
    try:
        await firebase_service.send_notification(
            token=user.fcm_token,
            title=title,
            body=body,
            data={
                "type": "campaign",
                "campaign_id": str(campaign.id),
                "origin_iata": origin,
                "destination_iata": destination,
                "click_action": "OPEN_SEARCH",
            },
        )
        cs.status = "sent"
        cs.sent_at = datetime.utcnow()
        cs.fcm_response = "ok"
        await db.commit()
        return "sent"

    except Exception as exc:
        cs.status = "failed"
        cs.fcm_response = str(exc)[:500]
        await db.commit()
        logger.debug(f"campaign_engine: FCM falló para {user.user_id}: {exc}")
        return "failed"


# ---------------------------------------------------------------------------
# Selección de ruta con anti-repetición
# ---------------------------------------------------------------------------

async def _pick_route(
    db: AsyncSession,
    user: UserProfile,
    campaign: Campaign,
) -> tuple[str | None, str | None]:
    """
    Devuelve (origin, destination) para el usuario dado el tipo de campaña.
    Aplica exclusiones de cooldown y rotación entre top-3.
    """
    # Ruta fija en la campaña
    if campaign.campaign_type == "route" and campaign.route_origin and campaign.route_destination:
        excluded = await get_excluded_routes(db, user.user_id)
        key = (campaign.route_origin, campaign.route_destination)
        if key in excluded:
            return None, None
        return campaign.route_origin, campaign.route_destination

    # Ruta automática (top rutas del usuario)
    excluded = await get_excluded_routes(db, user.user_id)

    # Top-5 rutas buscadas por el usuario
    top_routes_result = await db.execute(
        select(
            SearchEvent.origin,
            SearchEvent.destination,
            func.count().label("cnt"),
        )
        .where(SearchEvent.user_id == user.user_id)
        .group_by(SearchEvent.origin, SearchEvent.destination)
        .order_by(func.count().desc())
        .limit(5)
    )
    top_routes = [
        (row.origin, row.destination)
        for row in top_routes_result.all()
        if (row.origin, row.destination) not in excluded
    ]

    if top_routes:
        # Rotación: elegir aleatoriamente entre las disponibles (hasta top-3)
        pool = top_routes[:3]
        return random.choice(pool)

    # Fallback: ruta más popular global del segmento
    fallback_result = await db.execute(
        select(
            SearchEvent.origin,
            SearchEvent.destination,
            func.count().label("cnt"),
        )
        .join(UserProfile, UserProfile.user_id == SearchEvent.user_id)
        .where(UserProfile.user_segment == (campaign.segment or "casual"))
        .group_by(SearchEvent.origin, SearchEvent.destination)
        .order_by(func.count().desc())
        .limit(10)
    )
    global_routes = [
        (row.origin, row.destination)
        for row in fallback_result.all()
        if (row.origin, row.destination) not in excluded
    ]
    if global_routes:
        return random.choice(global_routes[:3])

    return None, None


# ---------------------------------------------------------------------------
# Exclusiones de rutas — función reutilizable
# ---------------------------------------------------------------------------

async def get_excluded_routes(db: AsyncSession, user_id: str) -> set[tuple[str, str]]:
    """
    Devuelve el conjunto de rutas que NO deben enviarse al usuario:
      • Rutas en campaign_sends de los últimos 14 días
      • Rutas en notification_log (price-drops) de los últimos 7 días
    """
    now = datetime.utcnow()
    excluded: set[tuple[str, str]] = set()

    # 1. Rutas de campañas recientes
    campaign_cutoff = now - timedelta(days=_CAMPAIGN_COOLDOWN_DAYS)
    cs_result = await db.execute(
        select(Campaign.route_origin, Campaign.route_destination)
        .join(CampaignSend, CampaignSend.campaign_id == Campaign.id)
        .where(
            CampaignSend.user_id == user_id,
            CampaignSend.status == "sent",
            CampaignSend.sent_at >= campaign_cutoff,
            Campaign.route_origin.isnot(None),
            Campaign.route_destination.isnot(None),
        )
    )
    for row in cs_result.all():
        if row.route_origin and row.route_destination:
            excluded.add((row.route_origin, row.route_destination))

    # 2. Rutas de price-drops recientes
    pd_cutoff = now - timedelta(days=_PRICE_DROP_COOLDOWN_DAYS)
    pd_result = await db.execute(
        select(NotificationLog.origin, NotificationLog.destination)
        .where(
            NotificationLog.user_id == user_id,
            NotificationLog.type != "reengagement",
            NotificationLog.sent_at >= pd_cutoff,
            NotificationLog.origin.isnot(None),
            NotificationLog.destination.isnot(None),
        )
    )
    for row in pd_result.all():
        if row.origin and row.destination:
            excluded.add((row.origin, row.destination))

    return excluded


async def get_fallback_category(db: AsyncSession, user_id: str) -> str | None:
    """
    Devuelve un category_tag de fallback basado en el historial de búsqueda del usuario.
    Actualmente mapea destinos populares a categorías conocidas.
    """
    # Destino más buscado en los últimos 30 días
    cutoff = datetime.utcnow() - timedelta(days=30)
    result = await db.execute(
        select(SearchEvent.destination, func.count().label("cnt"))
        .where(
            SearchEvent.user_id == user_id,
            SearchEvent.occurred_at >= cutoff,
        )
        .group_by(SearchEvent.destination)
        .order_by(func.count().desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None

    # Mapeo simple destino → categoría
    beach_codes = {"CUN", "PMV", "MAR", "PUJ", "FLL", "MIA", "GRU", "GIG", "SDQ", "HAV"}
    if row.destination in beach_codes:
        return "playa"
    return "destinos_populares"


# ---------------------------------------------------------------------------
# Selección de template de campaña
# ---------------------------------------------------------------------------

async def _pick_template(
    db: AsyncSession,
    user: UserProfile,
    campaign: Campaign,
) -> NotificationTemplate | None:
    """
    Selecciona un template de campaña (drop_level='campaign').
    Fallback chain: país del usuario → AR → * → None
    """
    country = user.selected_country or "AR"

    for cc in [country, "AR", "*"]:
        result = await db.execute(
            select(NotificationTemplate)
            .where(
                NotificationTemplate.country_code == cc,
                NotificationTemplate.drop_level == "campaign",
                NotificationTemplate.is_active.is_(True),
            )
        )
        templates = result.scalars().all()
        if templates:
            return random.choice(list(templates))

    return None
