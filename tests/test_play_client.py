from pathlib import Path

import pytest
from play_fake import EMAIL, PEM, PKG, FakePlay, key, key_json

from rankwell import secrets
from rankwell.errors import NotConfigured, StoreError
from rankwell.secrets import PlayKey
from rankwell.stores.play import SCOPE, Play


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RANKWELL_PLAY_KEY_PATH", raising=False)


def test_token_exchange_signs_rs256_and_caches() -> None:
    with FakePlay() as fake:
        client = Play(key())
        client.get(f"/{PKG}/reviews")
        client.get(f"/{PKG}/reviews")
        assert len(fake.tokens) == 1
        claims = fake.tokens[0]
        assert claims["iss"] == EMAIL and claims["scope"] == SCOPE
        assert claims["exp"] - claims["iat"] == 3600
        client._exp = 0  # expired → a new token
        client.get(f"/{PKG}/reviews")
        assert len(fake.tokens) == 2


def test_short_lived_token_is_refreshed() -> None:
    with FakePlay() as fake:
        fake.expires_in = 30  # inside the one-minute margin, so every call signs in again
        client = Play(key())
        client.get(f"/{PKG}/reviews")
        client.get(f"/{PKG}/reviews")
        assert len(fake.tokens) == 2


def test_rejected_token_signs_in_again_once() -> None:
    with FakePlay() as fake:
        client = Play(key())
        client._token, client._exp = "stale", 9e12
        assert client.get(f"/{PKG}/reviews") == {"reviews": []}
        assert len(fake.tokens) == 1


def test_sign_in_failure_is_readable_and_keeps_secrets_out() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with FakePlay(), pytest.raises(StoreError) as e:
        Play(PlayKey(EMAIL, "kid-1", other)).get(f"/{PKG}/reviews")
    msg = str(e.value)
    assert "Google sign-in with the Play service account failed" in msg
    assert not any(ln in msg for ln in other.splitlines() if len(ln) > 8)


def test_api_error_message_has_no_token() -> None:
    with FakePlay() as fake:
        fake.fail[("GET", "/reviews$")] = (403, "The caller does not have permission")
        with pytest.raises(StoreError) as e:
            Play(key()).get(f"/{PKG}/reviews")
    assert e.value.status == 403
    assert "does not have permission" in str(e.value) and "tok-" not in str(e.value)


def test_play_key_round_trip(tmp_path: Path) -> None:
    f = tmp_path / "sa.json"
    f.write_text(key_json())
    assert not secrets.has_play_key()
    with pytest.raises(NotConfigured, match="rankwell play init"):
        secrets.play_key()
    k = secrets.store_play_key(f)
    assert k.client_email == EMAIL and secrets.has_play_key()
    assert secrets.play_key().private_key == PEM
    assert "BEGIN" not in repr(k) and "<hidden>" in repr(k)
    stored = secrets._keyring().get_password("rankwell", "play_service_account")
    assert '"client_id"' not in stored  # only what Rankwell needs is kept
    secrets.delete_play_key()
    assert not secrets.has_play_key()
    secrets.delete_play_key()  # twice is fine


def test_play_key_env_override_and_bad_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "sa.json"
    f.write_text(key_json())
    monkeypatch.setenv("RANKWELL_PLAY_KEY_PATH", str(f))
    assert secrets.play_key().client_email == EMAIL
    bad = tmp_path / "bad.json"
    bad.write_text('{"type": "authorized_user", "client_email": "x"}')
    with pytest.raises(NotConfigured, match="not a Google service account"):
        secrets.store_play_key(bad)
    bad.write_text("not json")
    with pytest.raises(NotConfigured) as e:
        secrets.store_play_key(bad)
    assert "not json" not in str(e.value)
