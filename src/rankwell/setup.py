"""Setup from chat: what is in place, saving the App Store Connect key from a file path, and putting
`rankwell` on the PATH. `rankwell doctor` and the aso-setup skill read the same checks."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from . import apps, schedule, secrets
from . import db as dbmod
from .errors import RankwellError

KEY_NAME_RE = re.compile(r"AuthKey_([A-Z0-9]{8,})\.p8$")
ISSUER_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def key_id_from_name(name: str) -> str | None:
    m = KEY_NAME_RE.match(name)
    return m.group(1) if m else None


def save_asc_key(key_path: str, issuer_id: str, key_id: str | None = None) -> dict[str, Any]:
    """Read a .p8 file and store it in the Keychain. Returns only the key id: the key itself never leaves here."""
    path = Path(key_path).expanduser()
    if not path.is_file():
        raise RankwellError(f"No file at {path}.")
    kid = key_id or key_id_from_name(path.name)
    if not kid:
        raise RankwellError("Cannot read the key id from the file name (AuthKey_<KEY ID>.p8); pass key_id.")
    if not ISSUER_RE.fullmatch(issuer_id.strip()):
        raise RankwellError("The issuer id looks wrong: it is a UUID shown at the top of App Store Connect → "
                            "Users and Access → Integrations → App Store Connect API.")
    text = path.read_text(errors="replace")
    if "BEGIN PRIVATE KEY" not in text:
        raise RankwellError(f"{path.name} is not an App Store Connect private key (.p8).")
    secrets.store_asc_key(issuer_id.strip(), kid, text)
    return {"saved": True, "key_id": kid, "file": str(path),
            "note": "Saved in the macOS Keychain. The .p8 file is no longer needed by Rankwell."}


def user_bin() -> Path:
    return Path.home() / ".local" / "bin"


def on_path(folder: Path) -> bool:
    return any(Path(p).expanduser() == folder for p in os.environ.get("PATH", "").split(os.pathsep) if p)


def install_cli(bin_dir: Path | None = None) -> dict[str, Any]:
    """Link ~/.local/bin/rankwell to the stable launcher in ~/.rankwell/bin, so `rankwell` works in any terminal
    and keeps working across plugin updates."""
    schedule.record_root()
    launcher = schedule.write_launcher(schedule.rankwell_command())
    folder = bin_dir or user_bin()
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "rankwell"
    if link.is_symlink() or link.exists():
        if link.is_symlink() and link.resolve() == launcher.resolve():
            return {"linked": True, "link": str(link), "already": True, "on_path": on_path(folder)}
        if not link.is_symlink():
            raise RankwellError(f"{link} exists and is not a link Rankwell made; leaving it alone.")
        link.unlink()
    link.symlink_to(launcher)
    out: dict[str, Any] = {"linked": True, "link": str(link), "target": str(launcher), "on_path": on_path(folder)}
    if not out["on_path"]:
        out["note"] = (f"{folder} is not on your PATH. Add this line to ~/.zshrc and open a new terminal: "
                       f'export PATH="{folder}:$PATH"')
    return out


def checks() -> list[dict[str, Any]]:
    """The setup checks: name, ok, detail, fix, and whether the check is optional."""
    out: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", fix: str = "", optional: bool = False) -> None:
        out.append({"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix, "optional": optional})

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0], "Python 3.11 or newer is needed.")
    add("uv", schedule.find_uv() is not None, "", "brew install uv")
    db = dbmod.Database()
    add("Database", True, db.path)
    n = len(apps.list_apps(db))
    add("Apps", n > 0, f"{n} tracked", "Ask Claude: 'set up Rankwell for my app' (skill aso-setup).")
    add("App Store Connect key", secrets.has_asc_key(), "",
        "Save it with asc_key_save, or: rankwell init --asc-key AuthKey_XXXX.p8 --issuer-id <issuer id>")
    last = db.one("SELECT * FROM runs WHERE kind = 'collect' ORDER BY id DESC LIMIT 1")
    add("Last collection", bool(last and last["status"] in ("ok", "partial")),
        f"{last['started_at']} {last['status']}" if last else "never", "rankwell collect (or collect_now)")
    sched = schedule.status(check_loaded=False) if sys.platform == "darwin" else {"installed": False}
    add("Daily schedule", bool(sched.get("installed")), f"at {sched['at']}" if sched.get("installed") else "",
        "rankwell schedule install --at 11:00", optional=True)
    add("Xcode command line tools", shutil.which("xcrun") is not None, "for uploading builds",
        "xcode-select --install", optional=True)
    link = user_bin() / "rankwell"
    found = shutil.which("rankwell")  # the plugin's own environment does not count: it is gone outside uv run
    add("rankwell in the terminal", link.exists() or bool(found and not found.startswith(sys.prefix)),
        str(link) if link.exists() else "", "cli_install (links ~/.local/bin/rankwell)", optional=True)
    return out


def checks_display(rows: list[dict[str, Any]]) -> str:
    lines = []
    for r in rows:
        mark = "✓" if r["ok"] else ("·" if r["optional"] else "✗")
        detail = f" ({r['detail']})" if r["detail"] else ""
        lines.append(f"{mark} {r['name']}{detail}" + (f"\n    → {r['fix']}" if r["fix"] else ""))
    return "\n".join(lines)

