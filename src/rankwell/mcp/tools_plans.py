"""Tools: apply or inspect a write plan made by any plan_* tool."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import plans
from .common import db, safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def apply_plan(plan_id: str) -> dict[str, Any]:
        """Apply a plan the user has reviewed and approved in chat. Never call without the user's explicit yes."""
        return {"applied": plan_id, "result": plans.apply(db(), plan_id)}

    @mcp.tool()
    @safe
    def recent_writes(limit: int = 20) -> dict[str, Any]:
        """The audit log: the latest changes Rankwell made in App Store Connect."""
        return {"writes": db().all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))}
