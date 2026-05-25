"""
segment_service.py — Recálculo nightly de segmentos de usuario.

Corre cada noche a las 3am (hora Argentina) via APScheduler.
Lee user_profiles, calcula user_segment y engagement_score basado en
last_app_open (señal principal de actividad), y actualiza user_profiles.

Segmentos (basados en recencia de último acceso a la app):
  heavy_searcher — activo en los últimos 7 días  (target principal de campañas)
  casual         — activo en los últimos 30 días  (engagement medio)
  inactive       — sin actividad en más de 30 días
  new_user       — nunca abrió la app (last_app_open IS NULL)

Nota: search_events se usa como señal secundaria si está disponible, pero
no es la señal principal para no depender de tracking de búsquedas.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import update

from app.database import AsyncSessionLocal
from app.models import UserProfile

logger = logging.getLogger(__name__)

# Thresholds de recencia
_HEAVY_SEARCHER_DAYS = 7   # activo en última semana
_CASUAL_DAYS = 30          # activo en último mes


async def recalculate_segments() -> None:
    """
    Recorre todos los usuarios y recalcula su segmento.
    Señal principal: last_app_open (ms epoch almacenado en user_profiles).
    Se llama desde APScheduler (crea su propia sesión) y desde el endpoint admin.
    Usa UPDATE en batch para evitar N+1 queries.
    """
    logger.info("segment_service: iniciando recálculo de segmentos")
    now = datetime.utcnow()
    now_ms = int(now.timestamp() * 1000)

    # Umbrales en ms epoch
    cutoff_7d_ms  = now_ms - (_HEAVY_SEARCHER_DAYS * 86400 * 1000)
    cutoff_30d_ms = now_ms - (_CASUAL_DAYS * 86400 * 1000)

    counts: dict[str, int] = {
        "heavy_searcher": 0,
        "casual": 0,
        "inactive": 0,
        "new_user": 0,
        "skipped": 0,
    }

    async with AsyncSessionLocal() as db:
        # ── UPDATE en batch por segmento (1 query por segmento, no 1 por usuario) ──

        # heavy_searcher: activo en los últimos 7 días
        r = await db.execute(
            update(UserProfile)
            .where(UserProfile.last_app_open >= cutoff_7d_ms)
            .values(user_segment="heavy_searcher", engagement_score=90.0)
        )
        counts["heavy_searcher"] = r.rowcount

        # casual: activo en los últimos 30 días (pero no en los últimos 7)
        r = await db.execute(
            update(UserProfile)
            .where(
                UserProfile.last_app_open >= cutoff_30d_ms,
                UserProfile.last_app_open < cutoff_7d_ms,
            )
            .values(user_segment="casual", engagement_score=50.0)
        )
        counts["casual"] = r.rowcount

        # inactive: tiene last_app_open pero fue hace más de 30 días
        r = await db.execute(
            update(UserProfile)
            .where(
                UserProfile.last_app_open.isnot(None),
                UserProfile.last_app_open < cutoff_30d_ms,
            )
            .values(user_segment="inactive", engagement_score=5.0)
        )
        counts["inactive"] = r.rowcount

        # new_user: nunca abrió la app (last_app_open IS NULL)
        r = await db.execute(
            update(UserProfile)
            .where(UserProfile.last_app_open.is_(None))
            .values(user_segment="new_user", engagement_score=10.0)
        )
        counts["new_user"] = r.rowcount

        await db.commit()

    total = sum(v for k, v in counts.items() if k != "skipped")
    logger.info(
        f"segment_service: completado — {total} usuarios actualizados — "
        f"heavy={counts['heavy_searcher']} casual={counts['casual']} "
        f"inactive={counts['inactive']} new_user={counts['new_user']}"
    )
