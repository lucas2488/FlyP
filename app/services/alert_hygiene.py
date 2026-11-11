"""
Pausa automática de alertas que nadie va a ver, y lectura de los avisos.

Dos motivos (siempre se PAUSA, nunca se borra; el usuario la reactiva desde la app):
  - no_devices: el usuario se quedó sin aparatos. FCM responde UNREGISTERED cuando la app se
    desinstaló (o se reinstaló: el token cambia y el viejo muere) y ese aparato se borra. Sin
    aparatos no hay a quién avisarle.
  - inactive: los últimos N avisos están sin abrir Y hace más de D días que no abre la app
    (devices.updatedAt, que las dos apps actualizan en cada arranque). Tienen que darse las dos:
    el push ya trae el precio, mucha gente lo ve y no lo abre.

En la alerta se escribe: status="paused", pausedBy="server", pausedReason, pausedAt.
Cuando el usuario la reactiva, la app vuelve a status="active".

Corre:
  - una vez por día (job alert_hygiene): valida los tokens de los usuarios con alertas activas
    con un envío de prueba de FCM (dry_run: no llega nada al celular), borra los muertos y
    aplica las dos reglas. También copia a Postgres qué avisos se leyeron (read_at).
  - al avisar (price_alert_results): si el push deja al usuario sin aparatos, se pausa ahí mismo.

Umbrales e interruptor editables desde el dashboard (app_settings).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import firestore, messaging
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import PriceAlertNotification, PriceAlertPause
from app.services import app_settings, price_alerts

logger = logging.getLogger(__name__)

REASON_TEXT = {"no_devices": "desinstaló (sin aparatos)", "inactive": "no mira los avisos"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def route_of(a: dict) -> str:
    return (f"{a.get('originName') or ''} ({a.get('originIata') or ''}) → "
            f"{a.get('destinationName') or ''} ({a.get('destinationIata') or ''})")[:160]


# ---------------------------------------------------------------------------
# Bloqueantes (SDK sync de Firebase): se llaman desde un thread
# ---------------------------------------------------------------------------

def pause_user_alerts(db, uid: str, reason: str, detail: str, now: datetime, dry_run: bool = False) -> list[dict]:
    """Pausa todas las alertas activas del usuario. Devuelve [{alert_path, route}]."""
    out = []
    for snap in db.collection("users").document(uid).collection("alerts").where("status", "==", "active").stream():
        a = snap.to_dict() or {}
        if not dry_run:
            snap.reference.update({"status": "paused", "pausedBy": "server", "pausedReason": reason, "pausedAt": now})
        out.append({"alert_path": f"users/{uid}/alerts/{snap.id}", "uid": uid, "reason": reason,
                    "detail": detail, "route": route_of(a)})
    return out


def _validate_devices(db, uid: str, dry_run: bool) -> tuple[list[dict], int]:
    """Envío de prueba a cada token (no llega nada). Borra los UNREGISTERED. (vivos, borrados)."""
    alive, removed = [], 0
    for dev in db.collection("users").document(uid).collection("devices").stream():
        d = dev.to_dict() or {}
        token = d.get("fcmToken")
        if not token:
            alive.append(d)          # sin token no se puede probar; no se toca
            continue
        try:
            messaging.send(messaging.Message(token=token, data={"type": "ping"}), dry_run=True)
            alive.append(d)
        except messaging.UnregisteredError:
            removed += 1
            if not dry_run:
                dev.reference.delete()
        except Exception as e:       # cuota, red, etc.: ante la duda el aparato sigue
            logger.info("alert_hygiene: no se pudo validar un token de %s (%s)", uid, type(e).__name__)
            alive.append(d)
    return alive, removed


def _last_notices(db, uid: str, n: int) -> list[dict]:
    items = [x.to_dict() or {} for x in db.collection("users").document(uid).collection("notifications").stream()]
    items = [x for x in items if x.get("type") == "price_drop"]
    items.sort(key=lambda x: _as_utc(x.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:n]


def _run_sync(cfg: dict, now: datetime, dry_run: bool, only_uid: str | None = None) -> dict:
    try:
        firebase_admin.get_app()
    except ValueError as e:
        raise price_alerts.FirestoreUnavailable("Firebase Admin SDK no inicializado") from e
    db = firestore.client()
    n, days = cfg["pause_unread_notices"], cfg["pause_inactive_days"]
    cutoff = now - timedelta(days=days)

    uids: set[str] = set()
    if only_uid:      # pruebas: un solo usuario
        uids = {only_uid} if any(True for _ in db.collection("users").document(only_uid).collection("alerts")
                                 .where("status", "==", "active").limit(1).stream()) else set()
    # Sin where: en un collection group necesitaría un índice creado a mano (ver price_alerts.py).
    for snap in ([] if only_uid else db.collection_group("alerts").stream()):
        parts = snap.reference.path.split("/")
        if len(parts) == 4 and parts[0] == "users" and (snap.to_dict() or {}).get("status") == "active":
            uids.add(parts[1])

    report = {"users_checked": len(uids), "devices_removed": 0, "paused": [], "kept_inactive": 0}
    for uid in sorted(uids):
        alive, removed = _validate_devices(db, uid, dry_run)
        report["devices_removed"] += removed
        if not alive:
            detail = ("FCM dio por muertos sus tokens: desinstaló o reinstaló la app." if removed
                      else "No tiene ningún aparato registrado: no hay a quién avisarle.")
            report["paused"] += pause_user_alerts(db, uid, "no_devices", detail, now, dry_run)
            continue
        last_open = max((_as_utc(d.get("updatedAt")) for d in alive if _as_utc(d.get("updatedAt"))), default=None)
        if last_open is None or last_open >= cutoff:
            continue
        notices = _last_notices(db, uid, n)
        if len(notices) < n or any(x.get("read") for x in notices):
            report["kept_inactive"] += 1      # no abre la app, pero todavía no juntó N avisos sin abrir
            continue
        idle = (now - last_open).days
        report["paused"] += pause_user_alerts(
            db, uid, "inactive", f"{n} avisos seguidos sin abrir y {idle} días sin abrir la app.", now, dry_run)
    return report


def _sync_reads_sync(rows: list[tuple[int, str, str]]) -> dict[int, datetime]:
    """rows: (id, uid, notice_id). Devuelve {id: read_at} de los que se leyeron."""
    db = firestore.client()
    out: dict[int, datetime] = {}
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        refs = [db.document(f"users/{uid}/notifications/{nid}") for _, uid, nid in chunk]
        by_path = {snap.reference.path: snap for snap in db.get_all(refs)}
        for (rid, uid, nid) in chunk:
            snap = by_path.get(f"users/{uid}/notifications/{nid}")
            if snap is not None and snap.exists:
                d = snap.to_dict() or {}
                if d.get("read"):
                    out[rid] = _as_utc(d.get("readAt")) or _now()
    return out


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------

async def record_pauses(items: list[dict], now: datetime) -> None:
    if not items:
        return
    async with AsyncSessionLocal() as db:
        for p in items:
            db.add(PriceAlertPause(alert_path=p["alert_path"], uid=p["uid"], reason=p["reason"],
                                   detail=p["detail"][:300], route=p["route"], paused_at=now))
        await db.commit()


async def sync_reads(days: int = 60) -> int:
    """Copia a Postgres (read_at) los avisos que el usuario abrió en la app."""
    since = _now() - timedelta(days=days)
    N = PriceAlertNotification
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(N.id, N.uid, N.notice_id)
                                 .where(N.created_at >= since, N.read_at.is_(None), N.uid.isnot(None)))).all()
    if not rows:
        return 0
    read = await asyncio.to_thread(_sync_reads_sync, [tuple(r) for r in rows])
    if read:
        async with AsyncSessionLocal() as db:
            for rid, at in read.items():
                await db.execute(update(N).where(N.id == rid).values(read_at=at))
            await db.commit()
    return len(read)


async def run(dry_run: bool = False, only_uid: str | None = None) -> dict:
    now = _now()
    cfg = await app_settings.get_all(force=True)
    reads = 0 if dry_run or only_uid else await sync_reads()
    if not cfg["auto_pause_enabled"] and not dry_run:
        return {"enabled": False, "reads_synced": reads}
    report = await asyncio.to_thread(_run_sync, cfg, now, dry_run, only_uid)
    if not dry_run:
        await record_pauses(report["paused"], now)
        if report["paused"] or report["devices_removed"]:
            await price_alerts.load(force=True)
    logger.info("alert_hygiene: %d usuarios, %d aparatos muertos, %d alertas pausadas%s",
                report["users_checked"], report["devices_removed"], len(report["paused"]), " (dry run)" if dry_run else "")
    return {
        "enabled": cfg["auto_pause_enabled"], "dry_run": dry_run, "reads_synced": reads,
        "users_checked": report["users_checked"], "devices_removed": report["devices_removed"],
        "kept_inactive": report["kept_inactive"],
        "paused": [{"alert_id": p["alert_path"].rsplit("/", 1)[-1], "user_ref": price_alerts._user_ref(p["uid"]),
                    "reason": p["reason"], "detail": p["detail"], "route": p["route"]} for p in report["paused"]],
    }


async def run_job() -> None:
    try:
        await run()
    except Exception:
        logger.exception("alert_hygiene: falló")
