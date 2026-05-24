"""
segment_service.py — Recálculo nightly de segmentos de usuario.

Corre cada noche a las 3am (hora Argentina) via APScheduler.
Lee user_profiles + search_events, calcula user_segment y engagement_score,
y actualiza user_profiles directamente (los campos ya existen en la tabla).

Segmentos:
  heavy_searcher — muchas búsquedas recientes, alta actividad
  casual         — actividad moderada
  inactive       — sin actividad reciente (>30 días)
  new_user       — cuenta creada hace menos de 7 días
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.database import AsyncSessionLocal
from app.models import SearchEvent, UserProfile

logger = logging.getLogger(__name__)


async def recalculate_segments() -> None:
    """
    Recorre todos los usuarios con FCM token activo y recalcula su segmento.
    Se llama desde APScheduler, por eso crea su propia sesión.
    """
    logger.info("segment_service: iniciando recálculo de segmentos")
    now = datetime.utcnow()
    cutoff_30d = now - timedelta(days=30)
    cutoff_7d = now - timedelta(days=7)

    counts: dict[str, int] = {
        "heavy_searcher": 0,
        "casual": 0,
        "inactive": 0,
        "new_user": 0,
        "skipped": 0,
    }

    async with AsyncSessionLocal() as db:
        # Traer todos los usuarios
        result = await db.execute(select(UserProfile))
        users: list[UserProfile] = list(result.scalars().all())
        logger.info(f"segment_service: procesando {len(users)} usuarios")

        for user in users:
            try:
                segment, score = await _compute_segment(db, user, now, cutoff_30d, cutoff_7d)
                counts[segment] += 1

                await db.execute(
                    update(UserProfile)
                    .where(UserProfile.user_id == user.user_id)
                    .values(user_segment=segment, engagement_score=score)
                )
            except Exception as exc:
                logger.warning(f"segment_service: error en user {user.user_id}: {exc}")
                counts["skipped"] += 1

        await db.commit()

    logger.info(
        f"segment_service: completado — "
        f"heavy={counts['heavy_searcher']} casual={counts['casual']} "
        f"inactive={counts['inactive']} new_user={counts['new_user']} "
        f"skipped={counts['skipped']}"
    )


async def _compute_segment(
    db,
    user: UserProfile,
    now: datetime,
    cutoff_30d: datetime,
    cutoff_7d: datetime,
) -> tuple[str, float]:
    """
    Calcula (segment, engagement_score) para un usuario.
    engagement_score: 0.0 – 100.0 (más alto = más activo).
    """
    # ── new_user: creado hace menos de 7 días ─────────────────────────────
    created = user.created_at
    # Normalizar a naive para comparar (DB guarda TIMESTAMP WITHOUT TIME ZONE)
    if created:
        created_naive = created.replace(tzinfo=None)
        if created_naive > cutoff_7d:
            return "new_user", 10.0

    # ── búsquedas en los últimos 30 días ──────────────────────────────────
    searches_30d_result = await db.execute(
        select(func.count())
        .select_from(SearchEvent)
        .where(
            SearchEvent.user_id == user.user_id,
            SearchEvent.occurred_at >= cutoff_30d,
        )
    )
    searches_30d: int = searches_30d_result.scalar_one() or 0

    # ── último acceso a la app (ms epoch → datetime) ──────────────────────
    last_open_ms: int | None = user.last_app_open
    days_since_open: float = 999.0
    if last_open_ms:
        # utcfromtimestamp devuelve naive UTC, compatible con now = datetime.utcnow()
        last_open_dt = datetime.utcfromtimestamp(last_open_ms / 1000)
        days_since_open = (now - last_open_dt).total_seconds() / 86400

    # ── engagement_score: búsquedas + recencia ────────────────────────────
    search_score = min(searches_30d * 5.0, 60.0)   # hasta 60 pts por búsquedas
    recency_score = max(0.0, 40.0 - days_since_open * 1.5)  # decae con el tiempo
    engagement_score = round(min(search_score + recency_score, 100.0), 1)

    # ── inactive: sin actividad en 30 días ────────────────────────────────
    if days_since_open > 30 and searches_30d == 0:
        return "inactive", engagement_score

    # ── heavy_searcher: muchas búsquedas ──────────────────────────────────
    if searches_30d >= 10:
        return "heavy_searcher", engagement_score

    # ── casual: el resto ──────────────────────────────────────────────────
    return "casual", engagement_score
