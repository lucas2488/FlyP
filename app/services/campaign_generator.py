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

import asyncio
import calendar
import json
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

    # Feriados oficiales — prioridad alta.
    # Nolaborables no trae mensaje propio → siempre generamos con IA.
    # Hacemos las llamadas en paralelo para no bloquear por cada feriado.
    feriado_tasks = [
        (h, date(year, month, h["dia"]))
        for h in official_holidays
    ]
    feriado_tasks = [(h, d) for h, d in feriado_tasks if _is_clear(d, existing_dates, drafts_map)]

    if feriado_tasks:
        ai_results = await asyncio.gather(
            *[_generate_message_with_ai(h["motivo"], "feriado") for h, _ in feriado_tasks],
            return_exceptions=True,
        )
        for (h, d), ai in zip(feriado_tasks, ai_results):
            if isinstance(ai, Exception):
                ai = (None, None)
            ai_title, ai_body = ai
            drafts_map[d] = {
                "source": "feriado_oficial",
                "nombre": f"{h['motivo']} {year}",
                "tipo": "feriado",
                "anticipacion_dias": 3,
                "custom_title": ai_title or f"🇦🇷 {h['motivo']}",
                "custom_body":  ai_body  or f"🇦🇷 {h['motivo']}: ¡feriado largo para volar! ✈️",
                "target_topic": "flypromociones_AR",
            }

    # Fechas especiales de la DB.
    # Si ya tienen mensaje_sugerido → lo usamos directamente.
    # Si no tienen mensaje → generamos con IA.
    for sd in db_special:
        trigger = sd.fecha - timedelta(days=sd.anticipacion_dias)
        target = trigger if trigger.month == month else sd.fecha

        if sd.mensaje_sugerido:
            # Mensaje ya definido por el operador → respetarlo
            custom_title = _build_title(sd.tipo, sd.nombre)
            custom_body  = sd.mensaje_sugerido
        else:
            # Sin mensaje → IA genera con jerga argentina
            ai_title, ai_body = await _generate_message_with_ai(sd.nombre, sd.tipo)
            custom_title = ai_title or _build_title(sd.tipo, sd.nombre)
            custom_body  = ai_body  or f"✈️ {sd.nombre}: ¡aprovechá para volar!"

        info = {
            "source": "special_date",
            "nombre": f"{sd.nombre} — {segment.replace('_', ' ').title()}",
            "tipo": sd.tipo,
            "anticipacion_dias": sd.anticipacion_dias,
            "special_date_id": sd.id,
            "custom_title": custom_title,
            "custom_body":  custom_body,
            "target_topic": "flypromociones_AR",
        }
        if _is_clear(target, existing_dates, drafts_map):
            drafts_map[target] = info
        elif sd.fecha.month == month and _is_clear(sd.fecha, existing_dates, drafts_map):
            drafts_map[sd.fecha] = info

    # Slots semanales — completan el mes (sin mensaje personalizado → usa template genérico)
    for slot_date in weekly_slots:
        if _is_clear(slot_date, existing_dates, drafts_map):
            day_name = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"][slot_date.weekday()]
            drafts_map[slot_date] = {
                "source": "weekly_slot",
                "nombre": f"Campaña semanal {day_name} {slot_date.strftime('%d/%m')} — {segment.replace('_', ' ').title()}",
                "tipo": "semanal",
                "anticipacion_dias": 0,
                # Sin custom_title/custom_body/target_topic → engine usa template genérico per-user
                "custom_title": None,
                "custom_body": None,
                "target_topic": None,
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
            custom_title=info.get("custom_title"),
            custom_body=info.get("custom_body"),
            target_topic=info.get("target_topic"),
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
            "custom_title": info.get("custom_title"),
            "custom_body": info.get("custom_body"),
            "target_topic": info.get("target_topic"),
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


async def _generate_message_with_ai(event_name: str, tipo: str) -> tuple[str | None, str | None]:
    """
    Genera título + cuerpo de notificación push usando OpenAI.
    Se llama solo cuando el evento no tiene mensaje_sugerido en la DB.

    Devuelve (None, None) si:
      - OPENAI_API_KEY no está configurada
      - La llamada falla por cualquier motivo (timeout, error de red, etc.)
    El caller debe tener un fallback hardcodeado.
    """
    from app.config import settings
    if not settings.openai_api_key:
        logger.debug("campaign_generator: OPENAI_API_KEY no configurada, saltando generación IA")
        return None, None

    tipo_label = {
        "feriado":    "feriado nacional",
        "comercial":  "fecha comercial importante",
        "vacaciones": "período de vacaciones",
    }.get(tipo, "fecha especial")

    prompt = (
        f'Sos el community manager de FlyPromociones, una app argentina de alertas de vuelos baratos.\n'
        f'Generá un mensaje de notificación push para la fecha: "{event_name}" ({tipo_label}).\n\n'
        f'Reglas:\n'
        f'- Título: máximo 45 caracteres, con emoji temático, jerga argentina, que enganche\n'
        f'- Cuerpo: máximo 90 caracteres, propuesta clara (volar barato / aprovechar el feriado), jerga argentina\n'
        f'- Tono: entusiasta, coloquial, argentino\n'
        f'- No incluyas nombres de aerolíneas ni precios inventados\n\n'
        f'Respondé SOLO con JSON: {{"title": "...", "body": "..."}}'
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.85,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        title = (data.get("title") or "").strip()[:45] or None
        body  = (data.get("body")  or "").strip()[:90] or None
        logger.info(f"campaign_generator: IA generó mensaje para '{event_name}': {title!r}")
        return title, body

    except Exception as exc:
        logger.warning(f"campaign_generator: OpenAI falló para '{event_name}': {exc}")
        return None, None


def _build_title(tipo: str, nombre: str) -> str:
    """
    Genera un título corto y temático para el push de una fecha especial.
    El tipo determina el emoji/tono del mensaje.
    """
    emoji_map = {
        "feriado":    "🗓",
        "comercial":  "🛍",
        "vacaciones": "☀️",
    }
    emoji = emoji_map.get(tipo, "✈️")
    # Truncar a 50 chars para que quede bien en la notificación
    nombre_short = nombre[:50] if len(nombre) > 50 else nombre
    return f"{emoji} {nombre_short}"


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
