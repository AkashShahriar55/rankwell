"""Tools: App Store screenshots. Read the live sets, check a local folder, and plan an upload (apply_plan)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, listing, screenshots
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def screenshots_live(app: str, locale: str = "en-US") -> dict[str, Any]:
        """The screenshot sets in App Store Connect for one locale: display type (APP_IPHONE_67, APP_IPAD_PRO_3GEN_129
        ...), count, file names in order and Apple's processing state. Reads the version in preparation, or the
        live version when there is none. Needs the API key."""
        a = apps.get(db(), app)
        asc = listing.connect()
        cur = screenshots.live(asc, listing.asc_app_id(asc, a), locale)
        return {**cur, "display": screenshots.live_display(cur)}

    @mcp.tool()
    @safe
    def screenshots_scan(folder: str, locale: str | None = None) -> dict[str, Any]:
        """Check a local folder of screenshots before uploading: pixel size → display type, wrong sizes, PNG alpha
        (Apple rejects it), more than 10 per set, file order. Layouts: <folder>/<locale>/<display type or any>/
        01-name.png, <folder>/<locale>/*.png, or a flat folder with `locale`. Nothing goes to App Store Connect."""
        result = screenshots.scan(folder, locale)
        return {**result, "display": screenshots.scan_display(result)}

    @mcp.tool()
    @safe
    def plan_screenshots(app: str, folder: str, locale: str = "en-US", mode: str = "replace") -> dict[str, Any]:
        """Plan uploading a folder's screenshots to the version in preparation for one locale, per display type,
        in file-name order. mode 'replace' makes the folder the whole set: the old screenshots of that set are
        deleted, but only after every new one finished processing (never on the live version). mode 'append'
        adds after the current ones. Sets not in the folder stay as they are. Show the plan to the user and ask
        before calling apply_plan; for many or large files suggest the terminal command in its notes."""
        a = apps.get(db(), app)
        return screenshots.present(screenshots.plan_screenshots(db(), listing.connect(), a, locale, folder, mode))
