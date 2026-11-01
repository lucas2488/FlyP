"""
Consultas del reporte de afiliados (Impact) sobre las tablas sincronizadas.

Reglas:
  - Días y horas en America/Argentina/Cordoba.
  - Clicks individuales (impact_clicks) solo hasta el último día exportado (ayer como mucho).
    Los clicks únicos por día salen del reporte diario (incluye hoy) cuando no hay filtros
    de click; con filtros salen de impact_clicks.
  - Conversiones = acciones no revertidas (igual que el reporte de Impact).
  - Montos siempre por moneda: [{"currency": "USD", "amount": 1.23}], nunca se suman monedas.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, case, cast, func, or_, select, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ImpactAction, ImpactClick, ImpactClickExport, ImpactDailyPerformance,
    ImpactInvoice, ImpactProgram, ImpactSyncRun,
)

TZ_NAME = "America/Argentina/Cordoba"
TZ = ZoneInfo(TZ_NAME)
STATES = ("PENDING", "APPROVED", "REVERSED")


# ---------------------------------------------------------------------------
# Parámetros comunes
# ---------------------------------------------------------------------------

@dataclass
class Filters:
    program: list[str] = field(default_factory=list)
    country: list[str] = field(default_factory=list)
    region: list[str] = field(default_factory=list)
    device: list[str] = field(default_factory=list)
    os: list[str] = field(default_factory=list)
    sub_id: list[str] = field(default_factory=list)
    state: list[str] = field(default_factory=list)

    @property
    def has_click_filters(self) -> bool:
        return bool(self.program or self.country or self.region or self.device or self.os or self.sub_id)

    def ignored_for_conversions(self) -> list[str]:
        return [n for n, v in (("device", self.device), ("os", self.os)) if v]

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class Period:
    start: date          # inclusive
    end: date            # inclusive

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, time.min, TZ)

    @property
    def end_utc(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, TZ)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


def split_list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def today() -> date:
    return datetime.now(TZ).date()


def make_period(start: date | None, end: date | None) -> Period:
    end = end or today()
    start = start or end - timedelta(days=29)
    if start > end:
        start, end = end, start
    return Period(start, end)


def compare_period(p: Period, mode: str) -> Period | None:
    if mode == "previous":
        return Period(p.start - timedelta(days=p.days), p.start - timedelta(days=1))
    if mode == "last_year":
        def back(d: date) -> date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29/02
                return d.replace(year=d.year - 1, day=28)
        return Period(back(p.start), back(p.end))
    return None


# ---------------------------------------------------------------------------
# Helpers SQL
# ---------------------------------------------------------------------------

def local(col):
    return func.timezone(TZ_NAME, col)


def local_day(col):
    return cast(local(col), Date)


def bucket(col, group_by: str):
    return cast(func.date_trunc(group_by, local(col)), Date)


def bucket_start(d: date, group_by: str) -> date:
    if group_by == "week":
        return d - timedelta(days=d.weekday())
    if group_by == "month":
        return d.replace(day=1)
    return d


def bucket_list(p: Period, group_by: str) -> list[date]:
    out, d = [], bucket_start(p.start, group_by)
    while d <= p.end:
        out.append(d)
        if group_by == "month":
            d = (d + timedelta(days=32)).replace(day=1)
        else:
            d += timedelta(days=7 if group_by == "week" else 1)
    return out


def click_where(p: Period, f: Filters) -> list:
    c = ImpactClick
    conds = [c.event_date >= p.start_utc, c.event_date < p.end_utc]
    if f.program:
        conds.append(c.program_id.in_(f.program))
    if f.country:
        conds.append(c.customer_country.in_(f.country))
    if f.region:
        conds.append(c.customer_region.in_(f.region))
    if f.device:
        conds.append(func.upper(c.device_type).in_([v.upper() for v in f.device]))
    if f.os:
        conds.append(func.lower(c.os).in_([v.lower() for v in f.os]))
    if f.sub_id:
        conds.append(or_(c.sub_id1.in_(f.sub_id), c.sub_id2.in_(f.sub_id),
                         c.sub_id3.in_(f.sub_id), c.shared_id.in_(f.sub_id)))
    return conds


def action_where(p: Period, f: Filters, date_col=None, with_state: bool = True) -> list:
    a = ImpactAction
    col = date_col if date_col is not None else a.event_date
    conds = [col >= p.start_utc, col < p.end_utc]
    if f.program:
        conds.append(a.campaign_id.in_(f.program))
    if f.country:
        conds.append(a.customer_country.in_(f.country))
    if f.region:
        conds.append(a.customer_region.in_(f.region))
    if f.sub_id:
        conds.append(or_(a.sub_id1.in_(f.sub_id), a.sub_id2.in_(f.sub_id),
                         a.sub_id3.in_(f.sub_id), a.shared_id.in_(f.sub_id)))
    if with_state and f.state:
        conds.append(a.state.in_([s.upper() for s in f.state]))
    return conds


NOT_REVERSED = func.coalesce(ImpactAction.state, "") != "REVERSED"


def num(v) -> float:
    return float(v) if v is not None else 0.0


def money(rows) -> list[dict]:
    """[(currency, amount)] → [{"currency", "amount"}], sin mezclar monedas."""
    return [{"currency": c or "USD", "amount": round(num(v), 4)} for c, v in rows if v is not None]


def pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 2)


def ratio(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return a / b


# ---------------------------------------------------------------------------
# Cobertura de clicks
# ---------------------------------------------------------------------------

async def clicks_available_until(db: AsyncSession) -> date | None:
    return await db.scalar(
        select(func.max(ImpactClickExport.day)).where(ImpactClickExport.status == "completed"))


async def clicks_coverage(db: AsyncSession, p: Period) -> dict:
    """Cuántos días del período tienen clicks individuales exportados (el resto, solo totales)."""
    days = (await db.execute(select(ImpactClickExport.day).where(
        ImpactClickExport.status == "completed",
        ImpactClickExport.day >= p.start, ImpactClickExport.day <= p.end))).scalars().all()
    return {"days_with_clicks": len(days), "days_in_period": p.days,
            "complete": len(days) >= p.days or (len(days) == p.days - 1 and p.end == today())}


# ---------------------------------------------------------------------------
# Totales de un período (para KPIs y comparación)
# ---------------------------------------------------------------------------

async def _totals(db: AsyncSession, p: Period, f: Filters) -> dict:
    c, a = ImpactClick, ImpactAction
    clicks, uniq_from_clicks = (await db.execute(
        select(func.count(), func.count().filter(c.unique_click.is_(True))).where(*click_where(p, f))
    )).one()

    if f.has_click_filters:
        unique = uniq_from_clicks
    else:
        unique = await db.scalar(select(func.sum(ImpactDailyPerformance.unique_clicks)).where(
            ImpactDailyPerformance.day >= p.start, ImpactDailyPerformance.day <= p.end))
        unique = int(unique) if unique is not None else uniq_from_clicks

    aw = action_where(p, f)
    conv = await db.scalar(select(func.count()).where(*aw, NOT_REVERSED)) or 0
    approved = await db.scalar(select(func.count()).where(*aw, a.state == "APPROVED")) or 0
    reversed_ = await db.scalar(select(func.count()).where(*aw, a.state == "REVERSED")) or 0

    by_cur = (await db.execute(
        select(a.currency,
               func.sum(a.payout).filter(NOT_REVERSED),
               func.sum(a.payout).filter(a.state == "PENDING"),
               func.sum(a.payout).filter(a.state == "APPROVED"),
               func.avg(a.amount).filter(NOT_REVERSED),
               func.sum(a.amount).filter(NOT_REVERSED))
        .where(*aw).group_by(a.currency)
    )).all()
    return {
        "clicks": clicks,
        "unique_clicks": unique,
        "conversions": conv,
        "approved": approved,
        "reversed": reversed_,
        "commission": {r[0] or "USD": num(r[1]) for r in by_cur},
        "commission_pending": {r[0] or "USD": num(r[2]) for r in by_cur},
        "commission_approved": {r[0] or "USD": num(r[3]) for r in by_cur},
        "avg_ticket": {r[0] or "USD": num(r[4]) for r in by_cur},
        "sales": {r[0] or "USD": num(r[5]) for r in by_cur},
    }


def _kpi(cur, prev) -> dict:
    return {"value": cur, "previous": prev, "change_pct": pct_change(cur, prev)}


def _money_kpi(cur: dict, prev: dict | None) -> list[dict]:
    currencies = sorted(set(cur) | set(prev or {}))
    return [{
        "currency": cc,
        "value": round(cur.get(cc, 0.0), 4),
        "previous": round(prev.get(cc, 0.0), 4) if prev is not None else None,
        "change_pct": pct_change(cur.get(cc, 0.0), prev.get(cc, 0.0)) if prev is not None else None,
    } for cc in currencies]


# Tasa de conversión y EPC sobre clicks ÚNICOS (como Impact): los únicos salen del reporte
# diario y cubren todo el período; el total de clicks solo cubre los días ya exportados.
def _epc(t: dict) -> dict:
    base = t["unique_clicks"]
    return {cc: v / base for cc, v in t["commission"].items()} if base else {}


def kpis(cur: dict, prev: dict | None) -> dict:
    def rate(t):
        base = t["unique_clicks"]
        return round(t["conversions"] / base, 4) if base else None
    return {
        "clicks": _kpi(cur["clicks"], prev and prev["clicks"]),
        "unique_clicks": _kpi(cur["unique_clicks"], prev and prev["unique_clicks"]),
        "conversions": _kpi(cur["conversions"], prev and prev["conversions"]),
        "conversion_rate": _kpi(rate(cur), prev and rate(prev)),
        "commission": _money_kpi(cur["commission"], prev and prev["commission"]),
        "epc": _money_kpi(_epc(cur), prev and _epc(prev)),
        "avg_ticket": _money_kpi(cur["avg_ticket"], prev and prev["avg_ticket"]),
        "sales": _money_kpi(cur["sales"], prev and prev["sales"]),
        "commission_pending": _money_kpi(cur["commission_pending"], prev and prev["commission_pending"]),
        "commission_approved": _money_kpi(cur["commission_approved"], prev and prev["commission_approved"]),
    }


# ---------------------------------------------------------------------------
# Series temporales
# ---------------------------------------------------------------------------

async def clicks_series(db: AsyncSession, p: Period, f: Filters, group_by: str) -> list[dict]:
    c = ImpactClick
    b = bucket(c.event_date, group_by)
    rows = {r[0]: r for r in (await db.execute(
        select(b, func.count(), func.count().filter(c.unique_click.is_(True)))
        .where(*click_where(p, f)).group_by(b)
    )).all()}
    perf = {}
    if not f.has_click_filters:
        pb = cast(func.date_trunc(group_by, ImpactDailyPerformance.day), Date)
        perf = {r[0]: r[1] for r in (await db.execute(
            select(pb, func.sum(ImpactDailyPerformance.unique_clicks))
            .where(ImpactDailyPerformance.day >= p.start, ImpactDailyPerformance.day <= p.end)
            .group_by(pb)
        )).all()}
    out = []
    for d in bucket_list(p, group_by):
        r = rows.get(d)
        unique = perf.get(d)
        out.append({
            "date": d.isoformat(),
            "clicks": r[1] if r else 0,
            "unique_clicks": int(unique) if unique is not None else (r[2] if r else 0),
        })
    return out


async def conversions_series(db: AsyncSession, p: Period, f: Filters, group_by: str) -> list[dict]:
    a = ImpactAction
    b = bucket(a.event_date, group_by)
    rows = (await db.execute(
        select(b, a.state, a.currency, func.count(), func.sum(a.payout), func.sum(a.amount))
        .where(*action_where(p, f)).group_by(b, a.state, a.currency)
    )).all()
    acc: dict[date, dict] = {}
    for d, state, cur, n, payout, amount in rows:
        e = acc.setdefault(d, {"by_state": {}, "commission": {}, "sales": {}, "conversions": 0})
        s = e["by_state"].setdefault(state or "UNKNOWN", {"count": 0, "commission": {}})
        s["count"] += n
        cc = cur or "USD"
        s["commission"][cc] = s["commission"].get(cc, 0.0) + num(payout)
        if state != "REVERSED":
            e["conversions"] += n
            e["commission"][cc] = e["commission"].get(cc, 0.0) + num(payout)
            e["sales"][cc] = e["sales"].get(cc, 0.0) + num(amount)
    out = []
    for d in bucket_list(p, group_by):
        e = acc.get(d, {"by_state": {}, "commission": {}, "sales": {}, "conversions": 0})
        out.append({
            "date": d.isoformat(),
            "conversions": e["conversions"],
            "commission": money(e["commission"].items()),
            "sales": money(e["sales"].items()),
            "by_state": {
                st: {"count": e["by_state"].get(st, {}).get("count", 0),
                     "commission": money(e["by_state"].get(st, {}).get("commission", {}).items())}
                for st in STATES
            },
        })
    return out


def merge_series(clicks: list[dict], conv: list[dict]) -> list[dict]:
    return [{**c, **{k: v for k, v in v_.items() if k != "date"}} for c, v_ in zip(clicks, conv)]


# ---------------------------------------------------------------------------
# Desgloses
# ---------------------------------------------------------------------------

ROUTE_RE = r"/vuelos/([a-z0-9]+)/([a-z0-9]+)/"

CLICK_DIMENSIONS = {
    "country": ImpactClick.customer_country,
    "region": ImpactClick.customer_region,
    "city": ImpactClick.customer_city,
    "device_type": ImpactClick.device_type,
    "device_family": ImpactClick.device_family,
    "os": ImpactClick.os,
    "browser": ImpactClick.browser,
    "referring_domain": ImpactClick.referring_domain,
    "traffic_source": ImpactClick.traffic_source,
    "traffic_category": ImpactClick.traffic_category,
    "landing_page": func.split_part(ImpactClick.landing_page_url, "?", 1),
    "route": func.upper(func.array_to_string(
        func.regexp_match(ImpactClick.landing_page_url, ROUTE_RE), "-")),
    "ad": ImpactClick.ad_name,
    "ad_type": ImpactClick.ad_type,
    "program": ImpactClick.program_name,
    "subid1": ImpactClick.sub_id1,
    "subid2": ImpactClick.sub_id2,
    "subid3": ImpactClick.sub_id3,
    "shared_id": ImpactClick.shared_id,
    "hour": func.extract("hour", local(ImpactClick.event_date)),
    "weekday": func.extract("isodow", local(ImpactClick.event_date)),
}

# Dimensión de click → columna equivalente en conversiones (para la tasa de conversión).
CLICK_TO_ACTION = {
    "country": ImpactAction.customer_country,
    "region": ImpactAction.customer_region,
    "city": ImpactAction.customer_city,
    "referring_domain": ImpactAction.referring_domain,
    "ad": None,
    "program": ImpactAction.campaign_name,
    "subid1": ImpactAction.sub_id1,
    "subid2": ImpactAction.sub_id2,
    "subid3": ImpactAction.sub_id3,
    "shared_id": ImpactAction.shared_id,
}

CONVERSION_DIMENSIONS = {
    "state": ImpactAction.state,
    "event_type": ImpactAction.action_tracker_name,
    "country": ImpactAction.customer_country,
    "region": ImpactAction.customer_region,
    "city": ImpactAction.customer_city,
    "program": ImpactAction.campaign_name,
    "promo_code": ImpactAction.promo_code,
    "referring_domain": ImpactAction.referring_domain,
    "referring_type": ImpactAction.referring_type,
    "subid1": ImpactAction.sub_id1,
    "subid2": ImpactAction.sub_id2,
    "subid3": ImpactAction.sub_id3,
    "shared_id": ImpactAction.shared_id,
    "source": func.nullif(func.concat_ws("/", ImpactAction.sub_id1, ImpactAction.sub_id2,
                                         ImpactAction.sub_id3), ""),
}


async def clicks_breakdown(db: AsyncSession, p: Period, f: Filters, dimension: str,
                           limit: int) -> list[dict]:
    col = CLICK_DIMENSIONS[dimension]
    c = ImpactClick
    rows = (await db.execute(
        select(col.label("k"), func.count(), func.count().filter(c.unique_click.is_(True)))
        .where(*click_where(p, f)).group_by(col).order_by(func.count().desc()).limit(limit)
    )).all()
    total = sum(r[1] for r in rows) or 0
    conv = {}
    acol = CLICK_TO_ACTION.get(dimension)
    if acol is not None:
        conv = {r[0]: r for r in (await db.execute(
            select(acol, func.count(), func.sum(ImpactAction.payout))
            .where(*action_where(p, f), NOT_REVERSED).group_by(acol)
        )).all()}
    out = []
    for k, n, u in rows:
        key = None if k is None else (str(int(k)) if isinstance(k, (Decimal, float)) else str(k))
        item = {"key": key, "clicks": n, "unique_clicks": u,
                "share": round(n / total, 4) if total else None}
        if acol is not None:
            cv = conv.get(k)
            item["conversions"] = cv[1] if cv else 0
            item["conversion_rate"] = round(item["conversions"] / n, 4) if n else None
            item["commission"] = num(cv[2]) if cv else 0.0
        out.append(item)
    return out


async def heatmap(db: AsyncSession, p: Period, f: Filters) -> dict:
    c = ImpactClick
    dow = func.extract("isodow", local(c.event_date))
    hour = func.extract("hour", local(c.event_date))
    rows = (await db.execute(
        select(dow, hour, func.count()).where(*click_where(p, f)).group_by(dow, hour)
    )).all()
    matrix = [[0] * 24 for _ in range(7)]       # [lunes..domingo][0..23]
    for d, h, n in rows:
        matrix[int(d) - 1][int(h)] = n
    return {"weekdays": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"], "matrix": matrix,
            "max": max((max(r) for r in matrix), default=0)}


async def conversions_breakdown(db: AsyncSession, p: Period, f: Filters, dimension: str,
                                limit: int) -> list[dict]:
    col = CONVERSION_DIMENSIONS[dimension]
    a = ImpactAction
    with_state = dimension != "state"
    rows = (await db.execute(
        select(col, a.currency, func.count(), func.count().filter(a.state == "REVERSED"),
               func.sum(a.payout).filter(NOT_REVERSED), func.sum(a.amount).filter(NOT_REVERSED))
        .where(*action_where(p, f, with_state=with_state)).group_by(col, a.currency)
    )).all()
    acc: dict = {}
    for k, cur, n, rev, payout, amount in rows:
        e = acc.setdefault(k, {"key": k, "count": 0, "reversed": 0, "commission": {}, "sales": {}})
        e["count"] += n
        e["reversed"] += rev
        cc = cur or "USD"
        e["commission"][cc] = e["commission"].get(cc, 0.0) + num(payout)
        e["sales"][cc] = e["sales"].get(cc, 0.0) + num(amount)
    out = sorted(acc.values(), key=lambda e: -e["count"])[:limit]
    for e in out:
        e["reversal_rate"] = round(e["reversed"] / e["count"], 4) if e["count"] else None
        e["commission"] = money(e["commission"].items())
        e["sales"] = money(e["sales"].items())
    return out


async def conversion_stats(db: AsyncSession, p: Period, f: Filters) -> dict:
    a = ImpactAction
    aw = action_where(p, f, with_state=False)
    total, rev = (await db.execute(
        select(func.count(), func.count().filter(a.state == "REVERSED")).where(*aw))).one()
    # Tiempo click → venta (horas) y puntos venta vs. comisión, calculados en Python.
    detail = (await db.execute(
        select(a.id, a.state, a.currency, a.payout, a.amount, a.event_date, a.referring_date).where(*aw)
    )).all()
    lags = sorted((r.event_date - r.referring_date).total_seconds() / 3600 for r in detail
                  if r.referring_date and r.event_date and r.event_date >= r.referring_date)
    avg_hours = sum(lags) / len(lags) if lags else None
    median = lags[len(lags) // 2] if lags else None
    bins = [(0, 1, "< 1 h"), (1, 6, "1–6 h"), (6, 24, "6–24 h"), (24, 72, "1–3 días"),
            (72, 168, "3–7 días"), (168, float("inf"), "> 7 días")]
    lag_bins = [{"label": l, "count": sum(1 for x in lags if lo <= x < hi)} for lo, hi, l in bins]
    scatter = [{"id": r.id, "state": r.state, "currency": r.currency or "USD",
                "sale": round(num(r.amount), 4), "commission": round(num(r.payout), 4)}
               for r in detail][:600]
    by_state = (await db.execute(
        select(a.state, a.currency, func.count(), func.sum(a.payout), func.sum(a.amount))
        .where(*aw).group_by(a.state, a.currency))).all()
    states = {st: {"count": 0, "commission": {}, "sales": {}} for st in STATES}
    for st, cur, n, payout, amount in by_state:
        e = states.setdefault(st or "UNKNOWN", {"count": 0, "commission": {}, "sales": {}})
        e["count"] += n
        cc = cur or "USD"
        e["commission"][cc] = e["commission"].get(cc, 0.0) + num(payout)
        e["sales"][cc] = e["sales"].get(cc, 0.0) + num(amount)
    return {
        "total": total,
        "reversal_rate": round(rev / total, 4) if total else None,
        "avg_click_to_sale_hours": round(float(avg_hours), 2) if avg_hours is not None else None,
        "median_click_to_sale_hours": round(median, 2) if median is not None else None,
        "lag_bins": lag_bins,
        "scatter": scatter,
        "by_state": {st: {"count": e["count"], "commission": money(e["commission"].items()),
                          "sales": money(e["sales"].items())} for st, e in states.items()},
    }


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

async def upcoming_locking(db: AsyncSession, f: Filters) -> dict:
    a = ImpactAction
    now = datetime.now(timezone.utc)
    out = {}
    for days in (7, 30):
        rows = (await db.execute(
            select(a.currency, func.sum(a.payout), func.count())
            .where(a.state == "PENDING", a.locking_date >= now, a.locking_date < now + timedelta(days=days),
                   *([a.campaign_id.in_(f.program)] if f.program else []))
            .group_by(a.currency))).all()
        out[f"next_{days}_days"] = {"count": sum(r[2] for r in rows), "commission": money((r[0], r[1]) for r in rows)}
    return out


async def summary(db: AsyncSession, p: Period, f: Filters, group_by: str, compare: str) -> dict:
    cp = compare_period(p, compare)
    cur = await _totals(db, p, f)
    prev = await _totals(db, cp, f) if cp else None

    series = merge_series(await clicks_series(db, p, f, group_by), await conversions_series(db, p, f, group_by))
    compare_series = None
    if cp:
        compare_series = merge_series(await clicks_series(db, cp, f, group_by),
                                      await conversions_series(db, cp, f, group_by))

    a = ImpactAction
    top_days = (await db.execute(
        select(local_day(a.event_date).label("d"), a.currency, func.sum(a.payout), func.count())
        .where(*action_where(p, f), NOT_REVERSED)
        .group_by("d", a.currency).order_by(func.sum(a.payout).desc()).limit(5))).all()

    return {
        "period": p.as_dict(),
        "compare_period": cp.as_dict() if cp else None,
        "kpis": kpis(cur, prev),
        "series": series,
        "compare_series": compare_series,
        "funnel": [
            {"step": "clicks", "value": cur["clicks"]},
            {"step": "unique_clicks", "value": cur["unique_clicks"]},
            {"step": "conversions", "value": cur["conversions"]},
            {"step": "approved", "value": cur["approved"]},
        ],
        "top_countries": await clicks_breakdown(db, p, f, "country", 5),
        "top_sources": await sources_table(db, p, f, limit=5),
        "top_days": [{"date": d.isoformat(), "conversions": n,
                      "commission": [{"currency": c or "USD", "amount": round(num(v), 4)}]}
                     for d, c, v, n in top_days],
        "upcoming_locking": await upcoming_locking(db, f),
    }


# ---------------------------------------------------------------------------
# Fuentes (SubIds) y plataformas
# ---------------------------------------------------------------------------

def _source_key(cols):
    """'sub_id1/sub_id2/sub_id3' sin vacíos; sin SubIds → NULL."""
    return func.nullif(func.concat_ws("/", *cols), "")


async def sources_table(db: AsyncSession, p: Period, f: Filters, limit: int = 100) -> list[dict]:
    c, a = ImpactClick, ImpactAction
    ck = _source_key([c.sub_id1, c.sub_id2, c.sub_id3])
    ak = _source_key([a.sub_id1, a.sub_id2, a.sub_id3])
    clicks = {r[0]: r for r in (await db.execute(
        select(ck, func.count(), func.count().filter(c.unique_click.is_(True)))
        .where(*click_where(p, f)).group_by(ck))).all()}
    conv: dict = {}
    for k, cur, n, payout in (await db.execute(
            select(ak, a.currency, func.count(), func.sum(a.payout))
            .where(*action_where(p, f), NOT_REVERSED).group_by(ak, a.currency))).all():
        e = conv.setdefault(k, {"n": 0, "commission": {}})
        e["n"] += n
        e["commission"][cur or "USD"] = e["commission"].get(cur or "USD", 0.0) + num(payout)
    # Si todo el tráfico es una sola fuente (hoy: sin SubId) y no hay filtros de click,
    # los únicos salen del reporte diario, que cubre todo el período (incluido hoy).
    report_unique = None
    if set(clicks) | set(conv) <= {None} and not f.has_click_filters:
        report_unique = await db.scalar(select(func.sum(ImpactDailyPerformance.unique_clicks)).where(
            ImpactDailyPerformance.day >= p.start, ImpactDailyPerformance.day <= p.end))
    out = []
    for k in set(clicks) | set(conv):
        cl = clicks.get(k)
        cv = conv.get(k, {"n": 0, "commission": {}})
        uniq = int(report_unique) if report_unique is not None else (cl[2] if cl else 0)
        # Tasa y EPC sobre clicks únicos (como Impact).
        out.append({
            "source": k,                   # None = clicks sin SubId
            "clicks": cl[1] if cl else 0,
            "unique_clicks": uniq,
            "conversions": cv["n"],
            "conversion_rate": round(cv["n"] / uniq, 4) if uniq else None,
            "commission": money(cv["commission"].items()),
            "epc": [{"currency": cc, "amount": round(v / uniq, 4)} for cc, v in cv["commission"].items()]
                   if uniq else [],
        })
    out.sort(key=lambda e: -e["clicks"])
    return out[:limit]


async def platforms(db: AsyncSession, p: Period, f: Filters) -> list[dict]:
    """Android vs iOS según el sistema operativo del click (las conversiones no traen dispositivo)."""
    c = ImpactClick
    plat = case((func.lower(c.os) == "android", "android"), (func.lower(c.os) == "ios", "ios"), else_="otro")
    rows = (await db.execute(
        select(plat, func.count(), func.count().filter(c.unique_click.is_(True)))
        .where(*click_where(p, f)).group_by(plat))).all()
    total = sum(r[1] for r in rows) or 0
    return [{"platform": k, "clicks": n, "unique_clicks": u, "share": round(n / total, 4) if total else None}
            for k, n, u in sorted(rows, key=lambda r: -r[1])]


# ---------------------------------------------------------------------------
# Listados y detalle
# ---------------------------------------------------------------------------

CLICK_SORT = {
    "date": ImpactClick.event_date, "country": ImpactClick.customer_country,
    "city": ImpactClick.customer_city, "os": ImpactClick.os, "device": ImpactClick.device_type,
    "browser": ImpactClick.browser,
}
ACTION_SORT = {
    "date": ImpactAction.event_date, "commission": ImpactAction.payout, "amount": ImpactAction.amount,
    "state": ImpactAction.state, "country": ImpactAction.customer_country,
    "locking_date": ImpactAction.locking_date,
}


def _order(sort: str | None, columns: dict, default):
    if not sort:
        return [default.desc(), ]
    desc = sort.startswith("-")
    col = columns.get(sort.lstrip("-"))
    if col is None:
        return [default.desc()]
    return [col.desc().nulls_last() if desc else col.asc().nulls_last()]


def _iso(v):
    return v.isoformat() if v is not None else None


def click_dict(c: ImpactClick, full: bool = False) -> dict:
    d = {
        "id": c.id, "impact_id": c.external_id, "date": _iso(c.event_date),
        "program": c.program_name, "ad": c.ad_name, "ad_type": c.ad_type,
        "unique": c.unique_click, "country": c.customer_country, "region": c.customer_region,
        "city": c.customer_city, "device_type": c.device_type, "device_family": c.device_family,
        "os": c.os, "browser": c.browser, "referring_domain": c.referring_domain,
        "landing_page_url": c.landing_page_url, "sub_id1": c.sub_id1, "sub_id2": c.sub_id2,
        "sub_id3": c.sub_id3, "shared_id": c.shared_id,
    }
    if full:
        d.update({
            "program_id": c.program_id, "ad_id": c.ad_id, "media_id": c.media_id,
            "product_sku": c.product_sku, "customer_area": c.customer_area,
            "referring_url": c.referring_url, "traffic_source": c.traffic_source,
            "traffic_category": c.traffic_category,
            "payout": num(c.payout) if c.payout is not None else None,
            "deal_name": c.deal_name, "deal_type": c.deal_type, "raw": c.raw,
        })
    return d


def action_dict(a: ImpactAction, full: bool = False) -> dict:
    d = {
        "id": a.id, "date": _iso(a.event_date), "state": a.state, "program": a.campaign_name,
        "event_type": a.action_tracker_name,
        "commission": {"amount": num(a.payout), "currency": a.currency or "USD"},
        "sale": {"amount": num(a.amount), "currency": a.currency or "USD"},
        "locking_date": _iso(a.locking_date), "cleared_date": _iso(a.cleared_date),
        "country": a.customer_country, "region": a.customer_region, "city": a.customer_city,
        "sub_id1": a.sub_id1, "sub_id2": a.sub_id2, "sub_id3": a.sub_id3, "shared_id": a.shared_id,
    }
    if full:
        d.update({
            "program_id": a.campaign_id, "event_type_id": a.action_tracker_id,
            "creation_date": _iso(a.creation_date), "referring_date": _iso(a.referring_date),
            "referring_type": a.referring_type, "referring_domain": a.referring_domain,
            "promo_code": a.promo_code, "event_code": a.event_code, "oid": a.oid, "ad_id": a.ad_id,
            "customer_area": a.customer_area,
            "delta_commission": num(a.delta_payout) if a.delta_payout is not None else None,
            "delta_sale": num(a.delta_amount) if a.delta_amount is not None else None,
            "intended_commission": num(a.intended_payout) if a.intended_payout is not None else None,
            "intended_sale": num(a.intended_amount) if a.intended_amount is not None else None,
            "raw": a.raw,
        })
    return d


async def list_clicks(db: AsyncSession, p: Period, f: Filters, page: int, page_size: int,
                      q: str | None, sort: str | None) -> dict:
    c = ImpactClick
    conds = click_where(p, f)
    if q:
        like = f"%{q}%"
        conds.append(or_(c.customer_city.ilike(like), c.customer_region.ilike(like),
                         c.customer_country.ilike(like), c.os.ilike(like), c.browser.ilike(like),
                         c.landing_page_url.ilike(like), c.ad_name.ilike(like),
                         c.external_id.ilike(like), c.device_family.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(c).where(*conds)) or 0
    rows = (await db.execute(select(c).where(*conds).order_by(*_order(sort, CLICK_SORT, c.event_date))
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"page": page, "page_size": page_size, "total": total,
            "items": [click_dict(r) for r in rows]}


async def list_conversions(db: AsyncSession, p: Period, f: Filters, page: int, page_size: int,
                           q: str | None, sort: str | None, locking_day: date | None = None) -> dict:
    a = ImpactAction
    if locking_day:
        # Ventas que quedan firmes ese día (calendario de Ganancias): ignora el período.
        conds = action_where(Period(locking_day, locking_day), f, date_col=a.locking_date)
    else:
        conds = action_where(p, f)
    if q:
        like = f"%{q}%"
        conds.append(or_(a.id.ilike(like), a.customer_city.ilike(like), a.customer_country.ilike(like),
                         a.campaign_name.ilike(like), a.promo_code.ilike(like), a.oid.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(a).where(*conds)) or 0
    rows = (await db.execute(select(a).where(*conds).order_by(*_order(sort, ACTION_SORT, a.event_date))
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"page": page, "page_size": page_size, "total": total,
            "items": [action_dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# Ganancias y facturas
# ---------------------------------------------------------------------------

async def earnings(db: AsyncSession, p: Period, f: Filters) -> dict:
    a = ImpactAction
    by_state = (await db.execute(
        select(a.state, a.currency, func.count(), func.sum(a.payout))
        .where(*action_where(p, f, with_state=False)).group_by(a.state, a.currency))).all()
    states = {st: {"count": 0, "commission": []} for st in STATES}
    for st, cur, n, payout in by_state:
        e = states.setdefault(st or "UNKNOWN", {"count": 0, "commission": []})
        e["count"] += n
        e["commission"].append({"currency": cur or "USD", "amount": round(num(payout), 4)})

    # Proyección del mes en curso según el ritmo de lo que va del mes (no revertidas).
    t = today()
    month = Period(t.replace(day=1), t)
    next_month = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
    days_in_month = (next_month - t.replace(day=1)).days
    so_far = (await db.execute(
        select(a.currency, func.sum(a.payout)).where(*action_where(month, f, with_state=False), NOT_REVERSED)
        .group_by(a.currency))).all()
    projection = [{
        "currency": cur or "USD",
        "so_far": round(num(v), 4),
        "projected": round(num(v) / t.day * days_in_month, 4),
    } for cur, v in so_far]

    # Calendario: cuándo se vuelven firmes las pendientes (de hoy en adelante).
    ld = local_day(a.locking_date)
    cal_rows = (await db.execute(
        select(ld.label("d"), a.currency, func.count(), func.sum(a.payout))
        .where(a.state == "PENDING", a.locking_date >= datetime.combine(t, time.min, TZ),
               *([a.campaign_id.in_(f.program)] if f.program else []))
        .group_by("d", a.currency).order_by("d"))).all()
    calendar: dict = {}
    for d, cur, n, payout in cal_rows:
        e = calendar.setdefault(d, {"date": d.isoformat(), "count": 0, "commission": []})
        e["count"] += n
        e["commission"].append({"currency": cur or "USD", "amount": round(num(payout), 4)})

    overdue_pending = await db.scalar(select(func.count()).where(
        a.state == "PENDING", a.locking_date < datetime.combine(t, time.min, TZ)))

    return {
        "period": p.as_dict(),
        "by_state": states,
        "month_projection": {"month": t.strftime("%Y-%m"), "day": t.day, "days_in_month": days_in_month,
                             "by_currency": projection},
        "locking_calendar": list(calendar.values()),
        "pending_past_locking_date": overdue_pending or 0,
    }


def invoice_dict(i: ImpactInvoice) -> dict:
    return {
        "id": i.id, "created_date": _iso(i.created_date),
        "total": {"amount": num(i.total_amount), "currency": i.currency or "USD"},
        "vat": {"amount": num(i.total_vat_amount), "currency": i.currency or "USD"},
        "status": i.status, "due_date": _iso(i.due_date), "paid_date": _iso(i.paid_date),
        "line_items": i.line_items, "detailed_line_items": i.detailed_line_items,
        "pdf_path": f"/api/v1/affiliate/invoices/{i.id}/pdf",
    }


async def list_invoices(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(ImpactInvoice).order_by(ImpactInvoice.created_date.desc()))).scalars().all()
    return [invoice_dict(i) for i in rows]


# ---------------------------------------------------------------------------
# Estado de sincronización
# ---------------------------------------------------------------------------

SYNC_KINDS = ("actions", "actions_backfill", "performance", "performance_backfill",
              "click_export", "invoices", "programs")


async def sync_status(db: AsyncSession) -> dict:
    from app.services.impact_reports_client import is_configured
    from app.services.impact_sync import click_exports_used_last_24h
    from app.config import settings

    kinds = {}
    for kind in SYNC_KINDS:
        last = (await db.execute(select(ImpactSyncRun).where(ImpactSyncRun.kind == kind)
                                 .order_by(ImpactSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
        last_ok = (await db.execute(select(ImpactSyncRun).where(ImpactSyncRun.kind == kind,
                                                                ImpactSyncRun.status == "ok")
                                    .order_by(ImpactSyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
        kinds[kind] = {
            "last_run": None if not last else {
                "started_at": _iso(last.started_at), "finished_at": _iso(last.finished_at),
                "status": last.status, "rows": last.rows, "error": last.error,
            },
            "last_ok_at": _iso(last_ok.finished_at) if last_ok else None,
        }
    ok_times = [k["last_ok_at"] for n, k in kinds.items()
                if n in ("actions", "actions_backfill", "performance", "performance_backfill") and k["last_ok_at"]]
    failed_days = (await db.execute(select(ImpactClickExport.day).where(ImpactClickExport.status == "failed")
                                    .order_by(ImpactClickExport.day.desc()))).scalars().all()
    return {
        "configured": is_configured(),
        "updated_at": max(ok_times) if ok_times else None,
        "clicks_available_until": _iso(await clicks_available_until(db)),
        "today": today().isoformat(),
        "click_exports_used_24h": await click_exports_used_last_24h(),
        "click_exports_daily_budget": settings.impact_click_export_daily_budget,
        "click_export_failed_days": [d.isoformat() for d in failed_days],
        "kinds": kinds,
    }


async def programs(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(ImpactProgram).order_by(ImpactProgram.name))).scalars().all()
    return [{"id": r.id, "name": r.name} for r in rows]
