import asyncio

from rankwell.mcp import server


def test_server_registers_tools() -> None:
    s = server.build()
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert {"list_apps", "add_app", "keyword_score", "market_check", "apply_plan"} <= names
