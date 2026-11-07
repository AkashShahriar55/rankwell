"""Keyword research: ideas, popularity proxy, difficulty, Google demand, market check.

Scores use Rankwell's own 0-100 scale. Popularity is a proxy from App Store autocomplete, not Apple Ads
popularity or search volume. It is built from how few letters must be typed before the store suggests the
term (fewer = more searched), discounted when that prefix is most of the term and by the term's position in
the suggestions:

    popularity = 100 × letters(typed) × saved(typed, length) × place(position)
    letters    = exp(-LETTER_DECAY × (typed - 2))      2 letters → 1.0, 4 → 0.79, 8 → 0.49, 13 → 0.27
    saved      = min(1, untyped share / FULL_SHARE)    0 when the whole term had to be typed
    place      = 1 - POSITION_STEP × (position - 1)    #1 → 1.0, #10 → 0.73

So a long phrase is not rewarded for being long: "photo enhancer for blurry images" suggested after 8
letters scores like any term suggested after 8 letters. A term the store never suggests has popularity
None (unknown), not 0. google_demand is the same scale on Google autocomplete (None when Google did not
answer).
"""

from __future__ import annotations

import datetime as dt
import math
import string
from collections.abc import Callable
from typing import Any

from .stores import appstore, google

Search = Callable[..., list[dict[str, Any]]]
Suggest = Callable[[str, str], list[str]]
Demand = Callable[[str, str], dict[str, Any] | None]

LETTER_DECAY = 0.12
FULL_SHARE = 0.5  # the store filled in half the term or more: full credit
POSITION_STEP = 0.03
MIN_SUGGESTED = 5
PROBES = (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20)  # every letter while it matters most


def ideas(seed: str, country: str = "us", suggest: Suggest = appstore.autocomplete) -> list[dict[str, Any]]:
    """Expand a seed through autocomplete on the seed and on 'seed a' … 'seed z'."""
    found: dict[str, dict[str, Any]] = {}
    for prefix in [seed] + [f"{seed} {c}" for c in string.ascii_lowercase]:
        for pos, term in enumerate(suggest(prefix, country)):
            found.setdefault(term.lower(), {"term": term.lower(), "prefix": prefix, "position": pos + 1})
    return list(found.values())


def popularity_value(typed: int | None, length: int, position: int | None) -> int | None:
    """0-100 from the letters typed before the term was suggested; None when it was never suggested."""
    if typed is None or length <= 0:
        return None
    letters = math.exp(-LETTER_DECAY * (max(typed, 2) - 2))
    saved = min(1.0, max(0.0, 1 - typed / length) / FULL_SHARE)
    place = 1 - POSITION_STEP * (max(1, min(position or 1, 10)) - 1)
    return max(MIN_SUGGESTED, min(100, round(100 * letters * saved * place)))


def popularity_basis(typed: int | None, length: int, position: int | None, source: str = "App Store") -> str:
    if typed is None:
        return f"never suggested by {source} autocomplete: unknown, not zero"
    return f"{source} suggested it after {typed} of {length} letters, #{position or 1}"


def popularity(keyword: str, country: str = "us",
               suggest: Suggest = appstore.autocomplete) -> tuple[int | None, int | None, int | None]:
    """(score, letters typed before the store suggested it, position). Never suggested → (None, None, None)."""
    kw = keyword.lower().strip()
    n = len(kw)
    for typed in sorted({min(n, k) for k in (*PROBES, n)}):
        sugg = [s.lower() for s in suggest(kw[:typed], country)]
        if kw in sugg:
            pos = sugg.index(kw) + 1
            return popularity_value(typed, n, pos), typed, pos
    return None, None, None


def google_demand(keyword: str, country: str = "us",
                  probe: Demand = google.first_suggested) -> tuple[int | None, str]:
    """(0-100 Google demand on the popularity scale, basis). None when Google never suggests it or did not answer."""
    kw = " ".join(keyword.lower().split())
    found = probe(kw, country)
    if found is None:
        return None, "Google did not answer (rate limited or offline): unknown"
    return (popularity_value(found["typed"], len(kw), found["position"]),
            popularity_basis(found["typed"], len(kw), found["position"], "Google"))


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
          suggest: Suggest = appstore.autocomplete, demand: Demand = google.first_suggested) -> dict[str, Any]:
    kw = " ".join(keyword.lower().split())
    pop, typed, pos = popularity(kw, country, suggest)
    diff, d = difficulty(kw, search(kw, country, limit=10))
    gdemand, gbasis = google_demand(kw, country, demand)
    return {
        "term": kw, "country": country, "popularity": pop, "suggested_after_chars": typed,
        "popularity_basis": popularity_basis(typed, len(kw), pos), "google_demand": gdemand,
        "google_basis": gbasis, "difficulty": diff, "median_ratings": d["median_ratings"],
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
