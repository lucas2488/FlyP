"""
Consultas del dashboard de AdMob sobre las tablas sincronizadas.

Reglas:
  - Las fechas son días de AdMob (hora de Los Ángeles), igual que en la consola de AdMob.
  - Los ratios se calculan SIEMPRE sobre las sumas del período (nunca promediando ratios):
    match rate = matched / solicitudes · show rate = impresiones / matched ·
    CTR = clicks / impresiones · eCPM = ganancias / impresiones × 1000.
  - Montos con su moneda: {"amount": 1.23, "currency": "USD"}.
  - Filtros: app, platform, format, ad_unit, country. Cada tabla aplica los que tiene;
    los que no aplica se informan en `ignored_filters`.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AdmobAccount, AdmobAdUnit, AdmobApp, AdmobDaily, AdmobDailyAdType, AdmobDailyServing,
    AdmobDailyVersion, AdmobIntradaySnapshot, AdmobMediationDaily, AdmobSyncRun,
)
from app.services.admob_sync import LA, la_today

METRICS = ("earnings", "impressions", "ecpm", "clicks", "ctr", "requests", "matched", "match_rate", "show_rate")
FILTER_COLS = {"app": "app_id", "platform": "platform", "format": "format", "ad_unit": "ad_unit_id", "country": "country"}


# ---------------------------------------------------------------------------
# Parámetros
# ---------------------------------------------------------------------------

@dataclass
class Filters:
    app: list[str] = field(default_factory=list)
    platform: list[str] = field(default_factory=list)
    format: list[str] = field(default_factory=list)
    ad_unit: list[str] = field(default_factory=list)
    country: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


def split_list(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def make_period(start: date | None, end: date | None) -> Period:
    end = end or la_today()
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
            except ValueError:
                return d.replace(year=d.year - 1, day=28)
        return Period(back(p.start), back(p.end))
    return None


def where(model, p: Period | None, f: Filters) -> tuple[list, list[str]]:
    conds, ignored = [], []
    if p is not None:
        conds += [model.date >= p.start, model.date <= p.end]
    for name, col in FILTER_COLS.items():
        vals = getattr(f, name)
        if not vals:
            continue
        if hasattr(model, col):
            conds.append(getattr(model, col).in_(vals))
        else:
            ignored.append(name)
    return conds, ignored


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def sums(model):
    has_req = hasattr(model, "requests")
    return [
        func.coalesce(func.sum(model.requests), 0).label("requests") if has_req else None,
        func.coalesce(func.sum(model.matched), 0).label("matched") if has_req else None,
        func.coalesce(func.sum(model.impressions), 0).label("impressions"),
        func.coalesce(func.sum(model.clicks), 0).label("clicks"),
        func.coalesce(func.sum(model.earnings), 0).label("earnings"),
    ]


def _cols(model):
    return [c for c in sums(model) if c is not None]


def metrics(row) -> dict:
    g = (lambda k: getattr(row, k, None)) if not isinstance(row, dict) else row.get
    req, mat = g("requests"), g("matched")
    imp, clk, earn = int(g("impressions") or 0), int(g("clicks") or 0), float(g("earnings") or 0)
    out = {
        "earnings": round(earn, 6),
        "impressions": imp,
        "clicks": clk,
        "ecpm": round(earn / imp * 1000, 4) if imp else None,
        "ctr": round(clk / imp, 6) if imp else None,
    }
    if req is not None:
        req, mat = int(req or 0), int(mat or 0)
        out.update({
            "requests": req, "matched": mat,
            "match_rate": round(mat / req, 6) if req else None,
            "show_rate": round(imp / mat, 6) if mat else None,
        })
    return out


def pct_change(cur, prev) -> float | None:
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 2)


async def currency(db: AsyncSession) -> str:
    return (await db.scalar(select(AdmobAccount.currency).limit(1))) or "USD"


def money(v, cur: str) -> dict:
    return {"amount": round(float(v or 0), 6), "currency": cur}


async def totals(db: AsyncSession, model, p: Period, f: Filters) -> dict:
    conds, _ = where(model, p, f)
    return metrics((await db.execute(select(*_cols(model)).where(*conds))).one())


def kpis(cur: dict, prev: dict | None) -> dict:
    return {k: {"value": cur.get(k), "previous": prev.get(k) if prev else None,
                "change_pct": pct_change(cur.get(k), prev.get(k)) if prev else None}
            for k in METRICS if k in cur}


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def bucket(col, group_by: str):
    return col if group_by == "day" else cast(func.date_trunc(group_by, col), Date)


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
        d = (d + timedelta(days=32)).replace(day=1) if group_by == "month" else d + timedelta(days=7 if group_by == "week" else 1)
    return out


async def series(db: AsyncSession, model, p: Period, f: Filters, group_by: str, split: str | None = None) -> dict:
    """{key: [{date, ...metrics}]}; sin split, key = 'total'."""
    b = bucket(model.date, group_by)
    conds, _ = where(model, p, f)
    gcols = [b] + ([getattr(model, split)] if split else [])
    rows = (await db.execute(select(*gcols, *_cols(model)).where(*conds).group_by(*gcols))).all()
    acc: dict[str, dict] = {}
    for r in rows:
        key = (r[1] or "Sin dato") if split else "total"
        acc.setdefault(key, {})[r[0]] = metrics(r)
    if not acc:
        acc["total"] = {}
    empty = metrics({"requests": 0 if hasattr(model, "requests") else None, "matched": 0 if hasattr(model, "requests") else None,
                     "impressions": 0, "clicks": 0, "earnings": 0})
    return {k: [{"date": d.isoformat(), **v.get(d, empty)} for d in bucket_list(p, group_by)] for k, v in acc.items()}


# ---------------------------------------------------------------------------
# Hoy
# ---------------------------------------------------------------------------

async def _snapshot_total(db: AsyncSession, report_date: date, target: datetime, f: Filters) -> tuple[dict | None, str | None]:
    """Totales de la foto de `report_date` más cercana a `target`."""
    S = AdmobIntradaySnapshot
    taken = (await db.execute(
        select(S.taken_at).where(S.report_date == report_date)
        .order_by(func.abs(func.extract("epoch", S.taken_at - target))).limit(1)
    )).scalar_one_or_none()
    if taken is None:
        return None, None
    conds = [S.report_date == report_date, S.taken_at == taken]
    if f.app:
        conds.append(S.app_id.in_(f.app))
    if f.platform:
        conds.append(S.platform.in_(f.platform))
    r = (await db.execute(select(func.coalesce(func.sum(S.earnings), 0), func.coalesce(func.sum(S.impressions), 0),
                                 func.coalesce(func.sum(S.clicks), 0)).where(*conds))).one()
    return {"earnings": float(r[0]), "impressions": int(r[1]), "clicks": int(r[2])}, taken.isoformat()


async def _intraday(db: AsyncSession, report_date: date, f: Filters) -> list[dict]:
    """Ganancias acumuladas del día según cada foto horaria, con la hora de Los Ángeles."""
    S = AdmobIntradaySnapshot
    conds = [S.report_date == report_date]
    if f.app:
        conds.append(S.app_id.in_(f.app))
    if f.platform:
        conds.append(S.platform.in_(f.platform))
    rows = (await db.execute(select(S.taken_at, func.sum(S.earnings), func.sum(S.impressions))
                             .where(*conds).group_by(S.taken_at).order_by(S.taken_at))).all()
    out = []
    for taken, e, i in rows:
        la = taken.astimezone(LA)
        out.append({"taken_at": taken.isoformat(), "la_hour": round(la.hour + la.minute / 60, 2),
                    "earnings": float(e or 0), "impressions": int(i or 0)})
    return out


async def today(db: AsyncSession, f: Filters) -> dict:
    cur = await currency(db)
    t = la_today()
    y = t - timedelta(days=1)
    now = datetime.now(timezone.utc)
    tot_today = await totals(db, AdmobDaily, Period(t, t), f)
    tot_y = await totals(db, AdmobDaily, Period(y, y), f)
    last7 = await totals(db, AdmobDaily, Period(t - timedelta(days=7), y), f)
    month = await totals(db, AdmobDaily, Period(t.replace(day=1), t), f)

    snap_y, snap_at = (None, None)
    snapshot_note = None
    if f.format or f.country or f.ad_unit:
        snapshot_note = "Con filtros de formato, país o bloque se compara contra el total de ayer."
    else:
        snap_y, snap_at = await _snapshot_total(db, y, now - timedelta(days=1), f)
        if snap_y is None:
            snapshot_note = "Todavía no hay fotos horarias de ayer: se compara contra el total de ayer."
    y_same = snap_y["earnings"] if snap_y else tot_y["earnings"]

    s7 = await series(db, AdmobDaily, Period(t - timedelta(days=6), t), f, "day")
    la_now = datetime.now(LA)
    return {
        "report_date": t.isoformat(),
        "la_now": la_now.isoformat(timespec="minutes"),
        "day_fraction": round((la_now.hour * 60 + la_now.minute) / 1440, 4),
        "intraday_today": await _intraday(db, t, f),
        "intraday_yesterday": await _intraday(db, y, f),
        "timezone": "America/Los_Angeles",
        "currency": cur,
        "today": {**money(tot_today["earnings"], cur), "impressions": tot_today["impressions"], "clicks": tot_today["clicks"],
                  "ecpm": tot_today["ecpm"], "estimated": True},
        "yesterday_same_time": {**money(y_same, cur), "source": "snapshot" if snap_y else "yesterday_total",
                                "snapshot_taken_at": snap_at, "note": snapshot_note,
                                "change_pct": pct_change(tot_today["earnings"], y_same)},
        "yesterday_total": money(tot_y["earnings"], cur),
        "avg_last_7_days": {**money(last7["earnings"] / 7, cur), "change_pct": pct_change(tot_today["earnings"], last7["earnings"] / 7)},
        "month_to_date": money(month["earnings"], cur),
        "last_7_days": [{"date": x["date"], "earnings": x["earnings"], "impressions": x["impressions"]} for x in s7["total"]],
    }


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

async def by_dim(db: AsyncSession, model, col_name: str, p: Period, f: Filters, limit: int = 500) -> list[dict]:
    col = getattr(model, col_name)
    conds, _ = where(model, p, f)
    rows = (await db.execute(select(col, *_cols(model)).where(*conds).group_by(col)
                             .order_by(func.sum(model.earnings).desc()).limit(limit))).all()
    total = sum(float(r.earnings or 0) for r in rows) or 0
    return [{"key": r[0], **metrics(r), "earnings_share": round(float(r.earnings or 0) / total, 4) if total else None}
            for r in rows]


async def month_block(db: AsyncSession, f: Filters) -> dict:
    t = la_today()
    first = t.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    dim = (nxt - first).days
    complete_days = t.day - 1
    so_far = (await totals(db, AdmobDaily, Period(first, t), f))["earnings"]
    complete = (await totals(db, AdmobDaily, Period(first, t - timedelta(days=1)), f))["earnings"] if complete_days else 0
    prev_last = first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    prev_total = (await totals(db, AdmobDaily, Period(prev_first, prev_last), f))["earnings"]
    prev_same = 0.0
    if complete_days:
        same_end = prev_first + timedelta(days=min(complete_days, prev_last.day) - 1)
        prev_same = (await totals(db, AdmobDaily, Period(prev_first, same_end), f))["earnings"]
    projection = complete / complete_days * dim if complete_days else None
    return {
        "month": first.strftime("%Y-%m"), "day": t.day, "days_in_month": dim, "complete_days": complete_days,
        "so_far": so_far, "projection": projection,
        "last_month_total": prev_total, "last_month_same_days": prev_same,
        "change_vs_last_month_same_days_pct": pct_change(complete, prev_same) if complete_days else None,
        "projection_vs_last_month_pct": pct_change(projection, prev_total) if projection is not None else None,
    }


async def summary(db: AsyncSession, p: Period, f: Filters, group_by: str, compare: str) -> dict:
    cur_name = await currency(db)
    cp = compare_period(p, compare)
    cur = await totals(db, AdmobDaily, p, f)
    prev = await totals(db, AdmobDaily, cp, f) if cp else None
    s = await series(db, AdmobDaily, p, f, group_by)
    sp = await series(db, AdmobDaily, cp, f, group_by) if cp else None
    return {
        "currency": cur_name,
        "compare_period": cp.as_dict() if cp else None,
        "kpis": kpis(cur, prev),
        "series": s["total"],
        "compare_series": sp["total"] if sp else None,
        "by_platform": await by_dim(db, AdmobDaily, "platform", p, f),
        "by_format": await by_dim(db, AdmobDaily, "format", p, f),
        "month": await month_block(db, f),
    }


# ---------------------------------------------------------------------------
# Novedades: últimos 7 días completos vs los 7 anteriores
# ---------------------------------------------------------------------------

FORMAT_LBL = {"banner": "banners", "interstitial": "intersticiales", "native": "nativos", "rewarded": "bonificados",
              "rewarded_interstitial": "bonificados intersticiales", "app_open": "apertura de app"}
FORMAT_NAME = {"banner": "Banner", "interstitial": "Intersticial", "native": "Nativo", "rewarded": "Bonificado",
               "rewarded_interstitial": "Bonificado intersticial", "app_open": "Apertura de app"}
METRIC_LBL = {"earnings": "Las ganancias", "impressions": "Las impresiones", "ecpm": "El eCPM"}


async def insights(db: AsyncSession, f: Filters, min_impressions: int, threshold: float) -> dict:
    t = la_today()
    cur_p = Period(t - timedelta(days=7), t - timedelta(days=1))
    prev_p = Period(t - timedelta(days=14), t - timedelta(days=8))
    out = []
    for group in (("platform",), ("format",), ("country",), ("platform", "format")):
        cols = [getattr(AdmobDaily, g) for g in group]
        async def grab(p):
            conds, _ = where(AdmobDaily, p, f)
            rows = (await db.execute(select(*cols, *_cols(AdmobDaily)).where(*conds).group_by(*cols))).all()
            return {tuple(r[:len(group)]): metrics(r) for r in rows}
        a, b = await grab(cur_p), await grab(prev_p)
        for key in set(a) | set(b):
            ca, cb = a.get(key, metrics({"impressions": 0, "clicks": 0, "earnings": 0})), b.get(key)
            if not cb or max(ca["impressions"], cb["impressions"]) < min_impressions:
                continue
            for m in ("earnings", "impressions", "ecpm"):
                v1, v0 = ca.get(m), cb.get(m)
                ch = pct_change(v1, v0)
                if ch is None or abs(ch) < threshold * 100:
                    continue
                parts = dict(zip(group, key))
                subject = " de " + FORMAT_LBL.get(parts["format"], parts["format"]) if "format" in parts else ""
                where_txt = (" en " + parts["platform"]) if "platform" in parts else (" en " + parts["country"] if "country" in parts else "")
                plural = m in ("earnings", "impressions")   # "Las ganancias subieron" / "El eCPM subió"
                up_v, down_v = ("subieron", "bajaron") if plural else ("subió", "bajó")
                if ch > 0 and v0 and v1 / v0 >= 2:
                    verb = f"{up_v} {round(v1 / v0, 1):g}×".replace(".", ",")
                else:
                    verb = (up_v if ch > 0 else down_v) + f" {abs(round(ch)):g} %"
                fmt_name = FORMAT_NAME.get(parts.get("format", ""), parts.get("format", ""))
                if group == ("platform", "format"):
                    title, tag = f"{fmt_name} en {parts['platform']}", "Formato"
                elif group == ("format",):
                    title, tag = fmt_name, "Formato"
                elif group == ("platform",):
                    title, tag = parts["platform"], "Plataforma"
                else:
                    title, tag = parts["country"], "País"
                out.append({
                    "group": "_".join(group), "dims": parts, "metric": m,
                    "current": v1, "previous": v0, "change_pct": ch, "dir": "up" if ch > 0 else "down",
                    "impressions": ca["impressions"], "title": title, "tag": tag,
                    "text": f"{METRIC_LBL[m]}{subject}{where_txt} {verb} vs. la semana anterior",
                    "short": f"{METRIC_LBL[m]} {verb} vs. la semana anterior",
                })
    out.sort(key=lambda x: -abs(x["change_pct"]))
    return {"current_period": cur_p.as_dict(), "previous_period": prev_p.as_dict(),
            "min_impressions": min_impressions, "threshold_pct": threshold * 100, "items": out[:20]}


# ---------------------------------------------------------------------------
# Apps y bloques
# ---------------------------------------------------------------------------

def default_app_ids() -> list[str]:
    return split_list(settings.admob_default_app_ids)


async def apps(db: AsyncSession, p: Period, f: Filters, compare: str) -> list[dict]:
    cp = compare_period(p, compare)
    cur = {r["key"]: r for r in await by_dim(db, AdmobDaily, "app_id", p, f)}
    prev = {r["key"]: r for r in await by_dim(db, AdmobDaily, "app_id", cp, f)} if cp else {}
    info = {a.app_id: a for a in (await db.execute(select(AdmobApp))).scalars()}
    defaults = set(default_app_ids())
    ids = set(cur) | ({k for k in info if not f.app or k in f.app} if not f.platform else set(cur))
    out = []
    for app_id in ids:
        a, m, pv = info.get(app_id), cur.get(app_id), prev.get(app_id)
        if not m and app_id not in defaults and not (f.app and app_id in f.app):
            continue
        m = m or metrics({"requests": 0, "matched": 0, "impressions": 0, "clicks": 0, "earnings": 0})
        out.append({
            "app_id": app_id, "name": a.name if a else app_id, "platform": a.platform if a else None,
            "store_id": a.app_store_id if a else None, "approval_state": a.approval_state if a else None,
            "is_default": app_id in defaults, **{k: v for k, v in m.items() if k != "key"},
            "previous": {k: pv.get(k) for k in ("earnings", "impressions", "ecpm")} if pv else None,
            "earnings_change_pct": pct_change(m["earnings"], pv["earnings"]) if pv else None,
        })
    out.sort(key=lambda x: (not x["is_default"], -x["earnings"]))
    return out


async def ad_units(db: AsyncSession, p: Period, f: Filters, compare: str) -> list[dict]:
    cp = compare_period(p, compare)
    D = AdmobDaily
    conds, _ = where(D, p, f)
    rows = (await db.execute(select(D.ad_unit_id, func.max(D.ad_unit_name), D.app_id, func.max(D.app_name),
                                    func.max(D.format), func.max(D.platform), *_cols(D))
                             .where(*conds).group_by(D.ad_unit_id, D.app_id)
                             .order_by(func.sum(D.earnings).desc()))).all()
    prev = {}
    if cp:
        pc, _ = where(D, cp, f)
        prev = {r[0]: float(r[1] or 0) for r in (await db.execute(
            select(D.ad_unit_id, func.sum(D.earnings)).where(*pc).group_by(D.ad_unit_id))).all()}
    units = {u.ad_unit_id: u for u in (await db.execute(select(AdmobAdUnit))).scalars()}
    out = []
    for r in rows:
        u = units.get(r[0])
        m = metrics(r)
        out.append({"ad_unit_id": r[0], "name": (u.name if u else None) or r[1], "app_id": r[2], "app_name": r[3],
                    "format": r[4], "platform": r[5], "ad_types": u.ad_types if u else [], **m,
                    "earnings_change_pct": pct_change(m["earnings"], prev.get(r[0])) if cp else None})
    return out


async def ad_unit_detail(db: AsyncSession, ad_unit_id: str, p: Period, f: Filters, group_by: str, compare: str) -> dict | None:
    D = AdmobDaily
    u = await db.get(AdmobAdUnit, ad_unit_id)
    label = (await db.execute(select(func.max(D.ad_unit_name), func.max(D.app_id), func.max(D.app_name),
                                     func.max(D.format), func.max(D.platform)).where(D.ad_unit_id == ad_unit_id))).one()
    if not u and label[0] is None:
        return None
    fu = Filters(**{**f.__dict__, "ad_unit": [ad_unit_id]})
    cp = compare_period(p, compare)
    cur = await totals(db, D, p, fu)
    prev = await totals(db, D, cp, fu) if cp else None
    return {
        "ad_unit_id": ad_unit_id, "name": (u.name if u else None) or label[0], "app_id": (u.app_id if u else None) or label[1],
        "app_name": label[2], "format": label[3] or (u.format.lower() if u and u.format else None), "platform": label[4],
        "ad_types": u.ad_types if u else [],
        "kpis": kpis(cur, prev),
        "series": (await series(db, D, p, fu, group_by))["total"],
        "compare_series": (await series(db, D, cp, fu, group_by))["total"] if cp else None,
        "by_country": (await by_dim(db, D, "country", p, fu))[:10],
    }


# ---------------------------------------------------------------------------
# Desgloses
# ---------------------------------------------------------------------------

BREAKDOWNS = {
    "app": (AdmobDaily, "app_id", None),
    "platform": (AdmobDaily, "platform", None),
    "ad_unit": (AdmobDaily, "ad_unit_id", None),
    "format": (AdmobDaily, "format", None),
    "country": (AdmobDaily, "country", None),
    "ad_type": (AdmobDailyAdType, "ad_type", None),
    "serving_restriction": (AdmobDailyServing, "serving_restriction", None),
    "app_version": (AdmobDailyVersion, "version", "app_version"),
    "os_version": (AdmobDailyVersion, "version", "os_version"),
    "sdk_version": (AdmobDailyVersion, "version", "sdk_version"),
}


async def breakdown(db: AsyncSession, dimension: str, p: Period, f: Filters, compare: str, limit: int) -> dict:
    model, col_name, dim_type = BREAKDOWNS[dimension]
    col = getattr(model, col_name)
    cp = compare_period(p, compare)

    async def grab(per: Period, by_platform: bool = False):
        conds, ignored = where(model, per, f)
        if dim_type:
            conds.append(model.dimension_type == dim_type)
        cols = [col] + ([model.platform] if by_platform else [])
        rows = (await db.execute(select(*cols, *_cols(model)).where(*conds).group_by(*cols)
                                 .order_by(func.sum(model.earnings).desc()))).all()
        return rows, ignored

    rows, ignored = await grab(p, by_platform=bool(dim_type))
    prev = {}
    if cp:
        prows, _ = await grab(cp, by_platform=bool(dim_type))
        prev = {tuple(r[:2] if dim_type else r[:1]): metrics(r) for r in prows}
    names = {}
    if dimension == "app":
        names = {a.app_id: a.name for a in (await db.execute(select(AdmobApp))).scalars()}
    elif dimension == "ad_unit":
        names = {u.ad_unit_id: u.name for u in (await db.execute(select(AdmobAdUnit))).scalars()}

    first_seen = {}
    if dim_type:
        # Primer día con impresiones de cada versión (marcador "apareció una versión nueva").
        conds, _ = where(model, None, f)
        conds += [model.dimension_type == dim_type, model.impressions > 0]
        first_seen = {(r[0], r[1]): r[2] for r in (await db.execute(
            select(col, model.platform, func.min(model.date)).where(*conds).group_by(col, model.platform))).all()}

    total = sum(float(r.earnings or 0) for r in rows) or 0
    items = []
    for r in rows[:limit]:
        key = tuple(r[:2] if dim_type else r[:1])
        m = metrics(r)
        pv = prev.get(key)
        item = {"key": r[0], "label": names.get(r[0], r[0]), **m,
                "earnings_share": round(m["earnings"] / total, 4) if total else None,
                "previous": {k: pv.get(k) for k in ("earnings", "impressions", "ecpm", "ctr", "match_rate")} if pv else None,
                "earnings_change_pct": pct_change(m["earnings"], pv["earnings"]) if pv else None}
        if dim_type:
            item["platform"] = r[1]
            fs = first_seen.get((r[0], r[1]))
            item["first_seen"] = fs.isoformat() if fs else None
        items.append(item)
    return {"dimension": dimension, "items": items, "ignored_filters": ignored,
            "compare_period": cp.as_dict() if cp else None}


async def timeseries(db: AsyncSession, metric: str, p: Period, f: Filters, group_by: str, compare: str,
                     split: str | None, dimension_model: str | None = None) -> dict:
    model = AdmobDailyServing if dimension_model == "serving" else AdmobDaily
    cp = compare_period(p, compare)
    s = await series(db, model, p, f, group_by, split)
    pick = lambda pts: [{"date": x["date"], "value": x.get(metric)} for x in pts]
    out = {k: pick(v) for k, v in s.items()}
    comp = None
    if cp and not split:
        comp = pick((await series(db, model, cp, f, group_by))["total"])
    return {"metric": metric, "split": split, "series": out, "compare_series": comp,
            "compare_period": cp.as_dict() if cp else None}


async def funnel(db: AsyncSession, p: Period, f: Filters, compare: str) -> dict:
    cp = compare_period(p, compare)
    cur = await totals(db, AdmobDaily, p, f)
    prev = await totals(db, AdmobDaily, cp, f) if cp else None
    steps = [("requests", "Solicitudes"), ("matched", "Con anuncio"), ("impressions", "Impresiones"), ("clicks", "Clicks")]
    rates = {"matched": "match_rate", "impressions": "show_rate", "clicks": "ctr"}
    return {
        "steps": [{"step": k, "label": l, "value": cur[k], "previous": prev[k] if prev else None,
                   "rate_from_previous": cur.get(rates[k]) if k in rates else None,
                   "rate_name": rates.get(k)} for k, l in steps],
        "kpis": kpis(cur, prev),
        "compare_period": cp.as_dict() if cp else None,
    }


# ---------------------------------------------------------------------------
# Mediación
# ---------------------------------------------------------------------------

async def mediation(db: AsyncSession, dimension: str, p: Period, f: Filters) -> dict:
    M = AdmobMediationDaily
    key, name = (M.ad_source_id, M.ad_source_name) if dimension == "ad_source" else (M.mediation_group_id, M.mediation_group_name)
    conds, ignored = where(M, p, f)
    rows = (await db.execute(select(
        key, func.max(name), *_cols(M),
        func.sum(M.observed_ecpm * M.impressions).label("ecpm_w"),
        func.sum(M.ad_load_latency * M.requests).label("lat_w"),
        func.sum(M.requests).filter(M.ad_load_latency.isnot(None)).label("lat_req"),
    ).where(*conds).group_by(key).order_by(func.sum(M.earnings).desc()))).all()
    extra = {}
    if dimension == "mediation_group":
        for gid, fmts, plats, nets in (await db.execute(
                select(M.mediation_group_id, func.array_agg(func.distinct(M.format)), func.array_agg(func.distinct(M.platform)),
                       func.count(func.distinct(M.ad_source_id))).where(*conds).group_by(M.mediation_group_id))).all():
            extra[gid] = {"formats": sorted(x for x in fmts if x), "platforms": sorted(x for x in plats if x), "networks": nets}
    items = []
    for r in rows:
        m = metrics(r)
        items.append({"key": r[0], "label": r[1] or r[0], **m, **extra.get(r[0], {}),
                      "observed_ecpm": round(float(r.ecpm_w) / m["impressions"], 4) if r.ecpm_w is not None and m["impressions"] else None,
                      "avg_load_latency_s": round(float(r.lat_w) / float(r.lat_req), 3) if r.lat_w is not None and r.lat_req else None})
    sources = (await db.execute(select(func.count(func.distinct(M.ad_source_id))).where(*where(M, p, f)[0]))).scalar() or 0
    names = [i["label"] for i in items] if dimension == "ad_source" else []
    only_admob = sources <= 1 and (not names or all("admob" in (n or "").lower() for n in names))
    return {"dimension": dimension, "items": items, "ignored_filters": ignored,
            "has_mediation": bool(items) and not only_admob}


# ---------------------------------------------------------------------------
# Opciones de filtros y estado
# ---------------------------------------------------------------------------

async def filter_options(db: AsyncSession) -> dict:
    D = AdmobDaily
    since = la_today() - timedelta(days=90)
    apps_ = [{"id": a.app_id, "name": a.name, "platform": a.platform, "is_default": a.app_id in default_app_ids()}
             for a in (await db.execute(select(AdmobApp).order_by(AdmobApp.name))).scalars()]
    units = [{"id": u.ad_unit_id, "name": u.name, "app_id": u.app_id, "format": u.format}
             for u in (await db.execute(select(AdmobAdUnit).order_by(AdmobAdUnit.name))).scalars()]
    async def distinct(col):
        return [r[0] for r in (await db.execute(select(col).where(D.date >= since).group_by(col)
                                                .order_by(func.sum(D.earnings).desc()))).all() if r[0]]
    return {"apps": apps_, "ad_units": units, "platforms": await distinct(D.platform),
            "formats": await distinct(D.format), "countries": await distinct(D.country),
            "default_app_ids": default_app_ids()}


async def sync_status(db: AsyncSession) -> dict:
    from app.services.admob_client import is_configured
    R = AdmobSyncRun
    kinds = {}
    for kind in ("hourly", "refresh", "daily", "initial"):
        last = (await db.execute(select(R).where(R.kind == kind).order_by(R.started_at.desc()).limit(1))).scalar_one_or_none()
        last_ok = (await db.execute(select(R).where(R.kind == kind, R.status == "ok").order_by(R.started_at.desc()).limit(1))).scalar_one_or_none()
        kinds[kind] = {
            "last_run": None if not last else {
                "started_at": last.started_at.isoformat(), "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                "status": last.status, "rows": last.rows, "error": last.error,
                "range": [last.range_from.isoformat() if last.range_from else None, last.range_to.isoformat() if last.range_to else None],
            },
            "last_ok_at": last_ok.finished_at.isoformat() if last_ok and last_ok.finished_at else None,
        }
    latest = (await db.execute(select(R).where(R.status != "running").order_by(R.started_at.desc()).limit(1))).scalar_one_or_none()
    ok_times = [k["last_ok_at"] for k in kinds.values() if k["last_ok_at"]]
    running = (await db.execute(select(R.kind).where(R.status == "running"))).scalars().all()
    acc = (await db.execute(select(AdmobAccount).limit(1))).scalar_one_or_none()
    return {
        "configured": is_configured(),
        "connection": "reconnect" if latest and latest.status == "auth_error" else "ok",
        "updated_at": max(ok_times) if ok_times else None,
        "running": running,
        "report_date_today": la_today().isoformat(),
        "timezone": acc.reporting_time_zone if acc else "America/Los_Angeles",
        "currency": acc.currency if acc else "USD",
        "publisher_id": acc.publisher_id if acc else settings.admob_publisher_id or None,
        "default_app_ids": default_app_ids(),
        "kinds": kinds,
    }


async def versions(db: AsyncSession, dim_type: str, metric: str, p: Period, f: Filters, group_by: str,
                   top: int = 6) -> dict:
    """Serie por versión (las `top` con más impresiones), fecha de salida y uso del último día con datos."""
    V = AdmobDailyVersion
    conds, ignored = where(V, p, f)
    conds.append(V.dimension_type == dim_type)
    tops = [r[0] for r in (await db.execute(select(V.version).where(*conds).group_by(V.version)
                                            .order_by(func.sum(V.impressions).desc()).limit(top))).all()]
    b = bucket(V.date, group_by)
    rows = (await db.execute(select(b, V.version, *_cols(V)).where(*conds, V.version.in_(tops))
                             .group_by(b, V.version))).all()
    acc: dict[str, dict] = {v: {} for v in tops}
    for r in rows:
        acc[r[1]][r[0]] = metrics(r).get(metric)
    buckets = bucket_list(p, group_by)
    fs_conds, _ = where(V, None, f)
    first_seen = {r[0]: r[1].isoformat() for r in (await db.execute(
        select(V.version, func.min(V.date)).where(*fs_conds, V.dimension_type == dim_type, V.impressions > 0,
                                                  V.version.in_(tops)).group_by(V.version))).all()}
    last_day = await db.scalar(select(func.max(V.date)).where(*conds, V.impressions > 0))
    usage = {}
    if last_day:
        ud = (await db.execute(select(V.version, func.sum(V.impressions)).where(*conds, V.date == last_day)
                               .group_by(V.version))).all()
        tot = sum(int(x[1] or 0) for x in ud) or 0
        usage = {x[0]: round(int(x[1] or 0) / tot, 4) for x in ud} if tot else {}
    return {
        "dimension": dim_type, "metric": metric, "ignored_filters": ignored,
        "usage_day": last_day.isoformat() if last_day else None,
        "versions": [{"version": v, "first_seen": first_seen.get(v), "usage_share": usage.get(v, 0.0),
                      "series": [{"date": d.isoformat(), "value": acc[v].get(d)} for d in buckets]} for v in tops],
    }
