"""
Sincronización Impact → Postgres para el reporte de afiliados.

Jobs (registrados en main.py):
  - sync_every_6h:  Actions (desde la última sync − 2 días, upsert) + reporte diario de los
                    últimos 3 días. La primera vez hace la carga inicial (3 años).
  - sync_daily:     06:00 Córdoba. Facturas, programas, ClickExport de ayer y de días
                    faltantes de los últimos 30 (respetando el cupo diario de ClickExport).
  - refresh:        botón del dashboard. Solo Actions + reporte diario (nunca ClickExport).

Cada corrida queda en impact_sync_runs (de ahí sale "Actualizado hace X h").
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    ImpactAction, ImpactClick, ImpactClickExport, ImpactDailyPerformance,
    ImpactInvoice, ImpactProgram, ImpactSyncRun,
)
from app.services.impact_reports_client import (
    ImpactError, ImpactReportsClient, is_configured,
)

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/Argentina/Cordoba")
ACTIONS_WINDOW = timedelta(days=45)          # máximo que acepta /Actions por request
ACTIONS_HISTORY_DAYS = 3 * 365               # Impact no da más de 3 años
PERFORMANCE_HISTORY_MONTHS = 36
CLICKS_HISTORY_DAYS = 30
BATCH = 500

_locks: dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    return _locks.setdefault(name, asyncio.Lock())


# ---------------------------------------------------------------------------
# Parseo de valores de Impact (todo viene como string; vacíos como "", "null", "NA")
# ---------------------------------------------------------------------------

_EMPTY = {"", "null", "NA", "N/A", "n/a", "None"}


def _s(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return None if v in _EMPTY else v


def _dec(v) -> Decimal | None:
    v = _s(v)
    if v is None:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _int(v) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def _dt(v) -> datetime | None:
    v = _s(v)
    if v is None:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    v = _s(v)
    return None if v is None else v.lower() in ("true", "1", "yes")


def _iso(d: datetime) -> str:
    return d.astimezone(TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Registro de corridas
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, run_id: int):
        self.id = run_id
        self.rows = 0
        self.detail: dict = {}


@asynccontextmanager
async def _run(kind: str):
    async with AsyncSessionLocal() as db:
        row = ImpactSyncRun(kind=kind, status="running")
        db.add(row)
        await db.commit()
        run = _Run(row.id)
    status, error = "ok", None
    try:
        yield run
    except Exception as e:
        status, error = "error", str(e)[:2000]
        logger.exception("impact_sync: %s falló", kind)
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(ImpactSyncRun, run.id)
            row.status, row.error = status, error
            row.rows = run.rows
            row.detail = run.detail or None
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()


async def _last_ok(kind: str) -> ImpactSyncRun | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(ImpactSyncRun).where(ImpactSyncRun.kind == kind, ImpactSyncRun.status == "ok")
            .order_by(ImpactSyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()


async def _upsert(model, rows: list[dict], key: list[str]) -> None:
    if not rows:
        return
    async with AsyncSessionLocal() as db:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            stmt = pg_insert(model).values(chunk)
            cols = {c: getattr(stmt.excluded, c) for c in chunk[0] if c not in key}
            cols["synced_at"] = func.now()
            await db.execute(stmt.on_conflict_do_update(index_elements=key, set_=cols))
        await db.commit()


# ---------------------------------------------------------------------------
# Actions (conversiones)
# ---------------------------------------------------------------------------

def _action_row(a: dict) -> dict:
    return {
        "id": a["Id"],
        "campaign_id": _s(a.get("CampaignId")),
        "campaign_name": _s(a.get("CampaignName")),
        "action_tracker_id": _s(a.get("ActionTrackerId")),
        "action_tracker_name": _s(a.get("ActionTrackerName")),
        "state": _s(a.get("State")),
        "payout": _dec(a.get("Payout")),
        "amount": _dec(a.get("Amount")),
        "currency": _s(a.get("Currency")),
        "event_date": _dt(a.get("EventDate")),
        "locking_date": _dt(a.get("LockingDate")),
        "creation_date": _dt(a.get("CreationDate")),
        "cleared_date": _dt(a.get("ClearedDate")),
        "referring_date": _dt(a.get("ReferringDate")),
        "referring_type": _s(a.get("ReferringType")),
        "referring_domain": _s(a.get("ReferringDomain")),
        "sub_id1": _s(a.get("SubId1")),
        "sub_id2": _s(a.get("SubId2")),
        "sub_id3": _s(a.get("SubId3")),
        "shared_id": _s(a.get("SharedId")),
        "oid": _s(a.get("Oid")),
        "delta_payout": _dec(a.get("DeltaPayout")),
        "delta_amount": _dec(a.get("DeltaAmount")),
        "intended_payout": _dec(a.get("IntendedPayout")),
        "intended_amount": _dec(a.get("IntendedAmount")),
        "ad_id": _s(a.get("AdId")),
        "promo_code": _s(a.get("PromoCode")),
        "event_code": _s(a.get("EventCode")),
        "customer_city": _s(a.get("CustomerCity")),
        "customer_region": _s(a.get("CustomerRegion")),
        "customer_country": _s(a.get("CustomerCountry")),
        "customer_area": _s(a.get("CustomerArea")),
        "raw": a,
    }


async def _sync_actions_range(client: ImpactReportsClient, start: datetime, end: datetime) -> int:
    """Actions actualizadas entre start y end (por fecha de última actualización), en ventanas de 45 días."""
    total = 0
    cursor = start
    while cursor < end:
        window_end = min(cursor + ACTIONS_WINDOW, end)
        rows = [_action_row(a) async for a in client.paginate(
            "/Actions", {"StartDate": _iso(cursor), "EndDate": _iso(window_end), "PageSize": 1000})]
        await _upsert(ImpactAction, rows, ["id"])
        total += len(rows)
        cursor = window_end
    return total


async def sync_actions() -> None:
    """Incremental; si nunca se hizo la carga inicial, la hace (3 años hacia atrás)."""
    async with _lock("actions"):
        now = datetime.now(timezone.utc)
        if await _last_ok("actions_backfill") is None:
            async with _run("actions_backfill") as run, ImpactReportsClient() as client:
                start = now - timedelta(days=ACTIONS_HISTORY_DAYS) + timedelta(hours=1)
                run.rows = await _sync_actions_range(client, start, now)
                run.detail = {"from": _iso(start), "to": _iso(now)}
            return
        last = await _last_ok("actions")
        start = (last.started_at if last else now - timedelta(days=7)) - timedelta(days=2)
        async with _run("actions") as run, ImpactReportsClient() as client:
            run.rows = await _sync_actions_range(client, start, now)
            run.detail = {"from": _iso(start), "to": _iso(now)}


# ---------------------------------------------------------------------------
# Reporte de rendimiento diario (Performance by Day)
# ---------------------------------------------------------------------------

def _report_day(rec: dict) -> date | None:
    for key in ("sort_date", "date_display", "Date", "date"):
        v = _s(rec.get(key))
        if not v:
            continue
        for fmt, text in (("%b %d, %Y", v), ("%Y-%m-%d", v[:10])):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        d = _dt(v)
        if d:
            return d.astimezone(TZ).date()
    return None


def _performance_row(day: date, rec: dict) -> dict:
    # Columnas de "Performance by Day": Clicks = clicks ÚNICOS (el reporte no trae el total);
    # Total_Cost = comisión (acciones + bonus); Sale_zzzAmount = monto de venta. Moneda: USD.
    return {
        "day": day,
        "clicks": None,
        "unique_clicks": _int(rec.get("Clicks")),
        "actions": _int(rec.get("Actions")),
        "payout": _dec(rec.get("Total_Cost")),
        "sale_amount": _dec(rec.get("Sale_zzzAmount")),
        "currency": "USD",
        "raw": [rec],
    }


async def _sync_performance_range(client: ImpactReportsClient, start: date, end: date) -> int:
    report = settings.impact_performance_report_id
    params = {"START_DATE": start.isoformat(), "END_DATE": end.isoformat()}
    try:
        records = [r async for r in client.paginate(f"/Reports/{report}", params)]
    except ImpactError as e:
        if e.status != 408:
            raise
        _, records = await client.run_job(f"/ReportExport/{report}", params)
    rows = []
    for rec in records:
        day = _report_day(rec)
        if day:
            rows.append(_performance_row(day, rec))
    await _upsert(ImpactDailyPerformance, rows, ["day"])
    return len(rows)


async def sync_performance() -> None:
    """Últimos 3 días; la primera vez, 36 meses en tramos mensuales."""
    async with _lock("performance"):
        today = datetime.now(TZ).date()
        if await _last_ok("performance_backfill") is None:
            async with _run("performance_backfill") as run, ImpactReportsClient() as client:
                start = (today.replace(day=1) - timedelta(days=31 * PERFORMANCE_HISTORY_MONTHS)).replace(day=1)
                cursor = start
                while cursor <= today:
                    nxt = (cursor + timedelta(days=32)).replace(day=1)
                    run.rows += await _sync_performance_range(client, cursor, min(nxt - timedelta(days=1), today))
                    cursor = nxt
                run.detail = {"from": start.isoformat(), "to": today.isoformat()}
            return
        async with _run("performance") as run, ImpactReportsClient() as client:
            start = today - timedelta(days=3)
            run.rows = await _sync_performance_range(client, start, today)
            run.detail = {"from": start.isoformat(), "to": today.isoformat()}


# ---------------------------------------------------------------------------
# Clicks individuales (ClickExport — máx. 10 por día en Impact)
# ---------------------------------------------------------------------------

def _click_row(day: date, c: dict) -> dict:
    return {
        "export_date": day,
        "external_id": _s(c.get("Id")),
        "event_date": _dt(c.get("EventDate")),
        "program_id": _s(c.get("ProgramId")),
        "program_name": _s(c.get("ProgramName")),
        "ad_id": _s(c.get("AdId")),
        "ad_name": _s(c.get("AdName")),
        "ad_type": _s(c.get("AdType")),
        "product_sku": _s(c.get("ProductSku")),
        "media_id": _s(c.get("MediaId")),
        "shared_id": _s(c.get("SharedId")),
        "sub_id1": _s(c.get("SubId1")),
        "sub_id2": _s(c.get("SubId2")),
        "sub_id3": _s(c.get("SubId3")),
        "customer_country": _s(c.get("CustomerCountry")),
        "customer_region": _s(c.get("CustomerRegion")),
        "customer_city": _s(c.get("CustomerCity")),
        "customer_area": _s(c.get("CustomerArea")),
        "ip_hash": _s(c.get("IpAddress")),
        "device_type": _s(c.get("DeviceType")),
        "device_family": _s(c.get("DeviceFamily")),
        "browser": _s(c.get("Browser")),
        "os": _s(c.get("Os")),
        "referring_url": _s(c.get("ReferringUrl")),
        "referring_domain": _s(c.get("ReferringDomain")),
        "landing_page_url": _s(c.get("LandingPageUrl")),
        "traffic_source": _s(c.get("TrafficSource")),
        "traffic_category": _s(c.get("TrafficCategory")),
        "unique_click": _bool(c.get("UniqueClick")),
        "payout": _dec(c.get("Payout")),
        "deal_name": _s(c.get("DealName")),
        "deal_type": _s(c.get("DealType")),
        "raw": c,
    }


async def click_exports_used_last_24h() -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    async with AsyncSessionLocal() as db:
        return await db.scalar(
            select(func.count()).select_from(ImpactSyncRun)
            .where(ImpactSyncRun.kind == "click_export", ImpactSyncRun.started_at >= since)
        ) or 0


async def export_clicks_day(day: date) -> None:
    """Un ClickExport = un día. Reemplaza los clicks guardados de ese día."""
    async with AsyncSessionLocal() as db:
        state = await db.get(ImpactClickExport, day)
        if state is None:
            state = ImpactClickExport(day=day, status="running", attempts=0)
            db.add(state)
        state.status, state.error = "running", None
        state.attempts += 1
        state.requested_at = datetime.now(timezone.utc)
        await db.commit()

    job_id, rows, error = None, [], None
    async with _run("click_export") as run, ImpactReportsClient() as client:
        run.detail = {"day": day.isoformat()}
        try:
            job_id, records = await client.run_job("/ClickExport", {"Date": day.isoformat()})
            rows = [_click_row(day, c) for c in records]
            async with AsyncSessionLocal() as db:
                await db.execute(delete(ImpactClick).where(ImpactClick.export_date == day))
                for i in range(0, len(rows), BATCH):
                    await db.execute(pg_insert(ImpactClick).values(rows[i:i + BATCH]))
                await db.commit()
            run.rows = len(rows)
            run.detail["job_id"] = job_id
        except Exception as e:
            error = str(e)[:2000]
            raise
        finally:
            async with AsyncSessionLocal() as db:
                state = await db.get(ImpactClickExport, day)
                state.job_id = job_id
                state.status = "failed" if error else "completed"
                state.error = error
                state.rows = None if error else len(rows)
                state.completed_at = None if error else datetime.now(timezone.utc)
                await db.commit()


async def sync_clicks() -> None:
    """Ayer primero, después los días faltantes (o fallidos) de los últimos 30, sin pasar el cupo."""
    async with _lock("clicks"):
        today = datetime.now(TZ).date()
        wanted = [today - timedelta(days=n) for n in range(1, CLICKS_HISTORY_DAYS + 1)]
        async with AsyncSessionLocal() as db:
            done = set((await db.execute(
                select(ImpactClickExport.day).where(ImpactClickExport.status == "completed")
            )).scalars())
        pending = [d for d in wanted if d not in done]
        budget = settings.impact_click_export_daily_budget - await click_exports_used_last_24h()
        for day in pending[:max(budget, 0)]:
            try:
                await export_clicks_day(day)
            except Exception:
                pass  # ya quedó registrado en impact_click_exports / impact_sync_runs


# ---------------------------------------------------------------------------
# Facturas y programas
# ---------------------------------------------------------------------------

def _invoice_status(items: list[dict]) -> str | None:
    states = {_s(i.get("Status")) for i in items} - {None}
    if not states:
        return None
    if "OVERDUE" in states:
        return "OVERDUE"
    if states == {"PAID"}:
        return "PAID"
    return "PENDING"


def _invoice_row(inv: dict) -> dict:
    items = inv.get("LineItems") or []
    due = [d for d in (_dt(i.get("DueDate")) for i in items) if d]
    paid = [d for d in (_dt(i.get("PaidDate")) for i in items) if d]
    return {
        "id": inv["Id"],
        "created_date": _dt(inv.get("CreatedDate")),
        "currency": _s(inv.get("Currency")),
        "total_amount": _dec(inv.get("TotalAmount")),
        "total_vat_amount": _dec(inv.get("TotalVatAmount")),
        "status": _invoice_status(items),
        "due_date": max(due) if due else None,
        "paid_date": max(paid) if paid else None,
        "line_items": items,
        "detailed_line_items": inv.get("DetailedLineItems") or [],
        "raw": inv,
    }


async def sync_invoices() -> None:
    async with _lock("invoices"), _run("invoices") as run, ImpactReportsClient() as client:
        rows = [_invoice_row(i) async for i in client.paginate("/Invoices", {"PageSize": 1000})]
        await _upsert(ImpactInvoice, rows, ["id"])
        run.rows = len(rows)


async def sync_programs() -> None:
    """
    Este token no tiene acceso a /Programs ni /Campaigns (404/403), así que los
    programas salen de lo ya sincronizado: CampaignId/Name de Actions y ProgramId/Name de Clicks.
    """
    async with _lock("programs"), _run("programs") as run:
        async with AsyncSessionLocal() as db:
            pairs = set((await db.execute(
                select(distinct(ImpactAction.campaign_id), ImpactAction.campaign_name)
                .where(ImpactAction.campaign_id.isnot(None))
            )).all())
            pairs |= set((await db.execute(
                select(distinct(ImpactClick.program_id), ImpactClick.program_name)
                .where(ImpactClick.program_id.isnot(None))
            )).all())
        rows = [{"id": pid, "name": name, "raw": {"Id": pid, "Name": name}} for pid, name in pairs]
        await _upsert(ImpactProgram, rows, ["id"])
        run.rows = len(rows)


# ---------------------------------------------------------------------------
# Entradas de los jobs
# ---------------------------------------------------------------------------

async def _safe(fn) -> None:
    try:
        await fn()
    except Exception:
        logger.exception("impact_sync: %s falló", fn.__name__)


async def sync_every_6h() -> None:
    if not is_configured():
        logger.info("impact_sync: sin credenciales de reportes, se omite")
        return
    await _safe(sync_actions)
    await _safe(sync_performance)


async def sync_daily() -> None:
    if not is_configured():
        logger.info("impact_sync: sin credenciales de reportes, se omite")
        return
    await _safe(sync_invoices)
    await _safe(sync_clicks)
    await _safe(sync_programs)


async def bootstrap() -> None:
    """Al arrancar: corre lo que falte (carga inicial incluida) sin esperar al próximo horario."""
    await sync_every_6h()
    await sync_daily()


async def refresh() -> None:
    """Botón del dashboard: Actions + reporte diario. Nunca ClickExport."""
    if not is_configured():
        return
    await _safe(sync_actions)
    await _safe(sync_performance)
