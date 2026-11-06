"""User settings in ~/.rankwell/config.toml.

Settings are small and human-edited; per-app data lives in the database and in each app's .aso/ folder.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

DEFAULT_TEXT = """\
# Rankwell settings. Apps, keywords and competitors live in the database: manage them through Claude.

[collect]
# Daily collection time (24h, local), used by `rankwell schedule install`.
at = "11:00"

[defaults]
countries = ["us"]

[analytics]
# App Store Connect analytics need report requests on each app. When true, the collector creates them once
# (the only automatic write Rankwell makes; it is recorded in the audit log). Apple needs 1-2 days to start.
create_report_requests = true

[knowledge]
# Extra folders of your own ASO notes (markdown). Skills read them after the shipped rules.
dirs = []
"""


@dataclass(frozen=True)
class Config:
    collect_at: str = "11:00"
    countries: list[str] = field(default_factory=lambda: ["us"])
    knowledge_dirs: list[Path] = field(default_factory=list)
    create_report_requests: bool = True


def ensure_file() -> Path:
    p = paths.config_path()
    if not p.exists():
        p.write_text(DEFAULT_TEXT)
    return p


def load(path: Path | None = None) -> Config:
    p = path or paths.config_path()
    if not p.exists():
        return Config()
    with p.open("rb") as f:
        raw = tomllib.load(f)
    collect = raw.get("collect", {})
    defaults = raw.get("defaults", {})
    knowledge = raw.get("knowledge", {})
    analytics = raw.get("analytics", {})
    return Config(
        collect_at=str(collect.get("at", "11:00")),
        countries=[str(c).lower() for c in defaults.get("countries", ["us"])],
        knowledge_dirs=[Path(d).expanduser() for d in knowledge.get("dirs", [])],
        create_report_requests=bool(analytics.get("create_report_requests", True)),
    )
