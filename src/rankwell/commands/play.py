"""`rankwell play`: the Google Play key, tracks, and bundle upload from a terminal (large .aab files, CI)."""

from __future__ import annotations

from pathlib import Path

import click

from .. import apps, listing, plans, play, secrets
from .. import db as dbmod


def _notes(values: tuple[str, ...]) -> dict[str, str]:
    out = {}
    for v in values:
        lang, sep, text = v.partition("=")
        if not sep or not lang.strip():
            raise click.UsageError(f"--notes takes LANGUAGE=TEXT (e.g. en-US=\"Bug fixes\"), not {v!r}.")
        out[lang.strip()] = text
    return out


def register(main: click.Group) -> None:
    @main.group("play")
    def play_group() -> None:
        """Google Play: service account key, tracks, bundle upload."""

    @play_group.command("init")
    @click.option("--key", "key_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                  help="Service account JSON key with access to your app in Play Console.")
    @click.option("--remove", is_flag=True, help="Delete the stored key from the Keychain.")
    def init(key_file: Path | None, remove: bool) -> None:
        """Store (or remove) the Google Play service account key in the Keychain."""
        if remove:
            secrets.delete_play_key()
            click.echo("Google Play key removed from the Keychain.")
            return
        if not key_file:
            raise click.UsageError("Pass --key <service-account.json> (or --remove).")
        key = secrets.store_play_key(key_file)
        click.echo(f"Google Play key for {key.client_email} saved in the Keychain (service 'rankwell').")
        click.echo("You can now delete the JSON file or keep it somewhere safe; Rankwell no longer needs it.")

    @play_group.command("tracks")
    @click.argument("app")
    def tracks(app: str) -> None:
        """Show the releases on every track."""
        a = apps.get(dbmod.Database(), app)
        click.echo(play.tracks_display(play.tracks(play.connect(), play.package_of(a))))

    @play_group.command("upload")
    @click.argument("app")
    @click.option("--aab", help="Upload this .aab instead of the newest one in build/app/outputs/bundle/release.")
    @click.option("--track", type=click.Choice(play.TRACKS), default="internal", show_default=True)
    @click.option("--status", type=click.Choice(play.STATUSES), help="Release status (default completed).")
    @click.option("--rollout", type=float, help="Staged rollout fraction, e.g. 0.1 for 10% (status inProgress).")
    @click.option("--notes", multiple=True, help="Release notes as LANGUAGE=TEXT; repeat per language.")
    @click.option("--name", help="Release name (default: from the bundle's version name).")
    @click.option("--yes", is_flag=True, help="Do not ask before applying (CI).")
    def upload(app: str, aab: str | None, track: str, status: str | None, rollout: float | None,
               notes: tuple[str, ...], name: str | None, yes: bool) -> None:
        """Upload an .aab to Google Play and make it the release on a track, after a confirm."""
        db = dbmod.Database()
        a = apps.get(db, app)
        plan = play.plan_upload(db, play.connect(), a, aab, track, _notes(notes), rollout, status, name)
        click.echo(listing.present(plan)["display"])
        if not yes:
            click.confirm("\nUpload and commit this release?", abort=True)
        click.echo("Uploading…")
        result = plans.apply(db, plan["id"])
        click.echo(f"Applied {plan['id']}: version code {result['version_code']} on {result['track']} "
                   f"({result['status']}).")
