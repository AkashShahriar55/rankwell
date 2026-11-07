import json
from typing import Any

import httpx
import pytest
import respx
from asc_fake import key

from rankwell import apps, listing, plans, replies
from rankwell import db as dbmod
from rankwell.errors import PlanError, RankwellError
from rankwell.stores import asc as ascmod


def review(rid: str, rating: int, created: str, response: str | None = None, title: str = "Bad") -> dict[str, Any]:
    r: dict[str, Any] = {"type": "customerReviews", "id": rid,
                         "attributes": {"rating": rating, "title": title, "body": f"body {rid}",
                                        "reviewerNickname": "x", "createdDate": created, "territory": "USA"},
                         "relationships": {"response": {"data": {"type": "customerReviewResponses",
                                                                 "id": f"resp-{rid}"} if response else None}}}
    return r


class FakeReviews:
    def __init__(self) -> None:
        self.reviews: dict[str, dict[str, Any]] = {}
        self.responses: dict[str, str] = {}
        self.posts: list[dict[str, Any]] = []
        self.fail_post: set[str] = set()
        self.mock = respx.mock(assert_all_called=False)
        self.mock.route(host="api.appstoreconnect.apple.com").mock(side_effect=self.handle)

    def add(self, rid: str, rating: int, created: str, response: str | None = None) -> None:
        self.reviews[rid] = review(rid, rating, created, response)
        if response:
            self.responses[rid] = response

    def _included(self, ids: list[str]) -> list[dict[str, Any]]:
        return [{"type": "customerReviewResponses", "id": f"resp-{i}",
                 "attributes": {"responseBody": self.responses[i], "state": "PUBLISHED"}}
                for i in ids if i in self.responses]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, request.url.params
        if request.method == "GET" and path == "/v1/apps/100/customerReviews":
            ratings = {int(r) for r in q.get("filter[rating]", "1,2,3,4,5").split(",")}
            rows = sorted((r for r in self.reviews.values() if r["attributes"]["rating"] in ratings),
                          key=lambda r: r["attributes"]["createdDate"], reverse=True)
            start = int(q.get("cursor", "0"))
            page = rows[start:start + 2]
            links = {"next": f"https://api.appstoreconnect.apple.com/v1/apps/100/customerReviews?cursor={start + 2}"
                     f"&filter[rating]={q.get('filter[rating]', '1,2,3,4,5')}"} if start + 2 < len(rows) else {}
            return httpx.Response(200, json={"data": page, "included": self._included([r["id"] for r in page]),
                                             "links": links})
        if request.method == "GET" and path.startswith("/v1/customerReviews/"):
            rid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"data": self.reviews[rid], "included": self._included([rid])})
        if request.method == "POST" and path == "/v1/customerReviewResponses":
            body = json.loads(request.content)
            rid = body["data"]["relationships"]["review"]["data"]["id"]
            if rid in self.fail_post:
                return httpx.Response(409, json={"errors": [{"detail": "nope"}]})
            self.posts.append(body)
            self.responses[rid] = body["data"]["attributes"]["responseBody"]
            self.reviews[rid] = review(rid, self.reviews[rid]["attributes"]["rating"],
                                       self.reviews[rid]["attributes"]["createdDate"], self.responses[rid])
            return httpx.Response(201, json={"data": {"type": "customerReviewResponses", "id": f"resp-{rid}",
                                                      "attributes": {"state": "PENDING_PUBLISH"}}})
        return httpx.Response(404, json={"errors": [{"detail": path}]})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    f = FakeReviews()
    client = ascmod.ASC(key())
    monkeypatch.setattr(listing, "connect", lambda: client)
    with f.mock:
        yield f


@pytest.fixture
def setup() -> tuple[dbmod.Database, dict[str, Any]]:
    db = dbmod.Database(":memory:")
    return db, apps.add(db, "Snap", store_id=100)


RECENT = "2099-01-0{}T10:00:00-07:00"


def test_reads_unanswered_low_ratings_across_pages(fake: FakeReviews, setup: Any) -> None:
    _, app = setup
    fake.add("1", 1, RECENT.format(5))
    fake.add("2", 2, RECENT.format(4), response="Thanks")
    fake.add("3", 3, RECENT.format(3))
    fake.add("4", 5, RECENT.format(2))
    fake.add("5", 1, "2001-01-01T00:00:00-07:00")
    rows = replies.asc_reviews(listing.connect(), app, days=30, max_rating=3, unanswered_only=True)
    assert [r["review_id"] for r in rows] == ["1", "3"]
    every = replies.asc_reviews(listing.connect(), app, days=30, max_rating=5)
    assert [r["review_id"] for r in every] == ["1", "2", "3", "4"]
    assert every[1]["response"]["body"] == "Thanks"
    assert "✓ published" in replies.reviews_display(every)


def test_plan_apply_and_replace(fake: FakeReviews, setup: Any) -> None:
    db, app = setup
    fake.add("1", 1, RECENT.format(5))
    fake.add("2", 2, RECENT.format(4), response="Old reply")
    plan = replies.plan_replies(db, listing.connect(), app, [{"review_id": "1", "text": "Sorry, fixed in 1.2."},
                                                             {"review_id": "2", "text": "New reply."}])
    shown = replies.present(plan)
    assert "Old reply" in shown["display"] and any("replaced" in n for n in shown["notes"])
    assert fake.posts == []
    result = plans.apply(db, plan["id"])
    assert [s["review_id"] for s in result["sent"]] == ["1", "2"] and result["failed"] == []
    assert fake.responses["2"] == "New reply."
    assert db.value("SELECT count(*) FROM audit_log WHERE kind = 'review_reply'") == 2
    assert [r["review_id"] for r in replies.history(db, app["id"])] and len(replies.history(db, app["id"])) == 2


def test_stale_plan_refused(fake: FakeReviews, setup: Any) -> None:
    db, app = setup
    fake.add("1", 1, RECENT.format(5))
    plan = replies.plan_replies(db, listing.connect(), app, [{"review_id": "1", "text": "Hi"}])
    fake.responses["1"] = "Someone replied in App Store Connect"
    fake.reviews["1"] = review("1", 1, RECENT.format(5), "x")
    with pytest.raises(PlanError, match="changed"):
        plans.apply(db, plan["id"])
    assert fake.posts == []


def test_partial_failure_reports_each(fake: FakeReviews, setup: Any) -> None:
    db, app = setup
    fake.add("1", 1, RECENT.format(5))
    fake.add("2", 1, RECENT.format(4))
    fake.fail_post.add("2")
    plan = replies.plan_replies(db, listing.connect(), app, [{"review_id": "1", "text": "a"},
                                                             {"review_id": "2", "text": "b"}])
    result = plans.apply(db, plan["id"])
    assert [s["review_id"] for s in result["sent"]] == ["1"] and result["failed"][0]["review_id"] == "2"
    assert db.value("SELECT count(*) FROM audit_log WHERE status = 'failed'") == 1


def test_reply_checks(fake: FakeReviews, setup: Any) -> None:
    db, app = setup
    fake.add("1", 1, RECENT.format(5))
    with pytest.raises(RankwellError, match="empty"):
        replies.plan_replies(db, listing.connect(), app, [{"review_id": "1", "text": "  "}])
    with pytest.raises(RankwellError, match="5970"):
        replies.plan_replies(db, listing.connect(), app, [{"review_id": "1", "text": "x" * 5971}])
    plan = replies.plan_replies(db, listing.connect(), app,
                                [{"review_id": "1", "text": "Please give us 5 stars at https://x.io"}])
    assert any("rr-no-incentives" in n for n in plan["notes"]) and any("clickable" in n for n in plan["notes"])
    assert replies.check_reply("Thanks, fixed in 1.2.") == []
