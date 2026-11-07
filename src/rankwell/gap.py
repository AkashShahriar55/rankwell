"""Market gap: the app against each competitor and the median competitor, with the evidence behind each point.

Everything is counted, nothing is guessed: listing facts come from the newest snapshot, update cadence from
version changes in the last 90 days, keyword coverage from whole-word matches of tracked keywords in title +
subtitle, the keyword battle from the newest ranks, and complaint themes from term frequency in reviews of
3★ or less (stopwords removed; a term counts once per review).
"""

from __future__ import annotations

import itertools
import json
import re
import statistics
from collections import Counter
from typing import Any

from . import apps, estimates, render, stats
from .apps import App
from .db import Database
from .stats import Data, ago, clip, trim
from .util import days_ago, today

CADENCE_DAYS = 90
REVIEW_DAYS = 365
MAX_THEMES = 8
MIN_THEME_REVIEWS = 2
QUOTE_CHARS = 120

_STOP = """
a about above after again against all also am an and any app apps are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each
even ever every few for from further get gets getting got had hadn't has hasn't have haven't having he her here
hers herself him himself his how i i'd i'll i'm i've if in into is isn't it it's its itself just let let's like
me more most much must mustn't my myself need no nor not now of off on once one only or other ought our ours
ourselves out over own please really same see she should shouldn't so some still such than that that's the their
theirs them themselves then there there's these they they'd they'll they're they've thing things this those
through to too under until up upon us use used using very want was wasn't way we we'd we'll we're we've well were
weren't what what's when where which while who whom why will with won't would wouldn't yet you you'd you'll you're
you've your yours yourself yourselves im ive dont cant doesnt didnt isnt wont thats also already always anything
everything something nothing lot lots make makes made many new now ok okay pretty say says said since stuff sure
take tell think time times try tried trying update updated version work works worked going go goes keep give
know actually anyone back day days great good love nice best better bad star stars review reviews rating
"""
STOPWORDS = frozenset(_STOP.split())

LISTING_FIELDS = ("rating_count", "rating_avg", "age_days", "days_since_update", "versions_90d", "price",
                  "screenshots", "description_chars", "coverage")


# ---------------------------------------------------------------- per-app facts

def _screenshot_count(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return len(v) if isinstance(v, list) else None


def covers(term: str, text: str) -> bool:
    """True when every word of the keyword appears as a whole word in the text (case-insensitive)."""
    words = term.lower().split()
    low = text.lower()
    return bool(words) and all(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", low) for w in words)


def _facts(db: Database, sid: int, name: str, country: str, terms: list[str], now: str) -> Data:
    snap = db.one("SELECT * FROM snapshots WHERE store_id = ? AND country = ? AND date <= ? ORDER BY date DESC "
                  "LIMIT 1", (sid, country, now))
    out: Data = {"store_id": sid, "name": name, "snapshot": None}
    if not snap:
        return out
    text = f"{snap.get('title') or ''} {snap.get('subtitle') or ''}"
    covered = [t for t in terms if covers(t, text)]
    versions = int(db.value("SELECT count(*) FROM changes WHERE store_id = ? AND country = ? AND field = 'version' "
                            "AND date > ?", (sid, country, days_ago(CADENCE_DAYS, now))) or 0)
    last_version = db.value("SELECT max(date) FROM changes WHERE store_id = ? AND country = ? AND field = 'version'",
                            (sid, country))
    first_seen = db.value("SELECT min(date) FROM snapshots WHERE store_id = ? AND country = ?", (sid, country))
    out.update({
        "snapshot": snap["date"], "name": snap.get("title") or name, "subtitle": snap.get("subtitle"),
        "rating_count": snap.get("rating_count"), "rating_avg": snap.get("rating_avg"), "price": snap.get("price"),
        "version": snap.get("version"), "released_at": snap.get("released_at"),
        "age_days": ago(snap.get("released_at"), now),
        "days_since_update": ago(last_version or first_seen, now), "update_lower_bound": last_version is None,
        "versions_90d": versions, "screenshots": _screenshot_count(snap.get("screenshots")),
        "description_chars": len(snap.get("description") or "") if snap.get("description") is not None else None,
        "coverage": len(covered), "covered": covered,
    })
    return out


def _median(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    return round(statistics.median(nums), 2) if nums else None


def _charts(db: Database, ids: list[int], country: str, now: str) -> dict[int, Data]:
    last = db.value("SELECT max(date) FROM chart_positions WHERE country = ? AND date <= ?", (country, now))
    if not last:
        return {}
    rows = db.all(f"SELECT store_id, chart, genre_id, min(rank) AS rank FROM chart_positions WHERE country = ? "
                  f"AND date = ? AND store_id IN ({stats.placeholders(ids)}) GROUP BY store_id, chart, genre_id "
                  "ORDER BY rank", (country, last, *ids))
    out: dict[int, Data] = {}
    for r in rows:
        out.setdefault(int(r["store_id"]), {"date": last, "rank": r["rank"], "chart": r["chart"],
                                            "genre_id": r["genre_id"]})
    return out


# ---------------------------------------------------------------- keyword battle

def battle(db: Database, own: int | None, comp: dict[int, str], terms: list[str], country: str,
           now: str) -> list[Data]:
    """For each tracked keyword on its newest rank date: own rank vs the best competitor and who owns it."""
    ids = ([own] if own else []) + list(comp)
    scores = stats.latest_scores(db, country, terms)
    out: list[Data] = []
    for t in terms:
        last = db.value("SELECT max(date) FROM ranks WHERE country = ? AND term = ? AND date <= ?", (country, t, now))
        if not last or not ids:
            out.append({"term": t, "date": None, "own": None, "best_competitor": None, "competitor": None,
                        "owner": "no data", "leader": None, "popularity": scores.get(t, {}).get("popularity")})
            continue
        ranks = {int(r["store_id"]): r["rank"] for r in db.all(
            f"SELECT store_id, rank FROM ranks WHERE country = ? AND term = ? AND date = ? "
            f"AND store_id IN ({stats.placeholders(ids)})", (country, t, last, *ids))}
        mine = ranks.get(own) if own else None
        theirs = sorted((r, sid) for sid, r in ranks.items() if sid != own and r is not None)
        best = theirs[0] if theirs else None
        if mine is not None and (best is None or mine < best[0]):
            owner = "you"
        elif best is not None:
            owner = comp.get(best[1], str(best[1]))
        else:
            owner = "nobody tracked"
        leader = db.value("SELECT name FROM keyword_top WHERE country = ? AND term = ? AND date = ? AND position = 1",
                          (country, t, last))
        out.append({"term": t, "date": last, "own": mine, "best_competitor": best[0] if best else None,
                    "competitor": comp.get(best[1]) if best else None, "owner": owner, "leader": leader,
                    "popularity": scores.get(t, {}).get("popularity")})
    out.sort(key=lambda b: (-(b["popularity"] or 0), b["term"]))
    return out


# ---------------------------------------------------------------- complaint themes

_SENTENCE = re.compile(r"[.!?;:\n\r]+")


def _tokens(text: str) -> list[str]:
    return [w.strip("'") for w in re.findall(r"[a-z][a-z']+", text.lower())]


def _sentences(text: str) -> list[str]:
    """Sentences of a review (title and content are joined with ". ", so they never share a sentence)."""
    return [s for s in _SENTENCE.split(text) if s.strip()]


def _pattern(term: str) -> re.Pattern[str]:
    """The term as the tokenizer saw it in the text: each word may carry the edge apostrophes _tokens stripped,
    and words are separated by anything that is not a letter or apostrophe (so "users data" finds "users' data"
    and "ios crashes" finds "iOS17 crashes")."""
    words = [f"'*{re.escape(w)}'*" for w in term.split()]
    return re.compile(r"(?<![a-z'])" + r"[^a-z']+".join(words) + r"(?![a-z'])", re.I)


def _example(docs: list[str], term: str) -> str | None:
    """A review that mentions the term: the first exact match, else the first containing the term's first word,
    else None. Never raises."""
    pat = _pattern(term)
    found = next((d for d in docs if pat.search(d)), None)
    if found is None:
        first = _pattern(term.split()[0]) if term.split() else None
        found = next((d for d in docs if first and first.search(d)), None)
    return found


def _quote(text: str, term: str) -> str:
    """About QUOTE_CHARS characters of the review around the first match of the term."""
    flat = " ".join(text.split())
    m = _pattern(term).search(flat) or (_pattern(term.split()[0]).search(flat) if term.split() else None)
    if not m or len(flat) <= QUOTE_CHARS:
        return trim(flat, QUOTE_CHARS)
    start = max(0, m.start() - QUOTE_CHARS // 3)
    piece = flat[start:start + QUOTE_CHARS].strip()
    return ("…" if start > 0 else "") + piece + ("…" if start + QUOTE_CHARS < len(flat) else "")


def _quoted(t: Data) -> str:
    """': “quote”' for a theme, or '' when no review text could be quoted."""
    return f": “{t['example']}”" if t.get("example") else ""


def themes(texts: list[str], top: int = MAX_THEMES, min_reviews: int = MIN_THEME_REVIEWS) -> list[Data]:
    """Most frequent words and two-word phrases across reviews (counted once per review), with a quote each.

    A phrase replaces its words when it covers most of their mentions, so "dark mode" shows instead of "dark".
    Phrases are built within one sentence only (never across the title/content join or a full stop). `example`
    is None when no review can be quoted for the term.
    """
    docs = [t for t in texts if t and t.strip()]
    if not docs:
        return []
    uni: Counter[str] = Counter()
    bi: Counter[str] = Counter()
    for d in docs:
        keep: set[str] = set()
        pairs: set[str] = set()
        for sentence in _sentences(d):
            toks = _tokens(sentence)
            keep.update(w for w in toks if len(w) > 2 and w not in STOPWORDS)
            pairs.update(f"{a} {b}" for a, b in itertools.pairwise(toks)
                         if a not in STOPWORDS and b not in STOPWORDS and len(a) > 2 and len(b) > 2)
        uni.update(keep)
        bi.update(pairs)
    picked: dict[str, int] = {p: n for p, n in bi.items() if n >= min_reviews}
    absorbed = {w for p, n in picked.items() for w in p.split() if n >= 0.6 * uni[w]}
    for w, n in uni.items():
        if n >= min_reviews and w not in absorbed:
            picked[w] = n
    ranked = sorted(picked.items(), key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0]))[:top]
    out = []
    for term, n in ranked:
        example = _example(docs, term)
        out.append({"term": term, "reviews": n, "share": round(100 * n / len(docs)),
                    "example": _quote(example, term) if example else None})
    return out


def _low_review_texts(db: Database, ids: list[int], country: str, now: str) -> list[Data]:
    if not ids:
        return []
    return db.all(f"SELECT store_id, rating, title, content FROM reviews WHERE store_id IN ({stats.placeholders(ids)}) "
                  "AND country = ? AND rating <= 3 AND substr(reviewed_at, 1, 10) BETWEEN ? AND ?",
                  (*ids, country, days_ago(REVIEW_DAYS, now), now))


def _text(r: Data) -> str:
    return ". ".join(x for x in (r.get("title"), r.get("content")) if x)


# ---------------------------------------------------------------- strengths and weaknesses

def _judge(own: Data, med: Data, comps: list[Data], fight: list[Data], charts: dict[int, Data],
           own_themes: list[Data], comp_themes: list[Data], low_own: int) -> tuple[list[Data], list[Data], list[Data]]:
    s: list[Data] = []
    w: list[Data] = []
    opp: list[Data] = []

    def add(to: list[Data], area: str, point: str, evidence: str) -> None:
        to.append({"area": area, "point": point, "evidence": evidence})

    rc, mrc = own.get("rating_count"), med.get("rating_count")
    if rc is not None and mrc:
        ev = f"{render.number(rc)} ratings vs median competitor {render.number(mrc)}"
        if rc >= 2 * mrc:
            add(s, "ratings", "More social proof than most competitors", ev)
        elif rc < 0.5 * mrc:
            add(w, "ratings", "Far fewer ratings than competitors; a rating prompt after a success moment helps", ev)
    ra, mra = own.get("rating_avg"), med.get("rating_avg")
    if ra and mra:
        ev = f"{ra:.2f}★ vs median {mra:.2f}★"
        if ra - mra >= 0.2:
            add(s, "ratings", "Higher average rating", ev)
        elif ra - mra <= -0.2:
            add(w, "ratings", "Lower average rating", ev)
    up, mup = own.get("days_since_update"), med.get("days_since_update")
    if up is not None and mup is not None:
        ev = f"last update {'over ' if own.get('update_lower_bound') else ''}{up} days ago vs median {mup:.0f}"
        if up < mup and up <= 45:
            add(s, "updates", "Updated more recently than most competitors", ev)
        elif up > max(mup, 60):
            add(w, "updates", "Looks less maintained than competitors", ev)
    v, mv = own.get("versions_90d"), med.get("versions_90d")
    if v is not None and mv is not None:
        ev = f"{v} versions in {CADENCE_DAYS} days vs median {mv:g}"
        if v > mv and v >= 2:
            add(s, "updates", "Faster release cadence", ev)
        elif v == 0 and mv >= 1:
            add(w, "updates", "No release in the last 90 days while competitors ship", ev)
    sc, msc = own.get("screenshots"), med.get("screenshots")
    if sc is not None and msc is not None:
        ev = f"{sc} screenshots vs median {msc:g}"
        if sc < msc:
            add(w, "screenshots", "Fewer screenshots than competitors (fewer chances to show value)", ev)
        elif sc > msc:
            add(s, "screenshots", "More screenshots than most competitors", ev)
    dc, mdc = own.get("description_chars"), med.get("description_chars")
    if dc is not None and mdc and dc < 0.5 * mdc:
        add(w, "description", "Much shorter description than competitors",
            f"{dc:,} characters vs median {mdc:,.0f}")
    cov, mcov = own.get("coverage"), med.get("coverage")
    if cov is not None and mcov is not None:
        ev = f"title + subtitle cover {cov} tracked keywords vs median competitor {mcov:g}"
        if cov > mcov:
            add(s, "keywords", "Title and subtitle cover more tracked keywords", ev)
        elif cov < mcov:
            add(w, "keywords", "Title and subtitle cover fewer tracked keywords than competitors", ev)
    ranked = [b for b in fight if b["date"]]
    wins = [b for b in ranked if b["owner"] == "you"]
    losses = [b for b in ranked if b["owner"] not in ("you", "nobody tracked")]
    if wins and len(wins) >= len(losses):
        add(s, "keywords", f"Ahead of every tracked competitor on {len(wins)} of {len(ranked)} keywords",
            ", ".join(f"{b['term']} {render.rank(b['own'])}" for b in wins[:5]))
    behind = [b for b in losses if (b["best_competitor"] or 999) <= 10 and (b["own"] is None or b["own"] > 50)]
    if behind:
        add(w, "keywords", f"Competitors hold the top 10 on {len(behind)} keywords where you are outside the top 50",
            ", ".join(f"{b['term']} ({b['competitor']} {render.rank(b['best_competitor'])}, you "
                      f"{render.rank(b['own'])})" for b in behind[:5]))
    p, mp = own.get("price"), med.get("price")
    if p is not None and mp is not None:
        if p > 0 and mp == 0:
            add(w, "price", "Paid upfront while most competitors are free to download", f"{p:g} vs median free")
        elif p == 0 and mp > 0:
            add(s, "price", "Free to download while most competitors charge", f"free vs median {mp:g}")
    if charts or comps:
        charted = sum(c["store_id"] in charts for c in comps)
        mine = charts.get(own["store_id"])
        if mine and charted < len(comps) / 2:
            add(s, "charts", "In the category chart while most competitors are not",
                f"{render.rank(mine['rank'])} {mine['chart']}; {charted} of {len(comps)} competitors charted")
        elif not mine and charted:
            add(w, "charts", "Not in the category top 100 while competitors are",
                f"{charted} of {len(comps)} competitors charted on {next(iter(charts.values()))['date']}")
    if own_themes and own_themes[0]["reviews"] >= MIN_THEME_REVIEWS:
        t = own_themes[0]
        add(w, "reviews", f"Your low reviews keep mentioning “{t['term']}”",
            f"{t['reviews']} of {low_own} reviews ≤3★{_quoted(t)}")
    mine_terms = {t["term"] for t in own_themes}
    for t in comp_themes[:5]:
        if t["term"] not in mine_terms:
            opp.append({"area": "reviews", "point": f"Competitors' users complain about “{t['term']}”",
                        "evidence": f"{t['reviews']} low reviews ({t['share']}%){_quoted(t)}"})
    return s, w, opp


# ---------------------------------------------------------------- report

def report(db: Database, app: App | str | int, country: str | None = None, *, as_of: str | None = None) -> Data:
    """The app vs each competitor and the median competitor: listing, ratings, updates, keywords, charts, reviews."""
    a = stats.resolve(db, app)
    c = stats.country_of(a, country)
    now = as_of or today()
    comp = {int(x["store_id"]): str(x["name"]) for x in apps.competitors(db, a["id"])}
    own_id = int(a["store_id"]) if a.get("store_id") else None
    if own_id:
        comp.pop(own_id, None)
    terms = [k["term"] for k in apps.keywords(db, a["id"], c)]
    base: Data = {"app": a["name"], "country": c, "date": now}
    if not comp:
        return {**base, "strengths": [], "weaknesses": [], "opportunities": [],
                "display": f"No competitors tracked for {a['name']}. Add 3-5 with competitors_add, then let the "
                           "daily collection run once."}
    own = _facts(db, own_id, a["name"], c, terms, now) if own_id else None
    comps = [_facts(db, sid, name, c, terms, now) for sid, name in comp.items()]
    seen = [x for x in comps if x["snapshot"]]
    if not seen:
        return {**base, "strengths": [], "weaknesses": [], "opportunities": [],
                "display": f"No listing snapshots for {a['name']}'s competitors in {c.upper()} yet. "
                           f"{stats.NO_COLLECTION}"}
    med = {f: _median([x.get(f) for x in seen]) for f in LISTING_FIELDS}
    ids = ([own_id] if own_id else []) + list(comp)
    charts = _charts(db, ids, c, now)
    fight = battle(db, own_id, comp, terms, c, now)
    comp_reviews = _low_review_texts(db, list(comp), c, now)
    own_reviews = _low_review_texts(db, [own_id], c, now) if own_id else []
    comp_themes = themes([_text(r) for r in comp_reviews])
    own_themes = themes([_text(r) for r in own_reviews])
    if own and own["snapshot"]:
        strengths, weaknesses, opps = _judge(own, med, seen, fight, charts, own_themes, comp_themes, len(own_reviews))
    else:
        strengths, weaknesses = [], []
        opps = [{"area": "reviews", "point": f"Competitors' users complain about “{t['term']}”",
                 "evidence": f"{t['reviews']} low reviews ({t['share']}%){_quoted(t)}"} for t in comp_themes[:5]]
    est = estimates.for_ids(db, ids, c, now)
    for x in [own, *comps]:
        if x is not None:
            ch = charts.get(x["store_id"])
            x["chart"] = f"{render.rank(ch['rank'])} {ch['chart']}" if ch else None
            if x["store_id"] in est:  # only apps in a free or paid chart
                x["downloads_estimate"] = est[x["store_id"]]
    out = {**base, "own": own, "competitors": comps, "median": med, "keyword_battle": fight,
           "complaints": {"competitors": {"reviews": len(comp_reviews), "themes": comp_themes},
                          "own": {"reviews": len(own_reviews), "themes": own_themes}},
           "strengths": strengths, "weaknesses": weaknesses, "opportunities": opps}
    out["display"] = _display(db, a, c, own, seen, med, fight, out)
    return out


def _row(x: Data) -> Data:
    up = x.get("days_since_update")
    return {"app": trim(x["name"], 28), "ratings": render.number(x.get("rating_count")),
            "stars": f"{x['rating_avg']:.2f}" if x.get("rating_avg") is not None else "–",
            "updated": "–" if up is None else (">" if x.get("update_lower_bound") else "") + f"{up}d",
            "v90": x.get("versions_90d"), "price": "free" if x.get("price") == 0 else x.get("price"),
            "shots": x.get("screenshots"), "desc": render.number(x.get("description_chars")),
            "coverage": x.get("coverage"), "chart": x.get("chart")}


def _g(v: float | None) -> str | None:
    return None if v is None else f"{v:g}"


def _estimates_block(db: Database, charted: list[Data]) -> list[str]:
    """Estimated downloads of the charted apps, labelled as estimates, or why there are none yet."""
    if not charted:
        return []
    rows = []
    for x in charted:
        d = x["downloads_estimate"]
        e = d["estimate"]
        rows.append({"app": trim(x["name"], 28), "chart": f"{render.rank(d['rank'])} {d['chart']}",
                     "est": estimates.text(e) if e else "–", "basis": e["basis"] if e else "–"})
    if not any(x["downloads_estimate"]["estimate"] for x in charted):
        return [f"**Downloads (estimate)**: {estimates.needs_more(estimates.data_status(db))}"]
    return ["**Downloads (estimate, not App Store numbers; 80% range)**\n\n" + render.table(
        rows, ["app", "chart", "est", "basis"], {"est": "estimate (range)"})]


def _display(db: Database, a: App, c: str, own: Data | None, comps: list[Data], med: Data, fight: list[Data],
             out: Data) -> str:
    rows = ([{**_row(own), "app": f"**{trim(own['name'], 26)}**"}] if own and own["snapshot"] else [])
    shown, note = clip(comps, 10)
    rows += [_row(x) for x in shown]
    rows.append({"app": "_median competitor_", "ratings": render.number(med["rating_count"]),
                 "stars": f"{med['rating_avg']:.2f}" if med["rating_avg"] is not None else "–",
                 "updated": f"{med['days_since_update']:.0f}d" if med["days_since_update"] is not None else "–",
                 "v90": _g(med["versions_90d"]), "price": "free" if med["price"] == 0 else _g(med["price"]),
                 "shots": _g(med["screenshots"]), "desc": render.number(med["description_chars"]),
                 "coverage": _g(med["coverage"]), "chart": None})
    n_terms = len(fight)
    lines = [f"## Gap report: {a['name']} vs {len(comps)} competitors ({c.upper()})",
             render.table(rows, ["app", "ratings", "stars", "updated", "v90", "price", "shots", "desc", "coverage",
                                 "chart"],
                          {"v90": f"versions {CADENCE_DAYS}d", "shots": "screenshots", "desc": "description",
                           "coverage": f"kw in title+sub (of {n_terms})"},
                          align_right=["ratings", "stars", "updated", "v90", "shots", "desc", "coverage"]) + note]
    lines += _estimates_block(db, [x for x in ([own] if own else []) + comps if "downloads_estimate" in x])
    if not own or not own["snapshot"]:
        lines.append(f"_{a['name']} has no listing snapshot yet (not live or not collected), so this is the market "
                     "baseline only._")
    ranked = [b for b in fight if b["date"]]
    if ranked:
        wins = sum(b["owner"] == "you" for b in ranked)
        nobody = sum(b["owner"] == "nobody tracked" for b in ranked)
        bshown, bnote = clip(ranked)
        empty = f", no tracked app in the top 200 for {nobody}" if nobody else ""
        lines.append(f"**Keyword battle**: you lead {wins}, competitors lead {len(ranked) - wins - nobody}{empty}."
                     "\n\n" + render.table(
                         [{**b, "own": render.rank(b["own"]),
                           "best_competitor": f"{render.rank(b['best_competitor'])} {trim(b['competitor'], 20)}"
                           if b["competitor"] else "–", "leader": trim(b["leader"], 24) if b["leader"] else None}
                          for b in bshown],
                         ["term", "popularity", "own", "best_competitor", "owner", "leader"],
                         {"term": "keyword", "popularity": "pop", "own": "you", "best_competitor": "best competitor",
                          "leader": "#1 overall"}, align_right=["popularity", "own"]) + bnote)
    elif fight:
        lines.append("**Keyword battle**: no ranks collected yet.")
    comp_t = out["complaints"]["competitors"]
    own_t = out["complaints"]["own"]
    for label, block in (("Competitor complaints", comp_t), ("Your complaints", own_t)):
        if block["themes"]:
            items = [f"- **{t['term']}** — {t['reviews']} reviews ({t['share']}%){_quoted(t)}"
                     for t in block["themes"]]
            lines.append(f"**{label}** ({block['reviews']} reviews ≤3★, last {REVIEW_DAYS} days)\n\n"
                         + "\n".join(items))
        elif label == "Competitor complaints" or own:
            lines.append(f"**{label}**: " + (f"{block['reviews']} reviews ≤3★, no repeated theme."
                                              if block["reviews"] else "no reviews of 3★ or less collected."))
    for label, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"), ("Opportunities", "opportunities")):
        if out[key]:
            lines.append(f"**{label}**\n\n" + "\n".join(f"- {x['point']} — {x['evidence']}" for x in out[key]))
    return "\n\n".join(lines)
