"""Tools: App Store metadata. Read live, check, keep drafts, and plan changes (applied with apply_plan)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, listing, secrets
from ..errors import RankwellError
from .common import db, safe


def _tracked(app: apps.App) -> list[str]:
    return sorted({k["term"] for k in apps.keywords(db(), app["id"])})


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def listing_live(app: str, locale: str = "en-US") -> dict[str, Any]:
        """The app's metadata in App Store Connect for one locale (name, subtitle, keywords, description, promo
        text, What's New, URLs), and which version is in preparation and which is live. Needs the API key."""
        a = apps.get(db(), app)
        asc = listing.connect()
        cur = listing.live(asc, listing.asc_app_id(asc, a), locale)
        return {**cur, "display": listing.live_display(cur)}

    @mcp.tool()
    @safe
    def listing_check(app: str, locale: str = "en-US", fields: dict[str, str] | None = None,
                      include_live: bool = True) -> dict[str, Any]:
        """Check listing text before saving or pushing it: character limits, spaces after commas in the keyword
        field, words repeated across name/subtitle/keywords, plural duplicates, wasted words ('app', category
        names) and tracked keywords that are not covered. Checks `fields` on top of the saved draft, on top of
        the live listing (when a key is set and include_live).
        Fields: name (30), subtitle (30), keywords (100, comma-separated, no spaces),
        promotional_text (170), description (4000), whats_new (4000), support_url, marketing_url, privacy_url,
        privacy_choices_url."""
        a = apps.get(db(), app)
        base: dict[str, Any] = {}
        if include_live and secrets.has_asc_key():
            try:
                asc = listing.connect()
                base = {k: v for k, v in listing.live(asc, listing.asc_app_id(asc, a), locale)["fields"].items() if v}
            except RankwellError:
                base = {}
        merged = {**base, **listing.draft(db(), a["id"], locale), **(fields or {})}
        if not merged:
            raise RankwellError("Nothing to check: pass fields, save a draft, or set the App Store Connect key.")
        result = listing.check(merged, _tracked(a), a.get("genre_id"))
        return {**result, "checked": merged, "display": listing.check_display(result)}

    @mcp.tool()
    @safe
    def listing_draft_save(app: str, fields: dict[str, str], locale: str = "en-US") -> dict[str, Any]:
        """Save listing text as a draft for an app and locale (merged into the existing draft; '' removes a
        field). Nothing goes to App Store Connect. Returns the draft and its check.
        Fields: name (30), subtitle (30), keywords (100, comma-separated, no spaces),
        promotional_text (170), description (4000), whats_new (4000), support_url, marketing_url, privacy_url,
        privacy_choices_url."""
        a = apps.get(db(), app)
        saved = listing.save_draft(db(), a["id"], locale, dict(fields))
        result = listing.check(saved, _tracked(a), a.get("genre_id"))
        return {"draft": saved, "locale": locale, **result, "display": listing.check_display(result)}

    @mcp.tool()
    @safe
    def plan_listing(app: str, locale: str = "en-US", fields: dict[str, str] | None = None) -> dict[str, Any]:
        """Plan a metadata change in App Store Connect for one locale: current → new per field, with notes
        (name/subtitle/keywords need a version in preparation; promo text can change on the live version).
        Uses `fields`, or the saved draft when fields is empty. Show the plan to the user and ask before
        calling apply_plan.
        Fields: name (30), subtitle (30), keywords (100, comma-separated, no spaces),
        promotional_text (170), description (4000), whats_new (4000), support_url, marketing_url, privacy_url,
        privacy_choices_url."""
        a = apps.get(db(), app)
        want = dict(fields) if fields else listing.draft(db(), a["id"], locale)
        if not want:
            raise RankwellError(f"No fields given and no draft for {locale} (listing_draft_save).")
        result = listing.check(want, _tracked(a), a.get("genre_id"))
        plan = listing.plan_listing(db(), listing.connect(), a, locale, dict(want))
        out = listing.present(plan)
        if result["problems"] or result["tips"]:
            out["check"] = result
            out["display"] += "\n\n" + listing.check_display(result)
        return out

    @mcp.tool()
    @safe
    def plan_create_version(app: str, version: str) -> dict[str, Any]:
        """Plan a new iOS version in preparation (e.g. '1.3'), needed before name, subtitle, keywords or
        description can change. Show the plan to the user and ask before calling apply_plan."""
        a = apps.get(db(), app)
        return listing.present(listing.plan_create_version(db(), listing.connect(), a, version))
