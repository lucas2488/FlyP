"""
Router de campañas de marketing.

Todos los endpoints requieren X-API-Key (mismo key que analytics/admin).

Endpoints CRUD:
  GET    /admin/campaigns                      — listar (filtros: status, segment)
  POST   /admin/campaigns                      — crear (draft)
  GET    /admin/campaigns/{id}                 — detalle
  PATCH  /admin/campaigns/{id}                 — actualizar (nombre, fecha, segmento, etc.)
  DELETE /admin/campaigns/{id}                 — solo si status=draft

Acciones:
  POST   /admin/campaigns/{id}/send            — dispara execute_campaign() async
  GET    /admin/campaigns/{id}/stats           — sent, opened, failed, open_rate_pct

Calendario / cooldown (para el dashboard):
  GET    /admin/campaigns/calendar             — próximas 4 semanas + fechas especiales próximas
  GET    /admin/campaigns/cooldown-routes      — rutas en cooldown (14d campaign + 7d price-drop)
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
# Nota: usar datetime.utcnow() (naive) para comparar con columnas TIMESTAMP WITHOUT TIME ZONE

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Campaign, CampaignCalendar, CampaignSend, NotificationLog, UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/campaigns", tags=["campaigns"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != settings.analytics_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    segment: str | None = None
    campaign_type: str | None = Field(None, pattern="^(route|top_auto|category)?$")
    route_origin: str | None = Field(None, max_length=10)
    route_destination: str | None = Field(None, max_length=10)
    category_tag: str | None = Field(None, max_length=100)
    scheduled_at: datetime | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    segment: str | None = None
    campaign_type: str | None = Field(None, pattern="^(route|top_auto|category)?$")
    route_origin: str | None = Field(None, max_length=10)
    route_destination: str | None = Field(None, max_length=10)
    category_tag: str | None = Field(None, max_length=100)
    scheduled_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _campaign_to_dict(c: Campaign) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "segment": c.segment,
        "status": c.status,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
        "campaign_type": c.campaign_type,
        "route_origin": c.route_origin,
        "route_destination": c.route_destination,
        "category_tag": c.category_tag,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /admin/campaigns/calendar  ← ANTES que /{id} para que no haya conflicto
# ---------------------------------------------------------------------------

@router.get("/calendar")
async def get_campaign_calendar(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    """
    Devuelve los slots del calendario para las próximas 4 semanas.
    Incluye slots semanales y fechas especiales próximas (30 días).
    """
    today = date.today()
    result = await db.execute(
        select(CampaignCalendar).where(CampaignCalendar.is_active.is_(True))
    )
    slots = result.scalars().all()

    weekly = []
    special = []
    cutoff_30d = today + timedelta(days=30)

    for slot in slots:
        if slot.slot_type == "weekly":
            # Calcular próxima ocurrencia
            days_ahead = (slot.day_of_week - today.weekday()) % 7
            next_date = today + timedelta(days=days_ahead)
            weekly.append({
                "id": slot.id,
                "slot_type": "weekly",
                "day_of_week": slot.day_of_week,
                "send_hour_ar": slot.send_hour_ar,
                "label": slot.label,
                "next_date": next_date.isoformat(),
            })
        elif slot.slot_type == "special" and slot.special_date:
            trigger = slot.special_date - timedelta(days=slot.advance_days)
            if trigger >= today and slot.special_date <= cutoff_30d + timedelta(days=60):
                special.append({
                    "id": slot.id,
                    "slot_type": "special",
                    "special_date": slot.special_date.isoformat(),
                    "trigger_date": trigger.isoformat(),
                    "send_hour_ar": slot.send_hour_ar,
                    "label": slot.label,
                    "advance_days": slot.advance_days,
                })

    # Ordenar especiales por fecha
    special.sort(key=lambda x: x["trigger_date"])

    return {
        "weekly_slots": weekly,
        "upcoming_special": special[:10],  # max 10 fechas especiales próximas
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /admin/campaigns/cooldown-routes  ← ANTES que /{id}
# ---------------------------------------------------------------------------

@router.get("/cooldown-routes")
async def get_cooldown_routes(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list[dict]:
    """
    Rutas que están en cooldown (no disponibles esta semana):
      - Enviadas como campañas en los últimos 14 días
      - Enviadas como price-drops en los últimos 7 días
    Agregado por ruta, no por usuario.
    """
    # Usar datetime naive (sin timezone) — las columnas son TIMESTAMP WITHOUT TIME ZONE
    now = datetime.utcnow()
    campaign_cutoff = now - timedelta(days=14)
    pd_cutoff = now - timedelta(days=7)

    # Rutas de campañas recientes
    cs_result = await db.execute(
        select(
            Campaign.route_origin,
            Campaign.route_destination,
            func.max(CampaignSend.sent_at).label("last_sent"),
        )
        .join(CampaignSend, CampaignSend.campaign_id == Campaign.id)
        .where(
            CampaignSend.status == "sent",
            CampaignSend.sent_at >= campaign_cutoff,
            Campaign.route_origin.isnot(None),
            Campaign.route_destination.isnot(None),
        )
        .group_by(Campaign.route_origin, Campaign.route_destination)
    )

    cooldown_map: dict[tuple, dict] = {}
    for row in cs_result.all():
        key = (row.route_origin, row.route_destination)
        last_sent = row.last_sent
        releases = last_sent + timedelta(days=14)
        days_remaining = max(0, (releases.date() - now.date()).days)
        cooldown_map[key] = {
            "origin": row.route_origin,
            "destination": row.route_destination,
            "cooldown_type": "campaign",
            "last_sent_at": last_sent.isoformat() if last_sent else None,
            "releases_at": releases.isoformat(),
            "days_remaining": days_remaining,
        }

    # Rutas de price-drops recientes
    pd_result = await db.execute(
        select(
            NotificationLog.origin,
            NotificationLog.destination,
            func.max(NotificationLog.sent_at).label("last_sent"),
        )
        .where(
            NotificationLog.type != "reengagement",
            NotificationLog.sent_at >= pd_cutoff,
            NotificationLog.origin.isnot(None),
            NotificationLog.destination.isnot(None),
        )
        .group_by(NotificationLog.origin, NotificationLog.destination)
    )
    for row in pd_result.all():
        key = (row.origin, row.destination)
        if key not in cooldown_map:
            last_sent = row.last_sent
            releases = last_sent + timedelta(days=7)
            days_remaining = max(0, (releases.date() - now.date()).days)
            cooldown_map[key] = {
                "origin": row.origin,
                "destination": row.destination,
                "cooldown_type": "price_drop",
                "last_sent_at": last_sent.isoformat() if last_sent else None,
                "releases_at": releases.isoformat(),
                "days_remaining": days_remaining,
            }

    return sorted(cooldown_map.values(), key=lambda x: x["days_remaining"], reverse=True)


# ---------------------------------------------------------------------------
# GET /admin/campaigns
# ---------------------------------------------------------------------------

@router.get("")
async def list_campaigns(
    status: str | None = None,
    segment: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list[dict]:
    q = select(Campaign).order_by(Campaign.created_at.desc()).limit(min(limit, 200))
    if status:
        q = q.where(Campaign.status == status)
    if segment:
        q = q.where(Campaign.segment == segment)
    result = await db.execute(q)
    return [_campaign_to_dict(c) for c in result.scalars().all()]


# ---------------------------------------------------------------------------
# POST /admin/campaigns
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_campaign(
    body: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = Campaign(
        name=body.name,
        segment=body.segment,
        status="draft",
        campaign_type=body.campaign_type,
        route_origin=body.route_origin,
        route_destination=body.route_destination,
        category_tag=body.category_tag,
        scheduled_at=body.scheduled_at,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return _campaign_to_dict(c)


# ---------------------------------------------------------------------------
# GET /admin/campaigns/{id}
# ---------------------------------------------------------------------------

@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = await db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign_not_found")
    return _campaign_to_dict(c)


# ---------------------------------------------------------------------------
# PATCH /admin/campaigns/{id}
# ---------------------------------------------------------------------------

@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = await db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign_not_found")
    if c.status not in ("draft", "scheduled"):
        raise HTTPException(409, "campaign_already_sent")

    if body.name is not None:
        c.name = body.name
    if body.segment is not None:
        c.segment = body.segment
    if body.campaign_type is not None:
        c.campaign_type = body.campaign_type
    if body.route_origin is not None:
        c.route_origin = body.route_origin
    if body.route_destination is not None:
        c.route_destination = body.route_destination
    if body.category_tag is not None:
        c.category_tag = body.category_tag
    if body.scheduled_at is not None:
        c.scheduled_at = body.scheduled_at
        c.status = "scheduled"

    await db.commit()
    await db.refresh(c)
    return _campaign_to_dict(c)


# ---------------------------------------------------------------------------
# DELETE /admin/campaigns/{id}
# ---------------------------------------------------------------------------

@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = await db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign_not_found")
    if c.status != "draft":
        raise HTTPException(409, "only_draft_campaigns_can_be_deleted")

    await db.execute(
        delete(CampaignSend).where(CampaignSend.campaign_id == campaign_id)
    )
    await db.execute(
        delete(Campaign).where(Campaign.id == campaign_id)
    )
    await db.commit()
    return {"success": True, "deleted_id": campaign_id}


# ---------------------------------------------------------------------------
# POST /admin/campaigns/{id}/send
# ---------------------------------------------------------------------------

@router.post("/{campaign_id}/send")
async def send_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = await db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign_not_found")
    if c.status not in ("draft", "scheduled"):
        raise HTTPException(409, f"campaign_status_is_{c.status}")

    # Disparar async (no bloquea el request)
    from app.services.campaign_engine import execute_campaign
    asyncio.create_task(execute_campaign(campaign_id))

    return {"success": True, "campaign_id": campaign_id, "message": "campaign_queued"}


# ---------------------------------------------------------------------------
# GET /admin/campaigns/{id}/stats
# ---------------------------------------------------------------------------

@router.get("/{campaign_id}/stats")
async def get_campaign_stats(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    c = await db.get(Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign_not_found")

    # Contar por status
    result = await db.execute(
        select(
            CampaignSend.status,
            func.count().label("cnt"),
        )
        .where(CampaignSend.campaign_id == campaign_id)
        .group_by(CampaignSend.status)
    )
    counts: dict[str, int] = {row.status: row.cnt for row in result.all()}

    sent = counts.get("sent", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)

    # Contar abiertos
    opened_result = await db.execute(
        select(func.count())
        .select_from(CampaignSend)
        .where(
            CampaignSend.campaign_id == campaign_id,
            CampaignSend.opened_at.isnot(None),
        )
    )
    opened: int = opened_result.scalar_one() or 0

    open_rate_pct = round((opened / sent * 100) if sent > 0 else 0.0, 1)

    return {
        "campaign_id": campaign_id,
        "campaign_name": c.name,
        "status": c.status,
        "sent": sent,
        "opened": opened,
        "failed": failed,
        "skipped": skipped,
        "open_rate_pct": open_rate_pct,
        "sent_at": c.sent_at.isoformat() if c.sent_at else None,
    }
