"""Google demand: Google autocomplete and Google Trends interest (both best effort).

Autocomplete shows what people type into Google per country; Trends shows interest over the last 12 months.
Neither gives search volume (that needs a Google Ads account). Both endpoints are public but unofficial and
rate limit without warning, so every call is throttled, cached on disk with a TTL, and any failure returns
None: Google data adds to a keyword score and never breaks it. After a 429 or 403 the endpoint is left alone
for a while (a marker file in the cache, so other processes see it too).
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .. import paths
from . import http

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
EXPLORE_URL = "https://trends.google.com/trends/api/explore"
MULTILINE_URL = "https://trends.google.com/trends/api/widgetdata/multiline"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SUGGEST_INTERVAL = 0.5
TRENDS_INTERVAL = 5.0
SUGGEST_TTL = 7 * 86400
TRENDS_TTL = 7 * 86400
BLOCK_SECONDS = {"suggest": 3600, "trends": 6 * 3600}
RECENT_WEEKS = 13  # Trends change: the last quarter against the 9 months before it

# Google's hl (language) for a storefront country; anything else asks in English.
LANG = {"us": "en", "gb": "en", "ca": "en", "au": "en", "in": "en", "ie": "en", "nz": "en", "sg": "en", "za": "en",
        "de": "de", "at": "de", "ch": "de", "fr": "fr", "be": "fr", "es": "es", "mx": "es", "ar": "es", "co": "es",
        "cl": "es", "it": "it", "nl": "nl", "br": "pt", "pt": "pt", "se": "sv", "dk": "da", "no": "no", "fi": "fi",
        "pl": "pl", "tr": "tr", "jp": "ja", "kr": "ko", "ru": "ru", "bd": "bn", "id": "id", "th": "th", "vn": "vi"}


# ---------------------------------------------------------------- cache and back-off

def _cache_file(kind: str, key: str) -> Path:
    d = paths.cache_dir() / "google"
    d.mkdir(exist_ok=True)
    return d / f"{kind}-{hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()}.json"


def _cached(kind: str, key: str, ttl: float) -> Any:
    p = _cache_file(kind, key)
    try:
        if time.time() - p.stat().st_mtime < ttl:
            return json.loads(p.read_text())["value"]
    except (OSError, ValueError, KeyError):
        pass
    return None


def _store(kind: str, key: str, value: Any) -> None:
    with suppress(OSError):
        _cache_file(kind, key).write_text(json.dumps({"key": key, "value": value}))


def blocked(kind: str) -> bool:
    """True while Google asked us to slow down on this endpoint ('suggest' or 'trends')."""
    p = paths.cache_dir() / "google" / f"blocked-{kind}"
    try:
        return float(p.read_text()) > time.time()
    except (OSError, ValueError):
        return False


def _block(kind: str) -> None:
    d = paths.cache_dir() / "google"
    d.mkdir(exist_ok=True)
    with suppress(OSError):
        (d / f"blocked-{kind}").write_text(str(time.time() + BLOCK_SECONDS[kind]))


def _get(kind: str, url: str, params: dict[str, Any]) -> str | None:
    """Body of a GET, or None on any failure. A 429/403 blocks the endpoint for a while."""
    if blocked(kind):
        return None
    http.throttle(f"google-{kind}", SUGGEST_INTERVAL if kind == "suggest" else TRENDS_INTERVAL)
    try:
        r = http.request("GET", url, params=params, headers={"User-Agent": UA}, retries=1)
    except Exception:  # best effort: no network, a test router, anything else
        return None
    if r.status_code in (403, 429):
        _block(kind)
        return None
    return r.text if r.status_code < 400 else None


# ---------------------------------------------------------------- autocomplete

def suggest(query: str, country: str = "us", source: str = "google") -> list[str] | None:
    """Google (or YouTube, source='youtube') autocomplete for a query, in order. None when Google did not answer."""
    q = query.lower().lstrip()  # a trailing space asks for the next word, so it stays
    key = f"{source}|{country}|{q}"
    hit = _cached("suggest", key, SUGGEST_TTL)
    if isinstance(hit, list):
        return [str(s) for s in hit]
    params = {"client": "firefox", "q": q, "hl": LANG.get(country, "en"), "gl": country}
    if source == "youtube":
        params["ds"] = "yt"
    body = _get("suggest", SUGGEST_URL, params)
    if body is None:
        return None
    try:
        data = json.loads(body)
        out = [str(s).lower() for s in data[1] if isinstance(s, str)]
    except (ValueError, IndexError, KeyError, TypeError):
        return None
    _store("suggest", key, out)
    return out


def _matches(suggestion: str, term: str) -> bool:
    """The term, its plural, or a longer query starting with either ("passport photos near me")."""
    return any(suggestion == t or suggestion.startswith(t + " ") for t in (term, term + "s"))


def _probes(n: int) -> list[int]:
    return sorted({min(n, k) for k in (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20, n)})


def first_suggested(term: str, country: str = "us") -> dict[str, Any] | None:
    """Fewest letters typed before Google suggests the term (its plural, or a longer query starting with either).

    Binary search over the same prefix lengths the App Store probe uses (3-4 calls; prefixes are cached and
    shared between terms). Returns {typed, position} with both None when Google never suggests it, or None when
    Google did not answer.
    """
    kw = " ".join(term.lower().split())
    probes = _probes(len(kw))
    found: dict[int, int | None] = {}

    def check(i: int) -> int | bool | None:
        sugg = suggest(kw[:probes[i]], country)
        if sugg is None:
            return False
        pos = next((j + 1 for j, s in enumerate(sugg) if _matches(s, kw)), None)
        found[i] = pos
        return pos

    lo, hi, best = 0, len(probes) - 1, None
    while lo <= hi:  # suggestions are close to monotonic: once suggested, longer prefixes suggest it too
        mid = (lo + hi) // 2
        pos = check(mid)
        if pos is False:
            return None
        if pos is None:
            lo = mid + 1
        else:
            best, hi = mid, mid - 1
    if best is None:
        return {"typed": None, "position": None}
    return {"typed": probes[best], "position": found[best]}


# ---------------------------------------------------------------- Trends

def _trends_json(body: str) -> Any:
    """Trends answers start with an anti-JSON-hijacking prefix such as ")]}'," before the JSON."""
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=-1)
    return json.loads(body[start:]) if start >= 0 else None


def interest(term: str, country: str = "us") -> dict[str, Any] | None:
    """Google Trends over 12 months for one term: {weeks, low_volume, change_pct}. None when Trends refused.

    Values are relative to the term's own peak, so only the direction (change_pct: last 13 weeks vs the 39
    before) and low_volume (Trends has too little data, i.e. few searches) mean anything on their own.
    """
    kw = " ".join(term.lower().split())
    key = f"{country}|{kw}"
    hit = _cached("trends", key, TRENDS_TTL)
    if isinstance(hit, dict):
        return hit
    geo = "" if country in ("", "ww") else country.upper()
    req = {"comparisonItem": [{"keyword": kw, "geo": geo, "time": "today 12-m"}], "category": 0, "property": ""}
    body = _get("trends", EXPLORE_URL, {"hl": "en-US", "tz": "0", "req": json.dumps(req)})
    try:
        widgets = (_trends_json(body) or {}).get("widgets", []) if body else None
        widget = next((w for w in widgets or [] if w.get("id") == "TIMESERIES"), None)
        if widget is None:
            return None
        body = _get("trends", MULTILINE_URL, {"hl": "en-US", "tz": "0", "req": json.dumps(widget["request"]),
                                              "token": widget["token"]})
        timeline = (_trends_json(body) or {}).get("default", {}).get("timelineData", []) if body else None
        if timeline is None:
            return None
        values = [float(p["value"][0]) for p in timeline if p.get("value")]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    out = trend_summary(values)
    _store("trends", key, out)
    return out


def trend_summary(values: list[float]) -> dict[str, Any]:
    """Direction of a weekly series: {weeks, low_volume, change_pct}."""
    if not values or max(values) <= 0:
        return {"weeks": len(values), "low_volume": True, "change_pct": None}
    recent, earlier = values[-RECENT_WEEKS:], values[:-RECENT_WEEKS]
    before = sum(earlier) / len(earlier) if earlier else 0
    change = round(100 * (sum(recent) / len(recent) - before) / before, 1) if before else None
    return {"weeks": len(values), "low_volume": False, "change_pct": change}
