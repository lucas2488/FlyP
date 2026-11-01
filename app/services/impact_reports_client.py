"""
Cliente HTTP de la API de reportes de Impact (token "Dashboard reportes", solo lectura).

- Base: https://api.impact.com/Mediapartners/{IMPACT_REPORTS_ACCOUNT_SID}
- Basic auth (SID:token), Accept: application/json, IR-Version: 16.
- 429/503 → espera Retry-After (o backoff exponencial) y reintenta.
- 400/401/403/404 → ImpactError, sin reintento.
- Paginación siguiendo @nextpageuri.
- Jobs asíncronos (ClickExport / ReportExport): poll de /Jobs/{id} y descarga.

Nunca se usa el token de la app (el que crea links): son credenciales separadas.
"""
import asyncio
import logging
from typing import Any, AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

HOST = "https://api.impact.com"
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_MAX_RETRIES = 5
_MAX_WAIT_SECONDS = 300
_NO_RETRY = {400, 401, 403, 404}


class ImpactError(RuntimeError):
    def __init__(self, status: int, message: str, errors: list | None = None):
        super().__init__(f"Impact {status}: {message}")
        self.status = status
        self.message = message
        self.errors = errors or []


class ImpactNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.impact_reports_account_sid and settings.impact_reports_auth_token)


def _prefix() -> str:
    return f"/Mediapartners/{settings.impact_reports_account_sid}"


def _url(path_or_uri: str) -> str:
    """Acepta '/Actions' (relativo a la cuenta), '/Mediapartners/...' o una URL absoluta."""
    if path_or_uri.startswith("http"):
        return path_or_uri
    if path_or_uri.startswith("/Mediapartners/"):
        return HOST + path_or_uri
    return HOST + _prefix() + path_or_uri


def _error_from(resp: httpx.Response) -> ImpactError:
    message, errors = resp.text[:300], []
    try:
        body = resp.json()
        message = body.get("Message") or body.get("title") or message
        errors = body.get("Errors") or []
    except Exception:
        pass
    return ImpactError(resp.status_code, message, errors)


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    value = resp.headers.get("Retry-After")
    if value and value.isdigit():
        return min(float(value), _MAX_WAIT_SECONDS)
    return min(2 ** attempt * 5, _MAX_WAIT_SECONDS)


def list_items(body: dict) -> list:
    """La lista de resultados viene bajo una key que depende del recurso (Actions, Clicks, Records…)."""
    for key, value in body.items():
        if not key.startswith("@") and isinstance(value, list):
            return value
    return []


class ImpactReportsClient:
    def __init__(self) -> None:
        if not is_configured():
            raise ImpactNotConfigured("Faltan IMPACT_REPORTS_ACCOUNT_SID / IMPACT_REPORTS_AUTH_TOKEN")
        self._client = httpx.AsyncClient(
            auth=(settings.impact_reports_account_sid, settings.impact_reports_auth_token),
            headers={"Accept": "application/json", "IR-Version": "16"},
            timeout=_TIMEOUT,
            follow_redirects=True,  # Download puede responder 302 a una URL firmada (httpx no reenvía el auth)
        )

    async def __aenter__(self) -> "ImpactReportsClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = _url(path)
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TransportError as e:
                wait = min(2 ** attempt * 5, _MAX_WAIT_SECONDS)
                logger.warning("impact: error de red en %s (%s), reintento en %ss", path, e, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code in (429, 503) or resp.status_code >= 500:
                wait = _retry_after(resp, attempt)
                logger.warning("impact: %s en %s, reintento en %ss", resp.status_code, path, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code in _NO_RETRY or resp.status_code >= 400:
                err = _error_from(resp)
                logger.error("impact: %s %s — %s %s", resp.status_code, path, err.message, err.errors)
                raise err
            return resp
        raise ImpactError(0, f"sin respuesta válida tras {_MAX_RETRIES} intentos: {path}")

    async def get_json(self, path: str, params: dict | None = None) -> dict:
        return (await self.get(path, params)).json()

    async def paginate(self, path: str, params: dict | None = None) -> AsyncIterator[dict]:
        """Itera todos los items de todas las páginas (sigue @nextpageuri)."""
        body = await self.get_json(path, params)
        while True:
            for item in list_items(body):
                yield item
            next_uri = body.get("@nextpageuri")
            if not next_uri:
                return
            body = await self.get_json(next_uri)  # ya trae los params

    async def run_job(self, path: str, params: dict, poll_seconds: int = 45,
                      timeout_seconds: int = 3600) -> tuple[str, list[dict]]:
        """
        Lanza un export asíncrono (ClickExport / ReportExport), espera a que termine y
        devuelve (job_id, filas). Descarga todas las páginas del resultado.
        """
        started = await self.get_json(path, {**params, "ResultFormat": "JSON"})
        queued_uri = started.get("QueuedUri") or ""
        result_uri = started.get("ResultUri") or ""
        job_id = queued_uri.rstrip("/").rsplit("/", 1)[-1]
        if not job_id:
            raise ImpactError(0, f"el export no devolvió job: {started}")

        waited = 0
        while True:
            job = await self.get_json(queued_uri)
            status = job.get("Status")
            if status == "COMPLETED":
                break
            if status in ("CANCELLED", "ERROR", "FAILED"):
                raise ImpactError(0, f"job {job_id} terminó en {status}: {job.get('StatusMessage')}")
            if waited >= timeout_seconds:
                raise ImpactError(0, f"job {job_id} sigue en {status} después de {timeout_seconds}s")
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds

        rows: list[dict] = []
        uri = result_uri or f"{queued_uri}/Download"
        for _ in range(10):  # 202 = todavía no está el archivo
            resp = await self.get(uri)
            if resp.status_code != 202:
                break
            await asyncio.sleep(poll_seconds)
        body = resp.json()
        while True:
            rows.extend(list_items(body))
            next_uri = body.get("@nextpageuri")
            if not next_uri:
                break
            body = await self.get_json(next_uri)
        return job_id, rows

    async def download(self, path: str) -> httpx.Response:
        """Para binarios (PDF de facturas). Sin Accept JSON."""
        resp = await self._client.get(_url(path), headers={"Accept": "application/pdf"})
        if resp.status_code >= 400:
            raise _error_from(resp)
        return resp


async def list_api_reports() -> list[dict[str, Any]]:
    """Reportes que se pueden correr por API (para elegir IMPACT_PERFORMANCE_REPORT_ID)."""
    async with ImpactReportsClient() as client:
        return [
            {"id": r.get("Id"), "name": r.get("Name"), "description": r.get("Description")}
            async for r in client.paginate("/Reports")
            if str(r.get("ApiAccessible")).lower() == "true"
        ]
