"""Tools: today's briefing, stats over time, keyword ranks, the market gap, reviews, competitor changes."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import gap, stats, today
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def app_today(app: str | None = None) -> dict[str, Any]:
        """What changed and what to do now. Use first when the user asks "how is my app doing", "what's new" or
        opens a session. With an app: movers, competitor changes, low reviews, last 7 days of App Store Connect,
        ratings, listing age and an ordered to-do list with reasons. Without: one line per app and each app's top
        to-do. Pre-launch apps get their keyword plan status and competitor activity."""
        if app:
            return today.briefing(db(), app)
        return today.overview(db())

    @mcp.tool()
    @safe
    def app_stats(app: str, metric: str, days: int = 30, country: str | None = None) -> dict[str, Any]:
        """Numbers over a period, for charts and comparisons. metric is one of:
        ranks (rank history per keyword), visibility (0-100 search presence index weighted by popularity),
        funnel (App Store Connect impressions → page views → first downloads vs the previous equal period, by
        source), ratings (count and average over time), charts (category chart positions).
        country defaults to the app's first country (the funnel covers all countries)."""
        return stats.metric(db(), app, metric, days, country)

    @mcp.tool()
    @safe
    def keyword_ranks(app: str, country: str | None = None) -> dict[str, Any]:
        """Current rank of every tracked keyword: rank, 7-day change, best in 30 days, trend sparkline (high = near
        #1), popularity and difficulty. Use for "where do I rank" and before changing keywords or metadata."""
        return stats.rank_history(db(), app, country, 30)

    @mcp.tool()
    @safe
    def gap_report(app: str, country: str | None = None) -> dict[str, Any]:
        """The app against each competitor and the median competitor: ratings, update cadence, price,
        screenshots, description length, keyword coverage in title + subtitle, keyword battle, category charts and
        complaint themes from reviews of 3★ or less, then strengths, weaknesses and opportunities with the numbers
        behind each. Use for "what am I missing", positioning, or before rewriting the listing. Reason from the
        evidence; the lists are counted rules, not conclusions."""
        return gap.report(db(), app, country)

    @mcp.tool()
    @safe
    def reviews_list(app: str, max_rating: int = 5, days: int = 30, competitors: bool = False) -> dict[str, Any]:
        """Customer reviews from the last `days` days with at most `max_rating` stars. competitors=True lists the
        competitors' reviews instead (what their users ask for or hate). Use to answer reviews or find themes."""
        return today.reviews(db(), app, max_rating, days, competitors=competitors)

    @mcp.tool()
    @safe
    def competitor_changes(app: str, days: int = 14) -> dict[str, Any]:
        """Competitors' listing changes (title, subtitle, version, description, screenshots, price) in the last
        `days` days as readable sentences, newest first, across the app's countries."""
        return today.competitor_changes(db(), app, days)
