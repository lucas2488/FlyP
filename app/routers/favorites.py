from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import UserFavorite
from app.schemas import SyncFavoritesRequest

router = APIRouter()


@router.post("/favorites")
async def sync_favorites(
    data: SyncFavoritesRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Sincroniza las rutas favoritas del usuario desde el Android (FavoriteSSDao).

    Estrategia full-replace: se eliminan todos los favoritos existentes del usuario
    y se insertan los nuevos. Enviar routes=[] limpia todos los favoritos.

    El Android llama este endpoint en cada app open junto con POST /profile.
    """
    # Borrar todos los favoritos actuales del usuario
    await db.execute(
        delete(UserFavorite).where(UserFavorite.user_id == data.user_id)
    )

    # Insertar los nuevos
    for route in data.routes:
        db.add(UserFavorite(
            user_id=data.user_id,
            origin_iata=route.origin,
            destination_iata=route.destination,
        ))

    await db.commit()
    return {"success": True, "user_id": data.user_id, "count": len(data.routes)}
