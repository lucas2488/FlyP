import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import UserFavorite, UserProfile
from app.schemas import SyncFavoritesRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/favorites")
async def sync_favorites(
    data: SyncFavoritesRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Sincroniza las rutas favoritas del usuario desde el Android (FavoriteSSDao).

    Identificador: fcm_token (más confiable que user_id en Android).
    El backend busca al usuario por fcm_token y usa su user_id internamente.

    Estrategia full-replace: se eliminan todos los favoritos existentes del usuario
    y se insertan los nuevos. Enviar routes=[] limpia todos los favoritos.
    """
    # Buscar usuario por FCM token
    result = await db.execute(
        select(UserProfile).where(UserProfile.fcm_token == data.fcm_token)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"favorites: fcm_token no encontrado: {data.fcm_token[:20]}...")
        raise HTTPException(status_code=404, detail="User not found")

    # Borrar todos los favoritos actuales del usuario
    await db.execute(
        delete(UserFavorite).where(UserFavorite.user_id == user.user_id)
    )

    # Insertar los nuevos
    for route in data.routes:
        db.add(UserFavorite(
            user_id=user.user_id,
            origin_iata=route.origin,
            destination_iata=route.destination,
        ))

    await db.commit()
    logger.info(f"favorites: {user.user_id} — {len(data.routes)} rutas sincronizadas")
    return {"success": True, "count": len(data.routes)}
