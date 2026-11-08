"""Tools: is the daily collection working, and start one now."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, collect, paths, render, schedule, secrets
from ..db import Database
from ..util import days_ago, today
from .common import db, safe


def app_freshness(d: Database, app: apps.App, has_key: bool) -> dict[str, Any]:
    """Last snapshot and rank dates of one app and the state of its App Store Connect analytics."""
    ids = apps.tracked_store_ids(d, app)
    marks = ",".join("?" for _ in ids)
    last_snapshot = d.value(f"SELECT max(date) FROM snapshots WHERE store_id IN ({marks})", ids) if ids else None
    last_rank = d.value(
        "SELECT max(t.date) FROM keyword_top t JOIN keywords k ON k.country = t.country AND k.term = t.term "
        "WHERE k.app_id = ?", (app["id"],))
    if ids:
        ranked = d.value(f"SELECT max(date) FROM ranks WHERE store_id IN ({marks})", ids)
        last_rank = max(filter(None, [last_rank, ranked]), default=None)
    return {"app": app["name"], "stage": app["stage"], "last_snapshot": last_snapshot, "last_rank": last_rank,
            "asc": asc_state(d, app, has_key)}


def asc_state(d: Database, app: apps.App, has_key: bool) -> str:
    if not app.get("store_id"):
        return "not live"
    if not has_key:
        return "no key"
    last = d.value("SELECT max(date) FROM asc_daily WHERE store_id = ?", (app["store_id"],))
    if not last:
        files = d.value("SELECT count(*) FROM asc_instances WHERE store_id = ?", (app["store_id"],))
        return "requested, waiting for Apple" if not files else "files without daily data"
    return f"flowing (to {last})" if last >= days_ago(collect.ASC_FRESH_DAYS) else f"stale (last {last})"


def recent_runs(d: Database, limit: int = 5) -> list[dict[str, Any]]:
    out = []
    for r in d.all("SELECT * FROM runs WHERE kind IN ('collect', 'import') ORDER BY id DESC LIMIT ?", (limit,)):
        summary = json.loads(r["summary"]) if r["summary"] else {}
        per_app = summary.get("apps", {})
        out.append({"id": r["id"], "kind": r["kind"], "date": summary.get("date") or r["started_at"][:10],
                    "status": r["status"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                    "apps": len(per_app), "errors": sum(len(a.get("errors", [])) for a in per_app.values())})
    return out


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def collection_status() -> dict[str, Any]:
        """Is the daily data collection working? Last runs, per app the newest listing and rank dates, missed days,
        whether App Store Connect analytics are flowing, and whether the daily job is installed. Use before
        trusting ranks or stats, or when data looks old."""
        d = db()
        collect.mark_stale_runs(d)
        has_key = secrets.has_asc_key()
        fresh = [app_freshness(d, a, has_key) for a in apps.list_apps(d)]
        runs = recent_runs(d)
        missed = collect.missed_days(d)
        job = schedule.status(check_loaded=False)
        lines = ["**Collection**", render.table(
            [{**r, "kind": r["kind"], "finished_at": r["finished_at"] or "running"} for r in runs],
            ["id", "kind", "date", "status", "apps", "errors", "finished_at"], align_right=["apps", "errors"])]
        lines += ["**Per app**", render.table(fresh, ["app", "stage", "last_snapshot", "last_rank", "asc"],
                                              {"last_snapshot": "listing", "last_rank": "ranks", "asc": "ASC"})]
        notes = []
        if collect.is_locked():
            notes.append("A collection is running now.")
        if not collect.collected_on(d, today()):
            notes.append("Today has not been collected yet.")
        if missed:
            notes.append(f"Missed days (last 30): {', '.join(missed)}. Past days cannot be filled in.")
        notes.append(f"Daily job: installed, {job['at']}." + (f" {job['note']}" if job.get("note") else "")
                     if job["installed"]
                     else "Daily job: not installed (`rankwell schedule install --at 11:00`).")
        if not has_key:
            notes.append("No App Store Connect key: impressions, page views and downloads are not collected.")
        lines.append("\n".join(f"- {n}" for n in notes))
        return {"runs": runs, "apps": fresh, "missed_days": missed, "schedule": job, "asc_key": has_key,
                "running": collect.is_locked(), "display": "\n\n".join(lines)}

    @mcp.tool()
    @safe
    def collect_now(app: str | None = None) -> dict[str, Any]:
        """Start a collection in the background (all apps, or one) and return at once. It takes about 3 s per
        keyword per country plus a few seconds per app; check progress later with collection_status."""
        d = db()
        targets = [apps.get(d, app)] if app else apps.list_apps(d)
        if not targets:
            return {"started": False, "display": "No apps tracked yet. Add one first (add_app)."}
        if collect.is_locked():
            return {"started": False, "display": "A collection is already running; check collection_status later."}
        seconds = collect.estimate_seconds(d, targets, with_asc=secrets.has_asc_key())
        cmd = [*schedule.rankwell_command(), "collect", "--quiet", *(["--app", str(targets[0]["id"])] if app else [])]
        log = paths.logs_dir() / "collect-now.log"
        with log.open("ab") as out:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,  # noqa: S603
                                    start_new_session=True, cwd=paths.home())
        minutes = max(1, round(seconds / 60))
        who = targets[0]["name"] if app else f"{len(targets)} app(s)"
        return {"started": True, "pid": proc.pid, "estimate_seconds": seconds, "log": str(log),
                "display": f"Collecting {who} in the background, about {minutes} min "
                           f"(~3 s per keyword per country plus each app's listings). Log: `{log}`."}
