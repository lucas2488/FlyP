"""
Router interno — para n8n y sistemas externos de confianza.

Autenticación: header X-Internal-Secret (distinto del X-API-Key del dashboard).
NO exponer en documentación pública (tag: internal).

Endpoints:
  POST /internal/campaign-trigger — crea y dispara una campaña inmediatamente
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Campaign

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])
internal_secret_header = APIKeyHeader(name="X-Internal-Secret", auto_error=False)


async def verify_internal_secret(secret: str = Security(internal_secret_header)) -> str:
    if not secret or secret != settings.internal_secret:
        raise HTTPException(status_code=401, detail="Invalid or missing internal secret")
    return secret


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class CampaignTriggerRequest(BaseModel):
    segment: str | None = Field(None, description="Segmento destino (ej: heavy_searcher)")
    campaign_type: str | None = Field(
        "top_auto",
        pattern="^(route|top_auto|category)$",
        description="Tipo de campaña",
    )
    route_origin: str | None = Field(None, max_length=10)
    route_destination: str | None = Field(None, max_length=10)
    category_tag: str | None = Field(None, max_length=100)
    name: str | None = Field(None, max_length=200, description="Nombre de la campaña (opcional)")
    message_override: str | None = Field(
        None, description="(Reservado para futuro uso — override del cuerpo del mensaje)"
    )


# ---------------------------------------------------------------------------
# POST /internal/campaign-trigger
# ---------------------------------------------------------------------------

@router.post("/campaign-trigger", status_code=201)
async def trigger_campaign(
    body: CampaignTriggerRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_internal_secret),
) -> dict:
    """
    Crea una campaña draft y la dispara inmediatamente.
    Usado por n8n cuando detecta eventos externos (deals, fechas comerciales).
    """
    name = body.name or f"n8n trigger — {body.campaign_type or 'top_auto'}"
    campaign = Campaign(
        name=name,
        segment=body.segment,
        status="draft",
        campaign_type=body.campaign_type or "top_auto",
        route_origin=body.route_origin,
        route_destination=body.route_destination,
        category_tag=body.category_tag,
    )
    db.add(campaign)
    await db.flush()
    campaign_id = campaign.id
    await db.commit()

    logger.info(
        f"internal: campaña '{name}' creada (id={campaign_id}) via n8n trigger, "
        f"disparando execute_campaign()"
    )

    from app.services.campaign_engine import execute_campaign
    asyncio.create_task(execute_campaign(campaign_id))

    return {
        "success": True,
        "campaign_id": campaign_id,
        "campaign_name": name,
        "message": "campaign_created_and_queued",
    }
