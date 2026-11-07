"""`rankwell hook session-start`: a short status for Claude when a session starts.

Runs on every Claude Code session start, so it must be fast (no network, read-only database access apart from
closing collect runs a crashed collector left `running`, no migrations) and must never fail: any error still
prints valid hook JSON and exits 0.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

import click

from .. import paths, state

STALE_DAYS = 30
STDIN_BUDGET = 0.3  # seconds; the hook input is tiny and arrives at once
STDIN_MAX = 1_000_000
COLLECT_LOCK = "collect.lock"  # collect.LOCK_NAME; not imported because collect pulls in the network stack
LISTING_FIELDS = ("title", "subtitle", "description", "screenshots")
BUNDLE_RE = re.compile(r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*([^;\s]+)\s*;")
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


def _stdin_json(stream: IO[str] | None = None, budget: float = STDIN_BUDGET) -> dict[str, Any]:
    """The hook input JSON from stdin, read within `budget` seconds. Never blocks, even when the caller keeps
    stdin open without sending anything: select + non-blocking reads until EOF, a complete JSON object or the
    deadline, whichever comes first. Anything unreadable gives {}."""
    stream = sys.stdin if stream is None else stream
    if stream is None:
        return {}
    try:
        if stream.isatty():
            return {}
        fd = stream.fileno()
    except (OSError, ValueError, AttributeError):
        fd = -1
    try:
        raw = _read_fd(fd, budget) if fd >= 0 else stream.read(STDIN_MAX)  # in-memory streams cannot block
    except Exception:
        return {}
    try:
        value = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_fd(fd: int, budget: float) -> str:
    import fcntl
    import select

    deadline = time.monotonic() + budget
    chunks: list[bytes] = []
    size = 0
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    try:
        while size < STDIN_MAX:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                break
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                break  # EOF
            chunks.append(chunk)
            size += len(chunk)
            with contextlib.suppress(ValueError):
                json.loads(b"".join(chunks))
                break  # a complete JSON value: no need to wait for EOF
    finally:
        with contextlib.suppress(OSError):
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    return b"".join(chunks)[:STDIN_MAX].decode("utf-8", errors="replace")


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


def collector_running() -> bool:
    """Whether a collector holds the collection lock (same non-blocking flock probe as collect.is_locked).

    A `runs` row says `running` also after a crash; only the lock, which the kernel drops when the process
    dies, says whether a collector is really alive.
    """
    import fcntl

    try:
        fd = os.open(paths.home() / COLLECT_LOCK, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def mark_stale_runs(ids: list[int]) -> None:
    """Best effort: mark collect rows that no live collector owns as failed, with a note. The ids were read before
    the lock was found free, so a collector starting now (its row comes after it takes the lock) is never hit.
    This is the hook's only write; it never runs migrations and gives up after a short busy timeout."""
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(paths.db_path(), timeout=0.5)
        try:
            now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            note = "the collector stopped without finishing (crashed or killed); marked failed at session start"
            with conn:
                conn.executemany("UPDATE runs SET status = 'failed', finished_at = ?, summary = json_set("
                                 "CASE WHEN json_valid(summary) THEN summary ELSE '{}' END, '$.note', ?) "
                                 "WHERE id = ? AND status = 'running'", [(now, note, i) for i in ids])
        finally:
            conn.close()


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
        "SELECT id, slug, name, store_id, bundle_id, repo_path FROM apps WHERE archived_at IS NULL ORDER BY id")]
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
    else:
        lines += _unlinked_repo(here, rows)

    lines += _collection(conn, tables, day)
    lines += _due(rows, day)
    lines += _stale(conn, tables, rows, day)
    lines += _rejections(conn, tables, rows)
    return "\n".join(lines)


def _repo_root(cwd: Path) -> Path:
    """The git root above cwd, or cwd itself outside a git repo."""
    for d in (cwd, *cwd.parents):
        if (d / ".git").exists():
            return d
    return cwd


def _app_kind(root: Path) -> str | None:
    """'Flutter', 'React Native' or 'Xcode' when root holds an iOS app's code, else None. Only looks at root and
    root/ios, so it stays fast in big repos."""
    pubspec = root / "pubspec.yaml"
    if pubspec.is_file() and (root / "ios").is_dir() and re.search(r"^\s*flutter\s*:", _read(pubspec), re.M):
        return "Flutter"
    pkg = root / "package.json"
    if pkg.is_file() and re.search(r'"(react-native|expo)"\s*:', _read(pkg)):
        return "React Native"
    if any(root.glob("*.xcodeproj")) or any(root.glob("*.xcworkspace")):
        return "Xcode"
    return None


def _bundle_ids(root: Path) -> list[str]:
    """Main-target bundle ids from the Xcode projects in root and root/ios (tests and extensions dropped:
    an id that extends another one, like com.x.app.ShareExtension, is not the app's)."""
    found: set[str] = set()
    for proj in [*root.glob("*.xcodeproj"), *root.glob("ios/*.xcodeproj")]:
        pbx = proj / "project.pbxproj"
        if pbx.is_file():
            found |= {m.strip('"') for m in BUNDLE_RE.findall(_read(pbx)) if "$(" not in m}
    return sorted(b for b in found if not any(b.startswith(o + ".") for o in found))


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")[:5_000_000]
    except OSError:
        return ""


def _unlinked_repo(here: Path, rows: list[dict[str, Any]]) -> list[str]:
    """When the session is in an iOS app repo no tracked app is linked to: say so, and point at the tracked app it
    probably is (same bundle id) or at setup."""
    git_root = _repo_root(here)
    root, kind = git_root, _app_kind(git_root)
    if kind is None and here != git_root:  # an app in a subfolder of a bigger repo
        root, kind = here, _app_kind(here)
    if kind is None:
        return []
    ids = _bundle_ids(root)
    shown = f", bundle id {ids[0]}" if len(ids) == 1 else (f", bundle ids {', '.join(ids[:3])}" if ids else "")
    lines = [f"This folder is an iOS app repo ({kind}, {root}{shown}) that no tracked app is linked to."]
    match = next((a for a in rows if a["bundle_id"] and a["bundle_id"] in ids), None)
    if match and match["repo_path"]:
        lines.append(f"Its bundle id matches {match['name']} (slug '{match['slug']}'), which is linked to "
                     f"{match['repo_path']}. Ask the user whether to link this folder instead "
                     f"(update_app('{match['slug']}', repo_path=...)).")
    elif match:
        lines.append(f"Its bundle id matches {match['name']} (slug '{match['slug']}'). Offer to link it: "
                     f"update_app('{match['slug']}', repo_path='{root}').")
    else:
        unlinked = [a for a in rows if not a["repo_path"]]
        if unlinked:
            names = ", ".join(f"{a['name']} (slug '{a['slug']}')" for a in unlinked[:5])
            lines.append(f"Tracked apps with no repo linked: {names}. If it is one of them, link it with "
                         f"update_app(<slug>, repo_path='{root}'" + (f", bundle_id='{ids[0]}'" if len(ids) == 1
                                                                     else "") + ").")
        lines.append("If it is a new app, offer the aso-setup skill when the user wants ASO or release help "
                     "(the user can say \"set up Rankwell for this app\").")
    return lines


def _collection(conn: sqlite3.Connection, tables: set[str], day: dt.date) -> list[str]:
    if "runs" not in tables:
        return []
    last = conn.execute("SELECT max(started_at) FROM runs WHERE kind = 'collect' AND status IN ('ok', 'partial')"
                        ).fetchone()[0]
    stale = [r[0] for r in conn.execute("SELECT id FROM runs WHERE kind = 'collect' AND status = 'running'")]
    running = collector_running()
    if stale and not running:
        mark_stale_runs(stale)  # left `running` by a crashed collector: no process holds the lock
    last_day = _local_date(last)
    if last_day == day:
        return []
    when = f"last successful run {last_day.isoformat()}" if last_day else "no successful collection yet"
    if running:
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
