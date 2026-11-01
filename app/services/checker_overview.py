"""
Métricas del buscador de precios para el dashboard (GET /analytics/checker/overview).

- Búsquedas por día, separadas en FlyAdmin y app de usuarios, con errores y bajadas raras.
- Reservas por día: hechas, fallidas, vencidas.
- Bajadas raras recientes (sobre el umbral) y de qué aparato vinieron, aunque la doble
  verificación esté apagada: es lo que sirve para decidir prenderla.
- Aparatos: cuántas búsquedas, errores y bajadas raras hizo cada uno.
- Comparación: misma búsqueda consultada por FlyAdmin y por la app el mismo día.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import Date, cast, func, select

from app.database import AsyncSessionLocal
from app.models import PriceAlertCheck, PriceAlertClaim, PriceAlertVerification
from app.services import app_settings

AR_TZ = "America/Argentina/Buenos_Aires"


def _short(device: str | None) -> str | None:
    return f"{device[:6]}…{device[-4:]}" if device and len(device) > 12 else device


def _f(v):
    return float(v) if v is not None else None


async def overview(days: int) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    C, K, V = PriceAlertCheck, PriceAlertClaim, PriceAlertVerification
    cday = cast(func.timezone(AR_TZ, C.checked_at), Date)
    kday = cast(func.timezone(AR_TZ, K.claimed_at), Date)
    src = func.coalesce(C.source, "admin")
    async with AsyncSessionLocal() as db:
        checks = (await db.execute(
            select(cday, src, func.count(), func.count().filter(C.status != "ok"),
                   func.count().filter(C.rare.is_(True)), func.count().filter(C.status == "pending_verification"))
            .where(C.checked_at >= since).group_by(cday, src).order_by(cday)
        )).all()
        claims = (await db.execute(
            select(kday, K.source, func.count(), func.count().filter(K.status == "done"),
                   func.count().filter(K.status == "failed"), func.count().filter(K.status == "expired"),
                   func.count().filter(K.status == "open"))
            .where(K.claimed_at >= since).group_by(kday, K.source).order_by(kday)
        )).all()
        rare = (await db.execute(
            select(C).where(C.checked_at >= since, C.rare.is_(True)).order_by(C.checked_at.desc()).limit(50)
        )).scalars().all()
        devices = (await db.execute(
            select(C.device_id, func.count(), func.count().filter(C.status != "ok"), func.count().filter(C.rare.is_(True)),
                   func.max(C.checked_at))
            .where(C.checked_at >= since, C.device_id.isnot(None), func.coalesce(C.source, "admin") == "app")
            .group_by(C.device_id).order_by(func.count().desc()).limit(50)
        )).all()
        verifs = (await db.execute(
            select(V.status, func.count()).where(V.created_at >= since).group_by(V.status)
        )).all()
        # Misma búsqueda, mismo día, consultada por FlyAdmin y por la app: diferencia de precio.
        a = select(C.search_key, cday.label("d"), func.min(C.price).label("p")).where(
            C.checked_at >= since, src == "admin", C.status == "ok").group_by(C.search_key, "d").subquery()
        b = select(C.search_key, cday.label("d"), func.min(C.price).label("p")).where(
            C.checked_at >= since, src == "app", C.status == "ok").group_by(C.search_key, "d").subquery()
        pairs = (await db.execute(
            select(a.c.search_key, a.c.d, a.c.p, b.c.p)
            .join(b, (a.c.search_key == b.c.search_key) & (a.c.d == b.c.d))
            .order_by(a.c.d.desc()).limit(100)
        )).all()

    by_day: dict[str, dict] = {}
    for d, s, n, err, rr, pv in checks:
        e = by_day.setdefault(d.isoformat(), {"date": d.isoformat(), "admin": 0, "app": 0, "errors": 0, "rare": 0, "pending_verification": 0})
        e[s if s in ("admin", "app") else "admin"] += n
        e["errors"] += err
        e["rare"] += rr
        e["pending_verification"] += pv
    claims_by_day: dict[str, dict] = {}
    for d, s, n, done, failed, expired, opened in claims:
        e = claims_by_day.setdefault(d.isoformat(), {"date": d.isoformat(), "total": 0, "admin": 0, "app": 0,
                                                     "done": 0, "failed": 0, "expired": 0, "open": 0})
        e["total"] += n
        e[s if s in ("admin", "app") else "admin"] += n
        e["done"] += done
        e["failed"] += failed
        e["expired"] += expired
        e["open"] += opened
    diffs = [abs(float(pa) - float(pb)) / float(pa) * 100 for _, _, pa, pb in pairs if pa]
    return {
        "days": days,
        "settings": app_settings.describe(await app_settings.get_all()),
        "checks_daily": list(by_day.values()),
        "claims_daily": list(claims_by_day.values()),
        "verifications": {s: n for s, n in verifs},
        "rare_drops": [{
            "checked_at": r.checked_at.isoformat(), "search_key": r.search_key, "source": r.source or "admin",
            "device": _short(r.device_id), "price": _f(r.price), "reference_price": _f(r.reference_price),
            "currency": r.currency, "drop_pct": r.drop_pct, "status": r.status, "verification": r.verification,
        } for r in rare],
        "devices": [{"device": _short(dv), "checks": n, "errors": err, "rare": rr, "last_seen": last.isoformat()}
                    for dv, n, err, rr, last in devices],
        "admin_vs_app": {
            "pairs": len(pairs),
            "avg_diff_pct": round(sum(diffs) / len(diffs), 2) if diffs else None,
            "items": [{"search_key": k, "date": d.isoformat(), "admin_price": _f(pa), "app_price": _f(pb),
                       "diff_pct": round(abs(float(pa) - float(pb)) / float(pa) * 100, 2) if pa else None}
                      for k, d, pa, pb in pairs[:30]],
        },
    }
