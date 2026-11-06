"""Public App Store data: search, lookup, autocomplete, subtitle, category charts, reviews.

Only the iTunes Search and Lookup APIs are documented. Autocomplete, the web page (subtitle) and the RSS
feeds are public but unofficial and can change without notice. Search and lookup allow about 20 calls a
minute, so they share one throttle.
"""

from __future__ import annotations

import json
import plistlib
import re
from html import unescape
from typing import Any

from ..errors import StoreError
from . import http

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
APP_UA = "AppStore/3.0 iOS/17.0 model/iPhone15,2"
WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
SEARCH_INTERVAL = 3.1
HINTS_INTERVAL = 0.3

# Apple storefront ids (needed by the autocomplete endpoint).
STOREFRONTS = {
    "us": 143441, "gb": 143444, "de": 143443, "fr": 143442, "ca": 143455, "au": 143460, "in": 143467,
    "jp": 143462, "es": 143454, "it": 143450, "nl": 143452, "br": 143503, "mx": 143468, "se": 143456,
    "bd": 143490, "kr": 143466, "cn": 143465, "tw": 143470, "hk": 143463, "sg": 143464, "ch": 143459,
    "at": 143445, "be": 143446, "dk": 143458, "no": 143457, "fi": 143447, "ie": 143449, "nz": 143461,
    "pl": 143478, "pt": 143453, "ru": 143469, "tr": 143480, "sa": 143479, "ae": 143481, "id": 143476,
    "th": 143475, "my": 143473, "ph": 143474, "vn": 143471, "za": 143472, "ar": 143505, "cl": 143483,
    "co": 143501, "pk": 143477, "eg": 143516, "il": 143491,
}
CHARTS = {"free": "topfreeapplications", "paid": "toppaidapplications", "grossing": "topgrossingapplications"}

App = dict[str, Any]


def _json(method: str, url: str, **kw: Any) -> Any:
    r = http.request(method, url, **kw)
    if r.status_code >= 400:
        raise StoreError(f"App Store returned {r.status_code} for {url.split('?')[0]}", r.status_code)
    try:
        return r.json()
    except json.JSONDecodeError as e:
        raise StoreError(f"App Store sent an unreadable answer for {url.split('?')[0]}") from e


def search(term: str, country: str = "us", limit: int = 10) -> list[App]:
    """Apps for a search term in result order (close to, not identical with, the live App Store order)."""
    http.throttle("itunes", SEARCH_INTERVAL)
    params = {"term": term, "entity": "software", "country": country, "limit": min(limit, 200)}
    return list(_json("GET", SEARCH_URL, params=params).get("results", []))


def lookup(store_ids: list[int], country: str = "us") -> dict[int, App]:
    """Listing data for up to 200 apps in one call: {store_id: result}. Missing apps are left out."""
    out: dict[int, App] = {}
    for i in range(0, len(store_ids), 200):
        http.throttle("itunes", SEARCH_INTERVAL)
        ids = ",".join(str(s) for s in store_ids[i : i + 200])
        data = _json("GET", LOOKUP_URL, params={"id": ids, "country": country, "entity": "software"})
        out.update({int(r["trackId"]): r for r in data.get("results", []) if "trackId" in r})
    return out


def autocomplete(prefix: str, country: str = "us") -> list[str]:
    """App Store search suggestions for a prefix, in order."""
    sf = STOREFRONTS.get(country)
    if sf is None:
        raise StoreError(f"Unknown country '{country}'. Known: {', '.join(sorted(STOREFRONTS))}")
    http.throttle("hints", HINTS_INTERVAL)
    r = http.request(
        "GET",
        HINTS_URL,
        params={"clientApplication": "Software", "term": prefix},
        headers={"User-Agent": APP_UA, "X-Apple-Store-Front": f"{sf}-1,29"},
    )
    if r.status_code >= 400:
        raise StoreError(f"App Store autocomplete returned {r.status_code}", r.status_code)
    try:
        data = plistlib.loads(r.content)
    except Exception:
        return []
    return [h["term"] for h in data.get("hints", []) if "term" in h]


def subtitle(store_id: int, country: str = "us") -> str | None:
    """Subtitle from the public product page (no API has it). None when not found."""
    r = http.request("GET", f"https://apps.apple.com/{country}/app/id{store_id}", headers={"User-Agent": WEB_UA})
    if r.status_code >= 400:
        return None
    html = r.text
    h1 = html.find("</h1>")
    m = re.search(r'<p class="subtitle[^"]*"[^>]*>(.*?)</p>', html[h1:] if h1 >= 0 else html, re.S)
    if not m:  # newer page layout keeps it in the JSON-LD / meta description block
        m = re.search(r'"subtitle"\s*:\s*"([^"]+)"', html)
    return unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else None


def chart(genre_id: int, country: str = "us", kind: str = "free", limit: int = 100) -> list[dict[str, Any]]:
    """Category chart in rank order: [{rank, store_id, name}] (the feed caps at 100 for most charts)."""
    url = f"https://itunes.apple.com/{country}/rss/{CHARTS[kind]}/limit={limit}/genre={genre_id}/json"
    entries = _json("GET", url).get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return [
        {"rank": i + 1, "store_id": int(e["id"]["attributes"]["im:id"]), "name": e["im:name"]["label"]}
        for i, e in enumerate(entries)
    ]


def reviews(store_id: int, country: str = "us", page: int = 1) -> list[dict[str, Any]]:
    """Newest customer reviews (50 per page, up to 10 pages) from the RSS feed."""
    url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={store_id}/sortby=mostrecent/json"
    try:
        entries = _json("GET", url).get("feed", {}).get("entry", [])
    except StoreError:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for e in entries:
        if "im:rating" not in e:  # the first entry can be the app itself
            continue
        out.append({
            "id": e["id"]["label"],
            "store_id": store_id,
            "country": country,
            "rating": int(e["im:rating"]["label"]),
            "title": e.get("title", {}).get("label"),
            "content": e.get("content", {}).get("label"),
            "author": e.get("author", {}).get("name", {}).get("label"),
            "version": e.get("im:version", {}).get("label"),
            "reviewed_at": e.get("updated", {}).get("label"),
        })
    return out


def snapshot_fields(result: App) -> dict[str, Any]:
    """The listing fields Rankwell keeps from a lookup result."""
    return {
        "title": result.get("trackName"),
        "seller": result.get("sellerName"),
        "version": result.get("version"),
        "rating_count": result.get("userRatingCount"),
        "rating_avg": round(float(result.get("averageUserRating") or 0), 2),
        "price": result.get("price"),
        "genre_id": int(result["primaryGenreId"]) if result.get("primaryGenreId") else None,
        "released_at": (result.get("releaseDate") or "")[:10] or None,
        "description": result.get("description"),
        "release_notes": result.get("releaseNotes"),
        "screenshots": result.get("screenshotUrls") or [],
    }


def store_id_from(value: str | int) -> int:
    """Accept 6759825215, 'id6759825215' or an apps.apple.com URL."""
    if isinstance(value, int):
        return value
    m = re.search(r"id(\d{6,})", value) or re.fullmatch(r"\s*(\d{6,})\s*", value)
    if not m:
        raise StoreError(f"No App Store id in '{value}'")
    return int(m.group(1))
