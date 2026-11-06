"""App Review: where a version stands, and the rejections the user pasted from App Store Connect.

The App Store Connect API shows review submissions and their items (state REJECTED, resolved or not), but
it has no documented endpoint for the Resolution Center messages, so the rejection text is what the user
pastes from App Store Connect (record_rejection).
"""

from __future__ import annotations

import re
from typing import Any

from . import listing, render
from .apps import App
from .db import Database
from .errors import RankwellError
from .stores import asc as ascmod
from .stores.asc import ASC

MAX_SUBMISSIONS = 5


def _items(asc: ASC, submission_id: str, version_names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for i in asc.get_all(f"/v1/reviewSubmissions/{submission_id}/items", include="appStoreVersion"):
        a = i["attributes"]
        vid = ((i.get("relationships", {}).get("appStoreVersion") or {}).get("data") or {}).get("id")
        out.append({"id": i["id"], "state": a.get("state"), "resolved": a.get("resolved"),
                    "version": version_names.get(vid or "", vid) if vid else None,
                    "kind": "version" if vid else "other (in-app purchase, event, ...)"})
    return out


def status(asc: ASC, app: App) -> dict[str, Any]:
    """Version states, recent review submissions with their items, and what to do next."""
    sid = listing.asc_app_id(asc, app)
    vs = listing.versions(asc, sid)
    names = {v["id"]: v["version"] for v in vs}
    subs = asc.get_all("/v1/reviewSubmissions", **{"filter[app]": sid, "filter[platform]": "IOS"})
    subs.sort(key=lambda s: s["attributes"].get("submittedDate") or "9999", reverse=True)
    submissions = []
    for s in subs[:MAX_SUBMISSIONS]:
        a = s["attributes"]
        submissions.append({"id": s["id"], "state": a.get("state"), "submitted": a.get("submittedDate"),
                            "items": _items(asc, s["id"], names)})
    rejected = [(s, i) for s in submissions if s["state"] == "UNRESOLVED_ISSUES"
                for i in s["items"] if i["state"] == "REJECTED"]
    last = max((s["submitted"] for s in submissions if s["submitted"]), default=None)
    ev, lv, rv = (listing.pick(vs, st) for st in (ascmod.EDITABLE, ascmod.LIVE, ascmod.IN_REVIEW))
    if rejected:
        what = ", ".join(f"version {i['version']}" if i["version"] else i["kind"] for _, i in rejected)
        hint = (f"Apple rejected {what}. Paste the Resolution Center message (rejection_record), fix it, then "
                "plan_submit resubmits in the same submission.")
    elif rv:
        hint = f"Version {rv['version']} is {rv['state']}; nothing to do until Apple answers."
    elif ev:
        hint = f"Version {ev['version']} is {ev['state']}; release_check shows what is left before plan_submit."
    else:
        hint = "No version in review or in preparation."
    return {"versions": vs[:5], "submissions": submissions, "rejected": bool(rejected), "last_submitted": last,
            "live_version": lv["version"] if lv else None, "next": hint}


def status_display(st: dict[str, Any]) -> str:
    rows = [{"version": v["version"], "state": v["state"], "created": (v.get("created") or "")[:10]}
            for v in st["versions"]]
    subs = [{"submitted": (s["submitted"] or "")[:16].replace("T", " "), "state": s["state"],
             "items": "; ".join(f"{i['version'] or i['kind']}: {i['state']}" + (" (resolved)" if i["resolved"] else "")
                                for i in s["items"]) or "–"} for s in st["submissions"]]
    return ("**Versions**\n\n" + render.table(rows) + "\n\n**Review submissions**\n\n"
            + render.table(subs, ["submitted", "state", "items"]) + f"\n\n{st['next']}")


def record_rejection(db: Database, app_id: int, text: str, guideline: str | None = None,
                     version: str | None = None, submission_id: str | None = None) -> dict[str, Any]:
    """Store App Review's message as pasted by the user; guideline numbers are read from it when not given."""
    text = text.strip()
    if not text:
        raise RankwellError("Paste the message from App Store Connect → App Review (Resolution Center).")
    if not guideline:
        found = dict.fromkeys(re.findall(r"Guideline\s+(\d+(?:\.\d+)*)", text, re.IGNORECASE))
        guideline = ", ".join(found) or None
    cur = db.execute("INSERT INTO rejections (app_id, version, guideline, text, submission_id) VALUES (?, ?, ?, ?, ?)",
                     (app_id, version, guideline, text, submission_id))
    row = db.one("SELECT * FROM rejections WHERE id = ?", (cur.lastrowid,))
    assert row is not None
    return row


def rejections(db: Database, app_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return db.all("SELECT * FROM rejections WHERE app_id = ? ORDER BY id DESC LIMIT ?", (app_id, limit))
