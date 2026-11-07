"""Tools: setup checks, saving the App Store Connect key from a file path, `rankwell` on the PATH."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import setup
from .common import safe


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def setup_status() -> dict[str, Any]:
        """What is set up and what is missing (Python, uv, apps, App Store Connect key, last collection, daily
        schedule, Xcode tools, `rankwell` in the terminal), each with the fix."""
        rows = setup.checks()
        return {"checks": rows, "ready": all(r["ok"] or r["optional"] for r in rows),
                "display": setup.checks_display(rows)}

    @mcp.tool()
    @safe
    def asc_key_save(key_path: str, issuer_id: str, key_id: str | None = None) -> dict[str, Any]:
        """Store an App Store Connect API key in the macOS Keychain from its .p8 file path. Rankwell reads the
        file itself; never read or show the .p8 content. key_id is taken from AuthKey_<KEY ID>.p8 when left out.
        Ask the user before calling: this changes their Keychain."""
        return setup.save_asc_key(key_path, issuer_id, key_id)

    @mcp.tool()
    @safe
    def cli_install() -> dict[str, Any]:
        """Link ~/.local/bin/rankwell so the user can run `rankwell` in any terminal (survives plugin updates).
        Ask the user before calling."""
        return setup.install_cli()
