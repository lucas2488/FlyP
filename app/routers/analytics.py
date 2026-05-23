"""
Analytics router — expone métricas agregadas para el dashboard de marketing.
Autenticación: header X-API-Key debe coincidir con settings.analytics_api_key
"""
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.database import get_db
from app.models import (
    UserProfile, PriceWatch, NotificationLog,
    ImpactLinkLog, SearchEvent,
)
from app.config import settings

router = APIRouter(prefix="/analytics", tags=["analytics"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != settings.analytics_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# ---------------------------------------------------------------------------
# GET /analytics/overview
# KPIs principales del producto
# ---------------------------------------------------------------------------
@router.get("/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    now_ms = int(time.time() * 1000)
    seven_days_ms  = 7  * 24 * 3600 * 1000
    thirty_days_ms = 30 * 24 * 3600 * 1000

    now_dt      = datetime.utcnow()
    seven_days  = now_dt - timedelta(days=7)
    thirty_days = now_dt - timedelta(days=30)

    # Usuarios
    total_users   = await db.scalar(select(func.count()).select_from(UserProfile))
    active_7d     = await db.scalar(
        select(func.count()).select_from(UserProfile)
        .where(UserProfile.last_app_open >= now_ms - seven_days_ms)
    )
    active_30d    = await db.scalar(
        select(func.count()).select_from(UserProfile)
        .where(UserProfile.last_app_open >= now_ms - thirty_days_ms)
    )
    new_users_7d  = await db.scalar(
        select(func.count()).select_from(UserProfile)
        .where(UserProfile.created_at >= seven_days)
    )

    # Búsquedas
    total_searches = await db.scalar(
        select(func.coalesce(func.sum(UserProfile.total_searches), 0))
        .select_from(UserProfile)
    )

    # Price watches
    active_price_watches = await db.scalar(
        select(func.count()).select_from(PriceWatch)
        .where(PriceWatch.is_active.is_(True))
    )

    # Clicks afiliados
    impact_total = await db.scalar(select(func.count()).select_from(ImpactLinkLog))
    impact_7d    = await db.scalar(
        select(func.count()).select_from(ImpactLinkLog)
        .where(ImpactLinkLog.clicked_at >= seven_days)
    )
    impact_30d   = await db.scalar(
        select(func.count()).select_from(ImpactLinkLog)
        .where(ImpactLinkLog.clicked_at >= thirty_days)
    )

    # Notificaciones
    notif_7d    = await db.scalar(
        select(func.count()).select_from(NotificationLog)
        .where(NotificationLog.sent_at >= seven_days)
    )
    notif_total = await db.scalar(select(func.count()).select_from(NotificationLog))

    return {
        "total_users":           total_users or 0,
        "active_users_7d":       active_7d or 0,
        "active_users_30d":      active_30d or 0,
        "new_users_7d":          new_users_7d or 0,
        "total_searches":        int(total_searches or 0),
        "active_price_watches":  active_price_watches or 0,
        "impact_clicks_total":   impact_total or 0,
        "impact_clicks_7d":      impact_7d or 0,
        "impact_clicks_30d":     impact_30d or 0,
        "notifications_sent_7d": notif_7d or 0,
        "notifications_total":   notif_total or 0,
    }


# ---------------------------------------------------------------------------
# GET /analytics/top-routes
# Rutas más buscadas (desde search_events)
# ---------------------------------------------------------------------------
@router.get("/top-routes")
async def get_top_routes(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list:
    day_trunc = func.date_trunc("day", SearchEvent.occurred_at)

    result = await db.execute(
        select(
            SearchEvent.origin,
            SearchEvent.destination,
            func.count().label("search_count"),
            func.min(SearchEvent.best_price).label("min_price"),
            func.avg(SearchEvent.best_price).label("avg_price"),
            func.max(SearchEvent.occurred_at).label("last_seen"),
        )
        .group_by(SearchEvent.origin, SearchEvent.destination)
        .order_by(func.count().desc())
        .limit(limit)
    )

    return [
        {
            "origin":       r.origin,
            "destination":  r.destination,
            "search_count": r.search_count,
            "min_price":    round(float(r.min_price), 2) if r.min_price else None,
            "avg_price":    round(float(r.avg_price), 2) if r.avg_price else None,
            "last_seen":    r.last_seen.isoformat() if r.last_seen else None,
        }
        for r in result.all()
    ]


# ---------------------------------------------------------------------------
# GET /analytics/notifications
# Métricas de push notifications
# ---------------------------------------------------------------------------
@router.get("/notifications")
async def get_notifications(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    since = datetime.utcnow() - timedelta(days=days)

    total_sent   = await db.scalar(
        select(func.count()).select_from(NotificationLog)
        .where(NotificationLog.sent_at >= since)
    )
    total_failed = await db.scalar(
        select(func.count()).select_from(NotificationLog)
        .where(and_(
            NotificationLog.sent_at >= since,
            NotificationLog.delivery_status == "failed",
        ))
    )

    # Por tipo
    by_type_res = await db.execute(
        select(NotificationLog.type, func.count().label("count"))
        .where(NotificationLog.sent_at >= since)
        .group_by(NotificationLog.type)
        .order_by(func.count().desc())
    )
    by_type = [{"type": r.type or "unknown", "count": r.count} for r in by_type_res.all()]

    # Por día
    day_trunc = func.date_trunc("day", NotificationLog.sent_at)
    by_day_res = await db.execute(
        select(
            day_trunc.label("day"),
            NotificationLog.delivery_status,
            func.count().label("count"),
        )
        .where(NotificationLog.sent_at >= since)
        .group_by(day_trunc, NotificationLog.delivery_status)
        .order_by(day_trunc)
    )

    by_day_map: dict = {}
    for r in by_day_res.all():
        d = r.day.strftime("%Y-%m-%d") if r.day else "unknown"
        if d not in by_day_map:
            by_day_map[d] = {"date": d, "sent": 0, "failed": 0}
        if r.delivery_status == "failed":
            by_day_map[d]["failed"] = r.count
        else:
            by_day_map[d]["sent"] = r.count

    total  = total_sent or 0
    failed = total_failed or 0
    delivery_rate = round((total - failed) / total * 100, 1) if total > 0 else 0.0

    return {
        "total_sent":        total,
        "total_failed":      failed,
        "delivery_rate_pct": delivery_rate,
        "by_type":           by_type,
        "by_day":            list(by_day_map.values()),
    }


# ---------------------------------------------------------------------------
# GET /analytics/revenue
# Clicks en affiliate links (Impact.com)
# ---------------------------------------------------------------------------
@router.get("/revenue")
async def get_revenue(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    since       = datetime.utcnow() - timedelta(days=days)
    seven_days  = datetime.utcnow() - timedelta(days=7)

    total_clicks    = await db.scalar(
        select(func.count()).select_from(ImpactLinkLog)
        .where(ImpactLinkLog.clicked_at >= since)
    )
    total_clicks_7d = await db.scalar(
        select(func.count()).select_from(ImpactLinkLog)
        .where(ImpactLinkLog.clicked_at >= seven_days)
    )

    # Por día
    day_trunc = func.date_trunc("day", ImpactLinkLog.clicked_at)
    by_day_res = await db.execute(
        select(day_trunc.label("day"), func.count().label("clicks"))
        .where(ImpactLinkLog.clicked_at >= since)
        .group_by(day_trunc)
        .order_by(day_trunc)
    )
    by_day = [
        {"date": r.day.strftime("%Y-%m-%d") if r.day else "unknown", "clicks": r.clicks}
        for r in by_day_res.all()
    ]

    # Por dominio (extraído en Python)
    urls_res = await db.execute(
        select(ImpactLinkLog.url)
        .where(ImpactLinkLog.clicked_at >= since)
    )
    domain_counts: dict = {}
    for (url,) in urls_res.all():
        try:
            domain = urlparse(url).netloc or "unknown"
        except Exception:
            domain = "unknown"
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    by_domain = sorted(
        [{"domain": d, "clicks": c} for d, c in domain_counts.items()],
        key=lambda x: x["clicks"],
        reverse=True,
    )[:10]

    return {
        "total_clicks":    total_clicks or 0,
        "total_clicks_7d": total_clicks_7d or 0,
        "by_day":          by_day,
        "by_domain":       by_domain,
    }


# ---------------------------------------------------------------------------
# GET /analytics/price-watches
# Rutas más watcheadas — alta intención de compra
# ---------------------------------------------------------------------------
@router.get("/price-watches")
async def get_price_watches(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    top_res = await db.execute(
        select(
            PriceWatch.origin,
            PriceWatch.destination,
            func.count().label("watch_count"),
            func.avg(PriceWatch.interest_score).label("avg_interest"),
            func.avg(PriceWatch.last_price).label("avg_price"),
            func.sum(PriceWatch.notification_count).label("total_notifications"),
        )
        .where(PriceWatch.is_active.is_(True))
        .group_by(PriceWatch.origin, PriceWatch.destination)
        .order_by(func.count().desc())
        .limit(limit)
    )

    top_routes = [
        {
            "origin":             r.origin,
            "destination":        r.destination,
            "watch_count":        r.watch_count,
            "avg_interest_score": round(float(r.avg_interest or 0), 1),
            "avg_price":          round(float(r.avg_price), 2) if r.avg_price else None,
            "total_notifications": int(r.total_notifications or 0),
        }
        for r in top_res.all()
    ]

    total_active = await db.scalar(
        select(func.count()).select_from(PriceWatch)
        .where(PriceWatch.is_active.is_(True))
    )
    total_all = await db.scalar(select(func.count()).select_from(PriceWatch))

    return {
        "total_active": total_active or 0,
        "total_all":    total_all or 0,
        "top_routes":   top_routes,
    }
