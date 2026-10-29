"""
outbound_clicks — clasifica y desarma los links externos que loguean las apps
en impact_link_log (POST /impact).

Los links de afiliado de Skyscanner vienen envueltos por Impact:
    https://skyscanner.pxf.io/c/.../13416?u=<link de skyscanner>
El link real está en `u=`, a veces percent-encodeado y a veces crudo (con sus
propios `&` sin escapar), así que no se puede usar parse_qs para sacarlo.

Formatos de Skyscanner que se reconocen:
  - Vuelo config:     /transporte/vuelos/{orig}/{dest}/{AAMMDD}[/{AAMMDD}]/config/{tramos}
  - Vuelo deeplink:   /transport_deeplink/4.0/{mkt}/{locale}/{MONEDA}/.../{origId}.{destId}.{fecha}/air/...
                      ?ticket_price=...&passengers=...
  - Hotel:            /hoteles|hoteis|hotels/{pais}/{ciudad}-hoteles/{slug}/ht-{id}
                      ?checkin=...&checkout=...&clicked_details_price=...
"""
import re
from datetime import date
from urllib.parse import urlparse, unquote, parse_qs

_FLIGHT_CONFIG_RE = re.compile(
    r"/(?:transporte/vuelos|transport/flights|transporte/voos)/"
    r"([a-z0-9]+)/([a-z0-9]+)/(\d{6})(?:/(\d{6}))?",
    re.IGNORECASE,
)
_CONFIG_LEGS_RE = re.compile(r"/config/(.+)$")
# Ida y vuelta: "{orig}.{dest}.{fecha},{dest}.{orig}.{fecha}"
_DEEPLINK_PLACES_RE = re.compile(r"/(\d+)\.(\d+)\.(\d{4}-\d{2}-\d{2})([^/]*)/air/")
_HOTEL_SEGMENTS = {"hoteles", "hoteis", "hotels"}
_CITY_SUFFIX_RE = re.compile(r"-(hoteles|hoteis|hotels|hotel)$")
_LOWER_WORDS = {"de", "del", "da", "do", "dos", "das", "la", "las", "los", "el", "y"}
# Los slugs vienen sin tildes; corregimos los que más aparecen.
_SLUG_FIXES = {"Espana": "España", "Sao Paulo": "São Paulo", "Peru": "Perú", "Mexico": "México"}


def _slug_title(slug: str) -> str:
    """'rio-de-janeiro' → 'Rio de Janeiro'."""
    words = slug.lower().split("-")
    out = " ".join(w if i and w in _LOWER_WORDS else w.capitalize() for i, w in enumerate(words))
    return _SLUG_FIXES.get(out, out)


def extract_u(url: str) -> str | None:
    """Devuelve el link que viene en `u=` (decodificado), o None si no hay."""
    if "?" not in url:
        return None
    query = url.split("?", 1)[1]
    m = re.search(r"(?:^|&)u=", query)
    if not m:
        return None
    raw = query[m.end():]
    if re.match(r"https?%3a", raw, re.IGNORECASE):
        # Encodeado: termina en el próximo & real (los suyos vienen como %26).
        return unquote(raw.split("&", 1)[0])
    # Crudo: es lo último del query, se toma entero (incluye sus propios &).
    return raw


def _place_code(s: str) -> str:
    """'buea' → 'BUE', 'sla' → 'SLA'. Los de 4 letras son códigos de ciudad de SS."""
    return (s[:3] if len(s) == 4 else s).upper()


def _yymmdd(s: str | None) -> str | None:
    if not s or len(s) != 6:
        return None
    try:
        return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:])).isoformat()
    except ValueError:
        return None


def _float(s: str | None) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _parse_skyscanner(target: str) -> dict | None:
    p = urlparse(target)
    path = p.path
    qs = parse_qs(p.query)
    q = lambda k: (qs.get(k) or [None])[0]

    m = _FLIGHT_CONFIG_RE.search(path)
    if m:
        orig, dest, d1, d2 = m.groups()
        legs_m = _CONFIG_LEGS_RE.search(unquote(path))
        legs = legs_m.group(1).split("|") if legs_m else []
        return {
            "type": "flight",
            "origin": _place_code(orig),
            "destination": _place_code(dest),
            "date_from": _yymmdd(d1),
            "date_to": _yymmdd(d2),
            "price": None,
            "currency": None,
            # Para aprender ids numéricos de SS → código (ver learn_place_ids)
            "_legs": legs,
            "_raw_orig": orig,
            "_raw_dest": dest,
        }

    m = _DEEPLINK_PLACES_RE.search(path)
    if "/transport_deeplink/" in path and m:
        orig_id, dest_id, d1, rest = m.groups()
        parts = path.strip("/").split("/")
        currency = parts[4] if len(parts) > 4 and len(parts[4]) == 3 else None
        d2 = None
        if rest:
            found = re.search(r"\d{4}-\d{2}-\d{2}", rest)
            d2 = found.group(0) if found else None
        return {
            "type": "flight",
            "origin": None,
            "destination": None,
            "date_from": d1,
            "date_to": d2,
            "price": _float(q("ticket_price")),
            "currency": currency,
            "_orig_id": orig_id,
            "_dest_id": dest_id,
        }

    segs = [s for s in path.split("/") if s]
    if len(segs) >= 3 and segs[0].lower() in _HOTEL_SEGMENTS:
        city = _slug_title(_CITY_SUFFIX_RE.sub("", segs[2].lower()))
        country = _slug_title(segs[1])
        return {
            "type": "hotel",
            "city": city,
            "country": country,
            "date_from": q("checkin"),
            "date_to": q("checkout"),
            "price": _float(q("clicked_details_price")),
            "currency": None,
        }

    return {"type": "other"}


def learn_place_ids(parsed: list[dict]) -> dict[str, str]:
    """
    Los links config traen el código en el path y el id numérico en los tramos
    (`{origId}-{AAMMDDHHmm}--{carrier}-{escalas}-{destId}-{AAMMDDHHmm}`). Con eso
    se arma id → código para traducir los links transport_deeplink, que solo
    traen ids. Solo se aprende de códigos de aeropuerto (3 letras), no de ciudad.
    """
    ids: dict[str, str] = {}
    for sky in parsed:
        legs = sky.get("_legs") if sky else None
        if not legs:
            continue
        first = legs[0].split("-")
        if len(sky["_raw_orig"]) == 3 and first and first[0].isdigit():
            ids.setdefault(first[0], sky["origin"])
        # El destino es la llegada del primer tramo (en ida y vuelta, el segundo vuelve).
        if len(sky["_raw_dest"]) == 3 and len(first) >= 7 and first[-2].isdigit():
            ids.setdefault(first[-2], sky["destination"])
    return ids


def classify(url: str) -> tuple[str, str]:
    """(categoría, dominio). Categorías: skyscanner, trip, mytrip, checkin, otro."""
    host = (urlparse(url).netloc or "").lower()
    low = url.lower()
    if host.endswith("pxf.io") and "skyscanner" in host:
        return "skyscanner", host
    if host.endswith("tpo.li") or "trip.com" in host:
        return "trip", host
    if "mytrip" in host:
        return "mytrip", host
    if "checkin" in low or "check-in" in low or "webcheckin" in low:
        return "checkin", host
    return "otro", host


def build_items(rows) -> list[dict]:
    """rows: iterable de ImpactLinkLog. Devuelve los clicks clasificados y desarmados."""
    items = []
    for r in rows:
        category, host = classify(r.url)
        sky = None
        if category == "skyscanner":
            target = extract_u(r.url)
            if target and "skyscanner" in (urlparse(target).netloc or ""):
                sky = _parse_skyscanner(target)
                sky["target_url"] = target
        items.append({
            "id": r.id,
            "clicked_at": r.clicked_at.isoformat() + "Z" if r.clicked_at else None,
            "user_id": r.user_id,
            "url": r.url,
            "domain": host,
            "category": category,
            "sky": sky,
        })

    ids = learn_place_ids([it["sky"] for it in items])
    for it in items:
        sky = it["sky"]
        if not sky:
            continue
        if "_orig_id" in sky:
            # Se devuelven también los ids crudos: si no se pudieron traducir, el
            # dashboard muestra el id interno de SS en lugar del código.
            sky["origin_id"] = sky.pop("_orig_id")
            sky["destination_id"] = sky.pop("_dest_id")
            sky["origin"] = ids.get(sky["origin_id"])
            sky["destination"] = ids.get(sky["destination_id"])
        for k in ("_legs", "_raw_orig", "_raw_dest"):
            sky.pop(k, None)
    return items
