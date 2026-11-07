"""Tools: read reviews from App Store Connect with their reply state, and reply through a plan."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, listing, render
from .. import replies as rp
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def reviews_asc(app: str, days: int = 30, max_rating: int = 3, unanswered_only: bool = True,
                    territory: str | None = None) -> dict[str, Any]:
        """Customer reviews from App Store Connect (all countries, with their review_id and reply state), newest
        first. Defaults: last 30 days, 1-3 stars, no reply yet. Use before plan_review_replies. Needs the
        App Store Connect key; reviews_list reads the public feed without one."""
        a = apps.get(db(), app)
        rows = rp.asc_reviews(listing.connect(), a, days, max_rating, unanswered_only, territory)
        return {"reviews": rows, "count": len(rows), "display": rp.reviews_display(rows)}

    @mcp.tool()
    @safe
    def plan_review_replies(app: str, replies: list[dict[str, str]]) -> dict[str, Any]:
        """Plan replies to reviews: replies = [{"review_id": ..., "text": ...}] (at most 25, each up to 5970
        characters). Shows the review, its current reply (replaced when there is one) and the new reply. Show the
        plan, ask the user, then apply_plan."""
        a = apps.get(db(), app)
        return rp.present(rp.plan_replies(db(), listing.connect(), a, replies))

    @mcp.tool()
    @safe
    def review_replies_sent(app: str, limit: int = 20) -> dict[str, Any]:
        """Replies Rankwell has sent for this app, newest first."""
        rows = rp.history(db(), app, limit)
        view = [{**r, "body": (r["body"][:80] + "…") if len(r["body"]) > 80 else r["body"]} for r in rows]
        return {"replies": rows, "display": render.table(view, ["replied_at", "rating", "territory", "state", "body"])}
