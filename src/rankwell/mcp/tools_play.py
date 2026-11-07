"""Tools: Google Play for the user's own apps. Listing, tracks, bundle upload, reviews and replies.

Writes are plans (applied with apply_plan after the user's yes). Needs a Play service account key
(`rankwell play init --key <file.json>`) and the app's play_package (update_app).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, listing, play
from ..errors import RankwellError
from .common import db, safe


def _tracked(app: apps.App) -> list[str]:
    return sorted({k["term"] for k in apps.keywords(db(), app["id"])})


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def play_listing_live(app: str, language: str = "en-US") -> dict[str, Any]:
        """The app's Google Play store listing for one language: title, short description, full description,
        video URL, and which languages exist. Needs the Play key and the app's play_package."""
        a = apps.get(db(), app)
        cur = play.live(play.connect(), play.package_of(a), language)
        return {**cur, "display": play.live_display(cur)}

    @mcp.tool()
    @safe
    def play_listing_check(app: str, fields: dict[str, str], language: str = "en-US",
                           include_live: bool = True) -> dict[str, Any]:
        """Check Google Play listing text before planning it: limits (title 30, short_description 80,
        full_description 4000), spaces, words Google's metadata policy bans in the title (best, top, #1, free,
        new, sale...), emoji, and tracked keywords missing from all three fields (Play indexes the full
        description too). Checks `fields` on top of the live listing when include_live and a key is set."""
        a = apps.get(db(), app)
        base: dict[str, str] = {}
        if include_live and a.get("play_package"):
            try:
                base = {k: v for k, v in play.live(play.connect(), a["play_package"], language)["fields"].items()
                        if v}
            except RankwellError:
                base = {}
        result = play.check({**base, **fields}, _tracked(a))
        return {**result, "checked": {**base, **fields}, "display": listing.check_display(result)}

    @mcp.tool()
    @safe
    def play_plan_listing(app: str, fields: dict[str, str], language: str = "en-US") -> dict[str, Any]:
        """Plan a Google Play store listing change for one language: current → new per field.
        Fields: title (30), short_description (80), full_description (4000), video (YouTube URL).
        Applied as one Play edit (validated, then committed). Show the plan and ask before apply_plan."""
        a = apps.get(db(), app)
        result = play.check(fields, _tracked(a))
        out = listing.present(play.plan_listing(db(), play.connect(), a, language, fields))
        if result["problems"] or result["tips"]:
            out["check"] = result
            out["display"] += "\n\n" + listing.check_display(result)
        return out

    @mcp.tool()
    @safe
    def play_tracks(app: str) -> dict[str, Any]:
        """Every release on the app's Google Play tracks (internal, alpha/closed, beta/open, production):
        release name, status (completed, inProgress, halted, draft), version codes, rollout %, note languages."""
        a = apps.get(db(), app)
        rows = play.tracks(play.connect(), play.package_of(a))
        return {"tracks": rows, "display": play.tracks_display(rows)}

    @mcp.tool()
    @safe
    def play_plan_upload(app: str, track: str = "internal", aab: str | None = None,
                         release_notes: dict[str, str] | None = None, rollout: float | None = None,
                         status: str | None = None, name: str | None = None) -> dict[str, Any]:
        """Plan uploading the newest .aab (build/app/outputs/bundle/release/*.aab in the app repo, or this path)
        to Google Play and making it the release on a track: internal, alpha, beta or production.
        status: completed (default), draft (needed while the app was never published) or a staged rollout via
        rollout (0-1, e.g. 0.1 = 10% → inProgress). release_notes: {"en-US": "..."} (500 characters each).
        Show the plan and ask before apply_plan. For a large .aab, the user can run
        `rankwell play upload <app> --track <track>` in the terminal instead."""
        a = apps.get(db(), app)
        return listing.present(play.plan_upload(db(), play.connect(), a, aab, track, release_notes, rollout,
                                                status, name))

    @mcp.tool()
    @safe
    def play_reviews(app: str, days: int = 30, unanswered: bool = False, refresh: bool = True,
                     limit: int = 50) -> dict[str, Any]:
        """The app's Google Play reviews, newest first: stars, text, version, whether it has a reply.
        refresh fetches from Play first (the API only returns reviews from the last 7 days, so Rankwell keeps
        older ones it has seen). Use the id with play_plan_review_reply."""
        a = apps.get(db(), app)
        fetched = play.fetch_reviews(db(), play.connect(), a) if refresh else None
        rows = play.reviews(db(), a, days, limit, unanswered)
        return {"reviews": rows, "fetched": fetched, "display": play.reviews_display(rows)}

    @mcp.tool()
    @safe
    def play_plan_review_reply(app: str, review_id: str, text: str) -> dict[str, Any]:
        """Plan a public reply to one Google Play review (350 characters at most; replying again replaces the
        old reply). Show the plan and ask before apply_plan."""
        a = apps.get(db(), app)
        return listing.present(play.plan_review_reply(db(), play.connect(), a, review_id, text))
