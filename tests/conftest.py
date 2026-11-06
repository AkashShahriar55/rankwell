from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rankwell import db as dbmod
from rankwell.stores import http


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own RANKWELL_HOME, no throttling and no real secrets."""
    monkeypatch.setenv("RANKWELL_HOME", str(tmp_path / "home"))
    for name in ("ISSUER_ID", "KEY_ID", "KEY_PATH"):
        monkeypatch.delenv(f"RANKWELL_ASC_{name}", raising=False)
    monkeypatch.setattr(http, "throttle", lambda bucket, interval: None)
    monkeypatch.setattr(dbmod, "_default", None)
    yield tmp_path
    http.close()


@pytest.fixture
def db() -> dbmod.Database:
    return dbmod.Database(":memory:")
