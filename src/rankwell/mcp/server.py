"""Rankwell MCP server (stdio). Every rankwell.mcp.tools_* module has register(mcp) and is loaded here."""

from __future__ import annotations

import importlib
import pkgutil

from mcp.server.mcpserver import MCPServer

from .. import mcp as package

INSTRUCTIONS = """\
Rankwell is the user's App Store Optimization (ASO) and release assistant for their own iOS apps.
Data comes from the daily collection (ranks, listings, reviews, charts, App Store Connect analytics) in a
local database, and each app repo's .aso/ folder (context, decisions, experiments, progress).

Rules:
- Tool results carry a `display` block (Markdown tables, sparklines, bars): show it as is, then explain.
- Never invent numbers. Say when data is missing and how to get it (e.g. the collection has not run yet).
- Anything that changes App Store Connect is two steps: a plan_* tool returns a plan with current → new
  values; show it, ask the user, and only then call apply_plan with its id.
- Popularity is a proxy from autocomplete, not search volume; compare suggested_after_chars across lengths.
"""


def build() -> MCPServer:
    server = MCPServer("rankwell", instructions=INSTRUCTIONS)
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        if info.name.startswith("tools_"):
            importlib.import_module(f"{package.__name__}.{info.name}").register(server)
    return server


def run() -> None:
    from .. import db, plans

    plans.sweep(db.get())  # expire old plans and drop any secret they still hold
    build().run()
