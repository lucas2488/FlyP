"""
Alertas de precio (Firestore `users/{uid}/alerts`).

  GET /analytics/price-alerts  → dashboard (X-API-Key)
  GET /checker/searches        → proceso que consulta precios (X-Checker-Key)
  POST /checker/results        → ese proceso informa los precios; el servidor avisa (X-Checker-Key)

La lectura y el armado están en app/services/price_alerts.py.
"""
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import settings
from app.routers.analytics import verify_api_key
from app.services import price_alert_results, price_alerts
from app.services.price_alerts import FirestoreUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(tags=["price-alerts"])
checker_key_header = APIKeyHeader(name="X-Checker-Key", auto_error=False)


async def verify_checker_key(key: str = Security(checker_key_header)) -> str:
    if not settings.checker_api_key or not key or key != settings.checker_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing checker key")
    return key


async def _load(force: bool = False) -> dict:
    try:
        return await price_alerts.load(force=force)
    except price_alerts.FirestoreUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("Error leyendo alertas de Firestore")
        raise HTTPException(status_code=502, detail="No se pudieron leer las alertas de Firestore")


@router.get("/analytics/price-alerts")
async def get_price_alerts(
    refresh: bool = False,
    _: str = Depends(verify_api_key),
):
    """Todas las alertas (activas, pausadas y vencidas) con dueño, aparatos y búsquedas únicas."""
    return await _load(force=refresh)


@router.get("/checker/searches")
async def get_checker_searches(
    limit: int = Query(50, ge=1, le=500),
    _: str = Depends(verify_checker_key),
):
    """Búsquedas únicas pendientes de consultar (no revisadas en las últimas 10 h)."""
    data = await _load()
    now = datetime.now(timezone.utc)
    searches, total = price_alerts.pending_searches(data["searches"], now, limit)
    return {
        "generated_at": price_alerts._iso(now),
        "total_pending": total,
        "searches": searches,
    }


# ---------------------------------------------------------------------------
# POST /checker/results — el buscador informa los precios que encontró
# ---------------------------------------------------------------------------

class CheckerResult(BaseModel):
    search_key: str = Field(..., max_length=32)
    price: float | None = Field(None, description="Precio TOTAL de la búsqueda (todos los pasajeros), como baselinePrice")
    currency: str | None = Field(None, max_length=8, description="Moneda del precio; tiene que ser la de la búsqueda")
    error: str | None = Field(None, max_length=500, description="Si la búsqueda falló, el motivo (no se toca la alerta)")


class CheckerResultsIn(BaseModel):
    worker_id: str | None = Field(None, max_length=64)
    results: list[CheckerResult] = Field(..., min_length=1, max_length=500)


@router.post("/checker/results", dependencies=[Depends(verify_checker_key)])
async def checker_results(body: CheckerResultsIn, dry_run: bool = Query(False, alias="dryRun")):
    """
    Precios encontrados por el buscador. Por cada alerta activa de cada search_key:
    actualiza lastPrice / lowestPrice / lastCheckedAt y, si corresponde, crea el aviso en
    users/{uid}/notifications, manda el push y marca lastNotifiedAt.
    Con ?dryRun=true solo devuelve lo que haría, sin escribir nada.
    """
    try:
        out = await price_alert_results.process(
            [price_alert_results.ResultIn(r.search_key, r.price, r.currency, r.error) for r in body.results],
            body.worker_id, dry_run,
        )
    except FirestoreUnavailable as e:
        raise HTTPException(503, str(e))
    return {
        "dry_run": dry_run,
        "processed": len(out),
        "notified": sum(1 for o in out for a in o.alerts if a.action in ("notified", "would_notify")),
        "results": [asdict(o) for o in out],
    }


@router.get("/analytics/price-alerts/notifications")
async def price_alert_notifications(days: int = Query(30, ge=1, le=365), limit: int = Query(200, ge=1, le=1000),
                                    _: str = Depends(verify_api_key)):
    """Avisos de bajada enviados (con el mensaje exacto) y precios consultados, por día (hora Argentina)."""
    return await price_alert_results.dashboard_history(days, limit)
