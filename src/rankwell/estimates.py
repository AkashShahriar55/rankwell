"""Download estimates from category chart positions, calibrated on the user's own App Store Connect numbers.

Training data: days where one of the user's apps had a top-100 position in a free or paid category chart and
App Store Connect first-time downloads in that chart's country (territory from the raw report rows). Fit:

    ln(downloads) = ln(a) + b × ln(rank)          least squares, per chart / genre / country

A chart needs MIN_FIT days over MIN_RANKS different ranks for its own slope. With fewer days (or a slope that
does not fall with rank) the slope is fixed at ASSUMED_SLOPE and only the level is fitted. A pooled 'global'
curve over every point is the fallback for charts without their own, with a wider range. Below MIN_POINTS
days there is no estimate, only a note saying how much data exists.

The range is the 80% band of ln(downloads) (± Z × sigma) with floors on sigma, wider again when the rank lies
far outside the ranks the curve was fitted on. Every output is labelled an estimate; never show it as a number
the store reported.
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import apps, render, stats
from .db import Database
from .errors import RankwellError
from .stats import Data
from .util import days_ago, today

MIN_POINTS = 3
MIN_FIT = 5
MIN_RANKS = 3
ASSUMED_SLOPE = -0.9  # downloads ∝ rank^-0.9: the usual shape of published App Store chart studies (assumption)
MIN_SIGMA_FIT = 0.5
MIN_SIGMA_FIXED = 1.0
GLOBAL_WIDEN = 1.5
OUTSIDE_WIDEN = 1.5
Z = 1.28  # 80% band
CHARTS = ("free", "paid")  # grossing ranks follow revenue, not downloads
RECENT_DAYS = 7
LABEL = "estimate"


# ---------------------------------------------------------------- training data

def own_first_downloads(db: Database, store_id: int) -> dict[tuple[str, str], float]:
    """{(date, country): first-time downloads} for one app, from the raw App Store Connect download rows."""
    rows = db.all(
        "SELECT report_date AS d, lower(json_extract(data, '$.territory')) AS c, "
        "sum(CAST(json_extract(data, '$.counts') AS REAL)) AS n FROM asc_report_rows "
        "WHERE store_id = ? AND report_name = 'App Downloads Standard' "
        "AND lower(json_extract(data, '$.\"download type\"')) LIKE '%first%' GROUP BY 1, 2", (store_id,))
    return {(str(r["d"]), str(r["c"])): float(r["n"] or 0) for r in rows if r["c"]}


def training_points(db: Database) -> list[Data]:
    """Days with both a free/paid chart position and first downloads in that country, for every own app."""
    out: list[Data] = []
    for a in apps.list_apps(db, include_archived=True):
        if not a.get("store_id"):
            continue
        sid = int(a["store_id"])
        downloads = own_first_downloads(db, sid)
        if not downloads:
            continue
        for p in db.all(f"SELECT date, country, genre_id, chart, rank FROM chart_positions WHERE store_id = ? "
                        f"AND chart IN ({stats.placeholders(list(CHARTS))})", (sid, *CHARTS)):
            n = downloads.get((p["date"], p["country"]))
            if n and n > 0:
                out.append({**p, "store_id": sid, "downloads": n})
    return out


def data_status(db: Database) -> Data:
    """How much calibration data exists: days of App Store Connect downloads, days charted, usable points."""
    own = [int(a["store_id"]) for a in apps.list_apps(db, include_archived=True) if a.get("store_id")]
    marks = stats.placeholders(own) if own else "NULL"
    asc_days = int(db.value(f"SELECT count(DISTINCT store_id || date) FROM asc_daily WHERE metric = 'first_downloads' "
                            f"AND source = 'all' AND store_id IN ({marks})", own) or 0)
    charted = int(db.value(f"SELECT count(DISTINCT store_id || date || country) FROM chart_positions "
                           f"WHERE chart IN ('free', 'paid') AND store_id IN ({marks})", own) or 0)
    points = len(training_points(db))
    return {"own_apps_live": len(own), "asc_download_days": asc_days, "own_chart_days": charted, "points": points,
            "needed": MIN_POINTS, "needed_for_slope": MIN_FIT}


def needs_more(status: Data) -> str:
    return (f"Download estimates need more data: your apps have {status['points']} days with both a top-100 "
            f"category chart position and App Store Connect downloads in that country (need {status['needed']}; "
            f"{status['needed_for_slope']}+ on one chart for its own curve). So far: "
            f"{status['asc_download_days']} app-days of App Store Connect downloads, {status['own_chart_days']} "
            "app-days in a free or paid chart (each country counted). Estimates start once one of your apps charts.")


# ---------------------------------------------------------------- fitting

def fit(points: list[tuple[float, float]], slope: float | None = None) -> Data | None:
    """Power law through (rank, downloads) points in log-log space. None below MIN_POINTS usable points.

    Fits the slope when there are MIN_FIT+ points over MIN_RANKS+ ranks and the slope comes out negative;
    otherwise (or when `slope` is given) keeps the slope fixed and fits only the level.
    """
    pts = [(math.log(r), math.log(d)) for r, d in points if r >= 1 and d > 0]
    n = len(pts)
    if n < MIN_POINTS:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    ranks = {round(math.exp(x)) for x in xs}
    b: float | None = None
    if slope is None and n >= MIN_FIT and len(ranks) >= MIN_RANKS and var > 0:
        b = sum((x - mx) * (y - my) for x, y in pts) / var
        if b >= 0:  # more downloads at a worse rank: noise, not a curve
            b = None
    fixed = b is None
    if b is None:
        b = slope if slope is not None else ASSUMED_SLOPE
    ln_a = my - b * mx
    sse = sum((y - ln_a - b * x) ** 2 for x, y in pts)
    dof = n - (1 if fixed else 2)
    sigma = math.sqrt(sse / dof) if dof > 0 else 0.0
    sigma = max(sigma, MIN_SIGMA_FIXED if fixed else MIN_SIGMA_FIT)
    return {"a": math.exp(ln_a), "b": b, "slope_fixed": fixed, "sigma": sigma, "points": n,
            "rank_min": min(ranks), "rank_max": max(ranks)}


def fit_all(db: Database, on: str | None = None) -> Data:
    """Refit every curve from the current training data and replace the download_curves table."""
    day = on or today()
    points = training_points(db)
    groups: dict[tuple[str, int, str], list[Data]] = defaultdict(list)
    for p in points:
        groups[(p["chart"], int(p["genre_id"]), p["country"])].append(p)
    rows: list[Data] = []
    for (chart, genre, country), pts in sorted(groups.items()):
        c = fit([(p["rank"], p["downloads"]) for p in pts])
        if c:
            rows.append(_row(f"{chart}/{genre}/{country}", chart, genre, country, c, pts, day))
    pooled = fit([(p["rank"], p["downloads"]) for p in points])
    if pooled:
        pooled["sigma"] *= GLOBAL_WIDEN
        rows.append(_row("global", None, None, None, pooled, points, day))
    with db.tx():
        db.execute("DELETE FROM download_curves")
        db.upsert("download_curves", rows, ["key"])
    return {"points": len(points), "curves": len(rows), "fitted_on": day}


def _row(key: str, chart: str | None, genre: int | None, country: str | None, c: Data, pts: list[Data],
         day: str) -> Data:
    dates = sorted(p["date"] for p in pts)
    return {"key": key, "chart": chart, "genre_id": genre, "country": country, "a": c["a"], "b": c["b"],
            "slope_fixed": int(c["slope_fixed"]), "sigma": c["sigma"], "points": c["points"],
            "rank_min": c["rank_min"], "rank_max": c["rank_max"], "date_min": dates[0], "date_max": dates[-1],
            "fitted_on": day}


def curves(db: Database, on: str | None = None) -> list[Data]:
    """Stored curves, refitted first when they were not fitted today (fitting reads only the local database)."""
    day = on or today()
    last = db.value("SELECT max(fitted_on) FROM download_curves")
    if last != day:
        fit_all(db, day)
    return db.all("SELECT * FROM download_curves ORDER BY key")


def predict(curve: Data, rank: int) -> Data:
    """Estimate and 80% range for one rank from a stored curve."""
    sigma = float(curve["sigma"])
    outside = rank < (curve["rank_min"] or 1) / 2 or rank > (curve["rank_max"] or 100) * 2
    if outside:
        sigma *= OUTSIDE_WIDEN
    mid = math.log(float(curve["a"])) + float(curve["b"]) * math.log(rank)
    return {"estimate": math.exp(mid), "low": math.exp(mid - Z * sigma), "high": math.exp(mid + Z * sigma),
            "label": LABEL, "curve": curve["key"], "points": curve["points"], "slope_fixed": bool(curve["slope_fixed"]),
            "extrapolated": outside, "basis": basis(curve, outside)}


def basis(curve: Data, outside: bool = False) -> str:
    where = "all your charted days pooled" if curve["key"] == "global" else f"{curve['chart']} chart {curve['key']}"
    slope = f"assumed slope {curve['b']:.2f}" if curve["slope_fixed"] else f"fitted slope {curve['b']:.2f}"
    far = "; rank outside the fitted ranks" if outside else ""
    return (f"{curve['points']} days of your own downloads vs rank ({where}, ranks {curve['rank_min']}-"
            f"{curve['rank_max']}, {slope}){far}")


def pick(found: list[Data], chart: str, genre: int | None, country: str) -> Data | None:
    """The curve for a chart position: the exact chart/genre/country, else the pooled one."""
    by_key = {c["key"]: c for c in found}
    return by_key.get(f"{chart}/{genre}/{country}") or by_key.get("global")


def text(e: Data) -> str:
    """'~120/day (40-360)' for display; always read with the word estimate next to it."""
    return f"~{render.number(round(e['estimate']))}/day ({render.number(round(e['low']))}-" \
           f"{render.number(round(e['high']))})"


# ---------------------------------------------------------------- lookups

def for_ids(db: Database, ids: list[int], country: str, on: str | None = None) -> dict[int, Data]:
    """{store_id: {chart, rank, date, estimate}} for the ids with a free/paid chart position on the newest charted
    day of the last week (best rank across charts). estimate is None when there is no curve yet."""
    now = on or today()
    if not ids:
        return {}
    last = db.value("SELECT max(date) FROM chart_positions WHERE country = ? AND chart IN ('free', 'paid') "
                    "AND date BETWEEN ? AND ?", (country, days_ago(RECENT_DAYS, now), now))
    if not last:
        return {}
    rows = db.all(f"SELECT store_id, chart, genre_id, rank FROM chart_positions WHERE country = ? AND date = ? "
                  f"AND chart IN ('free', 'paid') AND store_id IN ({stats.placeholders(ids)}) ORDER BY rank",
                  (country, last, *ids))
    found = curves(db, now)
    out: dict[int, Data] = {}
    for r in rows:
        sid = int(r["store_id"])
        if sid in out:
            continue
        c = pick(found, r["chart"], r["genre_id"], country)
        out[sid] = {"chart": r["chart"], "rank": r["rank"], "date": last,
                    "estimate": predict(c, int(r["rank"])) if c else None}
    return out


def _target(db: Database, ref: str | int) -> tuple[int, str]:
    """(store id, name) for one of the user's apps or any tracked competitor (name or store id)."""
    s = str(ref).strip()
    try:
        a = apps.get(db, s)
    except RankwellError:
        a = None
    if a and a.get("store_id"):
        return int(a["store_id"]), str(a["name"])
    if a:
        raise RankwellError(f"{a['name']} is not on the App Store yet, so it has no chart position to estimate from.")
    row = (db.one("SELECT store_id, name FROM competitors WHERE store_id = ? LIMIT 1", (int(s),)) if s.isdigit()
           else db.one("SELECT store_id, name FROM competitors WHERE lower(name) = lower(?) LIMIT 1", (s,))
           or db.one("SELECT store_id, name FROM competitors WHERE lower(name) LIKE lower(?) LIMIT 1", (f"%{s}%",)))
    if row:
        return int(row["store_id"]), str(row["name"])
    if s.isdigit():
        return int(s), s
    raise RankwellError(f"No app or tracked competitor matches '{ref}'.")


def download_estimate(db: Database, ref: str | int, country: str | None = None, *, as_of: str | None = None) -> Data:
    """Estimated daily downloads of an app or competitor from its newest free/paid chart positions."""
    now = as_of or today()
    sid, name = _target(db, ref)
    where, params = "store_id = ? AND chart IN ('free', 'paid') AND date BETWEEN ? AND ?", [
        sid, days_ago(RECENT_DAYS, now), now]
    if country:
        where += " AND country = ?"
        params.append(country.lower())
    rows = db.all(f"SELECT p.* FROM chart_positions p JOIN (SELECT country, chart, max(date) AS d FROM chart_positions "
                  f"WHERE {where} GROUP BY country, chart) m ON p.country = m.country AND p.chart = m.chart "
                  f"AND p.date = m.d WHERE p.store_id = ? ORDER BY p.rank", (*params, sid))
    status = data_status(db)
    base: Data = {"store_id": sid, "name": name, "label": LABEL, "data": status}
    if not rows:
        return {**base, "estimates": [], "display": f"**{name}** has no free or paid category chart position in the "
                f"last {RECENT_DAYS} days{' in ' + country.upper() if country else ''}, so there is nothing to "
                "estimate from (charts list the top 100 only)."}
    found = curves(db, now)
    out: list[Data] = []
    for r in rows:
        c = pick(found, r["chart"], r["genre_id"], r["country"])
        e = predict(c, int(r["rank"])) if c else None
        out.append({"country": r["country"], "chart": r["chart"], "genre_id": r["genre_id"], "rank": r["rank"],
                    "date": r["date"], "estimate": e})
    if not found:
        return {**base, "estimates": out, "display": f"**{name}**: " + ", ".join(
            f"{render.rank(r['rank'])} {r['chart']} {r['country'].upper()}" for r in out) + f"\n\n{needs_more(status)}"}
    shown = [{"country": r["country"].upper(), "chart": r["chart"], "rank": render.rank(r["rank"]), "date": r["date"],
              "est": text(r["estimate"]) if r["estimate"] else "–",
              "basis": r["estimate"]["basis"] if r["estimate"] else "–"} for r in out]
    return {**base, "estimates": out, "display": f"**{name}**: estimated daily downloads from chart rank "
            "(estimate, not App Store numbers; range is an 80% band)\n\n" + render.table(
                shown, ["country", "chart", "rank", "date", "est", "basis"],
                {"est": "estimate (range)"}, align_right=["rank"])}
