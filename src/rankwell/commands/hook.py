"""`rankwell hook session-start`: a short status for Claude when a session starts.

Runs on every Claude Code session start, so it must be fast (no network, read-only database access, no
migrations) and must never fail: any error still prints valid hook JSON and exits 0.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from .. import paths, state

STALE_DAYS = 30
LISTING_FIELDS = ("title", "subtitle", "description", "screenshots")
SETUP_HINT = ("Rankwell (App Store Optimization assistant) is installed but has no apps yet. When the user wants "
              "ASO or App Store help, start with the aso-setup skill (the user can say \"set up Rankwell\").")


def register(main: click.Group) -> None:
    @main.group()
    def hook() -> None:
        """Claude Code hooks (the plugin runs these)."""

    @hook.command("session-start")
    def session_start() -> None:
        """Print session-start context as hook JSON. Always exits 0."""
        try:
            data = _stdin_json()
            cwd = Path(str(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
            text = build_context(cwd)
        except Exception:
            text = "Rankwell: status unavailable right now (run `rankwell doctor` in a terminal to check the setup)."
        emit(text)


def emit(text: str) -> None:
    with contextlib.suppress(Exception):
        click.echo(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


def _stdin_json() -> dict[str, Any]:
    stream = sys.stdin
    if stream is None or stream.isatty():
        return {}
    try:
        import select

        ready, _, _ = select.select([stream], [], [], 0.2)
        if not ready:
            return {}
    except (ValueError, OSError, AttributeError, TypeError):
        pass
    except Exception:
        return {}
    try:
        raw = stream.read(1_000_000)
        value = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def spawn_catch_up() -> bool:
    """Start `rankwell collect --catch-up` detached; output goes to the logs folder. False when disabled."""
    if os.environ.get("RANKWELL_HOOK_NO_SPAWN"):
        return False
    exe = Path(sys.executable).with_name("rankwell")
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-c", "from rankwell.cli import run; run()"]
    with (paths.logs_dir() / "catch-up.log").open("ab") as out:
        subprocess.Popen([*cmd, "collect", "--catch-up"], stdin=subprocess.DEVNULL, stdout=out,  # noqa: S603
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return True


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _local_date(ts: str | None) -> dt.date | None:
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t.astimezone() if t.tzinfo else t).date()


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def build_context(cwd: Path, today: dt.date | None = None) -> str:
    """The context text: tracked apps, active app, collection freshness, due experiments, nudges."""
    day = today or dt.date.today()
    db_file = paths.db_path()
    if not db_file.exists():
        return SETUP_HINT
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=1)
    try:
        return _context(conn, cwd, day)
    finally:
        conn.close()


def _context(conn: sqlite3.Connection, cwd: Path, day: dt.date) -> str:
    conn.row_factory = sqlite3.Row
    tables = _tables(conn)
    if "apps" not in tables:
        return SETUP_HINT
    rows = [dict(r) for r in conn.execute(
        "SELECT id, slug, name, store_id, repo_path FROM apps WHERE archived_at IS NULL ORDER BY id")]
    if not rows:
        return SETUP_HINT
    lines: list[str] = []
    names = ", ".join(f"{a['name']} ({'live' if a['store_id'] else 'pre-launch'})" for a in rows[:8])
    lines.append(f"Rankwell (ASO assistant) tracks {len(rows)} app(s): {names}.")

    try:
        here = cwd.expanduser().resolve()
    except OSError:
        here = cwd
    for a in rows:
        if not a["repo_path"]:
            continue
        try:
            rp = Path(a["repo_path"]).expanduser().resolve()
        except OSError:
            continue
        if _within(here, rp):
            lines.append(f"Active app in this repo: {a['name']} (slug '{a['slug']}'). Call aso_context('{a['slug']}') "
                         "before ASO work on it.")
            break

    lines += _collection(conn, tables, day)
    lines += _due(rows, day)
    lines += _stale(conn, tables, rows, day)
    lines += _rejections(conn, tables, rows)
    return "\n".join(lines)


def _collection(conn: sqlite3.Connection, tables: set[str], day: dt.date) -> list[str]:
    if "runs" not in tables:
        return []
    last = conn.execute("SELECT max(started_at) FROM runs WHERE kind = 'collect' AND status IN ('ok', 'partial')"
                        ).fetchone()[0]
    running = conn.execute("SELECT started_at FROM runs WHERE kind = 'collect' AND status = 'running' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    last_day = _local_date(last)
    if last_day == day:
        return []
    started = _local_date(running[0]) if running else None
    when = f"last successful run {last_day.isoformat()}" if last_day else "no successful collection yet"
    if started == day:
        return [f"Data collection: {when}; a collection is running now."]
    with contextlib.suppress(Exception):
        if spawn_catch_up():
            return [f"Data collection: {when}; a catch-up collection was started in the background "
                    "(results in a few minutes; collection_status shows progress)."]
    return [f"Data collection: {when}. Offer collect_now to refresh it."]


def _due(apps: list[dict[str, Any]], day: dt.date) -> list[str]:
    out: list[str] = []
    for a in apps:
        if not a["repo_path"]:
            continue
        with contextlib.suppress(Exception):
            for e in state.due(a["repo_path"], day.isoformat()):
                out.append(f"{a['name']} {e['id']} \"{e.get('title') or ''}\" (check after {e.get('check_after')})")
    if not out:
        return []
    more = f" and {len(out) - 5} more" if len(out) > 5 else ""
    return [f"Experiments due for a verdict: {'; '.join(out[:5])}{more}. Offer to close them (aso-experiment skill)."]


def _stale(conn: sqlite3.Connection, tables: set[str], apps: list[dict[str, Any]], day: dt.date) -> list[str]:
    if not {"changes", "snapshots"} <= tables:
        return []
    out = []
    marks = ", ".join("?" for _ in LISTING_FIELDS)
    for a in apps:
        if not a["store_id"]:
            continue
        changed = conn.execute(f"SELECT max(date) FROM changes WHERE store_id = ? AND field IN ({marks})",
                               (a["store_id"], *LISTING_FIELDS)).fetchone()[0]
        first = conn.execute("SELECT min(date) FROM snapshots WHERE store_id = ?", (a["store_id"],)).fetchone()[0]
        ref = changed or first
        try:
            age = (day - dt.date.fromisoformat(str(ref)[:10])).days if ref else None
        except ValueError:
            age = None
        if age is not None and age >= STALE_DAYS:
            since = "unchanged for" if changed else "unchanged since tracking began,"
            out.append(f"{a['name']} listing {since} {age} days")
    if not out:
        return []
    return [f"Nudge: {'; '.join(out)}. A keyword and listing review may be due (aso-today, then aso-listing)."]


def _rejections(conn: sqlite3.Connection, tables: set[str], apps: list[dict[str, Any]]) -> list[str]:
    if "rejections" not in tables:
        return []
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rejections)")}
    if "resolved_at" in cols:
        where = "resolved_at IS NULL"
    elif "status" in cols:
        where = "lower(coalesce(status, '')) NOT IN ('resolved', 'closed', 'fixed', 'approved', 'done')"
    else:
        where = "1 = 1"
    names = {a["id"]: a["name"] for a in apps}
    if "app_id" in cols:
        found = [names.get(r[0], f"app {r[0]}") for r in conn.execute(
            f"SELECT app_id, count(*) FROM rejections WHERE {where} GROUP BY app_id")]
    else:
        n = conn.execute(f"SELECT count(*) FROM rejections WHERE {where}").fetchone()[0]
        found = [f"{n} submission(s)"] if n else []
    if not found:
        return []
    return [f"Open App Review rejection: {', '.join(found)}. Offer the aso-review-fix skill."]
