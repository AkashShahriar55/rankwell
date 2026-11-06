"""The launchd job for the daily collection: a per-user agent running `rankwell collect --catch-up`.

It runs at the configured time and when it is loaded (login, wake after a missed time), and the catch-up
flag makes extra runs free. Every function takes the LaunchAgents folder and a launchctl runner so tests
never touch the real ones.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, paths
from .errors import RankwellError

LABEL = "com.rankwell.collect"
LAUNCHCTL = "/bin/launchctl"
BASE_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Takes launchctl arguments, returns (exit code, output).
Launchctl = Callable[[list[str]], tuple[int, str]]


def run_launchctl(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, check=False)  # noqa: S603
    return r.returncode, (r.stdout + r.stderr).strip()


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(agents_dir: Path) -> Path:
    return agents_dir / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def parse_time(at: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", at)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise RankwellError(f"'{at}' is not a time; use HH:MM in 24h, e.g. 11:00.")
    return int(m.group(1)), int(m.group(2))


def repo_root() -> Path | None:
    """The source checkout this package runs from (src layout), when there is one."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return str(Path(found).resolve())
    for p in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv", Path("/opt/homebrew/bin/uv"),
              Path("/usr/local/bin/uv")):
        if p.exists():
            return str(p)
    return None


def rankwell_command() -> list[str]:
    """How to start `rankwell` from launchd or a detached process (absolute paths, no shell, no PATH)."""
    entry = Path(sys.executable).parent / "rankwell"
    if entry.exists():
        return [str(entry)]
    uv, root = find_uv(), repo_root()
    if uv and root:
        return [uv, "run", "--quiet", "--project", str(root), "rankwell"]
    raise RankwellError("Cannot find the rankwell command. Install uv (brew install uv) and run from the plugin.")


def record_root() -> Path | None:
    """Remember where Rankwell runs from now. The plugin folder changes on every plugin update, so the launcher
    reads this file instead of a path frozen at install time. Called by the MCP server at each start."""
    root = repo_root()
    if root:
        (paths.home() / "plugin_root").write_text(str(root))
    return root


def write_launcher(fallback: list[str]) -> Path:
    """A stable script for launchd: runs the Rankwell install recorded in plugin_root, else the fallback."""
    uv = find_uv() or "uv"
    home = paths.home()
    script = home / "bin" / "rankwell"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        "# Written by `rankwell schedule install`: starts the current Rankwell, wherever the plugin lives now.\n"
        f"ROOT=\"$(cat {shlex.quote(str(home / 'plugin_root'))} 2>/dev/null)\"\n"
        'if [ -n "$ROOT" ] && [ -f "$ROOT/pyproject.toml" ]; then\n'
        f'  exec {shlex.quote(uv)} run --quiet --project "$ROOT" rankwell "$@"\n'
        "fi\n"
        f'exec {" ".join(shlex.quote(c) for c in fallback)} "$@"\n'
    )
    script.chmod(0o755)
    return script


def build_plist(at: str, command: list[str], logs: Path) -> dict[str, Any]:
    hour, minute = parse_time(at)
    env = {"PATH": ":".join(dict.fromkeys([str(Path(command[0]).parent), *BASE_PATH.split(":")]))}
    if os.environ.get("RANKWELL_HOME"):
        env["RANKWELL_HOME"] = os.environ["RANKWELL_HOME"]
    return {
        "Label": LABEL,
        "ProgramArguments": [*command, "collect", "--catch-up", "--quiet"],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": env,
        "WorkingDirectory": str(paths.home()),
        "StandardOutPath": str(logs / "collect.log"),
        "StandardErrorPath": str(logs / "collect.err.log"),
    }


def read_plist(agents_dir: Path) -> dict[str, Any] | None:
    p = plist_path(agents_dir)
    if not p.exists():
        return None
    with p.open("rb") as f:
        data = plistlib.load(f)
    return dict(data) if isinstance(data, dict) else None


def install(at: str | None = None, *, agents_dir: Path | None = None, launchctl: Launchctl | None = None,
            command: list[str] | None = None) -> dict[str, Any]:
    """Write the agent and (re)load it. Replaces an earlier install."""
    agents = agents_dir or launch_agents_dir()
    launchctl = launchctl or run_launchctl
    when = at or config.load().collect_at
    if command is None:
        record_root()
        command = [str(write_launcher(rankwell_command()))]
    plist = build_plist(when, command, paths.logs_dir())
    agents.mkdir(parents=True, exist_ok=True)
    target = plist_path(agents)
    launchctl(["bootout", _domain(), str(target)])  # not loaded yet is fine
    with target.open("wb") as f:
        plistlib.dump(plist, f)
    code, out = launchctl(["bootstrap", _domain(), str(target)])
    if code != 0:
        raise RankwellError(f"launchctl could not load {target.name} (exit {code}): {out[:300]}")
    return {"installed": True, "at": when, "plist": str(target), "command": plist["ProgramArguments"],
            "log": plist["StandardOutPath"]}


def uninstall(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None) -> dict[str, Any]:
    target = plist_path(agents_dir or launch_agents_dir())
    launchctl = launchctl or run_launchctl
    if not target.exists():
        return {"removed": False, "plist": str(target)}
    launchctl(["bootout", _domain(), str(target)])
    target.unlink()
    return {"removed": True, "plist": str(target)}


def status(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None,
           check_loaded: bool = True) -> dict[str, Any]:
    """Installed or not, the time, and (unless check_loaded is False) whether launchd has it loaded."""
    agents = agents_dir or launch_agents_dir()
    plist = read_plist(agents)
    if plist is None:
        return {"installed": False, "plist": str(plist_path(agents))}
    cal = plist.get("StartCalendarInterval") or {}
    out: dict[str, Any] = {
        "installed": True,
        "at": f"{int(cal.get('Hour', 0)):02d}:{int(cal.get('Minute', 0)):02d}",
        "plist": str(plist_path(agents)),
        "command": plist.get("ProgramArguments"),
        "log": plist.get("StandardOutPath"),
    }
    if check_loaded:
        code, text = (launchctl or run_launchctl)(["print", f"{_domain()}/{LABEL}"])
        out["loaded"] = code == 0
        m = re.search(r"last exit code = (-?\d+)", text)
        out["last_exit"] = int(m.group(1)) if m else None
    return out
