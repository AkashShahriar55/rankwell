import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from rankwell.errors import StoreError
from rankwell.secrets import AscKey
from rankwell.stores import appstore, asc, http


def test_retries_then_succeeds() -> None:
    with respx.mock:
        route = respx.get("https://itunes.apple.com/lookup").mock(side_effect=[
            httpx.Response(503), httpx.Response(200, json={"results": [{"trackId": 1, "trackName": "A"}]})])
        out = http.request("GET", "https://itunes.apple.com/lookup", sleep=lambda s: None)
        assert out.status_code == 200 and route.call_count == 2


def test_post_is_not_retried() -> None:
    with respx.mock:
        route = respx.post("https://example.com/x").mock(return_value=httpx.Response(503))
        assert http.request("POST", "https://example.com/x", sleep=lambda s: None).status_code == 503
        assert route.call_count == 1


def test_transport_error_becomes_store_error() -> None:
    with respx.mock:
        respx.get("https://example.com/x").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(StoreError, match=r"Could not reach example\.com"):
            http.request("GET", "https://example.com/x", sleep=lambda s: None)


def test_lookup_and_snapshot() -> None:
    with respx.mock:
        respx.get("https://itunes.apple.com/lookup").mock(return_value=httpx.Response(200, json={"results": [
            {"trackId": 5, "trackName": "App", "averageUserRating": 4.567, "primaryGenreId": 6008,
             "releaseDate": "2026-03-12T07:00:00Z", "screenshotUrls": ["u"]}]}))
        found = appstore.lookup([5])
    snap = appstore.snapshot_fields(found[5])
    assert snap["rating_avg"] == 4.57 and snap["genre_id"] == 6008 and snap["released_at"] == "2026-03-12"


def test_store_id_from() -> None:
    assert appstore.store_id_from("https://apps.apple.com/us/app/x/id6759825215") == 6759825215
    assert appstore.store_id_from(" 6759825215 ") == 6759825215
    with pytest.raises(StoreError):
        appstore.store_id_from("nope")


def _key() -> AscKey:
    pk = ec.generate_private_key(ec.SECP256R1())
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    return AscKey("issuer-1", "KEY123", pem)


def test_asc_token_and_pagination() -> None:
    client = asc.ASC(_key())
    claims = jwt.decode(client.token(), options={"verify_signature": False})
    assert claims["iss"] == "issuer-1" and claims["aud"] == "appstoreconnect-v1"
    assert "KEY123" in repr(client.key) and "BEGIN" not in repr(client.key)
    with respx.mock:
        respx.get("https://api.appstoreconnect.apple.com/v1/apps").mock(side_effect=[
            httpx.Response(200, json={"data": [{"id": "1", "attributes": {"name": "A", "bundleId": "a"}}],
                                      "links": {"next": "https://api.appstoreconnect.apple.com/v1/apps?cursor=2"}}),
            httpx.Response(200, json={"data": [{"id": "2", "attributes": {"name": "B", "bundleId": "b"}}],
                                      "links": {}})])
        assert [a["store_id"] for a in client.apps()] == [1, 2]


def test_asc_error_is_readable() -> None:
    client = asc.ASC(_key())
    with respx.mock:
        respx.patch("https://api.appstoreconnect.apple.com/v1/apps/1").mock(return_value=httpx.Response(
            409, json={"errors": [{"detail": "The version is not editable."}]}))
        with pytest.raises(StoreError, match="409: The version is not editable"):
            client.patch("/v1/apps/1", {})
