import datetime as dt
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rankwell import apps, paths, state
from rankwell import db as dbmod
from rankwell.cli import main
from rankwell.commands import hook

TODAY = dt.date.today()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []

    def fake() -> bool:
        calls.append(True)
        return True

    monkeypatch.setattr(hook, "spawn_catch_up", fake)
    return calls


def run(stdin: str = "{}") -> dict:
    result = CliRunner().invoke(main, ["hook", "session-start"], input=stdin)
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return out


def context(stdin: str = "{}") -> str:
    text = run(stdin)["hookSpecificOutput"]["additionalContext"]
    assert isinstance(text, str)
    return text


def test_no_database_says_how_to_start(spawned: list[bool]) -> None:
    assert "set up Rankwell" in context()
    assert not paths.db_path().exists()
    assert spawned == []


def test_empty_database(spawned: list[bool]) -> None:
    dbmod.Database().close()
    assert "set up Rankwell" in context()


def test_broken_database_still_exits_zero(spawned: list[bool]) -> None:
    paths.db_path().write_text("this is not sqlite")
    assert "unavailable" in context()


def test_garbage_stdin(spawned: list[bool]) -> None:
    assert "set up Rankwell" in context("not json")


def test_full_context(tmp_path: Path, spawned: list[bool]) -> None:
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    db = dbmod.Database()
    live = apps.add(db, "Live App", store_id=111, repo_path=str(repo))
    apps.add(db, "Next App")
    old = (TODAY - dt.timedelta(days=45)).isoformat()
    db.execute("INSERT INTO snapshots (store_id, country, date, title) VALUES (111, 'us', ?, 'Live')", (old,))
    db.execute("INSERT INTO runs (kind, started_at, status) VALUES ('collect', '2020-01-01T10:00:00Z', 'ok')")
    db.close()
    state.open_experiment(repo, "h", "Subtitle test", "rank", "#30", title="Subtitle test",
                          check_after=(TODAY - dt.timedelta(days=1)).isoformat(),
                          today=(TODAY - dt.timedelta(days=20)).isoformat())
    text = context(json.dumps({"cwd": str(repo / "lib"), "hook_event_name": "SessionStart"}))
    assert "tracks 2 app(s)" in text and "Next App (pre-launch)" in text
    assert f"Active app in this repo: Live App (slug '{live['slug']}')" in text
    assert "last successful run 2020-01-01" in text and "started in the background" in text
    assert spawned == [True]
    assert "EXP-001" in text and "Subtitle test" in text
    assert "Live App listing unchanged since tracking began, 45 days" in text
    assert len(text) < 2000


def test_fresh_collection_and_rejections(tmp_path: Path, spawned: list[bool]) -> None:
    db = dbmod.Database()
    a = apps.add(db, "Live App", store_id=111)
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute("INSERT INTO runs (kind, started_at, status) VALUES ('collect', ?, 'ok')", (now,))
    db.execute("INSERT INTO snapshots (store_id, country, date, title) VALUES (111, 'us', ?, 'Live')",
               (TODAY.isoformat(),))
    b = apps.add(db, "Fixed App")
    db.execute("CREATE TABLE IF NOT EXISTS rejections (id INTEGER PRIMARY KEY, app_id INTEGER, text TEXT, "
               "resolved_at TEXT)")
    db.execute("INSERT INTO rejections (app_id, text) VALUES (?, 'Guideline 2.1')", (a["id"],))
    db.execute("INSERT INTO rejections (app_id, text, resolved_at) VALUES (?, 'Guideline 5.1.1', "
               "'2026-01-01T00:00:00Z')", (b["id"],))
    db.close()
    text = context(json.dumps({"cwd": str(tmp_path)}))
    assert "Data collection" not in text and spawned == []
    assert "Active app" not in text and "Nudge" not in text
    assert "Open App Review rejection: Live App." in text


def test_spawn_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RANKWELL_HOOK_NO_SPAWN", "1")
    assert hook.spawn_catch_up() is False
    db = dbmod.Database()
    apps.add(db, "Live App", store_id=111)
    db.close()
    assert "Offer collect_now" in context()


def test_hooks_json_shape() -> None:
    data = json.loads((Path(__file__).resolve().parents[1] / "hooks" / "hooks.json").read_text())
    entry = data["hooks"]["SessionStart"][0]["hooks"][0]
    assert entry["type"] == "command" and entry["timeout"] <= 10
    assert "${CLAUDE_PLUGIN_ROOT}" in entry["command"] and entry["command"].endswith("rankwell hook session-start")
