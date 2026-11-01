"""
Sincronización AdMob → Postgres para el dashboard de AdMob.

Jobs (registrados en main.py):
  - sync_hourly:  reporte principal (R4) de hoy y ayer (día de Los Ángeles) + foto de "hoy" (R11).
  - sync_daily:   08:00 Córdoba. Cuenta, apps y bloques (R1–R3) y los 7 reportes (R4–R10)
                  de los últimos 7 días, que AdMob sigue ajustando.
  - bootstrap:    al arrancar; si nunca se hizo la carga inicial (24 meses), la hace.
  - refresh:      botón del dashboard; corrida horaria, como mucho una vez por minuto.

Toda la cuenta se sincroniza (todas las apps); el dashboard filtra por app.
Cada rango se REEMPLAZA (delete + insert): así desaparecen filas que AdMob corrigió.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    AdmobAccount, AdmobAdUnit, AdmobApp, AdmobDaily, AdmobDailyAdType, AdmobDailyServing,
    AdmobDailyVersion, AdmobIntradaySnapshot, AdmobMediationDaily, AdmobSyncRun,
)
from app.services.admob_client import (
    REPORT_TZ, AdmobAuthError, AdmobClient, ReportTooLarge, account_path, is_configured,
)

logger = logging.getLogger(__name__)

LA = ZoneInfo(REPORT_TZ)
BASE_METRICS = ["AD_REQUESTS", "MATCHED_REQUESTS", "IMPRESSIONS", "CLICKS", "ESTIMATED_EARNINGS"]
BATCH = 1000
_lock = asyncio.Lock()


def la_today() -> date:
    return datetime.now(LA).date()


# ---------------------------------------------------------------------------
# Definición de reportes (R4–R10). APP se agrega en todos para poder filtrar por app.
# ---------------------------------------------------------------------------

def _base(m: dict) -> dict:
    return {
        "requests": int(m.get("AD_REQUESTS", 0)),
        "matched": int(m.get("MATCHED_REQUESTS", 0)),
        "impressions": int(m.get("IMPRESSIONS", 0)),
        "clicks": int(m.get("CLICKS", 0)),
        "earnings": float(m.get("ESTIMATED_EARNINGS", 0.0)),
    }


def _day(v: str) -> date:
    return date(int(v[:4]), int(v[4:6]), int(v[6:8]))


def _main_row(r: dict) -> dict:
    d, l = r["dims"], r["labels"]
    return {"date": _day(d["DATE"]), "app_id": d.get("APP") or "", "ad_unit_id": d.get("AD_UNIT") or "",
            "format": d.get("FORMAT") or "", "platform": d.get("PLATFORM") or "", "country": d.get("COUNTRY") or "",
            "app_name": l.get("APP"), "ad_unit_name": l.get("AD_UNIT"), **_base(r["metrics"])}


def _version_row(dim: str, kind: str):
    def f(r: dict) -> dict:
        d = r["dims"]
        return {"date": _day(d["DATE"]), "dimension_type": kind, "app_id": d.get("APP") or "",
                "platform": d.get("PLATFORM") or "", "version": (d.get(dim) or "")[:64], **_base(r["metrics"])}
    return f


def _serving_row(r: dict) -> dict:
    d = r["dims"]
    return {"date": _day(d["DATE"]), "app_id": d.get("APP") or "", "platform": d.get("PLATFORM") or "",
            "format": d.get("FORMAT") or "", "serving_restriction": (d.get("SERVING_RESTRICTION") or "")[:64],
            **_base(r["metrics"])}


def _ad_type_row(r: dict) -> dict:
    d, m = r["dims"], r["metrics"]
    return {"date": _day(d["DATE"]), "app_id": d.get("APP") or "", "platform": d.get("PLATFORM") or "",
            "format": d.get("FORMAT") or "", "ad_type": d.get("AD_TYPE") or "",
            "impressions": int(m.get("IMPRESSIONS", 0)), "clicks": int(m.get("CLICKS", 0)),
            "earnings": float(m.get("ESTIMATED_EARNINGS", 0.0))}


def _mediation_row(r: dict) -> dict:
    d, l, m = r["dims"], r["labels"], r["metrics"]
    return {"date": _day(d["DATE"]), "app_id": d.get("APP") or "", "ad_source_id": d.get("AD_SOURCE") or "",
            "mediation_group_id": (d.get("MEDIATION_GROUP") or "")[:96], "format": d.get("FORMAT") or "",
            "platform": d.get("PLATFORM") or "", "ad_source_name": l.get("AD_SOURCE"),
            "mediation_group_name": l.get("MEDIATION_GROUP"), **_base(m),
            "observed_ecpm": m.get("OBSERVED_ECPM"), "ad_load_latency": m.get("AD_LOAD_LATENCY")}


# nombre → (endpoint, dimensiones, métricas, modelo, mapper, filtro extra para el delete, claves)
REPORTS = {
    "main": ("networkReport", ["DATE", "APP", "AD_UNIT", "FORMAT", "PLATFORM", "COUNTRY"], BASE_METRICS,
             AdmobDaily, _main_row, None),
    "app_version": ("networkReport", ["DATE", "APP", "PLATFORM", "APP_VERSION_NAME"], BASE_METRICS,
                    AdmobDailyVersion, _version_row("APP_VERSION_NAME", "app_version"), "app_version"),
    "os_version": ("networkReport", ["DATE", "APP", "PLATFORM", "MOBILE_OS_VERSION"], BASE_METRICS,
                   AdmobDailyVersion, _version_row("MOBILE_OS_VERSION", "os_version"), "os_version"),
    "sdk_version": ("networkReport", ["DATE", "APP", "PLATFORM", "GMA_SDK_VERSION"], BASE_METRICS,
                    AdmobDailyVersion, _version_row("GMA_SDK_VERSION", "sdk_version"), "sdk_version"),
    "serving": ("networkReport", ["DATE", "APP", "PLATFORM", "FORMAT", "SERVING_RESTRICTION"], BASE_METRICS,
                AdmobDailyServing, _serving_row, None),
    "ad_type": ("networkReport", ["DATE", "APP", "PLATFORM", "FORMAT", "AD_TYPE"],
                ["IMPRESSIONS", "CLICKS", "ESTIMATED_EARNINGS"], AdmobDailyAdType, _ad_type_row, None),
    "mediation": ("mediationReport", ["DATE", "APP", "AD_SOURCE", "MEDIATION_GROUP", "FORMAT", "PLATFORM"],
                  BASE_METRICS + ["OBSERVED_ECPM", "AD_LOAD_LATENCY"], AdmobMediationDaily, _mediation_row, None),
}

SUM_FIELDS = ("requests", "matched", "impressions", "clicks", "earnings")


def _merge(rows: list[dict], model) -> list[dict]:
    """Suma filas con la misma clave primaria (p. ej. país vacío repetido)."""
    keys = [c.name for c in model.__table__.primary_key.columns]
    acc: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[c] for c in keys)
        if k not in acc:
            acc[k] = dict(r)
            continue
        e = acc[k]
        for f in SUM_FIELDS:
            if f in r:
                e[f] = e.get(f, 0) + r[f]
        if "observed_ecpm" in r and r.get("observed_ecpm") is not None:
            e["observed_ecpm"] = r["observed_ecpm"]
    return list(acc.values())


async def _currency() -> str:
    async with AsyncSessionLocal() as db:
        cur = await db.scalar(select(AdmobAccount.currency).limit(1))
    return cur or "USD"


async def _sync_report(client: AdmobClient, name: str, start: date, end: date, currency: str) -> int:
    endpoint, dims, mets, model, mapper, dim_type = REPORTS[name]
    try:
        _, raw = await client.report(endpoint, start, end, dims, mets, currency)
    except ReportTooLarge:
        if start >= end:
            raise
        mid = start + (end - start) / 2
        return (await _sync_report(client, name, start, mid, currency)
                + await _sync_report(client, name, mid + timedelta(days=1), end, currency))
    rows = _merge([{**mapper(r), "currency": currency} for r in raw], model)
    async with AsyncSessionLocal() as db:
        cond = [model.date >= start, model.date <= end]
        if dim_type:
            cond.append(model.dimension_type == dim_type)
        await db.execute(delete(model).where(and_(*cond)))
        for i in range(0, len(rows), BATCH):
            await db.execute(pg_insert(model).values(rows[i:i + BATCH]))
        await db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Registro de corridas
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, run_id: int):
        self.id = run_id
        self.rows = 0
        self.detail: dict = {}


@asynccontextmanager
async def _run(kind: str, start: date | None = None, end: date | None = None):
    async with AsyncSessionLocal() as db:
        row = AdmobSyncRun(kind=kind, status="running", range_from=start, range_to=end)
        db.add(row)
        await db.commit()
        run = _Run(row.id)
    status, error = "ok", None
    try:
        yield run
    except AdmobAuthError as e:
        status, error = "auth_error", str(e)
        logger.error("admob_sync: %s — %s", kind, e)
    except Exception as e:
        status, error = "error", str(e)[:2000]
        logger.exception("admob_sync: %s falló", kind)
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(AdmobSyncRun, run.id)
            row.status, row.error, row.rows = status, error, run.rows
            row.detail = run.detail or None
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()


async def _last(kind: str, ok_only: bool = True) -> AdmobSyncRun | None:
    async with AsyncSessionLocal() as db:
        q = select(AdmobSyncRun).where(AdmobSyncRun.kind == kind)
        if ok_only:
            q = q.where(AdmobSyncRun.status == "ok")
        return (await db.execute(q.order_by(AdmobSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()


async def _auth_broken() -> bool:
    """Si la última corrida terminó en invalid_grant, no se insiste hasta reconectar."""
    async with AsyncSessionLocal() as db:
        last = (await db.execute(select(AdmobSyncRun).where(AdmobSyncRun.status != "running")
                                 .order_by(AdmobSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
    return bool(last and last.status == "auth_error")


# ---------------------------------------------------------------------------
# Inventario (R1–R3)
# ---------------------------------------------------------------------------

def _app_row(a: dict) -> dict:
    linked, manual = a.get("linkedAppInfo") or {}, a.get("manualAppInfo") or {}
    return {"app_id": a["appId"], "platform": a.get("platform"),
            "name": linked.get("displayName") or manual.get("displayName"),
            "app_store_id": linked.get("appStoreId"), "approval_state": a.get("appApprovalState"), "raw": a}


def _unit_row(u: dict) -> dict:
    return {"ad_unit_id": u["adUnitId"], "app_id": u.get("appId"), "name": u.get("displayName"),
            "format": u.get("adFormat"), "ad_types": u.get("adTypes") or [], "raw": u}


async def _upsert(model, rows: list[dict], key: str) -> None:
    if not rows:
        return
    async with AsyncSessionLocal() as db:
        stmt = pg_insert(model).values(rows)
        cols = {c: getattr(stmt.excluded, c) for c in rows[0] if c != key}
        cols["synced_at"] = func.now()
        await db.execute(stmt.on_conflict_do_update(index_elements=[key], set_=cols))
        await db.commit()


async def _sync_inventory(client: AdmobClient) -> dict:
    accounts = (await client.get("accounts")).get("account", [])
    acc = next((a for a in accounts if a.get("publisherId") == settings.admob_publisher_id), None)
    if acc:
        await _upsert(AdmobAccount, [{"publisher_id": acc["publisherId"], "reporting_time_zone": acc.get("reportingTimeZone"),
                                      "currency": acc.get("currencyCode"), "raw": acc}], "publisher_id")
    apps = await client.list_all(f"{account_path()}/apps", "apps")
    units = await client.list_all(f"{account_path()}/adUnits", "adUnits")
    await _upsert(AdmobApp, [_app_row(a) for a in apps], "app_id")
    await _upsert(AdmobAdUnit, [_unit_row(u) for u in units], "ad_unit_id")
    return {"apps": len(apps), "ad_units": len(units)}


# ---------------------------------------------------------------------------
# Corridas
# ---------------------------------------------------------------------------

async def _snapshot(report_date: date) -> None:
    """R11: totales de hoy por app, para comparar 'hoy vs ayer a la misma hora'."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AdmobDaily.app_id, AdmobDaily.platform, func.sum(AdmobDaily.earnings),
                   func.sum(AdmobDaily.impressions), func.sum(AdmobDaily.clicks))
            .where(AdmobDaily.date == report_date).group_by(AdmobDaily.app_id, AdmobDaily.platform)
        )).all()
        now = datetime.now(timezone.utc)
        for app_id, platform, e, i, c in rows:
            db.add(AdmobIntradaySnapshot(taken_at=now, report_date=report_date, app_id=app_id, platform=platform,
                                         earnings=e or 0, impressions=i or 0, clicks=c or 0))
        await db.commit()


async def _hourly(kind: str) -> None:
    today = la_today()
    start = today - timedelta(days=1)
    async with _run(kind, start, today) as run, AdmobClient() as client:
        run.rows = await _sync_report(client, "main", start, today, await _currency())
        await _snapshot(today)


async def _daily() -> None:
    today = la_today()
    start = today - timedelta(days=6)
    async with _run("daily", start, today) as run, AdmobClient() as client:
        run.detail = await _sync_inventory(client)
        cur = await _currency()
        for name in REPORTS:
            n = await _sync_report(client, name, start, today, cur)
            run.detail[name] = n
            run.rows += n
            await asyncio.sleep(1)


async def _initial() -> None:
    today = la_today()
    first = (today.replace(day=1) - timedelta(days=31 * (settings.admob_history_months - 1))).replace(day=1)
    async with _run("initial", first, today) as run, AdmobClient() as client:
        run.detail = await _sync_inventory(client)
        cur = await _currency()
        month = first
        while month <= today:
            nxt = (month + timedelta(days=32)).replace(day=1)
            end = min(nxt - timedelta(days=1), today)
            for name in REPORTS:
                n = await _sync_report(client, name, month, end, cur)
                run.detail[name] = run.detail.get(name, 0) + n
                run.rows += n
                await asyncio.sleep(1)
            month = nxt
        await _snapshot(today)


async def _guarded(fn, *args) -> None:
    if not is_configured():
        logger.info("admob_sync: sin credenciales, se omite")
        return
    if await _auth_broken():
        logger.warning("admob_sync: la conexión con AdMob está caída (invalid_grant); se omite hasta reconectar")
        return
    async with _lock:
        await fn(*args)


async def sync_hourly() -> None:
    await _guarded(_hourly, "hourly")


async def sync_daily() -> None:
    await _guarded(_daily)


async def bootstrap() -> None:
    """Al arrancar: carga inicial si falta; si no, una corrida horaria para tener 'hoy' fresco."""
    if await _last("initial") is None:
        await _guarded(_initial)
    else:
        await _guarded(_hourly, "hourly")


async def refresh() -> bool:
    """Botón del dashboard. Devuelve False si se pidió hace menos de un minuto."""
    last = await _last("refresh", ok_only=False)
    if last and datetime.now(timezone.utc) - last.started_at < timedelta(minutes=1):
        return False
    if not is_configured():
        return False
    # El botón también sirve para reintentar después de reconectar (no se bloquea por invalid_grant).
    async with _lock:
        await _hourly("refresh")
    return True
