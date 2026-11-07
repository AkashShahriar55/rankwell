"""FakeASC plus screenshot sets, the asset upload flow and the pre-signed upload host, behind respx."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import httpx
from asc_fake import FakeASC, res

UPLOAD_HOST = "upload.blob.example"


class MediaASC(FakeASC):
    def __init__(self, part_size: int = 64) -> None:
        super().__init__()
        self.part_size = part_size
        self.sets: dict[str, list[dict[str, Any]]] = {}  # version localization id → sets
        self.set_shots: dict[str, list[dict[str, Any]]] = {}  # set id → screenshots, in order
        self.parts: dict[str, dict[int, bytes]] = {}  # screenshot id → offset → bytes
        self.puts: list[tuple[str, int, dict[str, str]]] = []  # (screenshot id, offset, headers)
        self.fail_files: set[str] = set()  # file names that end FAILED after commit
        self.stuck_files: set[str] = set()  # file names that never leave UPLOAD_COMPLETE
        self.fail_put: set[str] = set()  # file names whose PUT returns 403
        self.polls_until_done = 1

    # -- setup helpers

    def shot_set(self, loc: str, set_id: str, display: str, files: list[tuple[str, str]] | None = None) -> None:
        """files: (screenshot id, file name); each is COMPLETE with checksum md5(name)."""
        self.sets.setdefault(loc, []).append(res("appScreenshotSets", set_id, screenshotDisplayType=display))
        self.set_shots[set_id] = [self._shot(i, n, hashlib.md5(n.encode()).hexdigest(), "COMPLETE")
                                  for i, n in files or []]

    def _shot(self, sid: str, name: str, checksum: str | None, state: str, size: int = 0) -> dict[str, Any]:
        r = res("appScreenshots", sid, fileName=name, fileSize=size, sourceFileChecksum=checksum,
                assetDeliveryState={"state": state, "errors": []})
        r["_polls"] = 0
        return r

    def names(self, set_id: str) -> list[str]:
        return [s["attributes"]["fileName"] for s in self.set_shots[set_id]]

    def _find(self, sid: str) -> tuple[str, dict[str, Any]]:
        return next((k, s) for k, rows in self.set_shots.items() for s in rows if s["id"] == sid)

    # -- the mock

    def __enter__(self) -> MediaASC:
        super().__enter__()
        self._mock.route(host=UPLOAD_HOST).mock(side_effect=self._put)
        return self

    def _routes(self) -> list[tuple[str, str, Callable[..., Any]]]:
        return [
            (r"/v1/appStoreVersionLocalizations/([\w-]+)/appScreenshotSets", "GET",
             lambda b, p, loc: {"data": self.sets.get(loc, [])}),
            (r"/v1/appScreenshotSets/([\w-]+)/appScreenshots", "GET",
             lambda b, p, s: {"data": [self._public(x) for x in self.set_shots[s]]}),
            (r"/v1/appScreenshotSets", "POST", self._post_set),
            (r"/v1/appScreenshotSets/([\w-]+)/relationships/appScreenshots", "PATCH", self._reorder),
            (r"/v1/appScreenshots", "POST", self._reserve),
            (r"/v1/appScreenshots/([\w-]+)", "PATCH", self._commit),
            (r"/v1/appScreenshots/([\w-]+)", "GET", self._get_shot),
            (r"/v1/appScreenshots/([\w-]+)", "DELETE", self._delete),
            *super()._routes(),
        ]

    def _public(self, s: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in s.items() if not k.startswith("_")}

    def _post_set(self, b: dict[str, Any], p: Any) -> dict[str, Any]:
        loc = b["data"]["relationships"]["appStoreVersionLocalization"]["data"]["id"]
        sid = self._new_id("set")
        self.shot_set(loc, sid, b["data"]["attributes"]["screenshotDisplayType"])
        return {"data": res("appScreenshotSets", sid, **b["data"]["attributes"])}

    def _reorder(self, b: dict[str, Any], p: Any, set_id: str) -> httpx.Response:
        by_id = {s["id"]: s for s in self.set_shots[set_id]}
        ids = [d["id"] for d in b["data"]]
        assert sorted(ids) == sorted(by_id), "reorder must list every screenshot of the set"
        self.set_shots[set_id] = [by_id[i] for i in ids]
        return httpx.Response(204)

    def _reserve(self, b: dict[str, Any], p: Any) -> Any:
        a = b["data"]["attributes"]
        set_id = b["data"]["relationships"]["appScreenshotSet"]["data"]["id"]
        if len(self.set_shots[set_id]) >= 10:  # assumed: Apple keeps the 10-per-set cap while uploading
            return httpx.Response(409, json={"errors": [{"detail": "set is full"}]})
        sid = self._new_id("shot")
        shot = self._shot(sid, a["fileName"], None, "AWAITING_UPLOAD", a["fileSize"])
        self.set_shots[set_id].append(shot)
        size, step = a["fileSize"], self.part_size
        ops = [{"method": "PUT", "url": f"https://{UPLOAD_HOST}/{sid}?part={n}", "offset": off,
                "length": min(step, size - off), "requestHeaders": [{"name": "Content-Type", "value": "image/png"}]}
               for n, off in enumerate(range(0, size, step))]
        return {"data": {**self._public(shot), "attributes": {**shot["attributes"], "uploadOperations": ops}}}

    def _put(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.path.strip("/")
        _, shot = self._find(sid)
        self.calls.append(("PUT", request.url.path, None))
        if shot["attributes"]["fileName"] in self.fail_put:
            return httpx.Response(403)
        assert "authorization" not in request.headers, "upload URLs must not get the JWT"
        off = int(request.url.params["part"]) * self.part_size
        self.parts.setdefault(sid, {})[off] = request.content
        self.puts.append((sid, off, {"Content-Type": request.headers["content-type"]}))
        return httpx.Response(200)

    def _commit(self, b: dict[str, Any], p: Any, sid: str) -> Any:
        _, shot = self._find(sid)
        a = b["data"]["attributes"]
        data = b"".join(v for _, v in sorted(self.parts.get(sid, {}).items()))
        if len(data) != shot["attributes"]["fileSize"]:
            return httpx.Response(409, json={"errors": [{"detail": "bytes received do not match fileSize"}]})
        assert a["uploaded"] is True
        assert a["sourceFileChecksum"] == hashlib.md5(data).hexdigest()
        shot["attributes"]["sourceFileChecksum"] = a["sourceFileChecksum"]
        shot["attributes"]["assetDeliveryState"] = {"state": "UPLOAD_COMPLETE", "errors": []}
        return {"data": self._public(shot)}

    def _get_shot(self, b: Any, p: Any, sid: str) -> dict[str, Any]:
        _, shot = self._find(sid)
        st = shot["attributes"]["assetDeliveryState"]
        name = shot["attributes"]["fileName"]
        if st["state"] == "UPLOAD_COMPLETE" and name not in self.stuck_files:
            shot["_polls"] += 1
            if shot["_polls"] >= self.polls_until_done:
                if name in self.fail_files:
                    shot["attributes"]["assetDeliveryState"] = {
                        "state": "FAILED", "errors": [{"code": "IMAGE_INCORRECT_DIMENSIONS",
                                                       "description": "The dimensions are wrong."}]}
                else:
                    shot["attributes"]["assetDeliveryState"] = {"state": "COMPLETE", "errors": []}
        return {"data": self._public(shot)}

    def _delete(self, b: Any, p: Any, sid: str) -> httpx.Response:
        set_id, shot = self._find(sid)
        self.set_shots[set_id].remove(shot)
        return httpx.Response(204)
