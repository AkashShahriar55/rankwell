"""`rankwell ship`: the release pipeline from a terminal or CI, one step at a time, with a confirm per write."""

from __future__ import annotations

import subprocess
import sys

import click

from .. import apps, listing, plans, release
from .. import db as dbmod
from ..errors import NotConfigured

STEPS = ("check", "build", "upload", "attach", "submit")


def _confirm(plan: plans.Plan, yes: bool, db: dbmod.Database) -> None:
    shown = listing.present(plan)
    click.echo(shown["display"])
    if not yes:
        click.confirm("\nApply this plan?", abort=True)
    result = plans.apply(db, plan["id"])
    click.echo(f"Applied {plan['id']}: " + ", ".join(f"{k}={v}" for k, v in result.items() if k != "output"))


def register(main: click.Group) -> None:
    @main.command()
    @click.argument("app")
    @click.option("--step", type=click.Choice(STEPS), default="check", show_default=True,
                  help="Which step to run.")
    @click.option("--yes", is_flag=True, help="Do not ask before applying (CI).")
    @click.option("--ipa", help="Upload this .ipa instead of the newest one.")
    @click.option("--build", "build_number", help="Attach this build number instead of the newest valid one.")
    @click.option("--wait/--no-wait", default=True, show_default=True,
                  help="After an upload, wait until Apple has processed the build.")
    @click.option("--force", is_flag=True, help="Submit even when the check lists problems.")
    def ship(app: str, step: str, yes: bool, ipa: str | None, build_number: str | None, wait: bool,
             force: bool) -> None:
        """Ship an app: check, build, upload, attach or submit (one step per run)."""
        db = dbmod.Database()
        a = apps.get(db, app)
        if step == "check":
            result = release.check(listing.connect(), a)
            click.echo(release.check_display(result))
            sys.exit(0 if result["ready"] else 1)
        if step == "build":
            repo = release.repo_of(a)
            cfg = release.load(repo)
            click.echo(f"This runs the build command from {repo / '.aso' / 'release.toml'}\nIn {repo}:\n"
                       f"  {cfg.build_command}")
            if not yes:
                click.confirm("Run this build?", abort=True)
            code = subprocess.run(["/bin/sh", "-c", cfg.build_command], cwd=repo, check=False).returncode  # noqa: S603
            sys.exit(code)
        if step == "upload":
            try:
                asc = listing.connect()
            except NotConfigured:
                asc = None
            plan = release.plan_upload(db, asc, a, ipa)
            _confirm(plan, yes, db)
            if wait and asc and plan.get("asc_app_id"):
                info = plan["info"]
                b = release.wait_for_processing(asc, plan["asc_app_id"], info["version"], info["build"],
                                                progress=lambda s: click.echo(f"  {s}"))
                click.echo(f"Build {info['build']}: {b['state']}")
                if b["state"] != "VALID":
                    sys.exit(1)
            return
        asc = listing.connect()
        if step == "attach":
            _confirm(release.plan_attach_build(db, asc, a, build_number), yes, db)
        elif step == "submit":
            _confirm(release.plan_submit(db, asc, a, force), yes, db)
