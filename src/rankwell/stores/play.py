"""Google Play Developer API (androidpublisher v3) client: service-account OAuth, retries, readable errors.

https://developers.google.com/android-publisher
Auth: an RS256 JWT signed with the service account key is exchanged for an access token (valid about an hour,
cached until a minute before it expires). Store listing, tracks and bundles change only inside an edit:
insert → changes → validate → commit; an edit that is not committed is deleted. Reviews need no edit.
Neither the key nor the token ever goes into an error message, log line or return value.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from ..errors import StoreError
from ..secrets import PlayKey
from ..secrets import play_key as load_key
from . import http

HOST = "https://androidpublisher.googleapis.com"
API = HOST + "/androidpublisher/v3/applications"
UPLOAD = HOST + "/upload/androidpublisher/v3/applications"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
TOKEN_TTL = 3600  # Google's maximum for a service account assertion
UPLOAD_TIMEOUT = httpx.Timeout(900.0, connect=10.0)  # a bundle upload can take minutes

Resource = dict[str, Any]


class Play:
    def __init__(self, key: PlayKey | None = None) -> None:
        self.key = key or load_key()
        self._token = ""
        self._exp = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ auth

    def token(self) -> str:
        with self._lock:
            if time.time() > self._exp - 60:
                now = int(time.time())
                assertion = jwt.encode(
                    {"iss": self.key.client_email, "scope": SCOPE, "aud": self.key.token_uri, "iat": now,
                     "exp": now + TOKEN_TTL},
                    self.key.private_key, algorithm="RS256",
                    headers={"kid": self.key.private_key_id} if self.key.private_key_id else None,
                )
                r = http.request("POST", self.key.token_uri, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
                body = _json(r)
                if r.status_code >= 400 or not body.get("access_token"):
                    why = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
                    raise StoreError(f"Google sign-in with the Play service account failed: {str(why)[:200]}",
                                     r.status_code)
                self._token = str(body["access_token"])
                self._exp = now + int(body.get("expires_in") or TOKEN_TTL)
            return self._token

    def _forget_token(self) -> None:
        with self._lock:
            self._exp = 0.0

    # ------------------------------------------------------------------ requests

    def request(self, method: str, url: str, body: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        url = url if url.startswith("http") else API + url
        extra = kwargs.pop("headers", {})
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.token()}", **extra}
            r = http.request(method, url, json=body, params=params, headers=headers, retries=4, **kwargs)
            if r.status_code == 401 and attempt == 0 and "content" not in kwargs:
                self._forget_token()  # revoked or expired early: sign in again once
                continue
            break
        if r.status_code >= 400:
            raise StoreError(f"Google Play {method} {_short(url)} → {r.status_code}: {_error(r)}", r.status_code)
        return _json(r)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", path, params=params or None)

    # ------------------------------------------------------------------ edits

    def insert_edit(self, package: str) -> str:
        return str(self.request("POST", f"/{_p(package)}/edits", {})["id"])

    def delete_edit(self, package: str, edit_id: str) -> None:
        with contextlib.suppress(StoreError):  # an edit that already expired or was committed is fine
            self.request("DELETE", f"/{_p(package)}/edits/{_p(edit_id)}")

    def validate(self, package: str, edit_id: str) -> None:
        self.request("POST", f"/{_p(package)}/edits/{_p(edit_id)}:validate")

    def commit(self, package: str, edit_id: str) -> dict[str, Any]:
        return self.request("POST", f"/{_p(package)}/edits/{_p(edit_id)}:commit")

    @contextlib.contextmanager
    def read_edit(self, package: str) -> Iterator[str]:
        """An edit used only to read; deleted afterwards so it never blocks or commits anything."""
        eid = self.insert_edit(package)
        try:
            yield eid
        finally:
            self.delete_edit(package, eid)

    def _e(self, package: str, edit_id: str) -> str:
        return f"/{_p(package)}/edits/{_p(edit_id)}"

    def listing(self, package: str, edit_id: str, language: str) -> Resource | None:
        try:
            return self.get(f"{self._e(package, edit_id)}/listings/{_p(language)}")
        except StoreError as e:
            if e.status == 404:
                return None
            raise

    def listings(self, package: str, edit_id: str) -> list[Resource]:
        return list(self.get(f"{self._e(package, edit_id)}/listings").get("listings", []))

    def put_listing(self, package: str, edit_id: str, language: str, listing: Resource) -> Resource:
        return self.request("PUT", f"{self._e(package, edit_id)}/listings/{_p(language)}", listing)

    def tracks(self, package: str, edit_id: str) -> list[Resource]:
        return list(self.get(f"{self._e(package, edit_id)}/tracks").get("tracks", []))

    def put_track(self, package: str, edit_id: str, track: str, releases: list[Resource]) -> Resource:
        return self.request("PUT", f"{self._e(package, edit_id)}/tracks/{_p(track)}",
                            {"track": track, "releases": releases})

    def bundles(self, package: str, edit_id: str) -> list[Resource]:
        return list(self.get(f"{self._e(package, edit_id)}/bundles").get("bundles", []))

    def upload_bundle(self, package: str, edit_id: str, path: Path) -> Resource:
        """Upload an .aab in one media request (streamed from disk). Returns the Bundle (versionCode, sha256)."""
        size = path.stat().st_size
        with path.open("rb") as f:
            return self.request(
                "POST", f"{UPLOAD}{self._e(package, edit_id)}/bundles", params={"uploadType": "media"},
                content=f, timeout=UPLOAD_TIMEOUT,
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)})

    # ------------------------------------------------------------------ reviews

    def reviews(self, package: str, max_pages: int = 20) -> list[Resource]:
        """Every review the API returns (only those written or changed in the last 7 days)."""
        out: list[Resource] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            params: dict[str, Any] = {"maxResults": 100}
            if token:
                params["token"] = token
            page = self.get(f"/{_p(package)}/reviews", **params)
            out.extend(page.get("reviews", []))
            token = (page.get("tokenPagination") or {}).get("nextPageToken")
            if not token or token in seen:
                break
            seen.add(token)
        return out

    def review(self, package: str, review_id: str) -> Resource:
        return self.get(f"/{_p(package)}/reviews/{_p(review_id)}")

    def reply(self, package: str, review_id: str, text: str) -> Resource:
        return self.request("POST", f"/{_p(package)}/reviews/{_p(review_id)}:reply", {"replyText": text})


def _p(part: str) -> str:
    return quote(str(part), safe="")


def _short(url: str) -> str:
    return url.replace(UPLOAD, "").replace(API, "").split("?")[0]


def _json(r: httpx.Response) -> dict[str, Any]:
    if not r.content:
        return {}
    try:
        data = r.json()
    except ValueError:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _error(r: httpx.Response) -> str:
    err = _json(r).get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "")[:400]
    return str(err or r.text[:200])
