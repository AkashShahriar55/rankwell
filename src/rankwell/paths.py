"""Where Rankwell keeps its files. Everything lives under RANKWELL_HOME (default ~/.rankwell)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("RANKWELL_HOME") or Path.home() / ".rankwell").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return home() / "rankwell.db"


def config_path() -> Path:
    return home() / "config.toml"


def logs_dir() -> Path:
    d = home() / "logs"
    d.mkdir(exist_ok=True)
    return d


def cache_dir() -> Path:
    d = home() / "cache"
    d.mkdir(exist_ok=True)
    return d


def backups_dir() -> Path:
    d = home() / "backups"
    d.mkdir(exist_ok=True)
    return d
