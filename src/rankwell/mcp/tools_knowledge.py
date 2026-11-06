"""Tools: ASO rules (shipped with Rankwell plus the user's own knowledge folders)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import knowledge, render
from .common import safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def knowledge_rules(topic: str | None = None, ids: list[str] | None = None) -> dict[str, Any]:
        """ASO rules to ground advice and to cite by id (e.g. kw-title-weight).

        topic: keywords, metadata, screenshots, ratings-reviews, conversion, review-guidelines, release,
        localization, experiments (aliases like 'icon' or 'rejection' work). ids: fetch specific rules.
        With neither, returns the list of topics and rule ids. The user's own rules come first and win when
        they disagree with a shipped rule; say so when that happens.
        """
        r = knowledge.rules(topic, ids)
        if "topics" in r:
            r["display"] = render.table(
                [{**t, "ids": ", ".join(t["ids"][:8]) + (" …" if len(t["ids"]) > 8 else "")} for t in r["topics"]],
                ["topic", "shipped", "user", "ids"], align_right=["shipped", "user"])
            return r
        rows = [{"id": x["id"], "rule": x["title"], "from": x["origin"]} for x in r["rules"]]
        r["display"] = render.table(rows, ["id", "rule", "from"])
        if r.get("missing"):
            r["display"] += "\n\nNot found: " + ", ".join(r["missing"])
        if not r["rules"] and topic:
            r["display"] = f"No rules for '{topic}'. Topics: " + ", ".join(knowledge.TOPIC_ALIASES)
        return r
