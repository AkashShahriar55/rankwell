"""A small in-memory App Store Connect behind respx, for the listing, release and review tests."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from rankwell.secrets import AscKey
from rankwell.stores import asc

APP = "100"


def key() -> AscKey:
    pk = ec.generate_private_key(ec.SECP256R1())
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    return AscKey("issuer-1", "KEY123", pem)


def res(type_: str, id_: str, **attrs: Any) -> dict[str, Any]:
    return {"type": type_, "id": id_, "attributes": attrs}


class FakeASC:
    def __init__(self) -> None:
        self.versions: list[dict[str, Any]] = []
        self.infos: list[dict[str, Any]] = []
        self.info_locs: dict[str, list[dict[str, Any]]] = {}
        self.ver_locs: dict[str, list[dict[str, Any]]] = {}
        self.builds: list[dict[str, Any]] = []
        self.attached: dict[str, str] = {}
        self.review: dict[str, dict[str, Any]] = {}
        self.submissions: list[dict[str, Any]] = []
        self.items: dict[str, list[dict[str, Any]]] = {}
        self.shots: dict[str, dict[str, int]] = {}
        self.app_attrs: dict[str, Any] = {"contentRightsDeclaration": "DOES_NOT_USE_THIRD_PARTY_CONTENT"}
        self.age: dict[str, dict[str, Any]] = {}
        self.iaps: list[dict[str, Any]] = []
        self.phased: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.fail: dict[tuple[str, str], tuple[int, str]] = {}
        self._n = 0

    # -- setup helpers

    def version(self, vid: str, version: str, state: str, **attrs: Any) -> None:
        self.versions.append(res("appStoreVersions", vid, versionString=version, appVersionState=state,
                                 platform="IOS", **attrs))

    def info(self, iid: str, state: str) -> None:
        self.infos.append(res("appInfos", iid, state=state))

    def info_loc(self, iid: str, lid: str, locale: str, **attrs: Any) -> None:
        self.info_locs.setdefault(iid, []).append(res("appInfoLocalizations", lid, locale=locale, **attrs))

    def ver_loc(self, vid: str, lid: str, locale: str, **attrs: Any) -> None:
        self.ver_locs.setdefault(vid, []).append(res("appStoreVersionLocalizations", lid, locale=locale, **attrs))

    def build(self, bid: str, version: str, number: str, state: str = "VALID", encryption: bool | None = False,
              uploaded: str = "2026-11-01T10:00:00Z") -> None:
        b = res("builds", bid, version=number, processingState=state, expired=False,
                usesNonExemptEncryption=encryption, uploadedDate=uploaded)
        b["relationships"] = {"preReleaseVersion": {"data": {"type": "preReleaseVersions", "id": f"pre-{version}"}}}
        b["_version"] = version
        self.builds.insert(0, b)

    def submission(self, sid: str, state: str, items: list[tuple[str, str, str | None]],
                   submitted: str | None = None) -> None:
        """items: (item id, state, version id or None)."""
        self.submissions.append(res("reviewSubmissions", sid, state=state, platform="IOS", submittedDate=submitted))
        self.items[sid] = [self._item(i, s, v) for i, s, v in items]

    def _item(self, iid: str, state: str, vid: str | None) -> dict[str, Any]:
        it = res("reviewSubmissionItems", iid, state=state, resolved=False)
        it["relationships"] = {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid} if vid else None}}
        return it

    # -- the mock

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    def _new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-new{self._n}"

    def __enter__(self) -> FakeASC:
        self._mock = respx.mock(assert_all_called=False)
        self._mock.__enter__()
        self._mock.route(host="api.appstoreconnect.apple.com").mock(side_effect=self._handle)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._mock.__exit__(*exc)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        params = dict(request.url.params)
        self.calls.append((method, path, body))
        if (method, path) in self.fail:
            code, detail = self.fail[(method, path)]
            return httpx.Response(code, json={"errors": [{"detail": detail}]})
        for pattern, verb, fn in self._routes():
            m = re.fullmatch(pattern, path)
            if m and verb == method:
                out = fn(body, params, *m.groups())
                if isinstance(out, httpx.Response):
                    return out
                return httpx.Response(200 if method != "POST" else 201, json=out)
        return httpx.Response(404, json={"errors": [{"detail": f"fake: no route {method} {path}"}]})

    def _routes(self) -> list[tuple[str, str, Callable[..., Any]]]:
        return [
            (r"/v1/apps", "GET", self._apps),
            (r"/v1/apps/(\w+)", "GET", lambda b, p, a: {"data": res("apps", a, **self.app_attrs)}),
            (r"/v1/apps/\w+/appStoreVersions", "GET", lambda b, p: {"data": self.versions}),
            (r"/v1/apps/\w+/appInfos", "GET", lambda b, p: {"data": self.infos}),
            (r"/v1/apps/\w+/inAppPurchasesV2", "GET", lambda b, p: {"data": self.iaps}),
            (r"/v1/appInfos/([\w-]+)/appInfoLocalizations", "GET", lambda b, p, i: {"data": self.info_locs.get(i, [])}),
            (r"/v1/appInfos/([\w-]+)/ageRatingDeclaration", "GET", self._age),
            (r"/v1/appStoreVersions/([\w-]+)/appStoreVersionLocalizations", "GET",
             lambda b, p, v: {"data": self.ver_locs.get(v, [])}),
            (r"/v1/appStoreVersionLocalizations/([\w-]+)/appScreenshotSets", "GET", self._sets),
            (r"/v1/appScreenshotSets/([\w~-]+)/appScreenshots", "GET", self._shots),
            (r"/v1/appStoreVersions/([\w-]+)/build", "GET", self._attached),
            (r"/v1/appStoreVersions/([\w-]+)/appStoreReviewDetail", "GET", self._review),
            (r"/v1/appStoreVersions/([\w-]+)/appStoreVersionPhasedRelease", "GET", self._phased),
            (r"/v1/builds", "GET", self._builds),
            (r"/v1/builds/([\w-]+)", "GET", self._build),
            (r"/v1/reviewSubmissions", "GET", self._subs),
            (r"/v1/reviewSubmissions/([\w-]+)/items", "GET", lambda b, p, s: {"data": self.items.get(s, [])}),
            (r"/v1/(appInfoLocalizations|appStoreVersionLocalizations)/([\w-]+)", "PATCH", self._patch_loc),
            (r"/v1/(appInfoLocalizations|appStoreVersionLocalizations)", "POST", self._post_loc),
            (r"/v1/appStoreVersions", "POST", self._post_version),
            (r"/v1/appStoreVersions/([\w-]+)", "PATCH", self._patch_version),
            (r"/v1/appStoreVersions/([\w-]+)/relationships/build", "PATCH", self._attach),
            (r"/v1/appStoreReviewDetails/([\w-]+)", "PATCH", lambda b, p, r: {"data": b["data"]}),
            (r"/v1/appStoreReviewDetails", "POST", lambda b, p: {"data": {**b["data"], "id": self._new_id("rd")}}),
            (r"/v1/reviewSubmissions", "POST", self._post_sub),
            (r"/v1/reviewSubmissions/([\w-]+)", "PATCH", self._patch_sub),
            (r"/v1/reviewSubmissionItems", "POST", self._post_item),
            (r"/v1/reviewSubmissionItems/([\w-]+)", "PATCH", self._patch_item),
            (r"/v1/appStoreVersionPhasedReleases", "POST",
             lambda b, p: {"data": {**b["data"], "id": self._new_id("ph")}}),
        ]

    def _apps(self, b: Any, p: dict[str, str]) -> dict[str, Any]:
        if p.get("filter[bundleId]") == "com.example.app":
            return {"data": [res("apps", APP, bundleId="com.example.app")]}
        return {"data": []}

    def _age(self, b: Any, p: Any, i: str) -> Any:
        return {"data": self.age[i]} if i in self.age else httpx.Response(404, json={"errors": [{"detail": "none"}]})

    def _sets(self, b: Any, p: Any, loc: str) -> dict[str, Any]:
        return {"data": [res("appScreenshotSets", f"{loc}~{t}", screenshotDisplayType=t)
                         for t in self.shots.get(loc, {})]}

    def _shots(self, b: Any, p: Any, set_id: str) -> dict[str, Any]:
        loc, t = set_id.split("~")
        return {"data": [res("appScreenshots", f"{set_id}~{n}") for n in range(self.shots[loc][t])]}

    def _attached(self, b: Any, p: Any, vid: str) -> dict[str, Any]:
        bid = self.attached.get(vid)
        return {"data": next((x for x in self.builds if x["id"] == bid), None)}

    def _review(self, b: Any, p: Any, vid: str) -> Any:
        if vid not in self.review:
            return httpx.Response(404, json={"errors": [{"detail": "none"}]})
        return {"data": self.review[vid]}

    def _phased(self, b: Any, p: Any, vid: str) -> Any:
        return {"data": self.phased[vid]} if vid in self.phased else httpx.Response(404, json={"errors": []})

    def _builds(self, b: Any, p: dict[str, str]) -> dict[str, Any]:
        rows = [x for x in self.builds if p.get("filter[preReleaseVersion.version]") in (None, x["_version"])]
        included = [res("preReleaseVersions", f"pre-{x['_version']}", version=x["_version"]) for x in rows]
        return {"data": rows, "included": included}

    def _build(self, b: Any, p: Any, bid: str) -> dict[str, Any]:
        return {"data": next(x for x in self.builds if x["id"] == bid)}

    def _subs(self, b: Any, p: dict[str, str]) -> dict[str, Any]:
        states = p.get("filter[state]")
        rows = [s for s in self.submissions if not states or s["attributes"]["state"] in states.split(",")]
        return {"data": rows}

    def _patch_loc(self, b: dict[str, Any], p: Any, kind: str, lid: str) -> dict[str, Any]:
        store = self.info_locs if kind == "appInfoLocalizations" else self.ver_locs
        loc = next(x for rows in store.values() for x in rows if x["id"] == lid)
        loc["attributes"].update(b["data"]["attributes"])
        return {"data": loc}

    def _post_loc(self, b: dict[str, Any], p: Any, kind: str) -> dict[str, Any]:
        lid = self._new_id("loc")
        rel = next(iter(b["data"]["relationships"].values()))["data"]["id"]
        store = self.info_locs if kind == "appInfoLocalizations" else self.ver_locs
        store.setdefault(rel, []).append(res(kind, lid, **b["data"]["attributes"]))
        return {"data": {"type": kind, "id": lid}}

    def _post_version(self, b: dict[str, Any], p: Any) -> dict[str, Any]:
        vid = self._new_id("v")
        self.version(vid, b["data"]["attributes"]["versionString"], "PREPARE_FOR_SUBMISSION")
        return {"data": {"type": "appStoreVersions", "id": vid}}

    def _patch_version(self, b: dict[str, Any], p: Any, vid: str) -> dict[str, Any]:
        v = next(x for x in self.versions if x["id"] == vid)
        v["attributes"].update(b["data"]["attributes"])
        return {"data": v}

    def _attach(self, b: dict[str, Any], p: Any, vid: str) -> httpx.Response:
        self.attached[vid] = b["data"]["id"]
        return httpx.Response(204)

    def _post_sub(self, b: dict[str, Any], p: Any) -> dict[str, Any]:
        sid = self._new_id("sub")
        self.submission(sid, "READY_FOR_REVIEW", [])
        return {"data": {"type": "reviewSubmissions", "id": sid}}

    def _patch_sub(self, b: dict[str, Any], p: Any, sid: str) -> dict[str, Any]:
        s = next(x for x in self.submissions if x["id"] == sid)
        if b["data"]["attributes"].get("submitted"):
            if any(i["attributes"]["state"] == "REJECTED" and not i["attributes"]["resolved"]
                   for i in self.items[sid]):
                return httpx.Response(409, json={"errors": [{"detail": "not ready to be submitted yet"}]})  # type: ignore[return-value]
            s["attributes"]["state"] = "WAITING_FOR_REVIEW"
        return {"data": s}

    def _post_item(self, b: dict[str, Any], p: Any) -> dict[str, Any]:
        rel = b["data"]["relationships"]
        sid, vid = rel["reviewSubmission"]["data"]["id"], rel["appStoreVersion"]["data"]["id"]
        iid = self._new_id("item")
        self.items.setdefault(sid, []).append(self._item(iid, "READY_FOR_REVIEW", vid))
        return {"data": {"type": "reviewSubmissionItems", "id": iid}}

    def _patch_item(self, b: dict[str, Any], p: Any, iid: str) -> dict[str, Any]:
        it = next(x for rows in self.items.values() for x in rows if x["id"] == iid)
        it["attributes"].update(b["data"]["attributes"])
        return {"data": it}


def client() -> asc.ASC:
    return asc.ASC(key())
