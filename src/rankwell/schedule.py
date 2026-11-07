"""The scheduled daily collection: `rankwell collect --catch-up`, run once a day.

macOS: a per-user launchd agent. It runs at the configured time and when it is loaded (login, wake after a
missed time), and the catch-up flag makes extra runs free.
Linux: a systemd user timer (Persistent=true, so a missed run happens at the next start), falling back to a
tagged crontab line when systemd --user is not usable. Cron cannot catch up missed runs.

Every function takes the folder and a command runner (launchctl, systemctl, crontab) so tests never touch
the real ones.
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

UNIT = "rankwell-collect"
SERVICE_FILE = f"{UNIT}.service"
TIMER_FILE = f"{UNIT}.timer"
CRON_TAG = "# rankwell-collect"
CRON_NOTE = "Using cron (systemd user timers are not available); cron will not catch up missed days."

# Takes launchctl arguments, returns (exit code, output).
Launchctl = Callable[[list[str]], tuple[int, str]]
# Takes `systemctl --user` arguments, returns (exit code, output).
Systemctl = Callable[[list[str]], tuple[int, str]]
# Takes crontab arguments and optional stdin text, returns (exit code, output).
Crontab = Callable[[list[str], str | None], tuple[int, str]]


def run_launchctl(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, check=False)  # noqa: S603
    return r.returncode, (r.stdout + r.stderr).strip()


def run_systemctl(args: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    except FileNotFoundError:
        return 127, "systemctl not found"
    return r.returncode, (r.stdout + r.stderr).strip()


def run_crontab(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(["crontab", *args], input=stdin, capture_output=True, text=True, check=False)  # noqa: S603, S607
    except FileNotFoundError:
        return 127, "crontab not found"
    return r.returncode, (r.stdout + r.stderr).strip()


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


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


def _resolve_command(command: list[str] | None) -> list[str]:
    if command is None:
        record_root()
        command = [str(write_launcher(rankwell_command()))]
    return command


# --------------------------------------------------------------------------------------------------- dispatch


def current_platform() -> str:
    """The running OS (sys.platform); a seam so tests can pick a backend without patching sys."""
    return sys.platform


def _backend(platform: str | None, agents_dir: Path | None, launchctl: Launchctl | None) -> str:
    """'launchd' or 'linux'. An explicit platform wins; passing launchd's folder or runner means launchd."""
    if platform is None:
        if agents_dir is not None or launchctl is not None:
            return "launchd"
        platform = current_platform()
    if platform == "darwin":
        return "launchd"
    if platform.startswith("linux"):
        return "linux"
    raise RankwellError(f"Scheduling is supported on macOS and Linux, not on '{platform}'.")


def install(at: str | None = None, *, agents_dir: Path | None = None, launchctl: Launchctl | None = None,
            command: list[str] | None = None, platform: str | None = None, unit_dir: Path | None = None,
            systemctl: Systemctl | None = None, crontab: Crontab | None = None) -> dict[str, Any]:
    """Schedule the daily collection and replace an earlier install."""
    if _backend(platform, agents_dir, launchctl) == "launchd":
        return _install_launchd(at, agents_dir=agents_dir, launchctl=launchctl, command=command)
    return _install_linux(at, unit_dir=unit_dir, systemctl=systemctl, crontab=crontab, command=command)


def uninstall(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None,
              platform: str | None = None, unit_dir: Path | None = None, systemctl: Systemctl | None = None,
              crontab: Crontab | None = None) -> dict[str, Any]:
    if _backend(platform, agents_dir, launchctl) == "launchd":
        return _uninstall_launchd(agents_dir=agents_dir, launchctl=launchctl)
    return _uninstall_linux(unit_dir=unit_dir, systemctl=systemctl, crontab=crontab)


def status(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None, check_loaded: bool = True,
           platform: str | None = None, unit_dir: Path | None = None, systemctl: Systemctl | None = None,
           crontab: Crontab | None = None) -> dict[str, Any]:
    """Installed or not, the time, and (unless check_loaded is False) whether the scheduler has it active."""
    try:
        backend = _backend(platform, agents_dir, launchctl)
    except RankwellError:
        return {"installed": False}  # nothing can be scheduled here, and status is only ever a question
    if backend == "launchd":
        return _status_launchd(agents_dir=agents_dir, launchctl=launchctl, check_loaded=check_loaded)
    return _status_linux(unit_dir=unit_dir, systemctl=systemctl, crontab=crontab, check_loaded=check_loaded)


# --------------------------------------------------------------------------------------------------- launchd


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


def _install_launchd(at: str | None = None, *, agents_dir: Path | None = None,
                     launchctl: Launchctl | None = None, command: list[str] | None = None) -> dict[str, Any]:
    """Write the agent and (re)load it. Replaces an earlier install."""
    agents = agents_dir or launch_agents_dir()
    launchctl = launchctl or run_launchctl
    when = at or config.load().collect_at
    command = _resolve_command(command)
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


def _uninstall_launchd(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None) -> dict[str, Any]:
    target = plist_path(agents_dir or launch_agents_dir())
    launchctl = launchctl or run_launchctl
    if not target.exists():
        return {"removed": False, "plist": str(target)}
    launchctl(["bootout", _domain(), str(target)])
    target.unlink()
    return {"removed": True, "plist": str(target)}


def _status_launchd(*, agents_dir: Path | None = None, launchctl: Launchctl | None = None,
                    check_loaded: bool = True) -> dict[str, Any]:
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


# --------------------------------------------------------------------------------------------------- linux


def _unit_quote(arg: str) -> str:
    """Quote one word for ExecStart= (systemd expands % and $ itself)."""
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _collect_args(command: list[str]) -> list[str]:
    return [*command, "collect", "--catch-up", "--quiet"]


def build_service(command: list[str], logs: Path) -> str:
    env = [f"PATH={':'.join(dict.fromkeys([str(Path(command[0]).parent), *BASE_PATH.split(':')]))}"]
    if os.environ.get("RANKWELL_HOME"):
        env.append(f"RANKWELL_HOME={os.environ['RANKWELL_HOME']}")
    lines = [
        "[Unit]",
        "Description=Rankwell daily collection",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=" + " ".join(_unit_quote(c) for c in _collect_args(command)),
        f"WorkingDirectory={paths.home()}",
        *(f"Environment={_unit_quote(e)}" for e in env),
        "Nice=10",
        "IOSchedulingClass=idle",
        f"StandardOutput=append:{logs / 'collect.log'}",
        f"StandardError=append:{logs / 'collect.err.log'}",
        "",
    ]
    return "\n".join(lines)


def build_timer(at: str) -> str:
    hour, minute = parse_time(at)
    return "\n".join([
        "[Unit]",
        "Description=Run the Rankwell daily collection",
        "",
        "[Timer]",
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def build_cron_line(at: str, command: list[str], logs: Path) -> str:
    hour, minute = parse_time(at)
    env = f"RANKWELL_HOME={shlex.quote(os.environ['RANKWELL_HOME'])} " if os.environ.get("RANKWELL_HOME") else ""
    cmd = " ".join(shlex.quote(c) for c in _collect_args(command))
    log = shlex.quote(str(logs / "collect.log"))
    err = shlex.quote(str(logs / "collect.err.log"))
    body = f"{minute} {hour} * * * {env}{cmd} >> {log} 2>> {err}".replace("%", "\\%")  # % is special in cron
    return f"{body} {CRON_TAG}"


def _systemd_usable(systemctl: Systemctl) -> bool:
    """False without systemctl or a user session (containers, WSL, ssh without lingering)."""
    code, _ = systemctl(["show-environment"])
    return code == 0


def _read_crontab(crontab: Crontab) -> list[str]:
    code, out = crontab(["-l"], None)  # exit 1 with "no crontab" when there is none yet
    return out.splitlines() if code == 0 else []


def _write_crontab(crontab: Crontab, lines: list[str]) -> None:
    code, out = crontab(["-"], "\n".join(lines) + "\n" if lines else "")
    if code != 0:
        raise RankwellError(f"crontab could not save the schedule (exit {code}): {out[:300]}")


def _is_ours(line: str) -> bool:
    return line.rstrip().endswith(CRON_TAG)


def _remove_cron(crontab: Crontab) -> bool:
    lines = _read_crontab(crontab)
    kept = [ln for ln in lines if not _is_ours(ln)]
    if len(kept) == len(lines):
        return False
    _write_crontab(crontab, kept)
    return True


def _install_linux(at: str | None, *, unit_dir: Path | None, systemctl: Systemctl | None,
                   crontab: Crontab | None, command: list[str] | None) -> dict[str, Any]:
    units = unit_dir or systemd_user_dir()
    systemctl = systemctl or run_systemctl
    crontab = crontab or run_crontab
    when = at or config.load().collect_at
    parse_time(when)  # fail on a bad time before anything is written
    command = _resolve_command(command)
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)

    if _systemd_usable(systemctl):
        units.mkdir(parents=True, exist_ok=True)
        service, timer = units / SERVICE_FILE, units / TIMER_FILE
        service.write_text(build_service(command, logs))
        timer.write_text(build_timer(when))
        code, out = systemctl(["daemon-reload"])
        if code == 0:
            code, out = systemctl(["enable", "--now", TIMER_FILE])
        if code != 0:
            raise RankwellError(f"systemctl could not enable {TIMER_FILE} (exit {code}): {out[:300]}")
        _remove_cron(crontab)  # never run twice if an earlier install fell back to cron
        return {"installed": True, "backend": "systemd", "at": when, "service": str(service),
                "timer": str(timer), "command": _collect_args(command), "log": str(logs / "collect.log"),
                "catch_up": True}

    lines = [ln for ln in _read_crontab(crontab) if not _is_ours(ln)]
    lines.append(build_cron_line(when, command, logs))
    _write_crontab(crontab, lines)
    return {"installed": True, "backend": "cron", "at": when, "command": _collect_args(command),
            "log": str(logs / "collect.log"), "catch_up": False, "note": CRON_NOTE}


def _uninstall_linux(*, unit_dir: Path | None, systemctl: Systemctl | None,
                     crontab: Crontab | None) -> dict[str, Any]:
    units = unit_dir or systemd_user_dir()
    systemctl = systemctl or run_systemctl
    crontab = crontab or run_crontab
    service, timer = units / SERVICE_FILE, units / TIMER_FILE
    removed_systemd = service.exists() or timer.exists()
    if removed_systemd:
        systemctl(["disable", "--now", TIMER_FILE])  # already stopped is fine
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)
        systemctl(["daemon-reload"])
    removed_cron = _remove_cron(crontab)
    return {"removed": removed_systemd or removed_cron, "timer": str(timer), "cron": removed_cron}


def _status_linux(*, unit_dir: Path | None, systemctl: Systemctl | None, crontab: Crontab | None,
                  check_loaded: bool = True) -> dict[str, Any]:
    units = unit_dir or systemd_user_dir()
    timer = units / TIMER_FILE
    if timer.exists():
        m = re.search(r"^OnCalendar=\*-\*-\* (\d{2}):(\d{2})", timer.read_text(), re.MULTILINE)
        out: dict[str, Any] = {
            "installed": True, "backend": "systemd", "at": f"{m.group(1)}:{m.group(2)}" if m else None,
            "service": str(units / SERVICE_FILE), "timer": str(timer),
            "log": str(paths.logs_dir() / "collect.log"), "catch_up": True,
        }
        if check_loaded:
            runner = systemctl or run_systemctl
            enabled, _ = runner(["is-enabled", TIMER_FILE])
            active, _ = runner(["is-active", TIMER_FILE])
            out["enabled"] = enabled == 0
            out["loaded"] = active == 0
        return out
    for line in _read_crontab(crontab or run_crontab):
        if _is_ours(line):
            m = re.match(r"\s*(\d+)\s+(\d+)\s", line)
            at = f"{int(m.group(2)):02d}:{int(m.group(1)):02d}" if m else None
            return {"installed": True, "backend": "cron", "at": at, "log": str(paths.logs_dir() / "collect.log"),
                    "catch_up": False, "note": CRON_NOTE}
    return {"installed": False, "timer": str(timer)}
