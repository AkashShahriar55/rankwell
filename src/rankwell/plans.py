"""Two-step writes: plan (read live state, list current → new) then apply (after the user says yes).

A plan stores the hash of the live state it was built from. apply() asks the plan kind's applier to
re-read the live state; when the hash changed, the plan is refused and the user gets a fresh plan.
Every applied change is written to audit_log. A payload key named `secret` (e.g. a demo account password)
is removed from the stored plan as soon as the plan is applied, fails or expires.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets as pysecrets
from collections.abc import Callable
from typing import Any

from .db import Database
from .errors import PlanError
from .util import now_utc

TTL_MINUTES = 30

Plan = dict[str, Any]
# An applier gets the plan and returns a result dict. It must call verify(plan, current_live_hash) before
# writing anything, and audit() for each change it makes.
Applier = Callable[[Database, Plan], dict[str, Any]]
_appliers: dict[str, Applier] = {}


def applier(kind: str) -> Callable[[Applier], Applier]:
    def wrap(fn: Applier) -> Applier:
        _appliers[kind] = fn
        return fn

    return wrap


def state_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def create(db: Database, kind: str, app_id: int | None, changes: list[dict[str, Any]], live_hash: str,
           payload: dict[str, Any] | None = None, notes: list[str] | None = None,
           ttl_minutes: int = TTL_MINUTES) -> Plan:
    if kind not in _appliers:
        raise PlanError(f"No applier for plan kind '{kind}'.")
    sweep(db)
    pid = f"{kind}-{pysecrets.token_hex(4)}"
    now = dt.datetime.now(dt.UTC)
    body = {"changes": changes, "notes": notes or [], **(payload or {})}
    db.execute(
        "INSERT INTO plans (id, kind, app_id, payload, live_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, kind, app_id, json.dumps(body, ensure_ascii=False, default=str), live_hash,
         now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         (now + dt.timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    return get(db, pid, pending_only=False)


def get(db: Database, plan_id: str, pending_only: bool = True) -> Plan:
    row = db.one("SELECT * FROM plans WHERE id = ?", (plan_id,))
    if not row:
        raise PlanError(f"No plan '{plan_id}'. Make a new plan.")
    row.update(json.loads(row.pop("payload")))
    if pending_only:
        if row["status"] != "pending":
            raise PlanError(f"Plan '{plan_id}' is {row['status']}; make a new plan.")
        if row["expires_at"] < now_utc():
            _close(db, plan_id, "expired")
            raise PlanError(f"Plan '{plan_id}' expired; make a new plan (live data may have changed).")
    return row


def sweep(db: Database) -> None:
    """Expire pending plans past their time and drop secrets from every plan that is no longer pending."""
    db.execute("UPDATE plans SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", (now_utc(),))
    db.execute("UPDATE plans SET payload = json_remove(payload, '$.secret') "
               "WHERE status != 'pending' AND json_extract(payload, '$.secret') IS NOT NULL")


def _close(db: Database, plan_id: str, status: str, result: dict[str, Any] | None = None) -> None:
    db.execute("UPDATE plans SET status = ?, result = ?, payload = json_remove(payload, '$.secret'), "
               "applied_at = CASE WHEN ? = 'applied' THEN ? ELSE applied_at END WHERE id = ?",
               (status, json.dumps(result, default=str) if result is not None else None, status, now_utc(),
                plan_id))


def verify(plan: Plan, current_hash: str) -> None:
    if current_hash != plan["live_hash"]:
        raise PlanError("App Store Connect changed since this plan was made. Make a new plan and review it again.")


def audit(db: Database, plan_id: str | None, kind: str, target: str | None, field: str | None,
          old: Any, new: Any, status: str = "ok", error: str | None = None) -> None:
    db.execute(
        "INSERT INTO audit_log (plan_id, kind, target, field, old, new, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (plan_id, kind, target, field, _text(old), _text(new), status, error),
    )


def _text(v: Any) -> str | None:
    return None if v is None else v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def apply(db: Database, plan_id: str) -> dict[str, Any]:
    plan = get(db, plan_id)
    fn = _appliers.get(plan["kind"])
    if fn is None:
        raise PlanError(f"No applier for plan kind '{plan['kind']}'.")
    try:
        result = fn(db, plan)
    except PlanError:
        _close(db, plan_id, "failed", {"error": "out of date"})
        raise
    except Exception as e:
        _close(db, plan_id, "failed", {"error": str(e)[:500]})
        raise
    _close(db, plan_id, "applied", result)
    return result
