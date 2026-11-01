"""
Resultados del buscador de precios (POST /checker/results) → alertas de Firestore + avisos.

El buscador (celular o compu) pide GET /checker/searches, busca cada `search_key` en
Skyscanner con el mismo mercado y manda el precio TOTAL de la búsqueda (todos los pasajeros),
igual que `baselinePrice`. Acá, por cada alerta activa y vigente de esa búsqueda, en este orden:

  1. users/{uid}/alerts/{id}: lastPrice, lowestPrice, lastCheckedAt (siempre, baje o no).
  2. Si corresponde avisar:
       a. users/{uid}/notifications/{id} con la copia entera de la búsqueda (contrato de
          NoticeMapper.kt / PriceNotice.swift). PRIMERO el documento…
       b. …DESPUÉS el push a todos los aparatos (users/{uid}/devices/*.fcmToken) con
          data {type: "price_drop", notificationId}; se borran los aparatos UNREGISTERED.
       c. lastNotifiedAt en la alerta.

Cuándo se avisa:
  - con objetivo:  precio <= targetPrice
  - sin objetivo:  precio <= baselinePrice × (1 − 3 %)
  - anti-spam: si ya se avisó en las últimas 24 h, solo se vuelve a avisar si el precio
    quedó por debajo del último aviso.

Las reglas de Firestore no dejan que las apps toquen los cuatro campos del servidor ni creen
avisos; el Admin SDK no pasa por las reglas.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import firestore, messaging
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import PriceAlertCheck, PriceAlertNotification
from app.services import price_alerts
from app.services.price_alerts import FirestoreUnavailable, search_key

logger = logging.getLogger(__name__)

DROP_RATIO = 0.03                       # sin objetivo: avisar si bajó al menos 3 % del precio base
ANTISPAM = timedelta(hours=24)
NOTICE_TTL = timedelta(days=30)
FLIGHT_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ---------------------------------------------------------------------------
# Entrada / salida
# ---------------------------------------------------------------------------

@dataclass
class ResultIn:
    search_key: str
    price: float | None
    currency: str | None
    error: str | None = None


@dataclass
class AlertOutcome:
    alert_id: str
    action: str                 # updated | notified | would_notify | skipped_antispam
    reason: str | None = None   # target | drop
    notice_id: str | None = None
    pushes_sent: int = 0
    devices: int = 0
    devices_removed: int = 0
    push_errors: list = field(default_factory=list)


@dataclass
class ResultOut:
    search_key: str
    status: str                 # ok | error | unknown_search | currency_mismatch | invalid_price
    detail: str | None = None
    alerts: list[AlertOutcome] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lectura de Firestore (sin caché: hay que decidir con el estado actual)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def _read_alerts(keys: set[str]) -> dict[str, list[tuple[str, str, dict]]]:
    """search_key → [(uid, alert_id, doc)] de alertas activas y vigentes. Bloqueante."""
    try:
        firebase_admin.get_app()
    except ValueError as e:
        raise FirestoreUnavailable("Firebase Admin SDK no inicializado") from e
    db = firestore.client()
    now = _now()
    out: dict[str, list] = {k: [] for k in keys}
    for snap in db.collection_group("alerts").stream():
        parts = snap.reference.path.split("/")
        if len(parts) != 4 or parts[0] != "users":
            continue
        a = snap.to_dict() or {}
        k = search_key(a)
        if k not in out:
            continue
        exp = _ts(a.get("expiresAt"))
        if a.get("status") != "active" or (exp is not None and exp <= now):
            continue
        out[k].append((parts[1], snap.id, a))
    return out


# ---------------------------------------------------------------------------
# Decisión
# ---------------------------------------------------------------------------

def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def decide(a: dict, price: float, last_notified_price: float | None, now: datetime) -> tuple[str | None, str | None]:
    """
    (motivo, None) si hay que avisar; (None, por_qué_no) si no.
    motivo: 'target' (llegó al objetivo) | 'drop' (bajó ≥ 3 % del precio base).
    """
    target = _num(a.get("targetPrice"))
    base = _num(a.get("baselinePrice"))
    if target is not None:
        if price > target:
            return None, "sobre_objetivo"
        reason = "target"
    else:
        if not base or price > base * (1 - DROP_RATIO):
            return None, "no_bajo_suficiente"
        reason = "drop"
    last = _ts(a.get("lastNotifiedAt"))
    if last is not None and now - last < ANTISPAM:
        if last_notified_price is None or price >= last_notified_price:
            return None, "antispam"
    return reason, None


# ---------------------------------------------------------------------------
# Aviso (documento) y push
# ---------------------------------------------------------------------------

NOTICE_SEARCH_FIELDS = (
    "originIata", "originName", "originEntityId", "destinationIata", "destinationName",
    "destinationEntityId", "tripType", "dateMode", "departureDate", "returnDate", "month",
    "adults", "childAges", "cabinClass", "currency", "country",
)


def _flight_day_end(a: dict) -> datetime | None:
    """El día del vuelo a las 23:59 (hora Argentina). En modo mes, el último día del mes."""
    try:
        if a.get("dateMode") == "month" and a.get("month"):
            y, m = (int(x) for x in str(a["month"]).split("-")[:2])
            d = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        elif a.get("departureDate"):
            d = date.fromisoformat(str(a["departureDate"])[:10])
        else:
            return None
    except ValueError:
        return None
    return datetime.combine(d, time(23, 59, 59), FLIGHT_TZ).astimezone(timezone.utc)


def notice_doc(alert_id: str, a: dict, price: float, now: datetime) -> dict:
    """Documento de users/{uid}/notifications. Nombres y valores iguales a los de la alerta."""
    doc = {k: a.get(k) for k in NOTICE_SEARCH_FIELDS}
    trip = str(a.get("tripType") or "").upper()          # contrato: nombre del enum, en mayúscula
    mode = str(a.get("dateMode") or "").lower()          # contrato: minúscula
    flight_end = _flight_day_end(a)
    expires = min(now + NOTICE_TTL, flight_end) if flight_end and flight_end > now else now + NOTICE_TTL
    doc.update({
        "type": "price_drop",
        "alertId": alert_id,
        "tripType": trip,
        "dateMode": mode,
        "price": float(price),
        "previousPrice": _num(a.get("baselinePrice")),
        "title": None,           # la app arma el texto en el idioma del teléfono
        "body": None,
        "createdAt": now,
        "expiresAt": expires,
        "read": False,
        "readAt": None,
    })
    doc["childAges"] = [int(x) for x in (a.get("childAges") or [])]
    return doc


SPANISH = {"AR", "UY", "CL", "PY", "BO", "PE", "EC", "CO", "VE", "MX", "ES", "CR", "PA", "DO", "GT",
           "HN", "SV", "NI", "CU", "PR"}


def _lang(country: str | None) -> str:
    c = (country or "").upper()
    if c in SPANISH:
        return "es"
    if c in ("BR", "PT"):
        return "pt"
    return "en"


def _money(v: float, cur: str, lang: str) -> str:
    dec = 0 if v >= 1000 or float(v).is_integer() else 2
    s = f"{v:,.{dec}f}"
    if lang in ("es", "pt"):
        s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return f"{cur} {s}"


def push_texts(a: dict, price: float, reason: str) -> tuple[str, str]:
    """Texto del push (el del aviso en la bandeja lo arma la app). Idioma según el mercado."""
    lang = _lang(a.get("country"))
    cur = str(a.get("currency") or "")
    route = f"{a.get('originIata') or ''} → {a.get('destinationIata') or ''}"
    base = _num(a.get("baselinePrice"))
    pct = round((1 - price / base) * 100) if base else None
    now_s = _money(price, cur, lang)
    was = _money(base, cur, lang) if base else None
    if lang == "es":
        title = f"¡Bajó el precio! {route}" if reason == "drop" else f"¡Llegó a tu precio! {route}"
        body = f"Ahora {now_s}" + (f" (antes {was}, −{pct} %)" if was and pct and pct > 0 else "") + ". Tocá para verlo."
    elif lang == "pt":
        title = f"O preço caiu! {route}" if reason == "drop" else f"Chegou ao seu preço! {route}"
        body = f"Agora {now_s}" + (f" (antes {was}, −{pct}%)" if was and pct and pct > 0 else "") + ". Toque para ver."
    else:
        title = f"Price drop! {route}" if reason == "drop" else f"Your target price! {route}"
        body = f"Now {now_s}" + (f" (was {was}, −{pct}%)" if was and pct and pct > 0 else "") + ". Tap to see it."
    return title, body


def _send_push(db, uid: str, notice_id: str, title: str, body: str) -> dict:
    """Push a todos los aparatos del usuario. Borra los que FCM marca UNREGISTERED. Bloqueante."""
    devices = list(db.collection("users").document(uid).collection("devices").stream())
    sent, removed, errors = 0, 0, []
    for dev in devices:
        token = (dev.to_dict() or {}).get("fcmToken")
        if not token:
            continue
        msg = messaging.Message(
            token=token,
            # data: lo que leen FlyMessagingService (Android) y FCMService (iOS) para abrir el aviso.
            data={"type": "price_drop", "notificationId": notice_id},
            # notification: sin esto iOS no muestra nada con la app cerrada.
            notification=messaging.Notification(title=title, body=body),
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(sound="default"))),
        )
        try:
            messaging.send(msg)
            sent += 1
        except messaging.UnregisteredError:
            dev.reference.delete()
            removed += 1
        except Exception as e:  # token inválido, cuota, etc.: se registra y se sigue
            errors.append({"device": dev.id, "error": f"{type(e).__name__}: {e}"[:300]})
    return {"devices": len(devices), "sent": sent, "removed": removed, "errors": errors}


# ---------------------------------------------------------------------------
# Proceso
# ---------------------------------------------------------------------------

def _apply(results: list[ResultIn], groups: dict, last_prices: dict[str, float], dry_run: bool) -> list[ResultOut]:
    """Escribe en Firestore y manda pushes. Bloqueante (SDK sync)."""
    db = None if dry_run else firestore.client()
    now = _now()
    out: list[ResultOut] = []
    for r in results:
        if r.error:
            out.append(ResultOut(r.search_key, "error", r.error[:300]))
            continue
        alerts = groups.get(r.search_key)
        if not alerts:
            out.append(ResultOut(r.search_key, "unknown_search", "no hay alertas activas con esa búsqueda"))
            continue
        cur = str(alerts[0][2].get("currency") or "").upper()
        if (r.currency or "").upper() != cur:
            out.append(ResultOut(r.search_key, "currency_mismatch", f"la búsqueda es en {cur} y llegó {r.currency}"))
            continue
        if r.price is None or not (r.price > 0):
            out.append(ResultOut(r.search_key, "invalid_price", "el precio tiene que ser un número mayor a 0"))
            continue

        res = ResultOut(r.search_key, "ok")
        for uid, alert_id, a in alerts:
            path = f"users/{uid}/alerts/{alert_id}"
            reason, why_not = decide(a, r.price, last_prices.get(path), now)
            lowest = min(x for x in (_num(a.get("lowestPrice")), r.price) if x is not None)
            oc = AlertOutcome(alert_id=alert_id, action="updated", reason=reason or why_not)

            if dry_run:
                oc.action = "would_notify" if reason else ("skipped_antispam" if why_not == "antispam" else "updated")
                res.alerts.append(oc)
                continue

            ref = db.collection("users").document(uid).collection("alerts").document(alert_id)
            try:
                # 1. Estado de la alerta (siempre).
                ref.update({"lastPrice": float(r.price), "lowestPrice": float(lowest), "lastCheckedAt": now})
                if reason:
                    # 2a. Primero el aviso…
                    notice_ref = db.collection("users").document(uid).collection("notifications").document()
                    notice_ref.set(notice_doc(alert_id, a, r.price, now))
                    oc.notice_id = notice_ref.id
                    # 2b. …después el push.
                    title, body = push_texts(a, r.price, reason)
                    p = _send_push(db, uid, notice_ref.id, title, body)
                    # 2c. Anti-spam.
                    ref.update({"lastNotifiedAt": now})
                    oc.action = "notified"
                    oc.devices, oc.pushes_sent, oc.devices_removed, oc.push_errors = p["devices"], p["sent"], p["removed"], p["errors"]
                elif why_not == "antispam":
                    oc.action = "skipped_antispam"
            except Exception as e:  # p. ej. la alerta se borró mientras tanto
                logger.exception("checker/results: falló %s", path)
                oc.action = "error" if not oc.notice_id else "notified"
                oc.push_errors.append({"error": f"{type(e).__name__}: {e}"[:300]})
            res.alerts.append(oc)
        out.append(res)
    return out


async def _last_notified_prices(paths: list[str]) -> dict[str, float]:
    if not paths:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PriceAlertNotification.alert_path, PriceAlertNotification.price)
            .where(PriceAlertNotification.alert_path.in_(paths))
            .order_by(PriceAlertNotification.alert_path, PriceAlertNotification.created_at.desc())
            .distinct(PriceAlertNotification.alert_path)
        )).all()
    return {p: float(v) for p, v in rows}


async def process(results: list[ResultIn], worker_id: str | None, dry_run: bool = False) -> list[ResultOut]:
    keys = {r.search_key for r in results}
    groups = await asyncio.to_thread(_read_alerts, keys)
    paths = [f"users/{uid}/alerts/{aid}" for g in groups.values() for uid, aid, _ in g]
    last_prices = await _last_notified_prices(paths)
    out = await asyncio.to_thread(_apply, results, groups, last_prices, dry_run)
    if dry_run:
        return out

    by_key = {r.search_key: r for r in results}
    async with AsyncSessionLocal() as db:
        for o in out:
            r = by_key[o.search_key]
            db.add(PriceAlertCheck(
                search_key=o.search_key, worker_id=worker_id,
                price=Decimal(str(r.price)) if r.price is not None else None,
                currency=(r.currency or "").upper() or None, status=o.status, error=o.detail if o.status != "ok" else None,
                alerts_updated=len(o.alerts), alerts_notified=sum(1 for x in o.alerts if x.action == "notified"),
            ))
            group = {aid: (uid, a) for uid, aid, a in groups.get(o.search_key, [])}
            for x in o.alerts:
                if x.action != "notified":
                    continue
                uid, a = group[x.alert_id]
                db.add(PriceAlertNotification(
                    alert_path=f"users/{uid}/alerts/{x.alert_id}", notice_id=x.notice_id, search_key=o.search_key,
                    price=Decimal(str(r.price)), previous_price=_dec(a.get("baselinePrice")),
                    target_price=_dec(a.get("targetPrice")), currency=(r.currency or "").upper(), reason=x.reason,
                    devices=x.devices, pushes_sent=x.pushes_sent, devices_removed=x.devices_removed,
                    push_errors=x.push_errors or None,
                ))
        await db.commit()
    # El dashboard y GET /checker/searches tienen que ver los precios nuevos.
    await price_alerts.load(force=True)
    return out


def _dec(v) -> Decimal | None:
    n = _num(v)
    return Decimal(str(n)) if n is not None else None
