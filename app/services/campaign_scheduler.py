"""
campaign_scheduler.py — Dispatcher automático de campañas.

Corre cada hora (APScheduler interval). Verifica si hay slots de
campaign_calendar que deban ejecutarse hoy:

  • weekly:  slot_type='weekly', día de semana y hora (hora Argentina) coinciden
  • special: slot_type='special', fecha - advance_days == hoy (hora Argentina)

Para cada match: crea una Campaign draft y la dispara vía execute_campaign().
Es idempotente: no crea duplicados si ya existe una campaña para esa fecha/slot.
"""

import logging
from datetime import date, datetime, timedelta, timezone

import pytz
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Campaign, CampaignCalendar

logger = logging.getLogger(__name__)

_AR_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

# Tolerancia para detectar si "es la hora" del slot (±30 minutos)
_HOUR_TOLERANCE_MINUTES = 30


async def check_scheduled_campaigns() -> None:
    """
    Job horario. Revisa campaign_calendar y crea campañas automáticas
    cuando corresponda según la hora/fecha en Argentina.
    """
    now_utc = datetime.now(tz=timezone.utc)
    now_ar = now_utc.astimezone(_AR_TZ)
    today_ar = now_ar.date()

    logger.info(
        f"campaign_scheduler: corriendo — hora AR: {now_ar.strftime('%Y-%m-%d %H:%M')}"
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CampaignCalendar).where(CampaignCalendar.is_active.is_(True))
        )
        slots: list[CampaignCalendar] = list(result.scalars().all())

        fired = 0
        for slot in slots:
            if slot.slot_type == "weekly":
                if _should_fire_weekly(slot, now_ar):
                    fired += await _maybe_create_and_fire(db, slot, today_ar, now_utc)
            elif slot.slot_type == "special":
                if _should_fire_special(slot, today_ar):
                    fired += await _maybe_create_and_fire(db, slot, today_ar, now_utc)

    logger.info(f"campaign_scheduler: {fired} campañas automáticas disparadas")


def _should_fire_weekly(slot: CampaignCalendar, now_ar: datetime) -> bool:
    """
    True si hoy es el día correcto Y la hora actual (AR) está dentro
    de la ventana del slot (±30 min).
    """
    if slot.day_of_week is None:
        return False
    # weekday() devuelve 0=Lunes .. 6=Domingo
    if now_ar.weekday() != slot.day_of_week:
        return False
    # Ventana de ±30 min alrededor de send_hour_ar:00
    slot_dt = now_ar.replace(hour=slot.send_hour_ar, minute=0, second=0, microsecond=0)
    diff = abs((now_ar - slot_dt).total_seconds())
    return diff <= _HOUR_TOLERANCE_MINUTES * 60


def _should_fire_special(slot: CampaignCalendar, today_ar: date) -> bool:
    """
    True si hoy == special_date - advance_days.
    """
    if slot.special_date is None:
        return False
    trigger_date = slot.special_date - timedelta(days=slot.advance_days)
    return today_ar == trigger_date


async def _maybe_create_and_fire(
    db,
    slot: CampaignCalendar,
    today_ar: date,
    now_utc: datetime,
) -> int:
    """
    Verifica que no exista ya una campaña automática para este slot/fecha.
    Si no existe, crea una Campaign draft y la dispara.
    Retorna 1 si disparó, 0 si ya existía.
    """
    label = slot.label or f"Campaña automática {today_ar}"

    # Idempotencia: buscar campaña con mismo nombre y fecha de hoy
    existing = await db.execute(
        select(Campaign).where(
            Campaign.name == label,
            Campaign.scheduled_at >= datetime.combine(today_ar, datetime.min.time()),
        )
    )
    if existing.scalar_one_or_none():
        logger.debug(f"campaign_scheduler: campaña '{label}' ya existe para hoy, saltando")
        return 0

    # Crear campaña automática
    campaign = Campaign(
        name=label,
        segment="heavy_searcher",  # segmento por defecto para campañas automáticas
        status="draft",
        scheduled_at=now_utc,
        campaign_type="top_auto",
        category_tag=slot.label,
    )
    db.add(campaign)
    await db.flush()
    campaign_id = campaign.id

    logger.info(
        f"campaign_scheduler: disparando campaña automática '{label}' "
        f"(id={campaign_id}, slot_id={slot.id})"
    )

    # Ejecutar en background para no bloquear el scheduler
    import asyncio
    from app.services.campaign_engine import execute_campaign
    asyncio.create_task(execute_campaign(campaign_id))

    await db.commit()
    return 1
