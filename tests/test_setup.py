import os
import subprocess
from pathlib import Path

import pytest

from rankwell import schedule, secrets, setup
from rankwell.errors import RankwellError

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "0f5d3c2a-7b1e-4c9a-9d2f-3e8b6a1c4d70"
P8 = "-----BEGIN PRIVATE KEY-----\nMIGTAgEAMBMG\n-----END PRIVATE KEY-----\n"


def test_save_key_from_path(tmp_path: Path) -> None:
    f = tmp_path / "AuthKey_ABCD1234EF.p8"
    f.write_text(P8)
    out = setup.save_asc_key(str(f), ISSUER)
    assert out["key_id"] == "ABCD1234EF" and "PRIVATE" not in str(out)
    key = secrets.asc_key()
    assert key.issuer_id == ISSUER and key.key_id == "ABCD1234EF" and key.private_key == P8


@pytest.mark.parametrize(("name", "issuer", "body", "msg"), [
    ("AuthKey_ABCD1234EF.p8", "not-a-uuid", P8, "issuer id"),
    ("key.p8", ISSUER, P8, "key id"),
    ("AuthKey_ABCD1234EF.p8", ISSUER, "hello", "not an App Store Connect private key"),
])
def test_save_key_rejects(tmp_path: Path, name: str, issuer: str, body: str, msg: str) -> None:
    f = tmp_path / name
    f.write_text(body)
    with pytest.raises(RankwellError, match=msg):
        setup.save_asc_key(str(f), issuer)
    assert not secrets.has_asc_key()


def test_save_key_error_never_echoes_content(tmp_path: Path) -> None:
    f = tmp_path / "AuthKey_ABCD1234EF.p8"
    f.write_text("SECRET-CONTENT")
    with pytest.raises(RankwellError) as e:
        setup.save_asc_key(str(f), ISSUER)
    assert "SECRET-CONTENT" not in str(e.value)


def test_install_cli_links_launcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "rankwell_command", lambda: ["/bin/echo"])
    monkeypatch.setenv("PATH", "/usr/bin")
    out = setup.install_cli(tmp_path / "bin")
    link = tmp_path / "bin" / "rankwell"
    assert link.is_symlink() and out["on_path"] is False and "export PATH" in out["note"]
    assert setup.install_cli(tmp_path / "bin")["already"] is True


def test_install_cli_leaves_foreign_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "rankwell_command", lambda: ["/bin/echo"])
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rankwell").write_text("mine")
    with pytest.raises(RankwellError, match="not a link"):
        setup.install_cli(tmp_path / "bin")


def test_checks_shape() -> None:
    rows = setup.checks()
    names = [r["name"] for r in rows]
    assert "App Store Connect key" in names and "Daily schedule" in names
    key = next(r for r in rows if r["name"] == "App Store Connect key")
    assert key["ok"] is False and "asc_key_save" in key["fix"]
    assert "✗ App Store Connect key" in setup.checks_display(rows)


def test_wrapper_without_uv_explains(tmp_path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RANKWELL_NO_UV_SEARCH": "1"}
    hook = subprocess.run([str(ROOT / "bin" / "rankwell"), "hook", "session-start"], env=env,
                          capture_output=True, text=True, check=False)
    assert hook.returncode == 0 and "uv is missing" in hook.stdout and '"SessionStart"' in hook.stdout
    mcp = subprocess.run([str(ROOT / "bin" / "rankwell"), "mcp"], env=env, capture_output=True, text=True,
                         check=False)
    assert mcp.returncode == 127 and "brew install uv" in mcp.stderr


def test_wrapper_is_executable() -> None:
    assert os.access(ROOT / "bin" / "rankwell", os.X_OK)


def test_install_cli_leaves_foreign_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "rankwell_command", lambda: ["/bin/echo"])
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rankwell").symlink_to("/usr/bin/true")
    with pytest.raises(RankwellError, match="not a link"):
        setup.install_cli(tmp_path / "bin")


def test_play_key_path_missing_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RANKWELL_PLAY_KEY_PATH", "/nonexistent/sa.json")
    assert secrets.has_play_key() is False
    assert any(r["name"] == "Google Play key" for r in setup.checks())
