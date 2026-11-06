"""Keyword research: ideas, popularity proxy, difficulty, market check.

Scores use Rankwell's own 0-100 scale. Popularity is a proxy from App Store autocomplete, not Apple Ads
popularity or search volume. It favours long phrases (the store completes them from a shorter share of
their letters), so compare `suggested_after_chars` between keywords of different lengths: fewer letters
typed means stronger demand. A popularity of 0 means "never suggested", not "no demand".
"""

from __future__ import annotations

import datetime as dt
import math
import string
from collections.abc import Callable
from typing import Any

from .stores import appstore

Search = Callable[..., list[dict[str, Any]]]
Suggest = Callable[[str, str], list[str]]


def ideas(seed: str, country: str = "us", suggest: Suggest = appstore.autocomplete) -> list[dict[str, Any]]:
    """Expand a seed through autocomplete on the seed and on 'seed a' … 'seed z'."""
    found: dict[str, dict[str, Any]] = {}
    for prefix in [seed] + [f"{seed} {c}" for c in string.ascii_lowercase]:
        for pos, term in enumerate(suggest(prefix, country)):
            found.setdefault(term.lower(), {"term": term.lower(), "prefix": prefix, "position": pos + 1})
    return list(found.values())


def popularity(keyword: str, country: str = "us",
               suggest: Suggest = appstore.autocomplete) -> tuple[int, int | None, int | None]:
    """(score, letters typed before the store suggested it, position). Never suggested → (0, None, None)."""
    kw = keyword.lower().strip()
    n = len(kw)
    for typed in sorted({min(n, k) for k in (2, 3, 4, 5, 6, 8, 10, 13, 16, 20, n)}):
        sugg = [s.lower() for s in suggest(kw[:typed], country)]
        if kw in sugg:
            pos = sugg.index(kw)
            score = 100 * (1 - typed / n) + 15 - 2 * pos
            return max(5, min(100, round(score))), typed, pos + 1
    return 0, None, None


def difficulty(keyword: str, top: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    """0-100 from the top 10: median ratings (55%), exact words in the title (30%), share rated 4.5+ (15%)."""
    if not top:
        return 0, {"median_ratings": 0, "title_match": 0.0, "top_app": ""}
    words = keyword.lower().split()
    counts = sorted(int(a.get("userRatingCount") or 0) for a in top)
    median = counts[len(counts) // 2]
    strength = min(1.0, math.log10(median + 1) / 5)
    title_match = sum(all(w in str(a.get("trackName", "")).lower() for w in words) for a in top) / len(top)
    quality = sum(float(a.get("averageUserRating") or 0) >= 4.5 for a in top) / len(top)
    score = round(100 * (0.55 * strength + 0.30 * title_match + 0.15 * quality))
    return score, {"median_ratings": median, "title_match": round(title_match, 2),
                   "top_app": top[0].get("trackName", "")}


def score(keyword: str, country: str = "us", search: Search = appstore.search,
          suggest: Suggest = appstore.autocomplete) -> dict[str, Any]:
    pop, typed, _ = popularity(keyword, country, suggest)
    diff, d = difficulty(keyword, search(keyword, country, limit=10))
    return {
        "term": keyword.lower().strip(), "country": country, "popularity": pop,
        "suggested_after_chars": typed, "difficulty": diff, "median_ratings": d["median_ratings"],
        "title_match": d["title_match"], "top_app": d["top_app"],
    }


def market_check(keyword: str, country: str = "us", search: Search = appstore.search,
                 today: dt.date | None = None) -> dict[str, Any]:
    """Who holds the top 10 for a keyword and can a new app get in.

    crowded by giants  half or more of the top 10 have 100k+ ratings
    hard               median 20k+ ratings, or 5k+ with no app released in the last 12 months
    free space         median under 5k ratings and 3+ apps released in the last 12 months
    beatable           everything else
    """
    top = search(keyword, country, limit=10)
    if not top:
        return {"keyword": keyword, "country": country, "verdict": "no results", "apps": []}
    year_ago = ((today or dt.date.today()) - dt.timedelta(days=365)).isoformat()
    counts = sorted(int(a.get("userRatingCount") or 0) for a in top)
    median = counts[len(counts) // 2]
    giants = sum(c >= 100_000 for c in counts)
    newcomers = sum(str(a.get("releaseDate", ""))[:10] >= year_ago for a in top)
    if giants >= len(top) / 2:
        verdict = "crowded by giants"
    elif median >= 20_000 or (median >= 5_000 and newcomers == 0):
        verdict = "hard"
    elif median < 5_000 and newcomers >= 3:
        verdict = "free space"
    else:
        verdict = "beatable"
    return {
        "keyword": keyword, "country": country, "verdict": verdict, "median_ratings": median,
        "giants_100k": giants, "new_last_12m": newcomers,
        "apps": [{"rank": i + 1, "store_id": a.get("trackId"), "name": a.get("trackName"),
                  "ratings": int(a.get("userRatingCount") or 0),
                  "stars": round(float(a.get("averageUserRating") or 0), 2),
                  "released": str(a.get("releaseDate", ""))[:10], "seller": a.get("sellerName")}
                 for i, a in enumerate(top)],
    }
