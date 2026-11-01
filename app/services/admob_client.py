"""
Cliente de la API de AdMob (scope admob.readonly) para el dashboard de AdMob.

- Access token con el refresh token (POST oauth2.googleapis.com/token), en memoria hasta
  5 minutos antes de vencer.
- `invalid_grant` → AdmobAuthError (refresh token revocado o vencido): no se reintenta;
  el dashboard muestra "Reconectar AdMob".
- 429 / 5xx → backoff exponencial.
- Reportes (networkReport / mediationReport): parseo de header / row / footer, montos en micros.
"""
import asyncio
import logging
import time
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
BASE = "https://admob.googleapis.com/v1"
REPORT_TZ = "America/Los_Angeles"
_TIMEOUT = httpx.Timeout(90.0, connect=15.0)
_MAX_RETRIES = 5

_token: dict = {"value": None, "expires": 0.0}
_token_lock = asyncio.Lock()


class AdmobError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"AdMob {status}: {message}")
        self.status = status
        self.message = message


class AdmobAuthError(AdmobError):
    """El refresh token ya no sirve (invalid_grant): hay que volver a autorizar."""


def is_configured() -> bool:
    return bool(settings.admob_client_id and settings.admob_client_secret
                and settings.admob_refresh_token and settings.admob_publisher_id)


def account_path() -> str:
    return f"accounts/{settings.admob_publisher_id}"


async def _access_token(client: httpx.AsyncClient) -> str:
    async with _token_lock:
        if _token["value"] and time.time() < _token["expires"] - 300:
            return _token["value"]
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": settings.admob_client_id,
            "client_secret": settings.admob_client_secret,
            "refresh_token": settings.admob_refresh_token,
        })
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        if resp.status_code != 200:
            err = body.get("error", "") if isinstance(body, dict) else ""
            if err == "invalid_grant":
                raise AdmobAuthError(resp.status_code, "invalid_grant: hay que reconectar AdMob")
            raise AdmobError(resp.status_code, f"token: {err or resp.text[:200]}")
        _token["value"] = body["access_token"]
        _token["expires"] = time.time() + int(body.get("expires_in", 3600))
        return _token["value"]


class AdmobClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def __aenter__(self) -> "AdmobClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(_MAX_RETRIES):
            token = await _access_token(self._client)
            try:
                resp = await self._client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.TransportError as e:
                wait = min(2 ** attempt * 2, 60)
                logger.warning("admob: error de red (%s), reintento en %ss", e, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code == 401 and attempt == 0:
                _token["value"] = None   # token vencido antes de tiempo: pedir otro
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = min(2 ** attempt * 2, 60)
                logger.warning("admob: %s en %s, reintento en %ss", resp.status_code, url, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                msg = resp.text[:500]
                try:
                    msg = resp.json().get("error", {}).get("message", msg)
                except Exception:
                    pass
                raise AdmobError(resp.status_code, msg)
            return resp
        raise AdmobError(0, f"sin respuesta válida tras {_MAX_RETRIES} intentos: {url}")

    async def get(self, path: str, params: dict | None = None) -> dict:
        return (await self._request("GET", f"{BASE}/{path}", params=params)).json()

    async def list_all(self, path: str, key: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"pageSize": 20000}
            if token:
                params["pageToken"] = token
            body = await self.get(path, params)
            out.extend(body.get(key, []))
            token = body.get("nextPageToken")
            if not token:
                return out

    async def report(self, kind: str, start: date, end: date, dimensions: list[str], metrics: list[str],
                     currency: str = "USD") -> tuple[str | None, list[dict]]:
        """kind: 'networkReport' | 'mediationReport'. Devuelve (moneda, filas parseadas)."""
        spec = {"reportSpec": {
            "dateRange": {
                "startDate": {"year": start.year, "month": start.month, "day": start.day},
                "endDate": {"year": end.year, "month": end.month, "day": end.day},
            },
            "dimensions": dimensions,
            "metrics": metrics,
            "localizationSettings": {"currencyCode": currency, "languageCode": "es-AR"},
            "timeZone": REPORT_TZ,
            "maxReportRows": 100000,
        }}
        resp = await self._request("POST", f"{BASE}/{account_path()}/{kind}:generate", json=spec)
        cur, rows, matching = parse_report(resp.json())
        if matching is not None and matching > len(rows):
            raise ReportTooLarge(matching, len(rows))
        return cur, rows


class ReportTooLarge(AdmobError):
    def __init__(self, matching: int, got: int):
        super().__init__(0, f"el reporte tiene {matching} filas y llegaron {got}")


def parse_report(messages: list[dict]) -> tuple[str | None, list[dict], int | None]:
    currency, rows, matching = None, [], None
    for msg in messages:
        if "header" in msg:
            currency = (msg["header"].get("localizationSettings") or {}).get("currencyCode")
        elif "row" in msg:
            dv = msg["row"].get("dimensionValues", {})
            mets = {}
            for k, v in msg["row"].get("metricValues", {}).items():
                if "microsValue" in v:
                    mets[k] = int(v["microsValue"]) / 1_000_000
                elif "integerValue" in v:
                    mets[k] = int(v["integerValue"])
                elif "doubleValue" in v:
                    mets[k] = float(v["doubleValue"])
            rows.append({
                "dims": {k: v.get("value") for k, v in dv.items()},
                "labels": {k: v.get("displayLabel") for k, v in dv.items()},
                "metrics": mets,
            })
        elif "footer" in msg:
            f = msg["footer"]
            if f.get("warnings"):
                logger.warning("admob: avisos del reporte: %s", f["warnings"])
            if f.get("matchingRowCount") is not None:
                matching = int(f["matchingRowCount"])
    return currency, rows, matching
