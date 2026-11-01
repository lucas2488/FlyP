"""
/api/v1/affiliate/* — reporte de afiliados de Impact para el dashboard.

El dashboard nunca habla con Impact: todo sale de Postgres (sincronizado por
app/services/impact_sync.py). Auth: la misma X-API-Key del dashboard.

Parámetros comunes: from, to (YYYY-MM-DD, hora de Córdoba), groupBy (day|week|month),
compare (previous|last_year|none) y filtros program, country, device, os, subId, state
(cada uno acepta varios valores separados por coma).
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ImpactAction, ImpactClick, ImpactInvoice
from app.routers.analytics import verify_api_key
from app.services import impact_analytics as ia
from app.services import impact_sync
from app.services.impact_reports_client import ImpactError, ImpactReportsClient, is_configured, list_api_reports

router = APIRouter(prefix="/affiliate", tags=["affiliate"], dependencies=[Depends(verify_api_key)])

GroupBy = Literal["day", "week", "month"]
Compare = Literal["previous", "last_year", "none"]


class Common:
    def __init__(
        self,
        from_: date | None = Query(None, alias="from"),
        to: date | None = Query(None),
        program: str | None = Query(None),
        country: str | None = Query(None),
        region: str | None = Query(None),
        device: str | None = Query(None),
        os: str | None = Query(None),
        sub_id: str | None = Query(None, alias="subId"),
        state: str | None = Query(None),
    ):
        self.period = ia.make_period(from_, to)
        self.filters = ia.Filters(
            program=ia.split_list(program), country=ia.split_list(country), region=ia.split_list(region),
            device=ia.split_list(device), os=ia.split_list(os),
            sub_id=ia.split_list(sub_id), state=ia.split_list(state),
        )


async def _meta(db: AsyncSession, c: Common) -> dict:
    return {
        "period": c.period.as_dict(),
        "filters": c.filters.as_dict(),
        "clicks_available_until": ia._iso(await ia.clicks_available_until(db)),
        "clicks_coverage": await ia.clicks_coverage(db, c.period),
        "today": ia.today().isoformat(),
    }


@router.get("/summary")
async def summary(c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                  compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    data = await ia.summary(db, c.period, c.filters, group_by, compare)
    return {**await _meta(db, c), **data, "group_by": group_by,
            "ignored_filters_for_conversions": c.filters.ignored_for_conversions()}


@router.get("/clicks/timeseries")
async def clicks_timeseries(c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                            compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    cp = ia.compare_period(c.period, compare)
    return {
        **await _meta(db, c), "group_by": group_by,
        "compare_period": cp.as_dict() if cp else None,
        "series": await ia.clicks_series(db, c.period, c.filters, group_by),
        "compare_series": await ia.clicks_series(db, cp, c.filters, group_by) if cp else None,
    }


@router.get("/clicks/breakdown")
async def clicks_breakdown(dimension: str, c: Common = Depends(), limit: int = Query(50, ge=1, le=500),
                           db: AsyncSession = Depends(get_db)):
    if dimension not in ia.CLICK_DIMENSIONS:
        raise HTTPException(400, f"dimension inválida; opciones: {', '.join(ia.CLICK_DIMENSIONS)}")
    return {**await _meta(db, c), "dimension": dimension,
            "items": await ia.clicks_breakdown(db, c.period, c.filters, dimension, limit)}


@router.get("/clicks/heatmap")
async def clicks_heatmap(c: Common = Depends(), db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c), **await ia.heatmap(db, c.period, c.filters)}


@router.get("/clicks/platforms")
async def clicks_platforms(c: Common = Depends(), db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c), "items": await ia.platforms(db, c.period, c.filters)}


@router.get("/clicks")
async def clicks_list(c: Common = Depends(), page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=500, alias="pageSize"),
                      q: str | None = None, sort: str | None = None, db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c), **await ia.list_clicks(db, c.period, c.filters, page, page_size, q, sort)}


@router.get("/clicks/{click_id}")
async def click_detail(click_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ImpactClick, click_id)
    if not row:
        raise HTTPException(404, "Click no encontrado")
    return ia.click_dict(row, full=True)


@router.get("/conversions/timeseries")
async def conversions_timeseries(c: Common = Depends(), group_by: GroupBy = Query("day", alias="groupBy"),
                                 compare: Compare = "previous", db: AsyncSession = Depends(get_db)):
    cp = ia.compare_period(c.period, compare)
    return {
        **await _meta(db, c), "group_by": group_by,
        "compare_period": cp.as_dict() if cp else None,
        "series": await ia.conversions_series(db, c.period, c.filters, group_by),
        "compare_series": await ia.conversions_series(db, cp, c.filters, group_by) if cp else None,
        "stats": await ia.conversion_stats(db, c.period, c.filters),
        "compare_stats": await ia.conversion_stats(db, cp, c.filters) if cp else None,
        "ignored_filters": c.filters.ignored_for_conversions(),
    }


@router.get("/conversions/breakdown")
async def conversions_breakdown(dimension: str, c: Common = Depends(), limit: int = Query(50, ge=1, le=500),
                                db: AsyncSession = Depends(get_db)):
    if dimension not in ia.CONVERSION_DIMENSIONS:
        raise HTTPException(400, f"dimension inválida; opciones: {', '.join(ia.CONVERSION_DIMENSIONS)}")
    return {**await _meta(db, c), "dimension": dimension,
            "items": await ia.conversions_breakdown(db, c.period, c.filters, dimension, limit)}


@router.get("/conversions")
async def conversions_list(c: Common = Depends(), page: int = Query(1, ge=1),
                           page_size: int = Query(50, ge=1, le=500, alias="pageSize"),
                           q: str | None = None, sort: str | None = None,
                           locking_date: date | None = Query(None, alias="lockingDate"),
                           db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c),
            **await ia.list_conversions(db, c.period, c.filters, page, page_size, q, sort, locking_date)}


@router.get("/conversions/{conversion_id}")
async def conversion_detail(conversion_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(ImpactAction, conversion_id)
    if not row:
        raise HTTPException(404, "Conversión no encontrada")
    return ia.action_dict(row, full=True)


@router.get("/earnings")
async def earnings(c: Common = Depends(), db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c), **await ia.earnings(db, c.period, c.filters)}


@router.get("/invoices")
async def invoices(db: AsyncSession = Depends(get_db)):
    return {"items": await ia.list_invoices(db)}


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, db: AsyncSession = Depends(get_db)):
    if not await db.get(ImpactInvoice, invoice_id):
        raise HTTPException(404, "Factura no encontrada")
    try:
        async with ImpactReportsClient() as client:
            resp = await client.download(f"/Invoices/{invoice_id}/Download")
    except ImpactError as e:
        raise HTTPException(502, f"Impact no devolvió el PDF ({e.status})")
    return Response(content=resp.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="factura-impact-{invoice_id}.pdf"'})


@router.get("/sources")
async def sources(c: Common = Depends(), db: AsyncSession = Depends(get_db)):
    return {**await _meta(db, c),
            "sources": await ia.sources_table(db, c.period, c.filters),
            "platforms": await ia.platforms(db, c.period, c.filters)}


@router.get("/programs")
async def programs(db: AsyncSession = Depends(get_db)):
    return {"items": await ia.programs(db)}


@router.get("/sync-status")
async def sync_status(db: AsyncSession = Depends(get_db)):
    return await ia.sync_status(db)


@router.post("/refresh")
async def refresh(db: AsyncSession = Depends(get_db)):
    """Actions + reporte diario (nunca ClickExport). Devuelve el sync-status nuevo."""
    if not is_configured():
        raise HTTPException(503, "Faltan las credenciales de reportes de Impact en el servidor")
    await impact_sync.refresh()
    return await ia.sync_status(db)


@router.get("/impact-reports")
async def impact_reports():
    """Reportes de Impact accesibles por API (para elegir IMPACT_PERFORMANCE_REPORT_ID)."""
    if not is_configured():
        raise HTTPException(503, "Faltan las credenciales de reportes de Impact en el servidor")
    return {"items": await list_api_reports()}
