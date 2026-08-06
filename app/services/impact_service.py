"""
impact_service — creación server-side de tracking links de Impact.com.

Replica lo que hoy hace la app (RedirectViewModel.retrieveUrl / SSApiService):
  1. Escapa los VALORES de los query params del deeplink.
  2. Antepone https://www.skyscanner.net.
  3. POST a la API de Impact (Type=Regular) con Basic auth.
  4. Devuelve la TrackingURL.

No toca la base de datos: eso lo maneja el router (cache/dedup por hash).
"""
import hashlib
import logging
from urllib.parse import quote_plus

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_IMPACT_URL = (
    f"https://api.impact.com/Mediapartners/{settings.impact_account_sid}"
    f"/Programs/{settings.impact_program_id}/TrackingLinks"
)
_SKYSCANNER_BASE = "https://www.skyscanner.net"
_TIMEOUT = 15.0


def url_hash(url: str) -> str:
    """SHA-256 hex del deeplink — clave de dedup, siempre 64 chars."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def encode_query_param_values(url: str) -> str:
    """
    Escapa solo los VALORES de cada query param (no las keys ni el path).
    Port de encodeQueryParamValues del RedirectViewModel (usa form-urlencoding,
    igual que Java URLEncoder → quote_plus). Sin esto Impact no decodifica bien
    el deeplink que le pasa a Skyscanner.
    """
    parts = url.split("?")
    if len(parts) != 2:
        return url  # sin query params
    path, query = parts
    encoded = []
    for param in query.split("&"):
        kv = param.split("=", 1)
        if len(kv) == 2:
            encoded.append(f"{kv[0]}={quote_plus(kv[1])}")
        else:
            encoded.append(param)
    return f"{path}?{'&'.join(encoded)}"


async def create_impact_link(url: str) -> str | None:
    """
    Crea el tracking link de Impact para un deeplink relativo de Skyscanner.
    Devuelve la TrackingURL, o None si Impact falla (el caller decide el fallback).
    """
    deep_link = f"{_SKYSCANNER_BASE}{encode_query_param_values(url)}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _IMPACT_URL,
                params={"DeepLink": deep_link, "Type": "Regular"},
                headers={
                    "Authorization": f"Basic {settings.impact_auth_token}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            tracking_url = resp.json().get("TrackingURL")
            if not tracking_url:
                logger.error("impact_service: respuesta sin TrackingURL: %s", resp.text[:300])
                return None
            return tracking_url
    except Exception as e:
        logger.error("impact_service: createTrackingLink falló: %s", e)
        return None
