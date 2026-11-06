"""`rankwell collect`, `rankwell schedule ...` and `rankwell import tracker`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from .. import collect, render, schedule
from .. import db as dbmod
from ..importers import tracker


def summary_table(result: dict[str, Any]) -> str:
    rows = [{"app": slug, "status": r["status"], "listings": r["snapshots"], "changes": r["changes"],
             "keywords": r["keywords"], "reviews": r["reviews"], "charts": r["charts"],
             "asc": r.get("asc") or "–"} for slug, r in result["apps"].items()]
    return render.table(rows, align_right=["listings", "changes", "keywords", "reviews", "charts"])


def problems(result: dict[str, Any]) -> list[str]:
    out = []
    for slug, r in result["apps"].items():
        out += [f"{slug}: {e}" for e in r.get("errors", [])]
        out += [f"{slug}: {n}" for n in r.get("notes", [])]
    return out


def register(main: click.Group) -> None:
    @main.command("collect")
    @click.option("--app", "app_refs", multiple=True, help="Only this app (id, slug or name). Repeatable.")
    @click.option("--catch-up", is_flag=True, help="Only when today has no collection yet (safe to call often).")
    @click.option("--quiet", is_flag=True, help="No progress lines, only the summary.")
    def collect_cmd(app_refs: tuple[str, ...], catch_up: bool, quiet: bool) -> None:
        """Collect ranks, listings, reviews, charts and App Store Connect analytics for today."""
        db = dbmod.Database()
        log = None if quiet else (lambda msg: click.echo(msg, err=True))
        if catch_up:
            result = collect.catch_up(db, list(app_refs) or None, log=log)
        else:
            with collect.lock():
                result = collect.run(db, list(app_refs) or None, log=log)
        if "skipped" in result:
            if not quiet:
                click.echo(f"Skipped: {result['skipped']}.")
            return
        click.echo(f"Collection {result['date']}: {result['status']}")
        if result["apps"]:
            click.echo(summary_table(result))
        else:
            click.echo("No apps tracked yet.")
        for line in problems(result):
            click.echo(f"  {line}")
        if result["status"] == "failed":
            sys.exit(1)

    @main.group("schedule")
    def schedule_group() -> None:
        """The daily collection job (launchd)."""

    @schedule_group.command("install")
    @click.option("--at", "at", help="Time of day, HH:MM (default: collect.at in config.toml).")
    def install_cmd(at: str | None) -> None:
        """Run the collection every day at a time, and at login when a day was missed."""
        out = schedule.install(at)
        click.echo(f"Daily collection at {out['at']} installed ({out['plist']}).")
        click.echo(f"Log: {out['log']}")

    @schedule_group.command("uninstall")
    def uninstall_cmd() -> None:
        """Stop the daily collection."""
        out = schedule.uninstall()
        click.echo("Daily collection removed." if out["removed"] else "No daily collection was installed.")

    @schedule_group.command("status")
    def status_cmd() -> None:
        """Whether the daily collection is installed and loaded."""
        out = schedule.status()
        if not out["installed"]:
            click.echo("Not installed. Run: rankwell schedule install --at 11:00")
            return
        click.echo(f"Installed: daily at {out['at']}, {'loaded' if out.get('loaded') else 'NOT loaded'}"
                   + (f", last exit {out['last_exit']}" if out.get("last_exit") is not None else ""))
        click.echo(f"Command: {' '.join(out['command'] or [])}")
        click.echo(f"Log: {out['log']}")
        db = dbmod.Database()
        missed = collect.missed_days(db)
        if missed:
            click.echo(f"Missed days (last 30): {', '.join(missed)}")

    @main.group("import")
    def import_group() -> None:
        """One-off imports from other tools."""

    @import_group.command("tracker")
    @click.option("--env", "env_path", type=click.Path(dir_okay=False, path_type=Path),
                  help=f"Tracker .env with SUPABASE_URL and SUPABASE_SERVICE_KEY (default {tracker.DEFAULT_ENV}).")
    def import_tracker_cmd(env_path: Path | None) -> None:
        """Import projects, ranks, listings, reviews and App Store Connect data from the original tracker."""
        source = tracker.connect(env_path)
        result = tracker.run(dbmod.Database(), source, log=lambda msg: click.echo(msg, err=True))
        rows = [{"table": k, "rows": v} for k, v in result["counts"].items()]
        click.echo(render.table(rows, align_right=["rows"]))
