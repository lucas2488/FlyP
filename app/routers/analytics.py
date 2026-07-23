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
    ImpactLinkLog, SearchEvent, NotificationQueue, NotificationTemplate, PriceSnapshot,
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
# Rutas más buscadas — usa price_watches (fuente real de datos de búsqueda)
# ---------------------------------------------------------------------------
@router.get("/top-routes")
async def get_top_routes(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list:
    # Nota: last_search_best_price viene de POST /events/search-result (Android).
    # last_price era del cron de Skyscanner — no existe. Usar last_search_best_price.
    # last_seen usa MAX(created_at) como proxy de actividad reciente en esa ruta.
    result = await db.execute(
        select(
            PriceWatch.origin,
            PriceWatch.destination,
            func.count(func.distinct(PriceWatch.user_id)).label("search_count"),
            func.min(PriceWatch.last_search_best_price).label("min_price"),
            func.avg(PriceWatch.last_search_best_price).label("avg_price"),
            func.max(PriceWatch.created_at).label("last_seen"),
        )
        .where(PriceWatch.is_active.is_(True))
        .group_by(PriceWatch.origin, PriceWatch.destination)
        .order_by(func.count(func.distinct(PriceWatch.user_id)).desc())
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

    # Open rate — notification_queue retiene 7 días; la ventana de open rate es 7 días fijos
    queue_since = datetime.utcnow() - timedelta(days=7)
    opens_res = await db.execute(
        select(
            NotificationQueue.drop_level,
            func.count().label("sent"),
            func.count(NotificationQueue.opened_at).label("opened"),
        )
        .where(
            NotificationQueue.status == "sent",
            NotificationQueue.sent_at >= queue_since,
            NotificationQueue.notification_type == "price_drop",
        )
        .group_by(NotificationQueue.drop_level)
    )
    open_by_level: dict = {}
    total_q_sent = 0
    total_q_opened = 0
    for r in opens_res.all():
        if r.drop_level:
            open_by_level[r.drop_level] = (r.sent, r.opened)
            total_q_sent += r.sent
            total_q_opened += r.opened

    open_rate_pct = round(total_q_opened / total_q_sent * 100, 1) if total_q_sent > 0 else 0.0

    # Enriquecer by_type con open_rate_pct por nivel (solo price_drop)
    by_type_enriched = []
    for t in by_type:
        level_data = open_by_level.get(t["type"])
        t["open_rate_pct"] = (
            round(level_data[1] / level_data[0] * 100, 1) if level_data and level_data[0] > 0 else None
        )
        by_type_enriched.append(t)

    return {
        "total_sent":        total,
        "total_failed":      failed,
        "delivery_rate_pct": delivery_rate,
        "opens_total":       total_q_opened,
        "open_rate_pct":     open_rate_pct,
        "by_type":           by_type_enriched,
        "by_day":            list(by_day_map.values()),
    }


# ---------------------------------------------------------------------------
# GET /analytics/notifications/history
# Historial reciente de notification_log con paginación y filtro por tipo
# ---------------------------------------------------------------------------
@router.get("/notifications/history")
async def get_notification_history(
    limit: int = 100,
    offset: int = 0,
    type: str | None = None,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    since = datetime.utcnow() - timedelta(days=days)

    q = (
        select(NotificationLog)
        .where(NotificationLog.sent_at >= since)
        .order_by(NotificationLog.sent_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if type:
        q = q.where(NotificationLog.type == type)

    result = await db.execute(q)
    items = result.scalars().all()

    count_q = select(func.count()).select_from(NotificationLog).where(NotificationLog.sent_at >= since)
    if type:
        count_q = count_q.where(NotificationLog.type == type)
    total = await db.scalar(count_q) or 0

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": item.id,
                "user_id": item.user_id[:20] + "…" if item.user_id and len(item.user_id) > 20 else item.user_id,
                "type": item.type,
                "origin": item.origin,
                "destination": item.destination,
                "price": item.price,
                "delivery_status": item.delivery_status,
                "sent_at": item.sent_at.isoformat() if item.sent_at else None,
            }
            for item in items
        ],
    }


# ---------------------------------------------------------------------------
# GET /analytics/notifications/template-health
# Alerta: países activos sin templates configurados para algún nivel
# ---------------------------------------------------------------------------
@router.get("/notifications/template-health")
async def get_template_health(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list:
    now_ms = int(time.time() * 1000)
    thirty_days_ms = 30 * 24 * 3600 * 1000

    countries_res = await db.execute(
        select(UserProfile.selected_country, func.count().label("user_count"))
        .where(
            UserProfile.selected_country.isnot(None),
            UserProfile.last_app_open >= now_ms - thirty_days_ms,
        )
        .group_by(UserProfile.selected_country)
        .order_by(func.count().desc())
    )
    active_countries = {
        r.selected_country: r.user_count
        for r in countries_res.all()
        if r.selected_country
    }

    templates_res = await db.execute(
        select(NotificationTemplate.country_code, NotificationTemplate.drop_level)
        .where(NotificationTemplate.is_active.is_(True))
        .distinct()
    )
    covered = {(r.country_code, r.drop_level) for r in templates_res.all()}
    wildcard_levels = {lvl for (cc, lvl) in covered if cc == "*"}

    required_levels = ["soft", "strong", "urgent"]
    result = []
    for country, user_count in active_countries.items():
        missing = [
            lvl for lvl in required_levels
            if (country, lvl) not in covered and lvl not in wildcard_levels
        ]
        result.append({
            "country":        country,
            "user_count":     user_count,
            "missing_levels": missing,
            "ok":             len(missing) == 0,
        })

    return result


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


# ---------------------------------------------------------------------------
# GET /analytics/route-prices
# Calendario de precios por fecha de VIAJE de una ruta puntual — para que
# marketing arme placas con precios vigentes.
#
# Importante sobre price_snapshots:
#   - snapshot_date  = fecha del vuelo (día de salida que devuelve el calendario)
#   - received_at    = cuándo lo recibimos (momento de la observación)
# Por eso el filtro `days` es sobre received_at (precios FRESCOS, vistos en los
# últimos N días) y el agrupado/orden es por snapshot_date (fecha de viaje).
# Además se excluyen vuelos ya pasados (snapshot_date >= hoy): no se venden.
# ---------------------------------------------------------------------------
@router.get("/route-prices")
async def get_route_prices(
    origin: str,
    destination: str,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    origin = origin.upper()
    destination = destination.upper()
    days = max(1, min(days, 365))  # acotar entrada: evita negativos / valores absurdos

    observed_since = datetime.utcnow() - timedelta(days=days)     # ventana de observación
    today_str = datetime.utcnow().strftime("%Y-%m-%d")            # solo vuelos a futuro

    # Moneda dominante entre los precios recientes, para no promediar ARS con USD
    currency_res = await db.execute(
        select(PriceSnapshot.currency, func.count().label("c"))
        .where(and_(
            PriceSnapshot.origin == origin,
            PriceSnapshot.destination == destination,
            PriceSnapshot.received_at >= observed_since,
        ))
        .group_by(PriceSnapshot.currency)
        .order_by(func.count().desc())
    )
    currency_rows = currency_res.all()
    dominant_currency = currency_rows[0].currency if currency_rows else None

    by_day = []
    if dominant_currency is not None:
        by_day_res = await db.execute(
            select(
                PriceSnapshot.snapshot_date,
                func.min(PriceSnapshot.price_raw).label("min_price"),
                func.avg(PriceSnapshot.price_raw).label("avg_price"),
                func.max(PriceSnapshot.price_raw).label("max_price"),
                func.count().label("count"),
            )
            .where(and_(
                PriceSnapshot.origin == origin,
                PriceSnapshot.destination == destination,
                PriceSnapshot.currency == dominant_currency,
                PriceSnapshot.received_at >= observed_since,   # precios frescos
                PriceSnapshot.snapshot_date >= today_str,      # vuelos a futuro
            ))
            .group_by(PriceSnapshot.snapshot_date)
            .order_by(PriceSnapshot.snapshot_date)
        )
        by_day = [
            {
                "date":      r.snapshot_date,
                "min_price": round(float(r.min_price), 2),
                "avg_price": round(float(r.avg_price), 2),
                "max_price": round(float(r.max_price), 2),
                "count":     r.count,
            }
            for r in by_day_res.all()
        ]

    # Meta histórica de la ruta (todo el histórico, sin filtros) en una sola query
    meta_row = (await db.execute(
        select(
            func.max(PriceSnapshot.received_at).label("last_seen"),
            func.count().label("total"),
        )
        .where(and_(
            PriceSnapshot.origin == origin,
            PriceSnapshot.destination == destination,
        ))
    )).one()

    return {
        "origin":          origin,
        "destination":     destination,
        "currency":        dominant_currency,
        "days":            days,
        "total_snapshots": meta_row.total or 0,
        "last_seen":       meta_row.last_seen.isoformat() if meta_row.last_seen else None,
        "by_day":          by_day,
    }


# ---------------------------------------------------------------------------
# GET /analytics/airports
# Cache de aeropuertos vistos en búsquedas — incluye geo_id/place_id para
# deep-linking futuro a Skyscanner.
# ---------------------------------------------------------------------------
@router.get("/airports")
async def get_airports(
    search: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list:
    from app.models import AirportCache
    q = select(AirportCache).order_by(AirportCache.times_searched.desc())
    if search:
        like = f"%{search}%"
        q = q.where(
            AirportCache.iata_code.ilike(like)
            | AirportCache.name.ilike(like)
            | AirportCache.country.ilike(like)
        )
    q = q.limit(min(limit, 500))
    result = await db.execute(q)
    return [
        {
            "iata_code":      a.iata_code,
            "name":           a.name,
            "country":        a.country,
            "geo_id":         a.geo_id,
            "place_id":       a.place_id,
            "times_searched": a.times_searched,
            "last_seen_at":   a.last_seen_at.isoformat() if a.last_seen_at else None,
        }
        for a in result.scalars().all()
    ]
