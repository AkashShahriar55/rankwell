"""`rankwell screenshots`: check a folder, read the live sets, and upload (an upload can outlast a tool call)."""

from __future__ import annotations

import sys

import click

from .. import apps, listing, plans, screenshots
from .. import db as dbmod


def register(main: click.Group) -> None:
    @main.group(name="screenshots")
    def group() -> None:
        """App Store screenshots: scan a folder, show the live sets, upload."""

    @group.command()
    @click.argument("folder")
    @click.option("--locale", help="Locale for a flat folder (e.g. en-US).")
    def scan(folder: str, locale: str | None) -> None:
        """Check a folder: sizes, display types, alpha, count per set."""
        result = screenshots.scan(folder, locale)
        click.echo(screenshots.scan_display(result))
        sys.exit(0 if result["ok"] else 1)

    @group.command()
    @click.argument("app")
    @click.option("--locale", default="en-US", show_default=True)
    def live(app: str, locale: str) -> None:
        """Show the screenshot sets in App Store Connect for one locale."""
        a = apps.get(dbmod.Database(), app)
        asc = listing.connect()
        click.echo(screenshots.live_display(screenshots.live(asc, listing.asc_app_id(asc, a), locale)))

    @group.command()
    @click.argument("app")
    @click.option("--folder", required=True, help="Folder with the screenshots.")
    @click.option("--locale", default="en-US", show_default=True)
    @click.option("--mode", type=click.Choice(["replace", "append"]), default="replace", show_default=True,
                  help="replace: the folder becomes the whole set (old ones deleted after every upload finished).")
    @click.option("--yes", is_flag=True, help="Do not ask before applying.")
    def upload(app: str, folder: str, locale: str, mode: str, yes: bool) -> None:
        """Plan an upload to the version in preparation, show it, and apply it after a yes."""
        db = dbmod.Database()
        a = apps.get(db, app)
        plan = screenshots.plan_screenshots(db, listing.connect(), a, locale, folder, mode)
        click.echo(screenshots.present(plan)["display"])
        if not yes:
            click.confirm("\nApply this plan?", abort=True)
        screenshots.on_progress(click.echo)
        try:
            result = plans.apply(db, plan["id"])
        finally:
            screenshots.on_progress(None)
        click.echo(result["display"])
        sys.exit(0 if result["ok"] else 1)
