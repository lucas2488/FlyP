"""
cleanup_service.py — Limpieza periódica de tablas de notificaciones.

Job diario (APScheduler cron, 4am Argentina). Dos tareas:

  1. notification_queue: borra items con status terminal (sent/failed/skipped)
     con más de QUEUE_RETENTION_DAYS días (7). La cola es trabajo temporario.

  2. notification_log: borra registros con más de LOG_RETENTION_DAYS días (90).
     Es el audit trail de marketing — 90 días alcanza para análisis estacionales
     y comparativas mes a mes sin que la tabla crezca indefinidamente.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, and_

from app.database import AsyncSessionLocal
from app.models import NotificationLog, NotificationQueue

logger = logging.getLogger(__name__)

QUEUE_RETENTION_DAYS = 7
LOG_RETENTION_DAYS   = 90


async def purge_old_queue_items() -> None:
    queue_cutoff = datetime.utcnow() - timedelta(days=QUEUE_RETENTION_DAYS)
    log_cutoff   = datetime.utcnow() - timedelta(days=LOG_RETENTION_DAYS)

    async with AsyncSessionLocal() as db:
        # 1. Purgar notification_queue (items terminados viejos)
        q_result = await db.execute(
            delete(NotificationQueue).where(
                and_(
                    NotificationQueue.status.in_(["sent", "failed", "skipped"]),
                    NotificationQueue.created_at < queue_cutoff,
                )
            )
        )

        # 2. Purgar notification_log (historial de marketing > 90 días)
        log_result = await db.execute(
            delete(NotificationLog).where(
                NotificationLog.sent_at < log_cutoff,
            )
        )

        await db.commit()

    logger.info(
        f"cleanup: queue purgada {q_result.rowcount} items > {QUEUE_RETENTION_DAYS}d | "
        f"log purgado {log_result.rowcount} items > {LOG_RETENTION_DAYS}d"
    )
