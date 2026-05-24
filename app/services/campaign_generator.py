"""
campaign_generator.py — Generador de borradores de campañas para un mes.

Lógica de `generate_month_campaigns(year, month, db)`:
  1. Fetcha feriados oficiales de nolaborables.com.ar/api/v2/feriados/{año}
  2. Trae special_dates de la DB para ese mes (tipo: feriado/comercial/vacaciones)
  3. Trae los slots semanales de campaign_calendar (Lun/Mié/Vie)
  4. Por cada fecha relevante del mes genera un Campaign draft
  5. Deduplica: no crea si ya existe una campaña dentro de ±1 día
  6. Guarda los drafts con status='draft' y los devuelve
  7. NUNCA confirma ni envía nada — el usuario aprueba desde el dashboard

Devuelve lista de dict (no ORM objects) para serialización limpia.
"""

import calendar
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, CampaignCalendar, SpecialDate

logger = logging.getLogger(__name__)

_NOLABORABLES_URL = "https://nolaborables.com.ar/api/v2/feriados/{year}"
_TIMEOUT = 8.0  # segundos


async def generate_month_campaigns(
    year: int,
    month: int,
    db: AsyncSession,
    segment: str = "heavy_searcher",
) -> list[dict]:
    """
    Genera borradores de campañas para el mes indicado.
    Retorna lista de Campaign dict con los drafts creados.
    """
    logger.info(f"campaign_generator: generando mes {year}-{month:02d}, segmento={segment}")

    # 1. Fechas que ya tienen campaña (±1 día) para deduplicar
    existing_dates = await _get_existing_campaign_dates(db, year, month)

    # 2. Feriados oficiales de nolaborables.com.ar
    official_holidays = await _fetch_official_holidays(year, month)

    # 3. special_dates de la DB para el mes
    db_special = await _get_db_special_dates(db, year, month)

    # 4. Slots semanales del mes (Lun/Mié/Vie)
    weekly_slots = await _get_weekly_slots_for_month(db, year, month)

    # 5. Construir el mapa de borradores: date → draft_info
    drafts_map: dict[date, dict] = {}

    # Feriados oficiales — prioridad alta
    for h in official_holidays:
        d = date(year, month, h["dia"])
        if _is_clear(d, existing_dates, drafts_map):
            drafts_map[d] = {
                "source": "feriado_oficial",
                "nombre": f"{h['motivo']} {year}",
                "mensaje_sugerido": f"🇦🇷 {h['motivo']}: ¡feriado largo para volar! ✈️",
                "tipo": "feriado",
                "anticipacion_dias": 3,
            }

    # Fechas especiales de la DB — sobreescriben si más relevantes
    for sd in db_special:
        trigger = sd.fecha - timedelta(days=sd.anticipacion_dias)
        # Usar la fecha de trigger para el draft, no la fecha del evento
        target = trigger if trigger.month == month else sd.fecha
        if _is_clear(target, existing_dates, drafts_map):
            drafts_map[target] = {
                "source": "special_date",
                "nombre": f"{sd.nombre} — {segment.replace('_', ' ').title()}",
                "mensaje_sugerido": sd.mensaje_sugerido or f"✈️ {sd.nombre}: ¡aprovechá para volar!",
                "tipo": sd.tipo,
                "anticipacion_dias": sd.anticipacion_dias,
                "special_date_id": sd.id,
            }
        elif sd.fecha.month == month and _is_clear(sd.fecha, existing_dates, drafts_map):
            # Si el trigger cayó en otro mes, usar la fecha del evento igual
            drafts_map[sd.fecha] = {
                "source": "special_date",
                "nombre": f"{sd.nombre} — {segment.replace('_', ' ').title()}",
                "mensaje_sugerido": sd.mensaje_sugerido or f"✈️ {sd.nombre}",
                "tipo": sd.tipo,
                "anticipacion_dias": sd.anticipacion_dias,
                "special_date_id": sd.id,
            }

    # Slots semanales — completan el mes
    for slot_date in weekly_slots:
        if _is_clear(slot_date, existing_dates, drafts_map):
            day_name = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"][slot_date.weekday()]
            drafts_map[slot_date] = {
                "source": "weekly_slot",
                "nombre": f"Campaña semanal {day_name} {slot_date.strftime('%d/%m')} — {segment.replace('_', ' ').title()}",
                "mensaje_sugerido": "✈️ ¿A dónde querés volar esta semana?",
                "tipo": "semanal",
                "anticipacion_dias": 0,
            }

    # 6. Crear Campaign drafts en la DB
    created = []
    for target_date, info in sorted(drafts_map.items()):
        scheduled_dt = datetime.combine(target_date, datetime.min.time().replace(hour=10))
        campaign = Campaign(
            name=info["nombre"],
            segment=segment,
            status="draft",
            scheduled_at=scheduled_dt,
            campaign_type="top_auto",
        )
        db.add(campaign)
        await db.flush()
        created.append({
            "id": campaign.id,
            "name": campaign.name,
            "segment": campaign.segment,
            "status": campaign.status,
            "scheduled_at": scheduled_dt.isoformat(),
            "campaign_type": campaign.campaign_type,
            "source": info["source"],
            "tipo": info["tipo"],
            "mensaje_sugerido": info["mensaje_sugerido"],
            "route_origin": None,
            "route_destination": None,
            "category_tag": None,
            "created_at": datetime.utcnow().isoformat(),
        })

    await db.commit()
    logger.info(f"campaign_generator: {len(created)} borradores creados para {year}-{month:02d}")
    return created


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _is_clear(
    d: date,
    existing: set[date],
    drafts_map: dict[date, dict],
    window_days: int = 1,
) -> bool:
    """True si la fecha no tiene campaña existente ni borrador planificado (±window_days)."""
    for delta in range(-window_days, window_days + 1):
        check = d + timedelta(days=delta)
        if check in existing or check in drafts_map:
            return False
    return True


async def _get_existing_campaign_dates(db: AsyncSession, year: int, month: int) -> set[date]:
    """Fechas de campañas ya existentes en el mes (cualquier status)."""
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    result = await db.execute(
        select(Campaign.scheduled_at).where(
            Campaign.scheduled_at >= datetime.combine(month_start, datetime.min.time()),
            Campaign.scheduled_at <= datetime.combine(month_end, datetime.max.time()),
        )
    )
    dates: set[date] = set()
    for row in result.all():
        if row.scheduled_at:
            dates.add(row.scheduled_at.date())
    return dates


async def _fetch_official_holidays(year: int, month: int) -> list[dict]:
    """
    Fetcha feriados oficiales de nolaborables.com.ar.
    Devuelve solo los del mes solicitado.
    Si falla (timeout, red), retorna lista vacía con un log de advertencia.
    """
    url = _NOLABORABLES_URL.format(year=year)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            all_holidays: list[dict] = resp.json()
            return [h for h in all_holidays if h.get("mes") == month and h.get("dia")]
    except Exception as exc:
        logger.warning(f"campaign_generator: no se pudo obtener feriados de nolaborables.com.ar: {exc}")
        return []


async def _get_db_special_dates(db: AsyncSession, year: int, month: int) -> list[SpecialDate]:
    """
    Trae special_dates activas para el mes dado.
    Incluye también las que tienen trigger (fecha - anticipacion_dias) en el mes.
    """
    # Ventana ampliada: fechas cuyo trigger o fecha principal cae en el mes
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    # Lookahead: fechas del próximo mes cuyo trigger cae en este mes
    next_month_end = month_end + timedelta(days=14)

    result = await db.execute(
        select(SpecialDate).where(
            SpecialDate.activo.is_(True),
            SpecialDate.fecha >= month_start,
            SpecialDate.fecha <= next_month_end,
        ).order_by(SpecialDate.fecha)
    )
    return list(result.scalars().all())


async def _get_weekly_slots_for_month(db: AsyncSession, year: int, month: int) -> list[date]:
    """
    Devuelve todas las fechas del mes que caen en los días de la semana
    configurados en campaign_calendar (slot_type='weekly', is_active=True).
    Excluye el día de hoy y días pasados.
    """
    result = await db.execute(
        select(CampaignCalendar).where(
            CampaignCalendar.slot_type == "weekly",
            CampaignCalendar.is_active.is_(True),
        )
    )
    slots = result.scalars().all()
    active_weekdays: set[int] = {s.day_of_week for s in slots if s.day_of_week is not None}

    today = date.today()
    days_in_month = calendar.monthrange(year, month)[1]
    slot_dates: list[date] = []

    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d <= today:
            continue
        if d.weekday() in active_weekdays:
            slot_dates.append(d)

    return slot_dates
