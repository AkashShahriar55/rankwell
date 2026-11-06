"""App Store Connect API client: ES256 JWT auth, retries, pagination, readable errors.

https://developer.apple.com/documentation/appstoreconnectapi
Writes are never retried automatically when they could duplicate (POST).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt

from ..errors import StoreError
from ..secrets import AscKey
from ..secrets import asc_key as load_key
from . import http

API = "https://api.appstoreconnect.apple.com"
TOKEN_TTL = 1200  # Apple allows at most 20 minutes

Resource = dict[str, Any]


class ASC:
    def __init__(self, key: AscKey | None = None) -> None:
        self.key = key or load_key()
        self._token = ""
        self._exp = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if time.time() > self._exp - 60:
                now = int(time.time())
                self._token = jwt.encode(
                    {"iss": self.key.issuer_id, "iat": now, "exp": now + TOKEN_TTL, "aud": "appstoreconnect-v1"},
                    self.key.private_key,
                    algorithm="ES256",
                    headers={"kid": self.key.key_id, "typ": "JWT"},
                )
                self._exp = now + TOKEN_TTL
            return self._token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else API + path
        r = http.request(
            method, url, json=body, params=params,
            headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
            retries=4,
        )
        if r.status_code >= 400:
            raise StoreError(f"App Store Connect {method} {_short(path)} → {r.status_code}: {_errors(r)}",
                             r.status_code)
        return dict(r.json()) if r.content else {}

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", path, params=params or None)

    def get_all(self, path: str, **params: Any) -> list[Resource]:
        """Every item of a paginated list."""
        page = self.request("GET", path, params=params or None)
        data: list[Resource] = list(page.get("data", []))
        nxt = page.get("links", {}).get("next")
        seen: set[str] = set()
        while nxt and nxt not in seen:  # guard against a cursor that points back at itself
            seen.add(nxt)
            page = self.request("GET", nxt)
            data.extend(page.get("data", []))
            nxt = page.get("links", {}).get("next")
        return data

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", path, body)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, body)

    # Convenience reads used across modules.

    def apps(self) -> list[dict[str, Any]]:
        return [
            {"store_id": int(a["id"]), "name": a["attributes"]["name"], "bundle_id": a["attributes"]["bundleId"],
             "sku": a["attributes"].get("sku")}
            for a in self.get_all("/v1/apps", **{"fields[apps]": "name,bundleId,sku", "limit": 200})
        ]


def _short(path: str) -> str:
    return path.replace(API, "").split("?")[0]


def _errors(r: Any) -> str:
    try:
        errs = list(r.json().get("errors", []))
    except ValueError:
        return str(r.text[:200])
    parts = [e.get("detail") or e.get("title") or e.get("code", "") for e in errs]
    return str("; ".join(p for p in parts if p)[:400] or r.text[:200])


def version_state(attrs: dict[str, Any]) -> str:
    return str(attrs.get("appVersionState") or attrs.get("appStoreState") or attrs.get("state") or "")


EDITABLE = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED", "INVALID_BINARY"}
LIVE = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION"}
IN_REVIEW = {"WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_DEVELOPER_RELEASE", "PROCESSING_FOR_DISTRIBUTION"}
