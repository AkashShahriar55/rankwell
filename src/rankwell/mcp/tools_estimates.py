"""Tools: download estimates from chart positions, calibrated on the user's own App Store Connect downloads."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import estimates
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def download_estimate(app_or_competitor: str, country: str | None = None) -> dict[str, Any]:
        """Estimated daily downloads of one of the user's apps or a tracked competitor (name or App Store id),
        from its newest free/paid category chart rank and a rank → downloads curve fitted on the user's own App
        Store Connect downloads. Always an estimate with a range and the number of data points behind it; never
        present it as a real number. When the user's apps have not charted yet, says how much data exists."""
        return estimates.download_estimate(db(), app_or_competitor, country)
