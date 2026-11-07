"""A small in-memory Google Play Developer API behind respx: OAuth token, edits, listings, tracks, bundles,
reviews. Edits are copies of the published state; commit publishes them."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from rankwell.secrets import PlayKey

PKG = "com.example.snap"
EMAIL = "rankwell@proj.iam.gserviceaccount.com"
_PK = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _PK.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()


def key() -> PlayKey:
    return PlayKey(EMAIL, "kid-1", PEM)


def key_json() -> str:
    return json.dumps({"type": "service_account", "project_id": "proj", "private_key_id": "kid-1",
                       "private_key": PEM, "client_email": EMAIL, "client_id": "1",
                       "token_uri": "https://oauth2.googleapis.com/token"})


class FakePlay:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"listings": {}, "tracks": {}, "bundles": []}
        self.edits: dict[str, dict[str, Any]] = {}
        self.reviews: list[dict[str, Any]] = []
        self.tokens: list[dict[str, Any]] = []  # decoded assertions, one per token exchange
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: dict[tuple[str, str], tuple[int, str]] = {}  # (method, path regex) → (status, message)
        self.uploaded: list[bytes] = []
        self.next_code = 10
        self.expires_in = 3600
        self._n = 0

    # -- setup helpers

    def listing(self, lang: str, **fields: str) -> None:
        self.state["listings"][lang] = {"language": lang, **fields}

    def track(self, name: str, *releases: dict[str, Any]) -> None:
        self.state["tracks"][name] = list(releases)

    def bundle(self, code: int, sha: str = "") -> None:
        self.state["bundles"].append({"versionCode": code, "sha1": "", "sha256": sha})

    def review(self, rid: str, stars: int, text: str, seconds: int, reply: str | None = None) -> None:
        comments: list[dict[str, Any]] = [{"userComment": {
            "text": text, "starRating": stars, "reviewerLanguage": "en", "appVersionName": "1.0",
            "appVersionCode": 9, "device": "pixel", "lastModified": {"seconds": str(seconds), "nanos": 0}}}]
        if reply:
            comments.append({"developerComment": {"text": reply, "lastModified": {"seconds": str(seconds + 60)}}})
        self.reviews.append({"reviewId": rid, "authorName": "Ann", "comments": comments})

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    # -- respx

    def __enter__(self) -> FakePlay:
        self._mock = respx.mock(assert_all_called=False)
        self._mock.__enter__()
        self._mock.route(host="oauth2.googleapis.com").mock(side_effect=self._token)
        self._mock.route(host="androidpublisher.googleapis.com").mock(side_effect=self._handle)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._mock.__exit__(*exc)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        pub = _PK.public_key()
        try:
            claims = jwt.decode(form["assertion"], pub, algorithms=["RS256"],
                                audience="https://oauth2.googleapis.com/token")
        except jwt.PyJWTError as e:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": str(e)})
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        self.tokens.append(claims)
        return httpx.Response(200, json={"access_token": f"tok-{len(self.tokens)}", "expires_in": self.expires_in,
                                         "token_type": "Bearer"})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if not request.headers.get("Authorization", "").startswith("Bearer tok-"):
            return httpx.Response(401, json={"error": {"code": 401, "message": "no token"}})
        method, path = request.method, request.url.path
        body: Any = None
        if "/upload/" in path:
            body = request.read()
        elif request.content:
            body = json.loads(request.content)
        self.calls.append((method, path, body if not isinstance(body, bytes) else len(body)))
        for (m, pat), (status, msg) in self.fail.items():
            if m == method and re.search(pat, path):
                return httpx.Response(status, json={"error": {"code": status, "message": msg}})
        for pattern, verb, fn in self._routes():
            if verb == method and (match := re.fullmatch(pattern, path)):
                out = fn(body, dict(request.url.params), *match.groups())
                return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)
        return httpx.Response(404, json={"error": {"code": 404, "message": f"fake: no route {method} {path}"}})

    def _routes(self) -> list[tuple[str, str, Callable[..., Any]]]:
        a = r"/androidpublisher/v3/applications/([^/]+)"
        e = a + r"/edits/([^/:]+)"
        return [
            (a + "/edits", "POST", self._insert),
            (e, "DELETE", self._delete),
            (e + ":validate", "POST", lambda b, p, pkg, eid: self._edit(eid) and {"id": eid}),
            (e + ":commit", "POST", self._commit),
            (e + "/listings", "GET", lambda b, p, pkg, eid: {"listings": list(self._edit(eid)["listings"].values())}),
            (e + "/listings/([^/]+)", "GET", self._get_listing),
            (e + "/listings/([^/]+)", "PUT", self._put_listing),
            (e + "/tracks", "GET", lambda b, p, pkg, eid: {"tracks": [
                {"track": t, "releases": r} for t, r in self._edit(eid)["tracks"].items()]}),
            (e + "/tracks/([^/]+)", "PUT", self._put_track),
            (e + "/bundles", "GET", lambda b, p, pkg, eid: {"bundles": self._edit(eid)["bundles"]}),
            ("/upload" + e + "/bundles", "POST", self._upload),
            (a + "/reviews", "GET", self._list_reviews),
            (a + "/reviews/([^/]+)", "GET", self._get_review),
            (a + "/reviews/([^/]+):reply", "POST", self._reply),
        ]

    def _edit(self, eid: str) -> dict[str, Any]:
        return self.edits[eid]

    def _insert(self, b: Any, p: Any, pkg: str) -> dict[str, Any]:
        assert pkg == PKG
        self._n += 1
        eid = f"e{self._n}"
        self.edits[eid] = copy.deepcopy(self.state)
        return {"id": eid, "expiryTimeSeconds": "9999999999"}

    def _delete(self, b: Any, p: Any, pkg: str, eid: str) -> httpx.Response:
        self.edits.pop(eid, None)
        return httpx.Response(204)

    def _commit(self, b: Any, p: Any, pkg: str, eid: str) -> dict[str, Any]:
        self.state = self.edits.pop(eid)
        return {"id": eid}

    def _get_listing(self, b: Any, p: Any, pkg: str, eid: str, lang: str) -> Any:
        found = self._edit(eid)["listings"].get(lang)
        return found or httpx.Response(404, json={"error": {"code": 404, "message": "not found"}})

    def _put_listing(self, b: dict[str, Any], p: Any, pkg: str, eid: str, lang: str) -> dict[str, Any]:
        self._edit(eid)["listings"][lang] = {**b, "language": lang}
        return b

    def _put_track(self, b: dict[str, Any], p: Any, pkg: str, eid: str, track: str) -> dict[str, Any]:
        self._edit(eid)["tracks"][track] = b["releases"]
        return b

    def _upload(self, b: bytes, p: dict[str, str], pkg: str, eid: str) -> dict[str, Any]:
        assert p.get("uploadType") == "media"
        self.uploaded.append(b)
        bundle = {"versionCode": self.next_code, "sha1": "", "sha256": hashlib.sha256(b).hexdigest()}
        self._edit(eid)["bundles"].append(bundle)
        return bundle

    def _list_reviews(self, b: Any, p: dict[str, str], pkg: str) -> dict[str, Any]:
        start = int(p.get("token") or 0)
        page = self.reviews[start:start + 2]  # small pages so paging is exercised
        out: dict[str, Any] = {"reviews": page}
        if start + 2 < len(self.reviews):
            out["tokenPagination"] = {"nextPageToken": str(start + 2)}
        return out

    def _get_review(self, b: Any, p: Any, pkg: str, rid: str) -> Any:
        found = next((r for r in self.reviews if r["reviewId"] == rid), None)
        return found or httpx.Response(404, json={"error": {"code": 404, "message": "review not found"}})

    def _reply(self, b: dict[str, Any], p: Any, pkg: str, rid: str) -> dict[str, Any]:
        r = next(r for r in self.reviews if r["reviewId"] == rid)
        r["comments"] = [c for c in r["comments"] if "developerComment" not in c] + [
            {"developerComment": {"text": b["replyText"], "lastModified": {"seconds": "1800000000"}}}]
        return {"result": {"replyText": b["replyText"], "lastEdited": {"seconds": "1800000000"}}}
