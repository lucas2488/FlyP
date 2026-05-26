import logging
from fastapi import APIRouter, Depends
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

    Identificador real: fcm_token (el dispositivo, siempre presente aunque no haya login).
    user_id: se intenta obtener del perfil; si no existe, queda NULL — nunca falla.

    Estrategia full-replace por fcm_token: se eliminan todos los favoritos del token
    y se insertan los nuevos. Enviar routes=[] limpia todos los favoritos.
    """
    # Lookup opcional de user_id — best-effort, no bloquea si no hay perfil
    result = await db.execute(
        select(UserProfile).where(UserProfile.fcm_token == data.fcm_token)
        .order_by(UserProfile.updated_at.desc())
    )
    user = result.scalars().first()
    user_id = user.user_id if user else None

    # Borrar todos los favoritos actuales del token (full-replace)
    await db.execute(
        delete(UserFavorite).where(UserFavorite.fcm_token == data.fcm_token)
    )

    # Deduplicar rutas antes de insertar (puede haber dos vuelos distintos de la misma ruta)
    routes_unicas = {(r.origin, r.destination, r.trip_type) for r in data.routes}
    for origin, destination, trip_type in routes_unicas:
        db.add(UserFavorite(
            fcm_token=data.fcm_token,
            user_id=user_id,              # NULL si no hay perfil — OK
            origin_iata=origin,
            destination_iata=destination,
            trip_type=trip_type,
        ))

    await db.commit()
    logger.info(
        f"favorites: fcm={data.fcm_token[:20]}... user={user_id} "
        f"— {len(data.routes)} rutas sincronizadas"
    )
    return {"success": True, "count": len(data.routes)}
