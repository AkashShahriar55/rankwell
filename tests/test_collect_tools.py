import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from rankwell import apps, collect, schedule, secrets
from rankwell import db as dbmod
from rankwell.cli import main
from rankwell.mcp import server, tools_collect


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(secrets, "has_asc_key", lambda: False)
    monkeypatch.setattr(schedule, "launch_agents_dir", lambda: tmp_path / "LaunchAgents")


def _call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = asyncio.run(server.build().call_tool(name, args or {}))
    return dict(result.structured_content or {})  # type: ignore[union-attr]


def test_tools_registered() -> None:
    names = {t.name for t in asyncio.run(server.build().list_tools())}
    assert {"collection_status", "collect_now"} <= names


def test_collection_status(monkeypatch: pytest.MonkeyPatch) -> None:
    d = dbmod.get()
    a = apps.add(d, "Alpha", store_id=111, countries=["us"])
    apps.add_keywords(d, a["id"], ["planner"], "us")
    apps.add(d, "Beta", countries=["us"])
    d.upsert("snapshots", [{"store_id": 111, "country": "us", "date": "2026-11-05"}], ["store_id", "country", "date"])
    d.upsert("keyword_top", [{"date": "2026-11-06", "country": "us", "term": "planner", "position": 1,
                              "store_id": 5}], ["date", "country", "term", "position"])
    d.execute("INSERT INTO runs (kind, started_at, finished_at, status, summary) VALUES "
              "('collect', '2026-11-06T10:00:00Z', '2026-11-06T10:05:00Z', 'partial', ?)",
              (json.dumps({"date": "2026-11-06", "apps": {"alpha": {"errors": ["x"]}}}),))
    out = _call("collection_status")
    alpha = next(x for x in out["apps"] if x["app"] == "Alpha")
    assert alpha["last_snapshot"] == "2026-11-05" and alpha["last_rank"] == "2026-11-06" and alpha["asc"] == "no key"
    beta = next(x for x in out["apps"] if x["app"] == "Beta")
    assert beta["asc"] == "not live" and beta["last_snapshot"] is None
    assert out["runs"][0]["errors"] == 1 and out["schedule"]["installed"] is False
    assert "not installed" in out["display"] and "| Alpha |" in out["display"]


def test_asc_state(db: dbmod.Database) -> None:
    app = {"store_id": 111}
    assert tools_collect.asc_state(db, app, True) == "requested, waiting for Apple"
    db.upsert("asc_daily", [{"store_id": 111, "date": "2020-01-01", "metric": "impressions", "source": "all",
                             "value": 1}], ["store_id", "date", "metric", "source"])
    assert tools_collect.asc_state(db, app, True).startswith("stale")


def test_collect_now_starts_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[dict[str, Any]] = []

    class FakePopen:
        pid = 4242

        def __init__(self, cmd: list[str], **kw: Any) -> None:
            started.append({"cmd": cmd, **kw})

    monkeypatch.setattr(tools_collect.subprocess, "Popen", FakePopen)
    assert _call("collect_now")["started"] is False  # no apps yet
    a = apps.add(dbmod.get(), "Alpha", store_id=111)
    apps.add_keywords(dbmod.get(), a["id"], ["a", "b"], "us")
    out = _call("collect_now", {"app": "alpha"})
    assert out["started"] is True and out["pid"] == 4242 and out["estimate_seconds"] > 6
    assert started[0]["cmd"][-4:] == ["collect", "--quiet", "--app", str(a["id"])]
    assert started[0]["start_new_session"] is True


def test_collect_now_refuses_while_running(monkeypatch: pytest.MonkeyPatch) -> None:
    apps.add(dbmod.get(), "Alpha", store_id=111)
    monkeypatch.setattr(collect, "is_locked", lambda path=None: True)
    assert _call("collect_now")["started"] is False


def test_cli_collect_without_apps() -> None:
    r = CliRunner().invoke(main, ["collect", "--quiet"])
    assert r.exit_code == 0, r.output
    assert "No apps tracked yet" in r.output
    r = CliRunner().invoke(main, ["collect", "--catch-up"])
    assert "Skipped: already collected" in r.output


def test_cli_collect_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(db: Any, refs: Any = None, on: Any = None, **kw: Any) -> dict[str, Any]:
        assert refs == ["alpha"]
        return {"date": "2026-11-07", "status": "partial", "apps": {"alpha": {
            "status": "partial", "snapshots": 2, "changes": 1, "keywords": 3, "ranks": 6, "reviews": 50,
            "charts": 1, "asc": None, "errors": ["us · rank 'x': boom"], "notes": []}}}

    monkeypatch.setattr(collect, "run", fake_run)
    r = CliRunner().invoke(main, ["collect", "--app", "alpha", "--quiet"])
    assert r.exit_code == 0, r.output
    assert "Collection 2026-11-07: partial" in r.output and "| alpha | partial | 2 | 1 | 3 | 50 | 1 |" in r.output
    assert "alpha: us · rank 'x': boom" in r.output
