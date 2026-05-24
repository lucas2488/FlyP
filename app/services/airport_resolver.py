"""
Airport resolver — convierte códigos IATA a nombres de ciudades legibles.

Consulta la tabla airport_cache, que se popula automáticamente con cada
POST /profile que incluye búsquedas de vuelos.

Usado por notification_dispatcher y reengagement_service para mostrar
"Buenos Aires" en vez de "EZE" en las notificaciones push.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AirportCache

logger = logging.getLogger(__name__)


async def resolve_airports(db: AsyncSession, codes: list[str]) -> dict[str, str]:
    """
    Devuelve un dict {iata_code: display_name}.

    display_name = campo `name` de airport_cache (ej: "Buenos Aires", "Posadas").
    Si el aeropuerto no está en la BD, retorna el código IATA como fallback.
    Nunca lanza excepciones — errores de BD degradan gracefully a IATA crudo.
    """
    if not codes:
        return {}

    unique_codes = list(set(codes))

    try:
        result = await db.execute(
            select(AirportCache.iata_code, AirportCache.name)
            .where(AirportCache.iata_code.in_(unique_codes))
        )
        mapping: dict[str, str] = {
            row.iata_code: (row.name or row.iata_code)
            for row in result.all()
        }
    except Exception as exc:
        logger.warning(f"airport_resolver: BD error, usando IATA crudo: {exc}")
        mapping = {}

    # Fallback: cualquier código no encontrado retorna el código mismo
    for code in unique_codes:
        mapping.setdefault(code, code)

    return mapping
