from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import NotificationQueue

router = APIRouter()


@router.post("/notifications/{queue_id}/opened")
async def mark_notification_opened(
    queue_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Llamado por el Android cuando el usuario toca una notificación push.
    El queue_id viene en el campo 'notification_queue_id' del data payload de FCM.

    Idempotente: si ya fue marcada como abierta, retorna already_opened=True sin error.
    """
    item = await db.get(NotificationQueue, queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="notification_not_found")

    if item.opened_at is not None:
        return {"success": True, "already_opened": True}

    item.opened_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "already_opened": False}
