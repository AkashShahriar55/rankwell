"""Numbers for charts and reports: rank history, visibility, App Store Connect funnel, ratings, charts.

Every function reads the local database only and returns plain data plus a `display` Markdown block.
Missing data is reported as missing (a message), never as zeros.

Visibility (0-100) for one day, over the app's tracked keywords in one country:

    weight(keyword)  = latest popularity from keyword_scores; 50 when never scored; 0 (never suggested) counts as 5
    bucket(rank)     = #1: 1.0 · #2-3: 0.7 · #4-5: 0.5 · #6-10: 0.3 · #11-20: 0.15 · #21-50: 0.05
                       · #51-200: 0.01 · not ranked: 0
    visibility       = 100 × Σ weight × bucket(rank) / Σ weight

100 means #1 for every tracked keyword. It is an index of search presence, not a download estimate; it moves
when ranks move on keywords that people type more often.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from . import apps, render
from .apps import App
from .db import Database
from .errors import RankwellError
from .util import days_ago, today

Data = dict[str, Any]

NOT_RANKED = 201  # stand-in for "outside the top 200" when sorting
DEFAULT_POPULARITY = 50
MIN_POPULARITY = 5
RANK_BUCKETS = ((1, 1.0), (3, 0.7), (5, 0.5), (10, 0.3), (20, 0.15), (50, 0.05), (200, 0.01))
FUNNEL = (("impressions", "Impressions"), ("page_views", "Product page views"), ("first_downloads", "First downloads"))
MAX_ROWS = 12
SPARK_POINTS = 30
NO_ASC = ("No App Store Connect data yet. Add an App Store Connect key (aso-setup) and let the daily collection "
          "run; Apple's analytics arrive with a 2-3 day delay.")
NO_COLLECTION = ("Nothing collected yet. Run `rankwell collect` (or install the daily job with "
                 "`rankwell schedule install`).")


# ---------------------------------------------------------------- shared helpers

def resolve(db: Database, app: App | str | int) -> App:
    return app if isinstance(app, dict) else apps.get(db, app)


def country_of(app: App, country: str | None = None) -> str:
    return (country or (app.get("countries") or ["us"])[0]).strip().lower()


def names(db: Database, app: App) -> dict[int, str]:
    """Store id → name for the app (when live) and its competitors."""
    out = {int(c["store_id"]): str(c["name"]) for c in apps.competitors(db, app["id"])}
    if app.get("store_id"):
        out[int(app["store_id"])] = str(app["name"])
    return out


def ago(date: str | None, as_of: str | None = None) -> int | None:
    """Whole days from an ISO date (or timestamp) to as_of (default today)."""
    if not date:
        return None
    return (dt.date.fromisoformat(as_of or today()) - dt.date.fromisoformat(date[:10])).days


def windows(days: int, end: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """(current, previous): two back-to-back periods of `days` days, the current one ending on `end`."""
    return (days_ago(days - 1, end), end), (days_ago(2 * days - 1, end), days_ago(days, end))


def compare(current: float | None, previous: float | None) -> Data:
    """Period comparison: both values, the difference and the percent change (None when not computable)."""
    if current is None or previous is None:
        return {"current": current, "previous": previous, "change": None, "change_pct": None}
    pct = round(100 * (current - previous) / previous, 1) if previous else None
    return {"current": current, "previous": previous, "change": round(current - previous, 4), "change_pct": pct}


def clip(rows: list[Data], limit: int = MAX_ROWS) -> tuple[list[Data], str]:
    """The first `limit` rows and a "+N more" note for the rest."""
    extra = len(rows) - limit
    return rows[:limit], (f"\n\n_+{extra} more in the data._" if extra > 0 else "")


def trim(text: str | None, n: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def placeholders(items: list[Any]) -> str:
    return ", ".join("?" for _ in items)


def latest_scores(db: Database, country: str, terms: list[str] | None = None) -> dict[str, Data]:
    """Newest keyword_scores row per term in a country."""
    rows = db.all(
        "SELECT s.* FROM keyword_scores s JOIN (SELECT term, max(date) AS d FROM keyword_scores WHERE country = ? "
        "GROUP BY term) m ON s.term = m.term AND s.date = m.d WHERE s.country = ?", (country, country))
    wanted = set(terms) if terms is not None else None
    return {r["term"]: r for r in rows if wanted is None or r["term"] in wanted}


def move(old: int | None, new: int | None, had_old: bool = True) -> tuple[int | None, str]:
    """(places gained, status). Positive = moved up. Status: up, down, same, new, lost, unranked, no history."""
    if not had_old:
        return None, "no history"
    if old is None and new is None:
        return None, "unranked"
    if old is None:
        return None, "new"
    if new is None:
        return None, "lost"
    d = old - new
    return d, "up" if d > 0 else "down" if d < 0 else "same"


def move_text(change: int | None, status: str) -> str:
    if status == "new":
        return "new in top 200"
    if status == "lost":
        return "out of top 200 ⚠"
    if status in ("unranked", "no history"):
        return "–"
    return render.delta(change)


# ---------------------------------------------------------------- ranks

def rank_history(db: Database, app: App | str | int, country: str | None = None, days: int = 30, *,
                 store_id: int | None = None, as_of: str | None = None) -> Data:
    """Rank series per tracked keyword over `days` days, with current, best, change over the period and 7-day change.

    Changes are places gained (positive = better). The sparkline is inverted: high means close to #1.
    """
    a = resolve(db, app)
    c = country_of(a, country)
    sid = store_id or a.get("store_id")
    base: Data = {"app": a["name"], "country": c, "days": days, "dates": [], "keywords": []}
    terms = [k["term"] for k in apps.keywords(db, a["id"], c)]
    if not sid:
        return {**base, "display": f"{a['name']} is not on the App Store yet, so it has no ranks."}
    if not terms:
        return {**base, "display": f"No keywords tracked for {a['name']} in {c.upper()}. Add some with keywords_add."}
    end = db.value("SELECT max(date) FROM ranks WHERE store_id = ? AND country = ? AND date <= ?",
                   (sid, c, as_of or today()))
    if not end:
        return {**base, "display": f"No ranks collected for {a['name']} in {c.upper()} yet. {NO_COLLECTION}"}
    start = days_ago(days - 1, end)
    week = days_ago(7, end)
    rows = db.all(f"SELECT date, term, rank FROM ranks WHERE store_id = ? AND country = ? AND date BETWEEN ? AND ? "
                  f"AND term IN ({placeholders(terms)})", (sid, c, min(start, week), end, *terms))
    by: dict[str, dict[str, int | None]] = defaultdict(dict)
    for r in rows:
        by[r["term"]][r["date"]] = r["rank"]
    dates = sorted({r["date"] for r in rows if r["date"] >= start})
    scores = latest_scores(db, c, terms)
    out = []
    for t in terms:
        seen = by.get(t, {})
        series = [seen.get(d) for d in dates]
        current = seen.get(end)
        old_dates = [d for d in seen if d <= week]
        old = seen[max(old_dates)] if old_dates else None
        ch7, st7 = move(old, current, bool(old_dates))
        in_window = [d for d in dates if d in seen]
        first = seen[in_window[0]] if in_window else None
        ch, st = move(first, current, bool(in_window))
        ranked = [v for v in series if v is not None]
        s = scores.get(t, {})
        out.append({
            "term": t, "rank": current, "best": min(ranked) if ranked else None, "start": first,
            "change": ch, "status": st, "change_7d": ch7, "status_7d": st7, "rank_7d_ago": old,
            "popularity": s.get("popularity"), "difficulty": s.get("difficulty"),
            "series": series, "spark": render.sparkline(series[-SPARK_POINTS:], invert=True),
        })
    out.sort(key=lambda k: (k["rank"] or NOT_RANKED, -(k["popularity"] or 0), k["term"]))
    ranked_now = sum(k["rank"] is not None for k in out)
    shown, note = clip(out)
    table = render.table(
        [{**k, "rank": render.rank(k["rank"]), "best": render.rank(k["best"]),
          "change_7d": move_text(k["change_7d"], k["status_7d"])} for k in shown],
        ["term", "rank", "change_7d", "best", "spark", "popularity", "difficulty"],
        {"term": "keyword", "change_7d": "7 days", "spark": f"{days}-day trend", "popularity": "pop",
         "difficulty": "diff"}, align_right=["rank", "best", "popularity", "difficulty"])
    head = (f"**{a['name']}** ranks ({c.upper()}, {end}): in the top 200 for {ranked_now} of {len(out)} "
            f"tracked keywords, top 10 for {sum((k['rank'] or NOT_RANKED) <= 10 for k in out)}.")
    return {**base, "end": end, "dates": dates, "keywords": out, "display": f"{head}\n\n{table}{note}"}


# ---------------------------------------------------------------- visibility

def bucket(rank: int | None) -> float:
    if rank is None:
        return 0.0
    for top, weight in RANK_BUCKETS:
        if rank <= top:
            return weight
    return 0.0


def keyword_weight(score: Data | None) -> int:
    pop = (score or {}).get("popularity")
    if pop is None:
        return DEFAULT_POPULARITY
    return max(MIN_POPULARITY, int(pop))


def visibility(db: Database, app: App | str | int, country: str | None = None, days: int = 30, *,
               store_id: int | None = None, as_of: str | None = None) -> Data:
    """Daily visibility score (0-100, formula in the module docstring) and its trend."""
    a = resolve(db, app)
    c = country_of(a, country)
    sid = store_id or a.get("store_id")
    base: Data = {"app": a["name"], "country": c, "days": days, "series": []}
    terms = [k["term"] for k in apps.keywords(db, a["id"], c)]
    if not sid or not terms:
        why = "is not on the App Store yet" if not sid else f"has no tracked keywords in {c.upper()}"
        return {**base, "display": f"No visibility score: {a['name']} {why}."}
    end = db.value("SELECT max(date) FROM ranks WHERE store_id = ? AND country = ? AND date <= ?",
                   (sid, c, as_of or today()))
    if not end:
        return {**base, "display": f"No ranks collected for {a['name']} in {c.upper()} yet. {NO_COLLECTION}"}
    start = days_ago(days - 1, end)
    rows = db.all(f"SELECT date, term, rank FROM ranks WHERE store_id = ? AND country = ? AND date BETWEEN ? AND ? "
                  f"AND term IN ({placeholders(terms)})", (sid, c, start, end, *terms))
    scores = latest_scores(db, c, terms)
    weights = {t: keyword_weight(scores.get(t)) for t in terms}
    total = sum(weights.values())
    by: dict[str, dict[str, int | None]] = defaultdict(dict)
    for r in rows:
        by[r["date"]][r["term"]] = r["rank"]
    series: list[Data] = []
    for d in sorted(by):
        day = by[d]
        points = sum(weights[t] * bucket(r) for t, r in day.items())
        series.append({"date": d, "score": round(100 * points / total, 1),
                       "ranked": sum(r is not None for r in day.values()),
                       "top10": sum(r is not None and r <= 10 for r in day.values())})
    first, last = series[0], series[-1]
    week = [s for s in series if s["date"] <= days_ago(7, end)]
    trend = compare(last["score"], first["score"])
    out = {**base, "end": end, "series": series, "score": last["score"], "trend": trend,
           "change_7d": compare(last["score"], week[-1]["score"]) if week else None,
           "weights": weights,
           "formula": "100 × Σ popularity × bucket(rank) / Σ popularity over tracked keywords"}
    spark = render.sparkline([s["score"] for s in series][-SPARK_POINTS:])
    out["display"] = (
        f"**Visibility** {a['name']} ({c.upper()}): **{last['score']}** / 100 on {end} "
        f"({render.delta(trend['change'])} since {first['date']})\n\n`{spark}`\n\n"
        f"In the top 200 for {last['ranked']} of {len(terms)} keywords, top 10 for {last['top10']}. "
        f"100 = #1 for every tracked keyword, weighted by popularity.")
    return out


# ---------------------------------------------------------------- App Store Connect funnel

def _asc_totals(db: Database, sid: int, start: str, end: str) -> tuple[Data, dict[str, Data], int]:
    """(totals per metric, per-source totals, days with data). Totals use source 'all' when present."""
    rows = db.all("SELECT source, metric, sum(value) AS v FROM asc_daily WHERE store_id = ? AND date BETWEEN ? AND ? "
                  "GROUP BY source, metric", (sid, start, end))
    n = int(db.value("SELECT count(DISTINCT date) FROM asc_daily WHERE store_id = ? AND date BETWEEN ? AND ?",
                     (sid, start, end)) or 0)
    sources: dict[str, Data] = defaultdict(dict)
    for r in rows:
        sources[r["source"]][r["metric"]] = int(r["v"] or 0)
    totals: Data = dict(sources.get("all", {}))
    for src, metrics in sources.items():
        if src == "all":
            continue
        for m, v in metrics.items():
            if m not in sources.get("all", {}):
                totals[m] = totals.get(m, 0) + v
    return totals, {k: v for k, v in sources.items() if k != "all"}, n


def rates(t: Data) -> Data:
    """Step conversions in percent: page views / impressions, downloads / page views, downloads / impressions."""
    def pct(a: str, b: str) -> float | None:
        return round(100 * t[a] / t[b], 2) if t.get(a) is not None and t.get(b) else None
    return {"page_view_rate": pct("page_views", "impressions"), "download_rate": pct("first_downloads", "page_views"),
            "conversion": pct("first_downloads", "impressions")}


def _fmt(key: str, v: float | None) -> str:
    if key.endswith("rate") or key == "conversion":
        return "–" if v is None else f"{v:.2f}%"
    return render.number(v)


def funnel(db: Database, app: App | str | int, days: int = 30, *, as_of: str | None = None) -> Data:
    """Impressions → page views → first downloads over `days` days vs the previous `days` days, by source.

    The period ends on the newest day with App Store Connect data (Apple reports lag 2-3 days).
    """
    a = resolve(db, app)
    sid = a.get("store_id")
    base: Data = {"app": a["name"], "days": days, "available": False}
    if not sid:
        return {**base, "message": "Not on the App Store yet.", "display": f"{a['name']} is not on the App Store yet."}
    end = db.value("SELECT max(date) FROM asc_daily WHERE store_id = ? AND date <= ?", (sid, as_of or today()))
    if not end:
        return {**base, "message": NO_ASC, "display": f"**{a['name']}**: {NO_ASC}"}
    (start, _), (pstart, pend) = windows(days, end)
    cur, sources, n = _asc_totals(db, sid, start, end)
    prev, _, pn = _asc_totals(db, sid, pstart, pend)
    cur.update(rates(cur))
    has_prev = pn > 0
    if has_prev:
        prev.update(rates(prev))
    keys = [m for m, _ in FUNNEL] + ["redownloads", "page_view_rate", "download_rate", "conversion"]
    changes = {k: compare(cur.get(k), prev.get(k) if has_prev else None) for k in keys}
    by_source = sorted(({"source": s, **m, **rates(m)} for s, m in sources.items()),
                       key=lambda r: -(r.get("impressions") or 0))
    has_all = db.value("SELECT 1 FROM asc_daily WHERE store_id = ? AND metric = 'first_downloads' AND source = 'all' "
                       "LIMIT 1", (sid,))
    only_all = "AND source = 'all'" if has_all else ""
    daily = db.all("SELECT date, sum(value) AS v FROM asc_daily WHERE store_id = ? AND metric = 'first_downloads' "
                   f"AND date BETWEEN ? AND ? {only_all} GROUP BY date ORDER BY date", (sid, start, end))
    out = {**base, "available": True, "period": {"from": start, "to": end, "days_with_data": n},
           "previous_period": {"from": pstart, "to": pend, "days_with_data": pn} if has_prev else None,
           "current": cur, "previous": prev if has_prev else None, "changes": changes, "by_source": by_source,
           "daily_first_downloads": [{"date": r["date"], "value": int(r["v"] or 0)} for r in daily]}
    steps = [(label, float(cur.get(m) or 0)) for m, label in FUNNEL if cur.get(m) is not None]
    lines = [f"**App Store Connect funnel** {a['name']}: {start} → {end} ({n} of {days} days with data)"]
    if steps:
        lines.append(render.funnel(steps))
    rows = []
    for k in keys:
        ch = changes[k]
        if ch["current"] is None and ch["previous"] is None:
            continue
        rows.append({"metric": k.replace("_", " "), "now": _fmt(k, ch["current"]), "before": _fmt(k, ch["previous"]),
                     "change": render.delta(ch["change_pct"], pct=True) if ch["change_pct"] is not None else "–"})
    lines.append(render.table(rows, ["metric", "now", "before", "change"],
                              {"before": f"previous {days} days"}, align_right=["now", "before", "change"]))
    if not has_prev:
        lines.append(f"_No App Store Connect data for the {days} days before {start}, so no comparison._")
    if by_source:
        src, note = clip(by_source, 8)
        lines.append("By source:\n\n" + render.table(
            [{**r, "impressions": render.number(r.get("impressions")), "page_views": render.number(r.get("page_views")),
              "first_downloads": render.number(r.get("first_downloads")),
              "conversion": f"{r['conversion']:.2f}%" if r["conversion"] is not None else "–"} for r in src],
            ["source", "impressions", "page_views", "first_downloads", "conversion"],
            {"page_views": "page views", "first_downloads": "downloads"},
            align_right=["impressions", "page_views", "first_downloads", "conversion"]) + note)
    if daily:
        spark = render.sparkline([float(r["v"] or 0) for r in daily][-SPARK_POINTS:])
        lines.append(f"Daily first downloads: `{spark}`")
    out["display"] = "\n\n".join(lines)
    return out


# ---------------------------------------------------------------- ratings

def ratings(db: Database, app: App | str | int, country: str | None = None, days: int = 90, *,
            store_id: int | None = None, as_of: str | None = None) -> Data:
    """Rating count and average from daily listing snapshots, with the change over the period."""
    a = resolve(db, app)
    c = country_of(a, country)
    sid = store_id or a.get("store_id")
    base: Data = {"app": a["name"], "country": c, "days": days, "series": []}
    if not sid:
        return {**base, "display": f"{a['name']} is not on the App Store yet, so it has no ratings."}
    end = as_of or today()
    rows = db.all("SELECT date, rating_count, rating_avg FROM snapshots WHERE store_id = ? AND country = ? "
                  "AND date BETWEEN ? AND ? ORDER BY date", (sid, c, days_ago(days - 1, end), end))
    rows = [r for r in rows if r["rating_count"] is not None and r["rating_avg"] is not None]
    if not rows:
        return {**base, "display": f"No listing snapshots for {a['name']} in {c.upper()} yet. {NO_COLLECTION}"}
    first, last = rows[0], rows[-1]
    span = max(1, ago(first["date"], last["date"]) or 0)
    count = compare(last["rating_count"], first["rating_count"])
    avg = compare(last["rating_avg"], first["rating_avg"])
    per_day = round((count["change"] or 0) / span, 2) if len(rows) > 1 else None
    out = {**base, "series": rows, "from": first["date"], "to": last["date"], "count": count, "average": avg,
           "new_per_day": per_day}
    avg_ch = avg["change"]
    out["display"] = (
        f"**Ratings** {a['name']} ({c.upper()}): **{render.number(last['rating_count'])}** ratings, "
        f"**{last['rating_avg']:.2f}★** on {last['date']}\n\n"
        f"Since {first['date']}: {render.delta(count['change'])} ratings"
        + (f" (~{per_day}/day)" if per_day is not None else "")
        + (f", average {avg_ch:+.2f}" if avg_ch else ", average unchanged") + "\n\n"
        f"Count `{render.sparkline([float(r['rating_count']) for r in rows][-SPARK_POINTS:])}`  "
        f"Average `{render.sparkline([r['rating_avg'] for r in rows][-SPARK_POINTS:])}`")
    return out


# ---------------------------------------------------------------- charts

def charts(db: Database, app: App | str | int, country: str | None = None, days: int = 30, *,
           store_id: int | None = None, as_of: str | None = None) -> Data:
    """Category chart positions (top 100 free/paid/grossing) over `days` days; a gap means not in the chart."""
    a = resolve(db, app)
    c = country_of(a, country)
    sid = store_id or a.get("store_id")
    base: Data = {"app": a["name"], "country": c, "days": days, "charts": []}
    if not sid:
        return {**base, "display": f"{a['name']} is not on the App Store yet, so it has no chart positions."}
    end = as_of or today()
    start = days_ago(days - 1, end)
    collected = db.all("SELECT DISTINCT genre_id, chart, date FROM chart_positions WHERE country = ? "
                       "AND date BETWEEN ? AND ? ORDER BY date", (c, start, end))
    if not collected:
        return {**base, "display": f"No category charts collected in {c.upper()} in the last {days} days. "
                                   f"{NO_COLLECTION}"}
    rows = db.all("SELECT date, genre_id, chart, rank FROM chart_positions WHERE store_id = ? AND country = ? "
                  "AND date BETWEEN ? AND ?", (sid, c, start, end))
    mine: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for r in rows:
        mine[(r["genre_id"], r["chart"])][r["date"]] = r["rank"]
    dates: dict[tuple[int, str], list[str]] = defaultdict(list)
    for r in collected:
        dates[(r["genre_id"], r["chart"])].append(r["date"])
    out: list[Data] = []
    for key, seen in mine.items():
        ds = dates[key]
        series: list[int | None] = [seen.get(d) for d in ds]
        ranked = [v for v in series if v is not None]
        current = seen.get(ds[-1])
        out.append({"genre_id": key[0], "chart": key[1], "rank": current, "best": min(ranked),
                    "days_in_chart": len(ranked), "days_collected": len(ds), "last_date": ds[-1],
                    "series": series, "spark": render.sparkline(series[-SPARK_POINTS:], invert=True)})
    out.sort(key=lambda r: (r["rank"] or NOT_RANKED, r["best"]))
    if not out:
        return {**base, "display": f"{a['name']} was not in any collected category chart (top 100) in "
                                   f"{c.upper()} in the last {days} days."}
    table = render.table([{**r, "rank": render.rank(r["rank"]) if r["rank"] else "out",
                           "best": render.rank(r["best"]), "days": f"{r['days_in_chart']}/{r['days_collected']}"}
                          for r in out], ["chart", "genre_id", "rank", "best", "days", "spark"],
                         {"genre_id": "category", "days": "days in chart", "spark": "trend"},
                         align_right=["rank", "best", "days"])
    return {**base, "charts": out, "display": f"**Category charts** {a['name']} ({c.upper()})\n\n{table}"}


METRICS = ("ranks", "visibility", "funnel", "ratings", "charts")


def metric(db: Database, app: App | str | int, name: str, days: int = 30, country: str | None = None,
           as_of: str | None = None) -> Data:
    """Dispatch one of METRICS."""
    if name == "ranks":
        return rank_history(db, app, country, days, as_of=as_of)
    if name == "visibility":
        return visibility(db, app, country, days, as_of=as_of)
    if name == "funnel":
        return funnel(db, app, days, as_of=as_of)
    if name == "ratings":
        return ratings(db, app, country, days, as_of=as_of)
    if name == "charts":
        return charts(db, app, country, days, as_of=as_of)
    raise RankwellError(f"Unknown metric '{name}'. Use one of: {', '.join(METRICS)}.")
