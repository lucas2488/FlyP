"""
/api/v1/admob/* — dashboard de AdMob (independiente del de afiliados).

Todo sale de Postgres (sincronizado por app/services/admob_sync.py); el dashboard nunca
habla con Google. Auth: la misma X-API-Key del dashboard.

Parámetros comunes: from, to (YYYY-MM-DD, días de AdMob = hora de Los Ángeles),
groupBy (day|week|month), compare (previous|last_year|none) y filtros app, platform,
format, adUnit, country (varios valores separados por coma).
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.analytics import verify_api_key
from app.services import admob_analytics as aa
from app.services import admob_sync
from app.services.admob_client import is_configured

router = APIRouter(prefix="/admob", tags=["admob"], dependencies=[Depends(verify_api_key)])

GroupBy = Literal["day", "week", "month"]
Compare = Literal["previous", "last_year", "none"]
Metric = Literal["earnings", "impressions", "ecpm", "clicks", "ctr", "requests", "matched", "match_rate", "show_rate"]


class Common:
    def __init__(
        self,
        from_: date | None = Query(None, alias="from"),
        to: date | None = Query(None),
        app: str | None = Query(None),
        platform: str | None = Query(None),
        format: str | None = Query(None),
        ad_unit: str | None = Query(None, alias="adUnit"),
        country: str | None = Query(None),
    ):
        self.period = aa.make_period(from_, to)
        self.filters = aa.Filters(app=aa.split_list(app), platform=aa.split_list(platform),
                                  format=aa.split_list(format), ad_unit=aa.split_list(ad_unit),
                                  country=aa.split_list(country))

    def meta(self) -> dict:
        return {"period": self.period.as_dict(), "filters": self.filters.as_dict(),
                "report_date_today": admob_sync.la_today().isoformat(), "timezone": "America/Los_Angeles"}


@router.get("/today")
async def today(c: Common = Depends(), db: AsyncSession = Depends(get_db)):
    return {"filters": c.filters.as_dict(), **await aa.today(db, c.filters)}


@router.get("/summary")
async def summary(c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                  compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    return {**c.meta(), "group_by": group_by, **await aa.summary(db, c.period, c.filters, group_by, compare)}


@router.get("/insights")
async def insights(c: Common = Depends(), min_impressions: int = Query(200, alias="minImpressions", ge=0),
                   threshold: float = Query(0.25, ge=0.01, le=10), db: AsyncSession = Depends(get_db)):
    return {"filters": c.filters.as_dict(), **await aa.insights(db, c.filters, min_impressions, threshold)}


@router.get("/apps")
async def apps(c: Common = Depends(), compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    return {**c.meta(), "currency": await aa.currency(db), "items": await aa.apps(db, c.period, c.filters, compare)}


@router.get("/ad-units")
async def ad_units(c: Common = Depends(), compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    return {**c.meta(), "currency": await aa.currency(db), "items": await aa.ad_units(db, c.period, c.filters, compare)}


@router.get("/ad-units/{ad_unit_id:path}")
async def ad_unit(ad_unit_id: str, c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                  compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    d = await aa.ad_unit_detail(db, ad_unit_id, c.period, c.filters, group_by, compare)
    if not d:
        raise HTTPException(404, "Bloque de anuncios no encontrado")
    return {**c.meta(), "currency": await aa.currency(db), **d}


@router.get("/breakdown")
async def breakdown(dimension: str, c: Common = Depends(), compare: Compare = "previous",
                    limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    if dimension not in aa.BREAKDOWNS:
        raise HTTPException(400, f"dimension inválida; opciones: {', '.join(aa.BREAKDOWNS)}")
    return {**c.meta(), "currency": await aa.currency(db), **await aa.breakdown(db, dimension, c.period, c.filters, compare, limit)}


@router.get("/timeseries")
async def timeseries(metric: Metric = "earnings", c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                     compare: Compare = "previous", split: Literal["platform", "format", "country", "app_id"] | None = None,
                     source: Literal["daily", "serving"] = "daily", db: AsyncSession = Depends(get_db)):
    return {**c.meta(), "group_by": group_by, "currency": await aa.currency(db),
            **await aa.timeseries(db, metric, c.period, c.filters, group_by, compare, split, source)}


@router.get("/funnel")
async def funnel(c: Common = Depends(), compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    return {**c.meta(), **await aa.funnel(db, c.period, c.filters, compare)}


@router.get("/mediation")
async def mediation(dimension: Literal["ad_source", "mediation_group"] = "ad_source", c: Common = Depends(),
                    db: AsyncSession = Depends(get_db)):
    return {**c.meta(), "currency": await aa.currency(db), **await aa.mediation(db, dimension, c.period, c.filters)}


@router.get("/filters")
async def filters(db: AsyncSession = Depends(get_db)):
    return await aa.filter_options(db)


@router.get("/sync-status")
async def sync_status(db: AsyncSession = Depends(get_db)):
    return await aa.sync_status(db)


@router.post("/refresh")
async def refresh(db: AsyncSession = Depends(get_db)):
    """Corrida horaria en el momento (hoy y ayer). Como mucho una vez por minuto."""
    if not is_configured():
        raise HTTPException(503, "Faltan las credenciales de AdMob en el servidor")
    ran = await admob_sync.refresh()
    return {"ran": ran, **await aa.sync_status(db)}


@router.get("/versions")
async def versions(dimension: Literal["app_version", "os_version", "sdk_version"] = "app_version",
                   metric: Metric = "earnings", c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                   top: int = Query(6, ge=1, le=20), db: AsyncSession = Depends(get_db)):
    """Serie por versión con fecha de salida (marcador) y uso del último día."""
    return {**c.meta(), "group_by": group_by, "currency": await aa.currency(db),
            **await aa.versions(db, dimension, metric, c.period, c.filters, group_by, top)}
