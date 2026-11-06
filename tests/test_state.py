import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from rankwell import apps, state
from rankwell import db as dbmod
from rankwell.errors import RankwellError
from rankwell.mcp import tools_state

APP = {"name": "Demo", "store_id": 123, "bundle_id": "com.demo", "countries": ["us", "gb"]}


def test_init_writes_templates_and_keeps_existing(tmp_path: Path) -> None:
    (tmp_path / ".aso").mkdir()
    (tmp_path / ".aso" / "app.md").write_text("# Mine\n")
    r = state.init(tmp_path, APP)
    assert r["kept"] == ["app.md"]
    assert set(r["created"]) == {"decisions.md", "progress.md", "experiments.md", "release.toml"}
    assert (tmp_path / ".aso" / "app.md").read_text() == "# Mine\n"
    assert all(line.startswith("#") or not line.strip()
               for line in (tmp_path / ".aso" / "release.toml").read_text().splitlines())
    state.init(tmp_path, APP)
    assert "id123" not in (tmp_path / ".aso" / "app.md").read_text()
    fresh = tmp_path / "other"
    fresh.mkdir()
    state.init(fresh, APP)
    text = (fresh / ".aso" / "app.md").read_text()
    assert "# Demo" in text and "apps.apple.com/app/id123" in text and "us, gb" in text


def test_notes_newest_first_grouped_by_day(tmp_path: Path) -> None:
    state.init(tmp_path, APP)
    state.note(tmp_path, "decision", "First", today="2026-11-01")
    state.note(tmp_path, "decision", "Second\nwith a reason", today="2026-11-01")
    state.note(tmp_path, "decision", "Third", today="2026-11-03")
    state.note(tmp_path, "decision", "Late entry", today="2026-11-02")
    d = state.read(tmp_path)["decisions"]
    assert [(e["date"], e["text"]) for e in d] == [
        ("2026-11-03", "Third"), ("2026-11-02", "Late entry"), ("2026-11-01", "Second\nwith a reason"),
        ("2026-11-01", "First")]
    text = (tmp_path / ".aso" / "decisions.md").read_text()
    assert text.count("## 2026-11-01") == 1


def test_todo_and_done(tmp_path: Path) -> None:
    state.note(tmp_path, "todo", "Rewrite camera permission text", today="2026-11-01")
    state.note(tmp_path, "todo", "Add German listing", today="2026-11-01")
    assert state.read(tmp_path)["open_items"] == ["Add German listing", "Rewrite camera permission text"]
    r = state.note(tmp_path, "done", "camera permission", today="2026-11-02")
    assert r["closed_item"] == "Rewrite camera permission text"
    s = state.read(tmp_path)
    assert s["open_items"] == ["Add German listing"]
    assert s["progress"][0]["text"] == "Done: Rewrite camera permission text"
    state.note(tmp_path, "done", "Something not listed", today="2026-11-02")
    assert "- [x] Something not listed" in (tmp_path / ".aso" / "progress.md").read_text()


def test_note_errors(tmp_path: Path) -> None:
    with pytest.raises(RankwellError, match="Unknown note kind"):
        state.note(tmp_path, "idea", "x")
    with pytest.raises(RankwellError, match="empty"):
        state.note(tmp_path, "decision", "  ")


def test_experiment_round_trip(tmp_path: Path) -> None:
    state.init(tmp_path, APP)
    e1 = state.open_experiment(tmp_path, "If the name leads with 'habit tracker', its rank rises",
                               "Name: HabitKit -> Habit Tracker - HabitKit", "rank for 'habit tracker' (us)",
                               "#34 avg (2026-10-18..2026-10-31)", today="2026-11-01")
    assert e1["id"] == "EXP-001" and e1["check_after"] == "2026-11-15" and e1["status"] == "open"
    e2 = state.open_experiment(tmp_path, "h", "Icon v2", "conversion", "3.1%", title="New icon",
                               check_after="2026-11-30", today="2026-11-02")
    assert e2["id"] == "EXP-002" and e2["title"] == "New icon"
    assert [e["id"] for e in state.experiments(tmp_path)] == ["EXP-002", "EXP-001"]
    assert state.due(tmp_path, "2026-11-14") == []
    due = state.due(tmp_path, "2026-11-16")
    assert [e["id"] for e in due] == ["EXP-001"] and due[0]["days_overdue"] == 1
    closed = state.close_experiment(tmp_path, "exp-001", "#12 avg (2026-11-02..2026-11-15)", "Confirmed",
                                    today="2026-11-16")
    assert closed["status"] == "closed" and closed["verdict"] == "confirmed" and closed["closed"] == "2026-11-16"
    assert closed["baseline"] == "#34 avg (2026-10-18..2026-10-31)"
    assert state.due(tmp_path, "2026-12-31")[0]["id"] == "EXP-002"
    progress = [p["text"] for p in state.read(tmp_path)["progress"]]
    assert progress[0].startswith("Closed EXP-001") and any(p.startswith("Opened EXP-002") for p in progress)


def test_experiment_validation(tmp_path: Path) -> None:
    with pytest.raises(RankwellError, match="after today"):
        state.open_experiment(tmp_path, "h", "c", "m", "b", check_after="2026-11-01", today="2026-11-01")
    with pytest.raises(RankwellError, match="date like"):
        state.open_experiment(tmp_path, "h", "c", "m", "b", check_after="next week")
    with pytest.raises(RankwellError, match="baseline"):
        state.open_experiment(tmp_path, "h", "c", "m", " ")
    state.open_experiment(tmp_path, "h", "c", "m", "b", today="2026-11-01")
    with pytest.raises(RankwellError, match="Verdict"):
        state.close_experiment(tmp_path, "EXP-001", "r", "great")
    with pytest.raises(RankwellError, match="No experiment 'EXP-9'"):
        state.close_experiment(tmp_path, "EXP-9", "r", "failed")


HAND_EDITED = """\
# Experiments

Some notes I wrote.

## 2026-10-01 — PassFirst — subtitle change
- **Hypothesis / insight tested:** keyword in subtitle lifts rank
- **Change:** "Study app" → "Driving theory test"
- **Result:** #40 → #18 after 2 weeks
- **Verdict:** confirmed

## exp-7: Icon with wordmark
**Status:** open
**Opened:** 2026-10-20
- Metric: conversion from search
- Baseline: 2.9% (Oct 6-19)
- Check after: 2026-11-03
- Note: run with no ads
- Change: new icon with the name
  on a blue background
"""


def test_hand_edited_experiments(tmp_path: Path) -> None:
    (tmp_path / ".aso").mkdir()
    p = tmp_path / ".aso" / "experiments.md"
    p.write_text(HAND_EDITED)
    exps = state.experiments(tmp_path)
    old, icon = exps
    assert old["status"] == "closed" and old["verdict"] == "confirmed" and old["opened"] == "2026-10-01"
    assert old["hypothesis"] == "keyword in subtitle lifts rank"
    assert icon["id"] == "EXP-7" and icon["status"] == "open" and icon["check_after"] == "2026-11-03"
    assert icon["change"] == "new icon with the name on a blue background"
    assert icon["extra"] == {"Note": "run with no ads"}
    assert [e["id"] for e in state.due(tmp_path, "2026-11-07")] == ["EXP-7"]
    state.close_experiment(tmp_path, "EXP-7", "3.4% (Oct 21-Nov 3)", "inconclusive", today="2026-11-07")
    text = p.read_text()
    assert "**Status:** closed" in text and "- Note: run with no ads" in text and "Some notes I wrote." in text
    assert "- Result: 3.4% (Oct 21-Nov 3)" in text and "- Verdict: inconclusive" in text
    assert "- Change: new icon with the name\n  on a blue background\n- Result:" in text
    new = state.open_experiment(tmp_path, "h", "c", "m", "b", today="2026-11-07")
    assert new["id"] == "EXP-008"
    assert state.experiments(tmp_path)[0]["id"] == "EXP-008"


def test_repo_for_errors(tmp_path: Path) -> None:
    with pytest.raises(RankwellError, match="No repo folder"):
        state.repo_for({"name": "Demo", "repo_path": None})
    with pytest.raises(RankwellError, match="does not exist"):
        state.repo_for({"name": "Demo", "repo_path": str(tmp_path / "missing")})
    assert state.repo_for({"name": "Demo", "repo_path": str(tmp_path)}) == tmp_path


def call(server: MCPServer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = asyncio.run(server.call_tool(name, args)).structured_content
    assert isinstance(out, dict)
    return out


def test_mcp_tools(tmp_path: Path) -> None:
    server = MCPServer("test")
    tools_state.register(server)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"aso_context", "aso_init", "aso_note", "experiment_open", "experiment_close",
                     "experiments_due", "code_audit"}
    apps.add(dbmod.get(), "Demo")
    assert "No repo folder" in call(server, "aso_context", {"app": "demo"})["display"]
    assert "error" in call(server, "aso_note", {"app": "demo", "kind": "decision", "text": "x"})
    repo = tmp_path / "repo"
    repo.mkdir()
    r = call(server, "aso_init", {"app": "demo", "repo_path": str(repo)})
    assert "app.md" in r["created"]
    call(server, "aso_note", {"app": "demo", "kind": "decision", "text": "Keep the name"})
    e = call(server, "experiment_open", {"app": "demo", "hypothesis": "h", "change": "c", "metric": "m",
                                         "baseline": "b", "check_after": "2099-01-01"})["experiment"]
    ctx = call(server, "aso_context", {"app": "demo"})
    assert ctx["aso"]["decisions"][0]["text"] == "Keep the name"
    assert "Open experiments" in ctx["display"] and e["id"] in ctx["display"]
    assert call(server, "experiments_due", {})["due"] == []
    closed = call(server, "experiment_close", {"app": "demo", "experiment_id": e["id"], "result": "r",
                                               "verdict": "failed"})
    assert closed["experiment"]["verdict"] == "failed"
    audit = call(server, "code_audit", {"app_or_path": "demo"})
    assert audit["kind"] == "unknown" and "Code audit" in audit["display"]
