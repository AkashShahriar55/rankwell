import asyncio

from rankwell.mcp import server


def test_server_registers_tools() -> None:
    s = server.build()
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert {"list_apps", "add_app", "keyword_score", "market_check", "apply_plan"} <= names


def test_versions_match() -> None:
    import json
    import tomllib
    from pathlib import Path

    import rankwell

    root = Path(__file__).resolve().parents[1]
    py = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert rankwell.__version__ == py == plugin
