from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rankwell import db as dbmod
from rankwell import schedule, secrets
from rankwell.stores import http


class MemoryKeyring:
    """Stands in for the macOS Keychain so tests never read or write the developer's real one."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.items.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.items[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        self.items.pop((service, name), None)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own RANKWELL_HOME, no throttling and no real secrets."""
    monkeypatch.setenv("RANKWELL_HOME", str(tmp_path / "home"))
    for name in ("ISSUER_ID", "KEY_ID", "KEY_PATH"):
        monkeypatch.delenv(f"RANKWELL_ASC_{name}", raising=False)
    monkeypatch.setattr(http, "throttle", lambda bucket, interval: None)
    monkeypatch.setattr(dbmod, "_default", None)
    # On Linux nothing may reach the real systemd user session or crontab: both look "not available".
    monkeypatch.setattr(schedule, "run_systemctl", lambda args: (127, "no systemctl in tests"))
    monkeypatch.setattr(schedule, "run_crontab", lambda args, stdin=None: (127, "no crontab in tests"))
    monkeypatch.setattr(schedule, "systemd_user_dir", lambda: tmp_path / "systemd-user")
    ring = MemoryKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: ring)
    yield tmp_path
    http.close()


@pytest.fixture
def db() -> dbmod.Database:
    return dbmod.Database(":memory:")
