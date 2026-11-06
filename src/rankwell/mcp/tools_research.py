"""Tools: keyword research and market checks (live calls to the App Store)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import render, research
from ..stores import appstore
from ..util import today
from .common import db, safe

MAX_SCORE = 25  # each score takes ~10 App Store calls; keep a tool call under a few minutes


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def keyword_ideas(seed: str, country: str = "us") -> dict[str, Any]:
        """Keyword ideas from App Store autocomplete for a seed word (seed, then seed + a..z)."""
        found = research.ideas(seed, country)
        return {"ideas": found, "count": len(found)}

    @mcp.tool()
    @safe
    def keyword_score(terms: list[str], country: str = "us") -> dict[str, Any]:
        """Popularity proxy, difficulty and top-10 strength for up to 25 keywords. Saved for later use."""
        rows = [research.score(t, country) for t in terms[:MAX_SCORE]]
        db().upsert("keyword_scores", [{**{k: r[k] for k in (
            "country", "term", "popularity", "suggested_after_chars", "difficulty", "median_ratings",
            "title_match", "top_app")}, "date": today()} for r in rows], ["country", "term", "date"])
        rows.sort(key=lambda r: (-(r["popularity"] or 0), r["difficulty"]))
        shown = [{**r, "median_ratings": render.number(r["median_ratings"]),
                  "typed": r["suggested_after_chars"] or "–"} for r in rows]
        note = "" if len(terms) <= MAX_SCORE else f"\n\nScored the first {MAX_SCORE} of {len(terms)}."
        return {"scores": rows, "display": render.table(
            shown, ["term", "popularity", "typed", "difficulty", "median_ratings", "top_app"],
            {"typed": "letters", "median_ratings": "top-10 median ratings"},
            align_right=["popularity", "typed", "difficulty", "median_ratings"]) + note}

    @mcp.tool()
    @safe
    def market_check(keyword: str, country: str = "us") -> dict[str, Any]:
        """Who owns the top 10 for a keyword and whether a new app can get in (verdict with reasons)."""
        r = research.market_check(keyword, country)
        shown = [{**a, "ratings": render.number(a["ratings"])} for a in r["apps"]]
        r["display"] = f"**{keyword}** ({country}): **{r['verdict']}**\n\n" + render.table(
            shown, ["rank", "name", "ratings", "stars", "released"], align_right=["rank", "ratings", "stars"])
        return r

    @mcp.tool()
    @safe
    def app_lookup(query: str, country: str = "us") -> dict[str, Any]:
        """Find apps on the App Store by name or id (to add a competitor or check a listing)."""
        if query.strip().isdigit() or "apps.apple.com" in query:
            sid = appstore.store_id_from(query)
            found = list(appstore.lookup([sid], country).values())
        else:
            found = appstore.search(query, country, limit=10)
        rows = [{"store_id": a["trackId"], "name": a["trackName"], "seller": a.get("sellerName"),
                 "ratings": int(a.get("userRatingCount") or 0), "genre": a.get("primaryGenreName"),
                 "url": a.get("trackViewUrl", "").split("?")[0]} for a in found]
        return {"apps": rows, "display": render.table(
            [{**r, "ratings": render.number(r["ratings"])} for r in rows],
            ["store_id", "name", "seller", "ratings", "genre"], align_right=["ratings"])}
