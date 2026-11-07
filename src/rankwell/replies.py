"""Replies to customer reviews through App Store Connect.

The RSS feed the daily collection reads has no reply state, so this reads reviews from App Store Connect
(customerReviews with their response) and sends replies as a plan: show, ask, apply. Posting a reply to a
review that already has one replaces it; the plan shows the old reply as the current value.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import apps, listing, plans, render
from .apps import App
from .db import Database
from .errors import RankwellError
from .stores.asc import ASC, Resource
from .util import now_utc

MAX_REPLY = 5970  # App Store Connect's limit for a developer response
MAX_REVIEWS = 400  # reading stops here even when the period has more
MAX_PER_PLAN = 25


def _response(review: Resource, included: dict[str, Resource]) -> dict[str, Any] | None:
    ref = ((review.get("relationships") or {}).get("response") or {}).get("data")
    r = included.get(ref["id"]) if ref else None
    if not r:
        return None
    a = r["attributes"]
    return {"id": r["id"], "body": a.get("responseBody") or "", "state": a.get("state"),
            "modified": a.get("lastModifiedDate")}


def _row(review: Resource, included: dict[str, Resource]) -> dict[str, Any]:
    a = review["attributes"]
    return {"review_id": review["id"], "rating": a.get("rating"), "title": a.get("title") or "",
            "body": a.get("body") or "", "author": a.get("reviewerNickname") or "",
            "territory": a.get("territory") or "", "created": a.get("createdDate") or "",
            "response": _response(review, included)}


def asc_reviews(asc: ASC, app: App, days: int = 30, max_rating: int = 5, unanswered_only: bool = False,
                territory: str | None = None) -> list[dict[str, Any]]:
    """Reviews from App Store Connect, newest first, within `days`, with their reply when there is one."""
    if days < 1:
        raise RankwellError("days must be 1 or more.")
    sid = listing.asc_app_id(asc, app)
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    params: dict[str, Any] = {"sort": "-createdDate", "include": "response", "limit": 200,
                              "filter[rating]": ",".join(str(r) for r in range(1, max(1, min(5, max_rating)) + 1))}
    if territory:
        params["filter[territory]"] = territory.upper()
    page = asc.get(f"/v1/apps/{sid}/customerReviews", **params)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    while True:
        included = {r["id"]: r for r in page.get("included", []) if r.get("type") == "customerReviewResponses"}
        for rv in page.get("data", []):
            row = _row(rv, included)
            if row["created"][:19] < since:
                return out
            if not (unanswered_only and row["response"]):
                out.append(row)
            if len(out) >= MAX_REVIEWS:
                return out
        nxt = page.get("links", {}).get("next")
        if not nxt or nxt in seen:
            return out
        seen.add(nxt)
        page = asc.request("GET", nxt)


def reviews_display(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No reviews match._"
    table = [{"id": r["review_id"], "★": r["rating"], "date": r["created"][:10], "where": r["territory"],
              "review": _clip(f"{r['title']} — {r['body']}" if r["title"] else r["body"], 90),
              "reply": ("✓ " + (r["response"]["state"] or "").lower()) if r["response"] else "–"}
             for r in rows]
    return render.table(table, ["id", "★", "date", "where", "review", "reply"])


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _live(asc: ASC, review_id: str) -> dict[str, Any]:
    page = asc.get(f"/v1/customerReviews/{review_id}", include="response")
    included = {r["id"]: r for r in page.get("included", []) if r.get("type") == "customerReviewResponses"}
    return _row(page["data"], included)


def check_reply(text: str) -> list[str]:
    """Problems that would get a reply refused or read badly."""
    t = text.strip()
    problems = []
    if not t:
        problems.append("The reply is empty.")
    if len(t) > MAX_REPLY:
        problems.append(f"The reply is {len(t)} characters; App Store Connect allows {MAX_REPLY}.")
    lower = t.lower()
    if any(w in lower for w in ("5 stars", "five stars", "5-star", "rate us", "free premium", "gift card")):
        problems.append("Do not ask for a rating change or offer anything for one [rr-no-incentives].")
    if "http://" in lower or "https://" in lower:
        problems.append("Links in replies are not clickable; give an email or 'Settings → Help' instead.")
    return problems


def plan_replies(db: Database, asc: ASC, app: App, replies: list[dict[str, str]]) -> plans.Plan:
    """A plan that posts (or replaces) replies to these reviews: [{"review_id", "text"}]."""
    if not replies:
        raise RankwellError("No replies given.")
    if len(replies) > MAX_PER_PLAN:
        raise RankwellError(f"At most {MAX_PER_PLAN} replies per plan; split them.")
    changes: list[dict[str, Any]] = []
    live_state: dict[str, Any] = {}
    for r in replies:
        rid, text = str(r.get("review_id") or "").strip(), (r.get("text") or "").strip()
        if not rid:
            raise RankwellError("Each reply needs a review_id (from reviews_asc).")
        problems = check_reply(text)
        if any("empty" in p or "allows" in p for p in problems):
            raise RankwellError(f"Review {rid}: " + " ".join(problems))
        cur = _live(asc, rid)
        old = cur["response"]["body"] if cur["response"] else ""
        live_state[rid] = old
        changes.append({"review_id": rid, "rating": cur["rating"], "territory": cur["territory"],
                        "review": _clip(f"{cur['title']} — {cur['body']}" if cur["title"] else cur["body"], 120),
                        "field": "reply", "current": old, "new": text, "warnings": problems})
    notes = [f"{c['review_id']}: {w}" for c in changes for w in c["warnings"]]
    if any(c["current"] for c in changes):
        notes.append("Reviews that already have a reply get it replaced.")
    notes.append("The reviewer is notified; Apple shows the reply after a short check (state PENDING_PUBLISH).")
    return plans.create(db, "review_replies", app["id"], changes, plans.state_hash(live_state),
                        payload={"asc_app_id": listing.asc_app_id(asc, app)}, notes=notes)


def present(plan: plans.Plan) -> dict[str, Any]:
    rows = [{"★": c["rating"], "review": c["review"], "current reply": _clip(c["current"], 60) or "–",
             "new reply": _clip(c["new"], 80)} for c in plan["changes"]]
    text = render.table(rows, ["★", "review", "current reply", "new reply"])
    if plan["notes"]:
        text += "\n\n" + "\n".join(f"- {n}" for n in plan["notes"])
    return {"plan_id": plan["id"], "expires_at": plan["expires_at"], "changes": plan["changes"],
            "notes": plan["notes"], "display": text}


@plans.applier("review_replies")
def _apply(db: Database, plan: plans.Plan) -> dict[str, Any]:
    asc = listing.connect()
    live = {c["review_id"]: ((_live(asc, c["review_id"])["response"] or {}).get("body") or "")
            for c in plan["changes"]}
    plans.verify(plan, plans.state_hash(live))
    sent, failed = [], []
    for c in plan["changes"]:
        body = {"data": {"type": "customerReviewResponses", "attributes": {"responseBody": c["new"]},
                         "relationships": {"review": {"data": {"type": "customerReviews", "id": c["review_id"]}}}}}
        try:
            out = asc.post("/v1/customerReviewResponses", body)
        except Exception as e:  # keep going: each reply is its own write
            plans.audit(db, plan["id"], "review_reply", c["review_id"], "reply", c["current"], c["new"], "failed",
                        str(e)[:300])
            failed.append({"review_id": c["review_id"], "error": str(e)[:300]})
            continue
        state = ((out.get("data") or {}).get("attributes") or {}).get("state")
        plans.audit(db, plan["id"], "review_reply", c["review_id"], "reply", c["current"], c["new"])
        db.execute("INSERT INTO review_replies (review_id, app_id, rating, territory, body, state, plan_id, "
                   "replied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (review_id) DO UPDATE SET "
                   "body = excluded.body, state = excluded.state, plan_id = excluded.plan_id, "
                   "replied_at = excluded.replied_at",
                   (c["review_id"], plan["app_id"], c["rating"], c["territory"], c["new"], state, plan["id"],
                    now_utc()))
        sent.append({"review_id": c["review_id"], "state": state})
    if failed and not sent:
        raise RankwellError("No reply was sent: " + "; ".join(f"{f['review_id']}: {f['error']}" for f in failed))
    return {"sent": sent, "failed": failed}


def history(db: Database, app_ref: str | int, limit: int = 20) -> list[dict[str, Any]]:
    app = apps.get(db, app_ref)
    return db.all("SELECT review_id, rating, territory, body, state, replied_at FROM review_replies WHERE app_id = ? "
                  "ORDER BY replied_at DESC LIMIT ?", (app["id"], limit))
