"""
campaign_scheduler.py — Dispatcher de campañas programadas.

Job periódico (APScheduler). Dispara los borradores de campaña cuya fecha
programada (scheduled_at) ya venció. **NO crea campañas**: eso lo hace quien
las programa (dashboard / ChatGPT vía la API). El scheduler solo ejecuta lo
que está listo y a término, sin importar el nombre ni el segmento.

scheduled_at se interpreta en hora Argentina (naive local), igual que lo setea
el dashboard/ChatGPT. execute_campaign se protege solo contra doble envío
(saltea si la campaña ya no está en draft/scheduled).
"""

import asyncio
import logging
from datetime import datetime, timedelta

import pytz
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Campaign
from app.services.campaign_engine import execute_campaign

logger = logging.getLogger(__name__)

_AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

# Ventana de gracia: dispara campañas vencidas en las últimas N horas. Evita
# disparar borradores muy viejos (que quedaron sin enviar) pero tolera corridas
# perdidas del job.
_GRACE_WINDOW_HOURS = 6


async def check_scheduled_campaigns() -> None:
    """
    Dispara las campañas (draft|scheduled) cuyo scheduled_at ya venció dentro de
    la ventana de gracia. No crea nada.
    """
    now_ar = datetime.now(_AR_TZ).replace(tzinfo=None)  # naive, hora Argentina
    window_start = now_ar - timedelta(hours=_GRACE_WINDOW_HOURS)

    logger.info(
        f"campaign_scheduler: corriendo — hora AR: {now_ar.strftime('%Y-%m-%d %H:%M')}"
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Campaign).where(
                Campaign.status.in_(("draft", "scheduled")),
                Campaign.scheduled_at.isnot(None),
                Campaign.scheduled_at <= now_ar,
                Campaign.scheduled_at >= window_start,
            )
        )
        due = list(result.scalars().all())

        for c in due:
            logger.info(
                f"campaign_scheduler: disparando campaña programada '{c.name}' "
                f"(id={c.id}, scheduled_at={c.scheduled_at.isoformat()})"
            )
            asyncio.create_task(execute_campaign(c.id))

    logger.info(f"campaign_scheduler: {len(due)} campañas programadas disparadas")
