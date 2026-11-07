import pytest

from rankwell import plans
from rankwell.db import Database
from rankwell.errors import PlanError

calls: list[str] = []


@plans.applier("test-kind")
def _apply(db: Database, plan: plans.Plan) -> dict:
    plans.verify(plan, plan["current"])
    plans.audit(db, plan["id"], "test-kind", "target", "field", "old", "new")
    calls.append(plan["id"])
    return {"ok": True}


def test_plan_apply_once(db: Database) -> None:
    h = plans.state_hash({"a": 1})
    p = plans.create(db, "test-kind", None, [{"field": "x"}], h, payload={"current": h})
    assert p["changes"] == [{"field": "x"}] and p["status"] == "pending"
    assert plans.apply(db, p["id"]) == {"ok": True}
    assert db.value("SELECT count(*) FROM audit_log") == 1
    with pytest.raises(PlanError, match="applied"):
        plans.apply(db, p["id"])


def test_out_of_date_plan_is_refused(db: Database) -> None:
    p = plans.create(db, "test-kind", None, [], plans.state_hash(1), payload={"current": plans.state_hash(2)})
    with pytest.raises(PlanError, match="changed since"):
        plans.apply(db, p["id"])
    assert plans.get(db, p["id"], pending_only=False)["status"] == "failed"


def test_expired_plan(db: Database) -> None:
    p = plans.create(db, "test-kind", None, [], "h", payload={"current": "h"}, ttl_minutes=-1)
    with pytest.raises(PlanError, match="expired"):
        plans.apply(db, p["id"])


def test_unknown_kind(db: Database) -> None:
    with pytest.raises(PlanError):
        plans.create(db, "nope", None, [], "h")


def test_secret_is_dropped_when_plan_closes(db: Database) -> None:
    p = plans.create(db, "test-kind", None, [], "h", payload={"current": "h", "secret": {"pw": "x"}})
    plans.apply(db, p["id"])
    assert "secret" not in plans.get(db, p["id"], pending_only=False)
    q = plans.create(db, "test-kind", None, [], "h", payload={"current": "h", "secret": {"pw": "x"}},
                     ttl_minutes=-1)
    plans.sweep(db)
    row = plans.get(db, q["id"], pending_only=False)
    assert row["status"] == "expired" and "secret" not in row


def test_plan_cannot_be_applied_twice_concurrently(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import threading
    import time

    shared = Database(tmp_path / "plans.db")
    writes: list[str] = []

    @plans.applier("slow-kind")
    def _slow(db: Database, plan: plans.Plan) -> dict:
        time.sleep(0.2)
        writes.append(plan["id"])
        return {}

    p = plans.create(shared, "slow-kind", None, [], "h")
    errors: list[Exception] = []

    def go() -> None:
        try:
            plans.apply(shared, p["id"])
        except PlanError as e:
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert writes == [p["id"]] and len(errors) == 2


def test_reserved_payload_keys_refused() -> None:
    from rankwell import db as dbmod

    db = dbmod.Database(":memory:")
    plans.applier("noop_reserved")(lambda d, p: {})
    with pytest.raises(PlanError, match="reserved"):
        plans.create(db, "noop_reserved", None, [], "h", payload={"status": "draft"})
