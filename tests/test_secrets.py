from pathlib import Path

import pytest

from rankwell import config, secrets
from rankwell.errors import NotConfigured


def test_keychain_round_trip_is_stubbed() -> None:
    assert not secrets.has_asc_key()
    secrets.store_asc_key("iss", "KID", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")
    assert secrets.asc_key().key_id == "KID"
    secrets.delete_asc_key()
    with pytest.raises(NotConfigured):
        secrets.asc_key()


def test_rejects_non_key_file() -> None:
    with pytest.raises(NotConfigured):
        secrets.store_asc_key("iss", "KID", "hello")


def test_switch_needs_a_real_boolean(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text('[analytics]\ncreate_report_requests = "false"\n')
    assert config.load(f).create_report_requests is False
    f.write_text("[analytics]\ncreate_report_requests = true\n")
    assert config.load(f).create_report_requests is True
    f.write_text("")
    assert config.load(f).create_report_requests is True
