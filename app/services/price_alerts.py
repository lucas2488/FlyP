"""
price_alerts — lee de Firestore las alertas de precio que crean los usuarios en la app.

Las alertas viven en `users/{uid}/alerts/{alertId}` (una subcolección por usuario), así que
se leen todas juntas con una collection group query sobre `alerts`. La consulta va sin
`where` ni `orderBy` a propósito: en un collection group los dos necesitan un índice creado a
mano, y con pocos cientos de documentos filtrar en Python sale gratis. Cuando sean miles, el
cambio es agregar `where("status", "==", "active")` y el índice del grupo `alerts`.

Lo usan dos consumidores:
  - el dashboard (GET /analytics/price-alerts): todas las alertas, con su dueño y aparatos;
  - el proceso que consulta precios (GET /checker/searches): las búsquedas únicas pendientes.

Por eso la unidad de trabajo es la **búsqueda**, no la alerta: varias alertas con la misma
ruta, fechas, pasajeros, clase y mercado comparten una sola consulta (`search_key`).

El mercado (`country` + `currency`) es el que eligió el usuario y no se corrige ni se juzga:
la consulta se hace con ese mercado para que el precio que se le avisa sea el mismo que ve
cuando abre la app.

Los campos `lastPrice`, `lowestPrice`, `lastCheckedAt` y `lastNotifiedAt` los escribe solo el
servidor; las apps los crean en null.
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import firestore

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
# Una búsqueda consultada hace menos que esto no se vuelve a ofrecer al checker.
RECHECK_AFTER = timedelta(hours=10)   # permite buscar dos veces por día
# "A punto de dispararse": el objetivo está a menos de este % del precio de referencia.
NEAR_TARGET_RATIO = 0.02

# Por debajo de esto, un precio de vuelo en esa moneda es casi seguro un error de la app
# (por ejemplo ARS 596 para un ida y vuelta). Monedas que no están acá no se chequean.
SUSPICIOUS_BASE_BELOW = {
    "ARS": 20000, "BRL": 50, "USD": 20, "EUR": 20, "CLP": 15000,
    "COP": 50000, "MXN": 300, "PEN": 50, "UYU": 800,
}
_cache: dict = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


class FirestoreUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(v)


def _read_firestore() -> dict:
    """Bloqueante (SDK sync de Firestore): se llama con asyncio.to_thread."""
    try:
        firebase_admin.get_app()
    except ValueError as e:
        raise FirestoreUnavailable("Firebase Admin SDK no inicializado") from e

    db = firestore.client()
    alerts = []
    for snap in db.collection_group("alerts").stream():
        parts = snap.reference.path.split("/")
        # Solo users/{uid}/alerts/{id}; cualquier otra subcolección "alerts" se ignora.
        if len(parts) != 4 or parts[0] != "users":
            continue
        alerts.append((parts[1], snap.id, snap.to_dict() or {}))

    uids = sorted({uid for uid, _, _ in alerts})
    users: dict[str, dict] = {}
    if uids:
        refs = [db.collection("users").document(u) for u in uids]
        for snap in db.get_all(refs):
            users[snap.id] = (snap.to_dict() or {}) if snap.exists else {}

    devices: dict[str, list[dict]] = {u: [] for u in uids}
    for snap in db.collection_group("devices").stream():
        parts = snap.reference.path.split("/")
        if len(parts) == 4 and parts[0] == "users" and parts[1] in devices:
            devices[parts[1]].append(snap.to_dict() or {})

    return {"alerts": alerts, "users": users, "devices": devices}


async def load(force: bool = False) -> dict:
    """Alertas y búsquedas armadas, cacheadas CACHE_TTL_SECONDS."""
    async with _lock:
        fresh = time.monotonic() - _cache["at"] < CACHE_TTL_SECONDS
        if _cache["data"] is not None and fresh and not force:
            return _cache["data"]
        raw = await asyncio.to_thread(_read_firestore)
        data = build(raw, datetime.now(timezone.utc))
        _cache.update(at=time.monotonic(), data=data)
        return data


# ---------------------------------------------------------------------------
# Armado
# ---------------------------------------------------------------------------

def _upper(v) -> str:
    return str(v or "").strip().upper()


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def search_key(a: dict) -> str:
    """
    Misma búsqueda ⇒ misma clave, todos los días. Es la clave de dedup de la app de Android
    (AlertRules.dedupKey: ruta, tipo, modo, fechas, mes, adultos, menores, clase) más el
    mercado (moneda y país), porque el precio depende de dónde se busca.
    """
    child_ages = ",".join(str(int(x)) for x in sorted(a.get("childAges") or []))
    parts = [
        _upper(a.get("originIata")), _upper(a.get("destinationIata")),
        str(a.get("tripType") or ""), str(a.get("dateMode") or ""),
        str(a.get("departureDate") or ""), str(a.get("returnDate") or ""),
        str(a.get("month") or ""), str(a.get("adults") or ""), child_ages,
        str(a.get("cabinClass") or ""), _upper(a.get("currency")), _upper(a.get("country")),
    ]
    return "s_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _user_ref(uid: str) -> str:
    """Identificador opaco del dueño: agrupa alertas del mismo usuario sin exponer el uid."""
    return "u_" + hashlib.sha1(uid.encode()).hexdigest()[:10]


def _flags(a: dict, now: datetime) -> list[dict]:
    flags = []
    base = _num(a.get("baselinePrice"))
    target = _num(a.get("targetPrice"))
    last = _num(a.get("lastPrice"))
    cur = _upper(a.get("currency"))

    ref = last if last is not None else base
    if target is not None and ref:
        if last is not None and last <= target:
            flags.append({"kind": "near_target", "detail": "El precio de hoy ya llegó al objetivo."})
        elif target < ref and (ref - target) / ref < NEAR_TARGET_RATIO:
            gap = (ref - target) / ref * 100
            origen = "de hoy" if last is not None else "base"
            flags.append({
                "kind": "near_target",
                "detail": f"El objetivo está a {gap:.1f}% del precio {origen}.".replace(".", ",", 1),
            })

    for side, name in (("originIata", "origen"), ("destinationIata", "destino")):
        code = _upper(a.get(side))
        if len(code) != 3:
            flags.append({
                "kind": "bad_code",
                "detail": f'Guardó "{code or "vacío"}" como código de {name}: no es un código de 3 letras.',
            })

    limit = SUSPICIOUS_BASE_BELOW.get(cur)
    if base is not None and limit is not None and base < limit:
        flags.append({
            "kind": "suspicious_base",
            "detail": f"{cur} {base:,.2f} como precio base es demasiado bajo para un vuelo.",
        })

    if not a.get("originEntityId") or not a.get("destinationEntityId"):
        flags.append({
            "kind": "no_entity",
            "detail": "Le falta el geoId de Skyscanner: el proceso de precios no la puede consultar.",
        })
    return flags


def _place(a: dict, prefix: str) -> dict:
    return {
        "iata": _upper(a.get(f"{prefix}Iata")),
        "name": a.get(f"{prefix}Name") or "",
        "entity_id": a.get(f"{prefix}EntityId") or None,
    }


def _user(u: dict | None) -> dict:
    u = u or {}
    is_guest = u.get("isAnonymous") is not False  # sin documento ⇒ se trata como invitado
    return {
        "is_guest": is_guest,
        "name": None if is_guest else (u.get("displayName") or None),
        "email": None if is_guest else (u.get("email") or None),
    }


def _devices(devs: list[dict]) -> list[dict]:
    out = []
    for d in devs:
        out.append({
            "platform": d.get("platform") or None,
            "fcm_token": d.get("fcmToken") or None,
            "updated_at": _iso(d.get("updatedAt")),
        })
    out.sort(key=lambda x: x["updated_at"] or "", reverse=True)
    return out


def build(raw: dict, now: datetime) -> dict:
    alerts = []
    for uid, alert_id, a in raw["alerts"]:
        expires_at = a.get("expiresAt")
        expired = isinstance(expires_at, datetime) and expires_at < now
        alerts.append({
            "id": alert_id,
            "status": a.get("status") or None,
            "expired": expired,
            "created_at": _iso(a.get("createdAt")),
            "updated_at": _iso(a.get("updatedAt")),
            "expires_at": _iso(expires_at),
            "origin": _place(a, "origin"),
            "destination": _place(a, "destination"),
            "trip_type": a.get("tripType") or None,
            "date_mode": a.get("dateMode") or None,
            "departure_date": a.get("departureDate") or None,
            "return_date": a.get("returnDate") or None,
            "month": a.get("month") or None,
            "adults": int(a.get("adults") or 1),
            "child_ages": sorted(int(x) for x in (a.get("childAges") or [])),
            "cabin_class": a.get("cabinClass") or None,
            "currency": _upper(a.get("currency")) or None,
            "country": _upper(a.get("country")) or None,
            "baseline_price": _num(a.get("baselinePrice")),
            "target_price": _num(a.get("targetPrice")),
            "last_price": _num(a.get("lastPrice")),
            "lowest_price": _num(a.get("lowestPrice")),
            "last_checked_at": _iso(a.get("lastCheckedAt")),
            "last_notified_at": _iso(a.get("lastNotifiedAt")),
            "source": a.get("source") or None,
            "platform": a.get("platform") or None,
            "user_ref": _user_ref(uid),
            "user": _user(raw["users"].get(uid)),
            "devices": _devices(raw["devices"].get(uid, [])),
            "search_key": search_key(a),
            "flags": _flags(a, now),
        })
    alerts.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {
        "generated_at": _iso(now),
        "alerts": alerts,
        "searches": build_searches(alerts),
    }


def build_searches(alerts: list[dict]) -> list[dict]:
    """Una entrada por search_key, solo con alertas activas y no vencidas."""
    groups: dict[str, list[dict]] = {}
    for a in alerts:
        if a["status"] == "active" and not a["expired"]:
            groups.setdefault(a["search_key"], []).append(a)

    searches = []
    for key, group in groups.items():
        first = group[0]
        checked = [a for a in group if a["last_checked_at"]]
        latest = max(checked, key=lambda a: a["last_checked_at"]) if checked else None
        searches.append({
            "search_key": key,
            "origin": first["origin"],
            "destination": first["destination"],
            "trip_type": first["trip_type"],
            "date_mode": first["date_mode"],
            "departure_date": first["departure_date"],
            "return_date": first["return_date"],
            "month": first["month"],
            "adults": first["adults"],
            "child_ages": first["child_ages"],
            "cabin_class": first["cabin_class"],
            "currency": first["currency"],
            "country": first["country"],
            "alerts_count": len(group),
            "alert_ids": [a["id"] for a in group],
            # Alertas completas (mismo objeto que el dashboard) + alert_id para la app de admin.
            "alerts": [
                {"alert_id": a["id"], **a}
                for a in sorted(group, key=lambda a: a["created_at"] or "")
            ],
            "searchable": bool(first["origin"]["entity_id"] and first["destination"]["entity_id"]),
            "last_price": latest["last_price"] if latest else None,
            "last_checked_at": latest["last_checked_at"] if latest else None,
        })
    searches.sort(key=lambda s: (-s["alerts_count"], s["origin"]["iata"], s["destination"]["iata"]))
    return searches


def pending_searches(searches: list[dict], now: datetime, limit: int) -> tuple[list[dict], int]:
    """Lo que el checker tiene que consultar: no revisadas en RECHECK_AFTER, viejas primero."""
    cutoff = now - RECHECK_AFTER
    pending = []
    for s in searches:
        if not s["searchable"]:
            continue
        last = s["last_checked_at"]
        if last and datetime.fromisoformat(last.replace("Z", "+00:00")) > cutoff:
            continue
        pending.append(s)
    pending.sort(key=lambda s: s["last_checked_at"] or "")
    out = []
    for s in pending[:limit]:
        item = {k: v for k, v in s.items() if k not in ("alert_ids", "searchable")}
        out.append(item)
    return out, len(pending)
