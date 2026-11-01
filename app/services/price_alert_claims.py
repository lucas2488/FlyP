"""
Reservas de búsquedas (POST /checker/claim) y validación de resultados con reserva.

Quién busca:
  - admin: FlyAdmin, con X-Checker-Key. Puede reservar un lote; puede mandar resultados sin reserva.
  - app:   la app de usuarios, con Firebase App Check + X-Device-Id. Reserva de a 1 y SOLO puede
           mandar resultados de búsquedas que tiene reservadas.

Reparto atómico: price_alert_claims tiene un índice único parcial (search_key) WHERE status='open'.
Cada reserva es un INSERT … ON CONFLICT DO NOTHING: si dos pedidos llegan juntos por la misma
búsqueda, uno la obtiene y el otro pasa a la siguiente. Nunca hay dos reservas abiertas iguales.

Ver DESIGN_crowd_checker.md.
"""
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models import PriceAlertClaim, PriceAlertVerification
from app.services import app_settings, price_alerts

AR = ZoneInfo("America/Argentina/Buenos_Aires")
CLAIM_GRACE = timedelta(minutes=2)          # tolerancia para resultados que llegan justo al vencer
VERIFICATION_TTL = timedelta(hours=24)       # una bajada rara sin confirmar se descarta después de esto
ADMIN_MAX_LIMIT = 200

SEARCH_FIELDS = ("search_key", "origin", "destination", "trip_type", "date_mode", "departure_date",
                 "return_date", "month", "adults", "child_ages", "cabin_class", "currency", "country")


@dataclass(frozen=True)
class Worker:
    source: str                  # admin | app
    device_id: str | None = None
    worker_id: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_start_utc(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(AR).date(), time.min, AR).astimezone(timezone.utc)


def search_payload(s: dict) -> dict:
    """Solo lo necesario para buscar. Nunca datos de usuarios (nombres, emails, tokens)."""
    return {k: s.get(k) for k in SEARCH_FIELDS}


async def expire_stale(now: datetime) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(PriceAlertClaim)
                         .where(PriceAlertClaim.status == "open", PriceAlertClaim.expires_at < now - CLAIM_GRACE)
                         .values(status="expired", finished_at=now))
        await db.execute(update(PriceAlertVerification)
                         .where(PriceAlertVerification.status == "pending",
                                PriceAlertVerification.created_at < now - VERIFICATION_TTL)
                         .values(status="expired", resolved_at=now))
        await db.commit()


async def _usage(device_id: str, session_id: str | None, now: datetime) -> tuple[int, int]:
    """(reservas de hoy, reservas de esta sesión) del aparato."""
    C = PriceAlertClaim
    async with AsyncSessionLocal() as db:
        today = await db.scalar(select(func.count()).where(C.device_id == device_id, C.claimed_at >= _day_start_utc(now))) or 0
        session = 0
        if session_id:
            session = await db.scalar(select(func.count()).where(C.device_id == device_id, C.session_id == session_id)) or 0
    return today, session


async def claim(worker: Worker, limit: int, session_id: str | None) -> dict:
    now = _now()
    cfg = await app_settings.get_all()
    empty = {"claims": [], "next_poll_after_seconds": None}

    if worker.source == "app":
        if not cfg["crowd_checker_enabled"]:
            return {**empty, "reason": "disabled"}
        today, session = await _usage(worker.device_id, session_id, now)
        if today >= cfg["max_per_day"] or (session_id and session >= cfg["max_per_session"]):
            return {**empty, "reason": "limit"}
        limit = 1
    else:
        limit = max(1, min(limit, ADMIN_MAX_LIMIT))

    await expire_stale(now)
    data = await price_alerts.load()
    by_key = {s["search_key"]: s for s in data["searches"] if s.get("searchable")}

    async with AsyncSessionLocal() as db:
        open_keys = set((await db.execute(select(PriceAlertClaim.search_key)
                                          .where(PriceAlertClaim.status == "open"))).scalars())
        pending_verif = (await db.execute(
            select(PriceAlertVerification.search_key, PriceAlertVerification.device_id)
            .where(PriceAlertVerification.status == "pending")
            .order_by(PriceAlertVerification.created_at)
        )).all()

    # 1) Bajadas raras esperando confirmación: primero, y nunca al mismo aparato que la reportó.
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, reporter in pending_verif:
        if key in by_key and key not in seen and not (worker.source == "app" and reporter == worker.device_id):
            candidates.append((key, "verify"))
            seen.add(key)
    # 2) Búsquedas sin consultar hace RECHECK_AFTER, las más viejas primero.
    pending, total_pending = price_alerts.pending_searches(list(by_key.values()), now, len(by_key))
    for s in pending:
        if s["search_key"] not in seen:
            candidates.append((s["search_key"], "check"))
            seen.add(s["search_key"])

    expires = now + timedelta(minutes=cfg["claim_minutes"])
    claims = []
    async with AsyncSessionLocal() as db:
        for key, purpose in candidates:
            if len(claims) >= limit:
                break
            if key in open_keys:
                continue
            claim_id = "c_" + secrets.token_urlsafe(16)
            got = (await db.execute(
                pg_insert(PriceAlertClaim).values(
                    claim_id=claim_id, search_key=key, source=worker.source, device_id=worker.device_id,
                    session_id=session_id, status="open", purpose=purpose, claimed_at=now, expires_at=expires,
                ).on_conflict_do_nothing(index_elements=["search_key"], index_where=text("status = 'open'"))
                .returning(PriceAlertClaim.claim_id)
            )).scalar_one_or_none()
            await db.commit()
            if got:
                claims.append({"claim_id": got, "expires_at": expires.isoformat(), "purpose": purpose,
                               **search_payload(by_key[key])})

    left = len([c for c in candidates if c[0] not in open_keys]) - len(claims)
    poll = cfg["poll_interval_seconds"] if worker.source == "app" and claims else None
    return {"claims": claims, "next_poll_after_seconds": poll, "pending_left": max(left, 0)}


# ---------------------------------------------------------------------------
# Validación de resultados con reserva
# ---------------------------------------------------------------------------

async def load_claims(claim_ids: list[str]) -> dict[str, PriceAlertClaim]:
    if not claim_ids:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PriceAlertClaim).where(PriceAlertClaim.claim_id.in_(claim_ids)))).scalars().all()
    return {r.claim_id: r for r in rows}


def check_claim(c: PriceAlertClaim | None, search_key: str, worker: Worker, now: datetime) -> str | None:
    """None si la reserva es válida para este resultado; si no, el motivo."""
    if c is None:
        return "la reserva no existe"
    if c.search_key != search_key:
        return "la reserva es de otra búsqueda"
    if c.status != "open":
        return f"la reserva ya está {c.status}"
    if c.expires_at + CLAIM_GRACE < now:
        return "la reserva venció"
    if c.source != worker.source or (worker.source == "app" and c.device_id != worker.device_id):
        return "la reserva es de otro aparato"
    return None


async def finish_claims(statuses: dict[str, str], now: datetime) -> None:
    if not statuses:
        return
    async with AsyncSessionLocal() as db:
        for cid, st in statuses.items():
            await db.execute(update(PriceAlertClaim)
                             .where(PriceAlertClaim.claim_id == cid, PriceAlertClaim.status == "open")
                             .values(status=st, finished_at=now))
        await db.commit()
