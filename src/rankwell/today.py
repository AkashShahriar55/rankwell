"""What changed and what to do now: a briefing per app and an overview of all apps.

The "what to do" list comes from simple rules, checked in this order (at most five items, each with why):
stale or missing data, missing keywords or competitors, low reviews to answer, a big rank drop, downloads
down week over week, a listing left unchanged while the app ranks for few keywords, a competitor's new
title or subtitle, and App Store Connect not connected.
"""

from __future__ import annotations

import json
from typing import Any

from . import apps, render, stats
from .apps import App
from .db import Database
from .stats import Data, ago, clip, trim
from .util import days_ago, today

STALE_DAYS = 2
LISTING_FIELDS = ("title", "subtitle", "description", "screenshots", "version")
LISTING_NUDGE_DAYS = 30
FEW_KEYWORDS_SHARE = 1 / 3  # "ranks for few keywords": top 50 for less than a third of tracked keywords
BIG_DROP = 5
DOWNLOAD_DROP_PCT = -20.0
RATING_FIELDS = ("rating_count", "rating_avg")
MAX_TODO = 5


# ---------------------------------------------------------------- competitor changes

def _count_list(raw: str | None) -> int | None:
    if not raw:
        return 0 if raw == "" else None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return len(v) if isinstance(v, list) else None


def _price(raw: str | None) -> str:
    try:
        p = float(raw or 0)
    except ValueError:
        return str(raw)
    return "free" if p == 0 else f"{p:g}"


def change_sentence(name: str, field: str, old: str | None, new: str | None) -> str:
    """One readable sentence for a `changes` row."""
    if field in ("title", "subtitle"):
        return f"{name} changed its {field}: “{trim(old, 60) or '(none)'}” → “{trim(new, 60) or '(none)'}”"
    if field == "version":
        return f"{name} shipped version {new}" + (f" (was {old})" if old else "")
    if field == "description":
        return f"{name} rewrote its description ({len(old or '')} → {len(new or '')} characters)"
    if field == "screenshots":
        a, b = _count_list(old), _count_list(new)
        counts = f" ({a} → {b})" if a is not None and b is not None and a != b else ""
        return f"{name} updated its screenshots{counts}"
    if field == "price":
        return f"{name} changed its price from {_price(old)} to {_price(new)}"
    if field == "rating_count":
        return f"{name} went from {old} to {new} ratings"
    if field == "rating_avg":
        return f"{name}'s average rating moved from {old} to {new}"
    return f"{name} changed its {field.replace('_', ' ')}" + (f" to “{trim(new, 60)}”" if new else "")


def competitor_changes(db: Database, app: App | str | int, days: int = 14, *, country: str | None = None,
                       include_ratings: bool = False, as_of: str | None = None) -> Data:
    """Listing changes of the app's competitors in the last `days` days, newest first.

    The same change seen in several storefronts is listed once with all its countries.
    """
    a = stats.resolve(db, app)
    comp = stats.names(db, a)
    if a.get("store_id"):
        comp.pop(int(a["store_id"]), None)
    base: Data = {"app": a["name"], "days": days, "changes": []}
    if not comp:
        return {**base, "display": f"{a['name']} has no competitors tracked. Add some with competitors_add."}
    countries = [country.lower()] if country else (a.get("countries") or ["us"])
    end = as_of or today()
    ids = list(comp)
    rows = db.all(f"SELECT * FROM changes WHERE store_id IN ({stats.placeholders(ids)}) "
                  f"AND country IN ({stats.placeholders(countries)}) AND date BETWEEN ? AND ? "
                  "ORDER BY date DESC, id DESC", (*ids, *countries, days_ago(days, end), end))
    merged: dict[tuple[Any, ...], Data] = {}
    for r in rows:
        if r["field"] in RATING_FIELDS and not include_ratings:
            continue
        key = (r["store_id"], r["date"], r["field"], r["new"])
        if key in merged:
            merged[key]["countries"].append(r["country"])
            continue
        name = comp.get(int(r["store_id"]), str(r["store_id"]))
        merged[key] = {"date": r["date"], "store_id": r["store_id"], "app": name, "field": r["field"],
                       "countries": [r["country"]], "sentence": change_sentence(name, r["field"], r["old"], r["new"]),
                       "old": trim(r["old"], 300), "new": trim(r["new"], 300)}
    out = list(merged.values())
    for ch in out:
        ch["countries"].sort(key=lambda x: countries.index(x) if x in countries else len(countries))
    if not out:
        return {**base, "display": f"No competitor listing changes in the last {days} days."}
    shown, note = clip(out)
    multi = len(countries) > 1
    lines = [f"- {c['date']}: {c['sentence']}" + (f" ({', '.join(x.upper() for x in c['countries'])})" if multi else "")
             for c in shown]
    return {**base, "changes": out,
            "display": f"**Competitor changes** (last {days} days)\n\n" + "\n".join(lines) + note}


# ---------------------------------------------------------------- reviews

def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def reviews(db: Database, app: App | str | int, max_rating: int = 5, days: int = 30, *, competitors: bool = False,
            country: str | None = None, as_of: str | None = None) -> Data:
    """Reviews of the app (or of its competitors) from the last `days` days with at most `max_rating` stars."""
    a = stats.resolve(db, app)
    named = stats.names(db, a)
    own = int(a["store_id"]) if a.get("store_id") else None
    ids = [i for i in named if i != own] if competitors else ([own] if own else [])
    who = "its competitors" if competitors else a["name"]
    base: Data = {"app": a["name"], "competitors": competitors, "days": days, "reviews": [], "count": 0}
    if not ids:
        why = "has no competitors tracked" if competitors else "is not on the App Store yet"
        return {**base, "display": f"No reviews: {a['name']} {why}."}
    end = as_of or today()
    params: list[Any] = [*ids, max_rating, days_ago(days, end), end]
    where = f"store_id IN ({stats.placeholders(ids)}) AND rating <= ? AND substr(reviewed_at, 1, 10) BETWEEN ? AND ?"
    if country:
        where += " AND country = ?"
        params.append(country.lower())
    rows = db.all(f"SELECT * FROM reviews WHERE {where} ORDER BY reviewed_at DESC", params)
    out = [{"id": r["id"], "date": (r["reviewed_at"] or "")[:10], "app": named.get(int(r["store_id"])),
            "store_id": r["store_id"], "country": r["country"], "rating": r["rating"], "title": r["title"],
            "content": r["content"], "version": r["version"], "author": r["author"]} for r in rows]
    if not out:
        stars_txt = "" if max_rating >= 5 else f" of {max_rating}★ or less"
        return {**base, "display": f"No reviews{stars_txt} for {who} in the last {days} days."}
    by_star = {str(s): sum(r["rating"] == s for r in out) for s in range(1, 6)}
    shown, note = clip(out)
    cols = ["date", "app", "rating", "title", "text"] if competitors else ["date", "rating", "title", "text", "version"]
    table = render.table([{**r, "rating": stars(int(r["rating"])), "title": trim(r["title"], 40),
                           "text": trim(r["content"], 120)} for r in shown], cols)
    head = (f"**{len(out)} reviews** for {who} in the last {days} days"
            + ("" if max_rating >= 5 else f" (≤{max_rating}★)") + " · "
            + " ".join(f"{s}★ {by_star[str(s)]}" for s in range(5, 0, -1) if by_star[str(s)]))
    return {**base, "reviews": out, "count": len(out), "by_star": by_star, "display": f"{head}\n\n{table}{note}"}


# ---------------------------------------------------------------- freshness and listing age

def freshness(db: Database, app: App, country: str, as_of: str | None = None) -> Data:
    """Newest snapshot and rank dates for the app's tracked store apps; stale after STALE_DAYS days."""
    ids = apps.tracked_store_ids(db, app)
    if not ids:
        return {"last_snapshot": None, "last_rank": None, "age_days": None, "stale": False, "never": True}
    ph = stats.placeholders(ids)
    snap = db.value(f"SELECT max(date) FROM snapshots WHERE store_id IN ({ph}) AND country = ?", (*ids, country))
    rank = db.value(f"SELECT max(date) FROM ranks WHERE store_id IN ({ph}) AND country = ?", (*ids, country))
    last = max([d for d in (snap, rank) if d], default=None)
    age = ago(last, as_of)
    return {"last_snapshot": snap, "last_rank": rank, "age_days": age, "never": last is None,
            "stale": age is not None and age > STALE_DAYS}


def listing_age(db: Database, app: App, country: str, as_of: str | None = None) -> Data:
    """Days since the own listing last changed (title, subtitle, description, screenshots or version).

    When no change was seen since tracking began, the age is a lower bound from the first snapshot.
    """
    sid = app.get("store_id")
    if not sid:
        return {"days": None, "last_change": None, "lower_bound": False}
    row = db.one(f"SELECT date, field FROM changes WHERE store_id = ? AND country = ? "
                 f"AND field IN ({stats.placeholders(list(LISTING_FIELDS))}) ORDER BY date DESC LIMIT 1",
                 (sid, country, *LISTING_FIELDS))
    if row:
        return {"days": ago(row["date"], as_of), "last_change": row["date"], "field": row["field"],
                "lower_bound": False}
    first = db.value("SELECT min(date) FROM snapshots WHERE store_id = ? AND country = ?", (sid, country))
    return {"days": ago(first, as_of), "last_change": None, "tracked_since": first, "lower_bound": first is not None}


# ---------------------------------------------------------------- briefing

def _movers(ranks: Data) -> Data:
    """Keywords that moved in 7 days, biggest visibility impact first (popularity × change in rank bucket)."""
    kws = ranks.get("keywords") or []
    for k in kws:
        k["impact"] = round(stats.keyword_weight({"popularity": k["popularity"]})
                            * (stats.bucket(k["rank"]) - stats.bucket(k["rank_7d_ago"])), 2)
    up = sorted((k for k in kws if k["status_7d"] in ("up", "new")), key=lambda k: (-k["impact"], k["term"]))
    down = sorted((k for k in kws if k["status_7d"] in ("down", "lost")), key=lambda k: (k["impact"], k["term"]))
    slim = ("term", "rank", "rank_7d_ago", "change_7d", "status_7d", "popularity", "impact")
    return {"up": [{f: k[f] for f in slim} for k in up], "down": [{f: k[f] for f in slim} for k in down]}


def _mover_line(k: Data) -> str:
    return f"“{k['term']}” {stats.move_text(k['change_7d'], k['status_7d'])}" + (
        f" → {render.rank(k['rank'])}" if k["rank"] else "")


def _keyword_plan(db: Database, a: App) -> Data:
    plan = []
    for c in a.get("countries") or ["us"]:
        terms = [k["term"] for k in apps.keywords(db, a["id"], c)]
        scores = stats.latest_scores(db, c, terms)
        scored = [scores[t] for t in terms if t in scores]
        pops = [s["popularity"] for s in scored if s.get("popularity") is not None]
        diffs = [s["difficulty"] for s in scored if s.get("difficulty") is not None]
        best = sorted(scored, key=lambda s: -((s.get("popularity") or 0) - (s.get("difficulty") or 0)))[:5]
        plan.append({"country": c, "tracked": len(terms), "scored": len(scored),
                     "unscored": [t for t in terms if t not in scores],
                     "avg_popularity": round(sum(pops) / len(pops)) if pops else None,
                     "avg_difficulty": round(sum(diffs) / len(diffs)) if diffs else None,
                     "best": [{"term": s["term"], "popularity": s["popularity"], "difficulty": s["difficulty"]}
                              for s in best]})
    return {"countries": plan}


def _concept_briefing(db: Database, a: App, as_of: str | None) -> Data:
    plan = _keyword_plan(db, a)
    comp = competitor_changes(db, a, 7, as_of=as_of)
    n_comp = len(apps.competitors(db, a["id"]))
    todo: list[Data] = []
    total = sum(p["tracked"] for p in plan["countries"])
    unscored = [t for p in plan["countries"] for t in p["unscored"]]
    if total == 0:
        todo.append({"do": "Build a keyword plan: keyword_ideas on 2-3 seed words, then keywords_add the best 10-20.",
                     "why": "No keywords are tracked yet, so there is nothing to put in the title and subtitle."})
    elif unscored:
        todo.append({"do": f"Score the {len(unscored)} unscored keywords with keyword_score.",
                     "why": f"Without popularity and difficulty you can't pick the title words "
                            f"({', '.join(unscored[:3])}{'…' if len(unscored) > 3 else ''})."})
    if n_comp == 0:
        todo.append({"do": "Add 3-5 competitors with competitors_add.",
                     "why": "Their listings and reviews show what the market expects before you launch."})
    if comp["changes"]:
        todo.append({"do": "Read what competitors changed this week.",
                     "why": comp["changes"][0]["sentence"] + "."})
    lines = [f"## {a['name']} (pre-launch)"]
    rows = [{"country": p["country"].upper(), "tracked": p["tracked"], "scored": p["scored"],
             "avg_pop": p["avg_popularity"], "avg_diff": p["avg_difficulty"],
             "best": ", ".join(b["term"] for b in p["best"][:3]) or "–"} for p in plan["countries"]]
    lines.append("**Keyword plan**\n\n" + render.table(
        rows, ["country", "tracked", "scored", "avg_pop", "avg_diff", "best"],
        {"avg_pop": "avg popularity", "avg_diff": "avg difficulty", "best": "best bets (popularity − difficulty)"},
        align_right=["tracked", "scored", "avg_pop", "avg_diff"]))
    lines.append(f"**Competitors** ({n_comp} tracked)\n\n" + comp["display"].split("\n\n", 1)[-1])
    lines.append(_todo_md(todo))
    return {"app": a["name"], "slug": a["slug"], "stage": "prelaunch", "keyword_plan": plan,
            "competitors": n_comp, "competitor_changes": comp["changes"], "todo": todo, "display": "\n\n".join(lines)}


def _todo_md(todo: list[Data]) -> str:
    if not todo:
        return "**What to do**: nothing urgent. Keep the daily collection running."
    return "**What to do**\n\n" + "\n".join(f"{i}. {t['do']} — {t['why']}" for i, t in enumerate(todo, 1))


def briefing(db: Database, app: App | str | int, country: str | None = None, *, as_of: str | None = None) -> Data:
    """Today's briefing for one app: movers, competitor changes, low reviews, funnel, ratings, listing age, to-dos."""
    a = stats.resolve(db, app)
    if not a.get("store_id"):
        return _concept_briefing(db, a, as_of)
    c = stats.country_of(a, country)
    now = as_of or today()
    fresh = freshness(db, a, c, now)
    tracked = apps.keywords(db, a["id"], c)
    n_comp = len(apps.competitors(db, a["id"]))
    ranks = stats.rank_history(db, a, c, 8, as_of=now)
    movers = _movers(ranks)
    kws = ranks["keywords"]
    kw_stats = {"tracked": len(tracked), "ranked": sum(k["rank"] is not None for k in kws),
                "top10": sum((k["rank"] or stats.NOT_RANKED) <= 10 for k in kws),
                "top50": sum((k["rank"] or stats.NOT_RANKED) <= 50 for k in kws)}
    comp = competitor_changes(db, a, 7, country=c, as_of=now)
    low = reviews(db, a, 3, 14, country=c, as_of=now)
    fun = stats.funnel(db, a, 7, as_of=now)
    rat = stats.ratings(db, a, c, 8, as_of=now)
    listing = listing_age(db, a, c, now)

    todo: list[Data] = []
    if fresh["never"]:
        todo.append({"do": "Run the first collection: `rankwell collect`.",
                     "why": "Nothing has been collected for this app yet, so every section below is empty."})
    elif fresh["stale"]:
        newest = max(d for d in (fresh["last_snapshot"], fresh["last_rank"]) if d)
        todo.append({"do": "Run `rankwell collect` and check the daily job with `rankwell schedule status`.",
                     "why": f"The newest data is from {newest}, {fresh['age_days']} days old."})
    if not tracked:
        todo.append({"do": "Track 10-20 keywords (keyword_ideas, then keywords_add).",
                     "why": f"No keywords are tracked in {c.upper()}, so ranks and visibility can't be measured."})
    if n_comp == 0:
        todo.append({"do": "Add 3-5 competitors with competitors_add.",
                     "why": "Competitor alerts and the gap report need them."})
    if low["count"]:
        r = low["reviews"][0]
        todo.append({"do": f"Reply to the {low['count']} review(s) of 3★ or less from the last 14 days.",
                     "why": f"A public reply shows future users you listen; newest ({r['rating']}★): "
                            f"“{trim(r['title'] or r['content'], 70)}”."})
    drops = [k for k in movers["down"] if k["status_7d"] == "lost" or (k["change_7d"] or 0) <= -BIG_DROP]
    if drops:
        k = drops[0]
        title_moves = [x for x in comp["changes"] if x["field"] in ("title", "subtitle")]
        why = f"Biggest drop this week (popularity {k['popularity'] if k['popularity'] is not None else 'unscored'})."
        if title_moves:
            why += f" Same week: {title_moves[0]['sentence']} on {title_moves[0]['date']}."
        todo.append({"do": f"Look into {_mover_line(k)}.", "why": why})
    if fun.get("available"):
        d = fun["changes"]["first_downloads"]
        if d["change_pct"] is not None and d["change_pct"] <= DOWNLOAD_DROP_PCT:
            cv = fun["changes"]["download_rate"]
            todo.append({"do": "Find the funnel step that fell (app_stats metric=funnel).",
                         "why": f"First downloads {d['current']:,} vs {d['previous']:,} the week before "
                                f"({d['change_pct']:+.0f}%); page view → download {cv['current']}% vs "
                                f"{cv['previous']}%."})
    few = kw_stats["tracked"] and kw_stats["top50"] < kw_stats["tracked"] * FEW_KEYWORDS_SHARE
    if listing["days"] is not None and listing["days"] > LISTING_NUDGE_DAYS and few:
        since = ("over " if listing["lower_bound"] else "") + f"{listing['days']} days"
        todo.append({"do": "Plan a metadata update: title, subtitle and keyword field (aso-listing).",
                     "why": f"The listing has not changed in {since} and the app is in the top 50 for only "
                            f"{kw_stats['top50']} of {kw_stats['tracked']} tracked keywords."})
    renamed = [x for x in comp["changes"] if x["field"] in ("title", "subtitle")]
    if renamed and not drops:
        todo.append({"do": f"Check {renamed[0]['app']}'s new {renamed[0]['field']} for keywords you don't cover.",
                     "why": renamed[0]["sentence"] + "."})
    if not fun.get("available"):
        todo.append({"do": "Connect App Store Connect (aso-setup) to see impressions → downloads.",
                     "why": "Without it Rankwell can't tell whether rank changes move downloads."})
    todo = todo[:MAX_TODO]

    lines = [f"## {a['name']} · {c.upper()} · {now}"]
    if fresh["never"]:
        lines.append(f"⚠ {stats.NO_COLLECTION}")
    elif fresh["stale"]:
        lines.append(f"⚠ Data is stale: newest from {fresh['last_snapshot'] or fresh['last_rank']} "
                     f"({fresh['age_days']} days ago).")
    if kw_stats["tracked"]:
        lines.append(f"**Keywords**: top 200 for {kw_stats['ranked']} of {kw_stats['tracked']}, top 10 for "
                     f"{kw_stats['top10']}.")
    up, down = movers["up"][:4], movers["down"][:4]
    if up or down:
        lines.append("**Movers (7 days)**\n\n" + "\n".join(
            f"- {_mover_line(k)}" for k in up + down))
    elif kws:
        lines.append("**Movers (7 days)**: no rank changes.")
    if comp["changes"]:
        lines.append(comp["display"].replace("(last 7 days)", "(7 days)"))
    if low["count"]:
        lines.append(f"**Low reviews (14 days)**: {low['count']}\n\n" + "\n".join(
            f"- {r['rating']}★ {r['date']}: “{trim(r['title'] or r['content'], 90)}”" for r in low["reviews"][:3]))
    if fun.get("available"):
        ch = fun["changes"]
        parts = []
        for k, label in (("impressions", "impressions"), ("page_views", "page views"),
                         ("first_downloads", "downloads")):
            if ch[k]["current"] is not None:
                pct = ch[k]["change_pct"]
                parts.append(f"{label} {render.number(ch[k]['current'])}"
                             + (f" ({render.delta(pct, pct=True)})" if pct is not None else ""))
        lines.append(f"**Last 7 days** (to {fun['period']['to']}, vs the 7 before): " + ", ".join(parts))
    else:
        lines.append(f"**Funnel**: {fun.get('message', stats.NO_ASC)}")
    if rat.get("series"):
        cnt, avg = rat["count"], rat["average"]
        lines.append(f"**Ratings**: {render.number(cnt['current'])} ({render.delta(cnt['change'])} this week), "
                     f"{avg['current']:.2f}★" + (f" ({avg['change']:+.2f})" if avg["change"] else ""))
    if listing["days"] is not None:
        lines.append(f"**Listing**: last changed {listing['last_change']} ({listing['days']} days ago)"
                     if listing["last_change"] else
                     f"**Listing**: no change seen since tracking began {listing['tracked_since']} "
                     f"({listing['days']} days)")
    lines.append(_todo_md(todo))
    return {"app": a["name"], "slug": a["slug"], "stage": "live", "country": c, "date": now, "freshness": fresh,
            "keywords": kw_stats, "movers": movers, "competitor_changes": comp["changes"],
            "low_reviews": low["reviews"], "funnel": fun if fun.get("available") else {"available": False,
                                                                                      "message": fun.get("message")},
            "ratings": {k: rat.get(k) for k in ("count", "average", "from", "to")} if rat.get("series") else None,
            "listing": listing, "todo": todo, "display": "\n\n".join(lines)}


def overview(db: Database, *, as_of: str | None = None) -> Data:
    """One line per app and each app's most important to-do."""
    all_apps = apps.list_apps(db)
    if not all_apps:
        return {"apps": [], "display": "No apps yet. Add one with add_app (or run the aso-setup skill)."}
    rows, briefs = [], []
    for a in all_apps:
        b = briefing(db, a, as_of=as_of)
        briefs.append(b)
        live = b["stage"] == "live"
        fresh = b.get("freshness") or {}
        downloads = (b.get("funnel") or {}).get("changes", {}).get("first_downloads") if live else None
        rows.append({
            "app": a["name"], "stage": b["stage"],
            "data": ("never" if fresh.get("never") else f"{fresh.get('age_days')}d ago" + (" ⚠" if fresh.get("stale")
                                                                                         else "")) if live else "–",
            "movers": f"▲{len(b['movers']['up'])} ▼{len(b['movers']['down'])}" if live else "–",
            "competitor_changes": len(b["competitor_changes"]),
            "low_reviews": len(b.get("low_reviews") or []) if live else "–",
            "downloads_7d": (f"{render.number(downloads['current'])} "
                             f"{render.delta(downloads['change_pct'], pct=True)}") if downloads else "–",
            "next": b["todo"][0]["do"] if b["todo"] else "–",
        })
    table = render.table(rows, ["app", "stage", "data", "movers", "competitor_changes", "low_reviews",
                                "downloads_7d", "next"],
                         {"competitor_changes": "comp. changes", "low_reviews": "≤3★ reviews",
                          "downloads_7d": "downloads 7d"},
                         align_right=["competitor_changes", "low_reviews"])
    todos = [f"- **{b['app']}**: {b['todo'][0]['do']} — {b['todo'][0]['why']}" for b in briefs if b["todo"]]
    display = "## All apps\n\n" + table + ("\n\n**Most important per app**\n\n" + "\n".join(todos) if todos else "")
    return {"apps": [{k: b[k] for k in b if k != "display"} for b in briefs], "rows": rows, "display": display}
