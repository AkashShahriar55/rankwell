"""The `rankwell` command. Claude uses the MCP server; people and launchd use this."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__, apps, config, paths, secrets, setup
from . import db as dbmod
from .errors import RankwellError


@click.group()
@click.version_option(__version__, prog_name="rankwell")
def main() -> None:
    """Rankwell: App Store Optimization and release assistant for Claude Code."""


@main.command()
@click.option("--asc-key", "key_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="App Store Connect API key file (AuthKey_XXXXXXXXXX.p8).")
@click.option("--issuer-id", help="Issuer id from App Store Connect → Users and Access → Integrations.")
@click.option("--key-id", help="Key id (read from the file name when left out).")
def init(key_file: Path | None, issuer_id: str | None, key_id: str | None) -> None:
    """Create ~/.rankwell, the database and settings; store an App Store Connect key in the system keyring."""
    cfg = config.ensure_file()
    db = dbmod.Database()
    click.echo(f"Settings:  {cfg}")
    click.echo(f"Database:  {db.path}")
    if key_file:
        if not issuer_id:
            raise click.UsageError("--issuer-id is needed with --asc-key.")
        kid = key_id or setup.key_id_from_name(key_file.name)
        if not kid:
            raise click.UsageError("Cannot read the key id from the file name; pass --key-id.")
        secrets.store_asc_key(issuer_id, kid, key_file.read_text())
        click.echo(f"App Store Connect key {kid} saved in the system keyring (service 'rankwell').")
        click.echo("You can now delete the .p8 file or keep it somewhere safe; Rankwell no longer needs it.")


@main.command()
def doctor() -> None:
    """Check the setup and say what is missing."""
    rows = setup.checks()
    click.echo(setup.checks_display(rows))
    sys.exit(0 if all(r["ok"] or r["optional"] for r in rows) else 1)


@main.command("install-cli")
def install_cli() -> None:
    """Link ~/.local/bin/rankwell so `rankwell` works in any terminal, across plugin updates."""
    r = setup.install_cli()
    click.echo(f"Linked {r['link']}" + (" (already there)" if r.get("already") else ""))
    if r.get("note"):
        click.echo(r["note"])


@main.command("mcp")
def mcp_cmd() -> None:
    """Run the MCP server on stdio (Claude Code starts this)."""
    from .mcp.server import run

    run()


@main.command()
@click.option("--to", "dest", type=click.Path(file_okay=False, path_type=Path), help="Folder for the copy.")
def backup(dest: Path | None) -> None:
    """Write a consistent copy of the database."""
    from .util import today

    folder = dest or paths.backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = dbmod.Database().backup(folder / f"rankwell-{today()}.db")
    click.echo(f"Backup written: {out}")


@main.command("apps")
def apps_cmd() -> None:
    """List tracked apps."""
    for a in apps.list_apps(dbmod.Database()):
        click.echo(f"{a['id']:>3}  {a['name']}  ({a['stage']}, {', '.join(a['countries'])})")


def run() -> None:
    try:
        main(standalone_mode=True)
    except RankwellError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _load_commands() -> None:
    from .commands import register_all

    register_all(main)


_load_commands()
