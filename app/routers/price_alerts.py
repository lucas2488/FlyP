"""
Alertas de precio (Firestore `users/{uid}/alerts`).

  GET /analytics/price-alerts  → dashboard (X-API-Key)
  GET /checker/searches        → proceso que consulta precios (X-Checker-Key)
  POST /checker/claim          → reservar búsquedas (FlyAdmin con X-Checker-Key; la app con App Check)
  POST /checker/results        → informar precios; el servidor avisa (ídem)
  GET/PUT /analytics/checker/settings, GET /analytics/checker/overview → dashboard (X-API-Key)

Diseño de las búsquedas desde los celulares: DESIGN_crowd_checker.md.

La lectura y el armado están en app/services/price_alerts.py.
"""
import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Security
from firebase_admin import app_check
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import settings
from app.routers.analytics import verify_api_key
from app.services import app_settings, price_alert_claims, price_alert_results, price_alerts
from app.services.price_alert_claims import Worker
from app.services.price_alerts import FirestoreUnavailable

logger = logging.getLogger(__name__)
router = APIRouter(tags=["price-alerts"])
checker_key_header = APIKeyHeader(name="X-Checker-Key", auto_error=False)


async def verify_checker_key(key: str = Security(checker_key_header)) -> str:
    if not settings.checker_api_key or not key or key != settings.checker_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing checker key")
    return key


async def checker_worker(
    key: str | None = Security(checker_key_header),
    app_check_token: str | None = Header(None, alias="X-Firebase-AppCheck"),
    device_id: str | None = Header(None, alias="X-Device-Id", max_length=128),
) -> Worker:
    """
    FlyAdmin: X-Checker-Key. App de usuarios: X-Firebase-AppCheck (token de Firebase App Check,
    prueba que es la app real en un aparato real) + X-Device-Id (Firebase Installation ID).
    """
    if key:
        if not settings.checker_api_key or key != settings.checker_api_key:
            raise HTTPException(status_code=401, detail="Invalid checker key")
        return Worker("admin", device_id or None)
    if app_check_token:
        if not device_id or len(device_id) < 8:
            raise HTTPException(status_code=400, detail="Falta X-Device-Id")
        try:
            await asyncio.to_thread(app_check.verify_token, app_check_token)
        except Exception as e:
            logger.info("checker: App Check inválido (%s)", type(e).__name__)
            raise HTTPException(status_code=401, detail="Invalid App Check token")
        return Worker("app", device_id)
    raise HTTPException(status_code=401, detail="Missing checker credentials")


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
    itinerary_id: str | None = Field(None, max_length=256, description="Opcional: id del itinerario de Skyscanner del precio (como originalItineraryId del share)")
    claim_id: str | None = Field(None, max_length=64, description="Reserva de POST /checker/claim (obligatoria para la app de usuarios)")


class CheckerResultsIn(BaseModel):
    worker_id: str | None = Field(None, max_length=64)
    results: list[CheckerResult] = Field(..., min_length=1, max_length=500)


@router.post("/checker/results")
async def checker_results(body: CheckerResultsIn, dry_run: bool = Query(False, alias="dryRun"),
                          worker: Worker = Depends(checker_worker)):
    """
    Precios encontrados por el buscador. Por cada alerta activa de cada search_key:
    actualiza lastPrice / lowestPrice / lastCheckedAt y, si corresponde, crea el aviso en
    users/{uid}/notifications, manda el push y marca lastNotifiedAt.
    Con ?dryRun=true solo devuelve lo que haría, sin escribir nada.
    """
    try:
        out = await price_alert_results.process(
            [price_alert_results.ResultIn(r.search_key, r.price, r.currency, r.error, r.itinerary_id, r.claim_id)
             for r in body.results],
            body.worker_id, dry_run,
            Worker(worker.source, worker.device_id, body.worker_id if worker.source == "admin" else None),
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



# ---------------------------------------------------------------------------
# POST /checker/claim — reservar búsquedas antes de buscar
# ---------------------------------------------------------------------------

class ClaimIn(BaseModel):
    limit: int = Field(1, ge=1, le=200, description="Cuántas búsquedas reservar (la app de usuarios siempre recibe 1)")
    session_id: str | None = Field(None, max_length=64, description="Id de la apertura de la app (para el tope por sesión)")
    worker_id: str | None = Field(None, max_length=64)


@router.post("/checker/claim")
async def checker_claim(body: ClaimIn = Body(default_factory=ClaimIn), worker: Worker = Depends(checker_worker)):
    """
    Reserva búsquedas pendientes por unos minutos (por defecto 10). Mientras están reservadas
    nadie más las recibe. Devuelve solo los parámetros de búsqueda (sin datos de usuarios).
    claims vacío + next_poll_after_seconds null → no hay nada para buscar (o está apagado / tope).
    """
    try:
        return await price_alert_claims.claim(worker, body.limit, body.session_id)
    except FirestoreUnavailable as e:
        raise HTTPException(503, str(e))


# ---------------------------------------------------------------------------
# Dashboard: ajustes y métricas del buscador
# ---------------------------------------------------------------------------

@router.get("/analytics/checker/settings")
async def checker_settings(_: str = Depends(verify_api_key)):
    return {"settings": app_settings.describe(await app_settings.get_all(force=True))}


@router.put("/analytics/checker/settings")
async def update_checker_settings(changes: dict = Body(...), _: str = Depends(verify_api_key)):
    """Body: {"clave": valor, ...}. Se aplica en ~30 s, sin redeploy."""
    try:
        values = await app_settings.update(changes)
    except app_settings.InvalidSetting as e:
        raise HTTPException(400, str(e))
    return {"settings": app_settings.describe(values)}


@router.get("/analytics/checker/overview")
async def checker_overview(days: int = Query(14, ge=1, le=90), _: str = Depends(verify_api_key)):
    """Búsquedas por día (FlyAdmin vs. app), reservas, bajadas raras, aparatos y comparación de precios."""
    from app.services import checker_overview
    return await checker_overview.overview(days)
