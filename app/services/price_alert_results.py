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

import pytz

import firebase_admin
from firebase_admin import firestore, messaging
from sqlalchemy import select, update

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
    title: str | None = None
    body: str | None = None
    copy_source: str | None = None      # llm | template
    push_scheduled_for: str | None = None   # horario de silencio: cuándo sale el push


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


def notice_doc(alert_id: str, a: dict, price: float, now: datetime,
               title: str | None = None, body: str | None = None) -> dict:
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
        # Con texto de Claude va el mismo del push; si es None la app lo arma en el idioma del teléfono.
        "title": title,
        "body": body,
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

def _apply(results: list[ResultIn], groups: dict, last_prices: dict[str, float], dry_run: bool,
           now: datetime, copies: dict[str, tuple[str, str]]) -> list[ResultOut]:
    """Escribe en Firestore y manda pushes. Bloqueante (SDK sync)."""
    db = None if dry_run else firestore.client()
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
                    llm = copies.get(path)
                    notice_ref.set(notice_doc(alert_id, a, r.price, now, *(llm or (None, None))))
                    oc.notice_id = notice_ref.id
                    # 2b. …después el push.
                    title, body = llm or push_texts(a, r.price, reason)
                    oc.title, oc.body, oc.copy_source = title, body, "llm" if llm else "template"
                    due = quiet_until(a.get("country"), now)
                    if due:
                        # Horario de silencio: el aviso ya está en la bandeja; el push sale a las 9 locales.
                        oc.push_scheduled_for = due.isoformat()
                    else:
                        p = _send_push(db, uid, notice_ref.id, title, body)
                        oc.devices, oc.pushes_sent, oc.devices_removed, oc.push_errors = p["devices"], p["sent"], p["removed"], p["errors"]
                    # 2c. Anti-spam.
                    ref.update({"lastNotifiedAt": now})
                    oc.action = "notified"
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
    now = _now()
    copies = {} if dry_run else await _generate_copies(results, groups, last_prices, now)
    out = await asyncio.to_thread(_apply, results, groups, last_prices, dry_run, now, copies)
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
                    push_errors=x.push_errors or None, title=x.title, body=x.body, copy_source=x.copy_source,
                    push_status="scheduled" if x.push_scheduled_for else "sent",
                    push_due_at=datetime.fromisoformat(x.push_scheduled_for) if x.push_scheduled_for else None,
                    push_sent_at=None if x.push_scheduled_for else now,
                    uid=uid, country=(str(a.get("country") or "").upper() or None),
                    route=f"{a.get('originName') or ''} ({a.get('originIata') or ''}) → {a.get('destinationName') or ''} ({a.get('destinationIata') or ''})"[:160],
                ))
        await db.commit()
    # El dashboard y GET /checker/searches tienen que ver los precios nuevos.
    await price_alerts.load(force=True)
    return out


def _dec(v) -> Decimal | None:
    n = _num(v)
    return Decimal(str(n)) if n is not None else None


# ---------------------------------------------------------------------------
# Texto con Claude (app/services/push_copy.py). Si falla, queda la plantilla.
# ---------------------------------------------------------------------------

TRIP_LBL = {
    "es": ("ida y vuelta", "solo ida", "todo"), "pt": ("ida e volta", "só ida", "todo o mês de"),
    "en": ("round trip", "one way", "anytime in"),
}


def _dm(v) -> str:
    v = str(v or "")
    return f"{v[8:10]}/{v[5:7]}" if len(v) >= 10 else v


def _trip_txt(a: dict, lang: str) -> str:
    rt, ow, month = TRIP_LBL[lang]
    kind = rt if str(a.get("tripType") or "").upper() == "ROUND_TRIP" else ow
    if str(a.get("dateMode") or "").lower() == "month" and a.get("month"):
        return f"{kind}, {month} {a['month']}"
    if a.get("returnDate"):
        return f"{kind}, {_dm(a.get('departureDate'))} → {_dm(a.get('returnDate'))}"
    return f"{kind}, {_dm(a.get('departureDate'))}"


async def _generate_copies(results: list[ResultIn], groups: dict, last_prices: dict[str, float],
                           now: datetime) -> dict[str, tuple[str, str]]:
    """Para cada alerta que va a avisar: (título, texto) escritos por Claude, en paralelo."""
    from app.services import push_copy

    jobs = []
    for r in results:
        if r.error or r.price is None or not (r.price > 0):
            continue
        for uid, alert_id, a in groups.get(r.search_key, []):
            if (r.currency or "").upper() != str(a.get("currency") or "").upper():
                continue
            path = f"users/{uid}/alerts/{alert_id}"
            reason, _ = decide(a, r.price, last_prices.get(path), now)
            if not reason:
                continue
            lang = _lang(a.get("country"))
            cur = str(a.get("currency") or "")
            base = _num(a.get("baselinePrice"))
            pct = round((1 - r.price / base) * 100) if base else None
            jobs.append((path, push_copy.generate(
                country=str(a.get("country") or ""),
                origin=a.get("originName") or a.get("originIata") or "",
                destination=a.get("destinationName") or a.get("destinationIata") or "",
                trip=_trip_txt(a, lang),
                price_txt=_money(r.price, cur, lang),
                previous_txt=_money(base, cur, lang) if base else None,
                pct=pct if pct and pct > 0 else None,
                reason=reason,
            )))
    if not jobs:
        return {}
    outs = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
    return {path: (o.title, o.body) for (path, _), o in zip(jobs, outs) if o is not None and not isinstance(o, Exception)}


# ---------------------------------------------------------------------------
# Horario de silencio: de 22:00 a 9:00 (hora local del país de la alerta) el push espera.
# El aviso de la bandeja se crea igual en el momento; el push sale a las 9:00 locales.
# ---------------------------------------------------------------------------

QUIET_FROM, QUIET_TO = 22, 9
# Países con varios husos: el principal. El resto sale de pytz.country_timezones.
MAIN_TZ = {
    "AR": "America/Argentina/Buenos_Aires", "BR": "America/Sao_Paulo", "MX": "America/Mexico_City",
    "US": "America/New_York", "CA": "America/Toronto", "AU": "Australia/Sydney", "RU": "Europe/Moscow",
    "CL": "America/Santiago", "EC": "America/Guayaquil", "ES": "Europe/Madrid", "PT": "Europe/Lisbon",
    "CO": "America/Bogota", "ID": "Asia/Jakarta", "CD": "Africa/Kinshasa", "KZ": "Asia/Almaty",
}
DEFAULT_TZ = "America/Argentina/Buenos_Aires"


def country_tz(country: str | None) -> ZoneInfo:
    c = (country or "").upper()
    name = MAIN_TZ.get(c) or (pytz.country_timezones.get(c) or [DEFAULT_TZ])[0]
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def quiet_until(country: str | None, now: datetime) -> datetime | None:
    """None si se puede mandar ya; si es horario de silencio, las próximas 9:00 locales (UTC)."""
    local = now.astimezone(country_tz(country))
    if QUIET_TO <= local.hour < QUIET_FROM:
        return None
    day = local.date() if local.hour < QUIET_TO else local.date() + timedelta(days=1)
    return datetime.combine(day, time(QUIET_TO, 0), local.tzinfo).astimezone(timezone.utc)


def _send_due(rows: list[tuple]) -> list[dict]:
    """Manda los pushes que esperaban. Bloqueante. rows: (id, uid, alert_path, notice_id, title, body)."""
    db = firestore.client()
    out = []
    for nid, uid, alert_path, notice_id, title, body in rows:
        alert = db.document(alert_path).get()
        notice = db.collection("users").document(uid).collection("notifications").document(notice_id).get()
        if not alert.exists or (alert.to_dict() or {}).get("status") != "active" or not notice.exists:
            out.append({"id": nid, "status": "cancelled", "detail": "la alerta o el aviso ya no están"})
            continue
        try:
            p = _send_push(db, uid, notice_id, title, body)
            out.append({"id": nid, "status": "sent", **p})
        except Exception as e:
            out.append({"id": nid, "status": "failed", "detail": f"{type(e).__name__}: {e}"[:300]})
    return out


async def send_scheduled_pushes() -> None:
    """Job (cada 15 min): manda los pushes cuyo horario de silencio terminó."""
    try:
        firebase_admin.get_app()
    except ValueError:
        return
    N = PriceAlertNotification
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(N.id, N.uid, N.alert_path, N.notice_id, N.title, N.body)
            .where(N.push_status == "scheduled", N.push_due_at <= _now()).order_by(N.push_due_at).limit(500)
        )).all()
    if not rows:
        return
    results = await asyncio.to_thread(_send_due, [tuple(r) for r in rows])
    async with AsyncSessionLocal() as db:
        for r in results:
            values = {"push_status": r["status"], "push_sent_at": _now() if r["status"] == "sent" else None}
            if r["status"] == "sent":
                values.update(devices=r["devices"], pushes_sent=r["sent"], devices_removed=r["removed"],
                              push_errors=r["errors"] or None)
            else:
                values["push_errors"] = [{"error": r.get("detail")}]
            await db.execute(update(N).where(N.id == r["id"]).values(**values))
        await db.commit()
    logger.info("price_alerts: %d pushes en espera procesados", len(results))


# ---------------------------------------------------------------------------
# Dashboard: avisos enviados y precios consultados por día
# ---------------------------------------------------------------------------

AR_TZ = "America/Argentina/Buenos_Aires"


async def dashboard_history(days: int, limit: int) -> dict:
    from sqlalchemy import Date, cast, func

    since = _now() - timedelta(days=days)
    N, C = PriceAlertNotification, PriceAlertCheck
    async with AsyncSessionLocal() as db:
        nd = cast(func.timezone(AR_TZ, N.created_at), Date)
        daily = (await db.execute(
            select(nd, func.count(),
                   func.count().filter(N.reason == "drop"), func.count().filter(N.reason == "target"),
                   func.count().filter(N.push_status == "scheduled"), func.count().filter(N.push_status == "cancelled"),
                   func.coalesce(func.sum(N.pushes_sent), 0), func.count().filter(N.copy_source == "llm"))
            .where(N.created_at >= since).group_by(nd).order_by(nd)
        )).all()
        cd = cast(func.timezone(AR_TZ, C.checked_at), Date)
        checks = (await db.execute(
            select(cd, func.count(), func.count().filter(C.status != "ok"), func.coalesce(func.sum(C.alerts_updated), 0),
                   func.count(func.distinct(C.worker_id)))
            .where(C.checked_at >= since).group_by(cd).order_by(cd)
        )).all()
        rows = (await db.execute(
            select(N).where(N.created_at >= since).order_by(N.created_at.desc()).limit(limit)
        )).scalars().all()
        last_check = await db.scalar(select(func.max(C.checked_at)))

    # Nombre / email / invitado del dueño desde Firestore (caché de 60 s); si la alerta se borró, queda la foto.
    users: dict[str, dict] = {}
    try:
        data = await price_alerts.load()
        for a in data["alerts"]:
            users[a["id"]] = a["user"]
    except Exception:
        pass

    def f(v):
        return float(v) if v is not None else None

    return {
        "days": days,
        "last_check_at": last_check.isoformat() if last_check else None,
        "daily": [{"date": d.isoformat(), "notified": n, "drop": dr, "target": tg, "scheduled": sc,
                   "cancelled": ca, "pushes_sent": int(ps), "llm": llm} for d, n, dr, tg, sc, ca, ps, llm in daily],
        "checks_daily": [{"date": d.isoformat(), "checks": n, "errors": e, "alerts_updated": int(u), "workers": w}
                         for d, n, e, u, w in checks],
        "items": [{
            "id": r.id, "created_at": r.created_at.isoformat(), "alert_id": r.alert_path.rsplit("/", 1)[-1],
            "route": r.route, "country": r.country, "currency": r.currency, "reason": r.reason,
            "price": f(r.price), "previous_price": f(r.previous_price), "target_price": f(r.target_price),
            "title": r.title, "body": r.body, "copy_source": r.copy_source,
            "push_status": r.push_status, "push_due_at": r.push_due_at.isoformat() if r.push_due_at else None,
            "push_sent_at": r.push_sent_at.isoformat() if r.push_sent_at else None,
            "devices": r.devices, "pushes_sent": r.pushes_sent, "devices_removed": r.devices_removed,
            "push_errors": r.push_errors or [],
            "user": users.get(r.alert_path.rsplit("/", 1)[-1]),
        } for r in rows],
    }
