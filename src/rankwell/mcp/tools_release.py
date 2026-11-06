"""Tools: the release pipeline (check, build, upload, attach, review info, submit) and App Review state."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .. import apps, listing, release, render, review
from ..errors import NotConfigured
from ..stores.asc import ASC
from .common import db, safe


def _asc_if_configured() -> ASC | None:
    try:
        return listing.connect()
    except NotConfigured:
        return None


def register(mcp: MCPServer) -> None:
    @mcp.tool()
    @safe
    def release_check(app: str) -> dict[str, Any]:
        """What Apple still needs before the version in preparation can be submitted: build attached and
        valid, screenshots for the required sizes, metadata per language, App Review contact, copyright,
        content rights, age rating, in-app purchases. Use before plan_submit."""
        result = release.check(listing.connect(), apps.get(db(), app))
        return {**result, "display": release.check_display(result)}

    @mcp.tool()
    @safe
    def build_start(app: str, confirmed: bool = False) -> dict[str, Any]:
        """Build the app in the background with the shell command from <repo>/.aso/release.toml (e.g. flutter
        build ipa). Two steps: first call it without confirmed; nothing runs, it returns the exact command, the
        folder it runs in and the log path. Show those to the user and ask whether to run it. Only after the
        user's yes call build_start(app, confirmed=True); it returns at once with the job and log path, follow
        it with build_status."""
        d = db()
        result = release.build_start(d, apps.get(d, app), confirmed=confirmed)
        if not confirmed:
            return {**result, "display": f"Not started yet. This runs in `{result['cwd']}`:\n\n```\n"
                                         f"{result['command']}\n```\n\nLog: {result['log_path']}\n\n"
                                         "Ask the user; on yes call build_start(app, confirmed=True)."}
        return {"started": True, "job": result,
                "display": f"Build started (job {result['id']}): `{result['command']}`\n\nLog: {result['log_path']}"}

    @mcp.tool()
    @safe
    def build_status(app: str, lines: int = 30) -> dict[str, Any]:
        """State of the app's latest build (running, ok, failed, stopped), its last log lines and the .ipa it
        made. When it is ok, the next step is plan_upload."""
        st = release.build_status(db(), apps.get(db(), app), lines)
        if st["job"] is None:
            return st
        tail = "\n".join(st["log_tail"])
        st["display"] = (f"**Build {st['job']['id']}: {st['status']}**" + (f" · {st['ipa']}" if st.get("ipa") else "")
                         + f"\n\n```\n{tail}\n```")
        return st

    @mcp.tool()
    @safe
    def plan_upload(app: str, ipa: str | None = None) -> dict[str, Any]:
        """Plan an upload of the newest .ipa (or this path) to App Store Connect: version and build number read
        from the .ipa, upload method from release.toml (altool with the API key, fastlane or custom). Show the
        plan to the user and ask before calling apply_plan."""
        a = apps.get(db(), app)
        return listing.present(release.plan_upload(db(), _asc_if_configured(), a, ipa))

    @mcp.tool()
    @safe
    def plan_attach_build(app: str, build: str | None = None) -> dict[str, Any]:
        """Plan attaching the newest processed build (or this build number) to the version in preparation.
        Show the plan to the user and ask before calling apply_plan."""
        a = apps.get(db(), app)
        return listing.present(release.plan_attach_build(db(), listing.connect(), a, build))

    @mcp.tool()
    @safe
    def plan_review_info(app: str, first_name: str | None = None, last_name: str | None = None,
                         phone: str | None = None, email: str | None = None, notes: str | None = None,
                         demo_account_required: bool | None = None, demo_account_name: str | None = None,
                         demo_account_password: str | None = None) -> dict[str, Any]:
        """Plan the App Review information of the version in preparation: contact name, phone, email, notes for
        the reviewer, and the demo account (the password is never shown back). Only the fields given change.
        Show the plan to the user and ask before calling apply_plan."""
        a = apps.get(db(), app)
        values = {"first_name": first_name, "last_name": last_name, "phone": phone, "email": email, "notes": notes,
                  "demo_account_required": demo_account_required, "demo_account_name": demo_account_name,
                  "demo_account_password": demo_account_password}
        return listing.present(release.plan_review_info(db(), listing.connect(), a, values))

    @mcp.tool()
    @safe
    def plan_submit(app: str, force: bool = False) -> dict[str, Any]:
        """Plan submitting the version in preparation for App Review. After a rejection it resubmits in the same
        review submission (marks the rejected item resolved). Refuses while release_check lists blockers unless
        force is true (only when the user says a blocker is wrong). Also sets the release type and phased release
        from release.toml. Show the plan to the user and ask before calling apply_plan."""
        a = apps.get(db(), app)
        return listing.present(release.plan_submit(db(), listing.connect(), a, force))

    @mcp.tool()
    @safe
    def review_status(app: str) -> dict[str, Any]:
        """Where the app stands with App Review: version states, recent review submissions and their items
        (rejected, resolved), last submission date, saved rejection messages, and the next step."""
        a = apps.get(db(), app)
        st = review.status(listing.connect(), a)
        st["rejections"] = review.rejections(db(), a["id"], 5)
        st["display"] = review.status_display(st)
        return st

    @mcp.tool()
    @safe
    def rejection_record(app: str, text: str, guideline: str | None = None,
                         version: str | None = None) -> dict[str, Any]:
        """Save the rejection message the user pasted from App Store Connect (App Review / Resolution Center).
        Guideline numbers are read from the text when not given. Returns earlier rejections too, so repeated
        problems show up."""
        a = apps.get(db(), app)
        row = review.record_rejection(db(), a["id"], text, guideline, version)
        past = review.rejections(db(), a["id"])
        shown = [{"received": r["received_at"][:10], "version": r["version"], "guideline": r["guideline"],
                  "resolved": (r["resolved_at"] or "")[:10]} for r in past]
        return {"saved": row, "rejections": past, "display": render.table(shown)}
