import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from asc_fake import APP, FakeASC, client
from click.testing import CliRunner

from rankwell import apps, listing, plans, review
from rankwell import db as dbmod
from rankwell.cli import main
from rankwell.db import Database
from rankwell.mcp import server, tools_listing, tools_release


@pytest.fixture
def app(db: Database) -> dict[str, Any]:
    return apps.add(db, "Snap", store_id=int(APP), bundle_id="com.example.app")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    f = FakeASC()
    f.version("v2", "1.1", "REJECTED", createdDate="2026-11-01T00:00:00Z")
    f.version("v1", "1.0", "READY_FOR_SALE", createdDate="2026-10-01T00:00:00Z")
    f.submission("s1", "COMPLETE", [("old", "ACCEPTED", "v1")], submitted="2026-10-02T09:00:00Z")
    f.submission("s2", "UNRESOLVED_ISSUES", [("it1", "REJECTED", "v2"), ("it2", "ACCEPTED", None)],
                 submitted="2026-11-02T09:00:00Z")
    c = client()
    monkeypatch.setattr(listing, "connect", lambda: c)
    with f:
        yield f


def test_status_shows_rejection(app: dict[str, Any], fake: FakeASC) -> None:
    st = review.status(listing.connect(), app)
    assert st["rejected"] and st["last_submitted"] == "2026-11-02T09:00:00Z"
    assert [s["id"] for s in st["submissions"]] == ["s2", "s1"]
    assert st["submissions"][0]["items"][0] == {"id": "it1", "state": "REJECTED", "resolved": False,
                                                "version": "1.1", "kind": "version"}
    assert "rejection_record" in st["next"] and "plan_submit" in st["next"]
    shown = review.status_display(st)
    assert "UNRESOLVED_ISSUES" in shown and "1.1: REJECTED" in shown
    assert not fake.writes()


def test_status_in_review(app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions[0]["attributes"]["appVersionState"] = "IN_REVIEW"
    fake.submissions[1]["attributes"]["state"] = "IN_REVIEW"
    st = review.status(listing.connect(), app)
    assert not st["rejected"] and "IN_REVIEW" in st["next"]


def test_record_rejection(db: Database, app: dict[str, Any]) -> None:
    r = review.record_rejection(db, app["id"], " Guideline 2.1 - Performance\n...\nguideline 5.1.1(v) ... "
                                               "Guideline 2.1 again", version="1.1")
    assert r["guideline"] == "2.1, 5.1.1" and r["version"] == "1.1" and r["text"].startswith("Guideline")
    review.record_rejection(db, app["id"], "Missing demo account", guideline="2.1")
    assert [x["guideline"] for x in review.rejections(db, app["id"])] == ["2.1", "2.1, 5.1.1"]
    with pytest.raises(Exception, match="Paste the message"):
        review.record_rejection(db, app["id"], "  ")


# ------------------------------------------------------------------ MCP tools and CLI


class Capture:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., dict[str, Any]]] = {}

    def tool(self) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
        def wrap(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
            self.tools[fn.__name__] = fn
            return fn

        return wrap


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch, db: Database) -> dict[str, Callable[..., dict[str, Any]]]:
    monkeypatch.setattr(dbmod, "_default", db)
    cap = Capture()
    tools_listing.register(cap)  # type: ignore[arg-type]
    tools_release.register(cap)  # type: ignore[arg-type]
    return cap.tools


def test_tools_registered_with_contract_names() -> None:
    names = {t.name for t in asyncio.run(server.build().list_tools())}
    assert {"listing_live", "listing_check", "listing_draft_save", "plan_listing", "plan_create_version",
            "release_check", "build_start", "build_status", "plan_upload", "plan_attach_build", "plan_review_info",
            "plan_submit", "review_status", "rejection_record"} <= names


def test_tools_without_key_say_what_to_do(tools: dict[str, Any], app: dict[str, Any],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)
    for name, args in (("listing_live", {}), ("release_check", {}), ("plan_submit", {}), ("review_status", {}),
                       ("plan_listing", {"fields": {"name": "X"}})):
        out = tools[name](app="snap", **args)
        assert "No App Store Connect key" in out["error"], name


def test_tool_draft_check_and_plan(tools: dict[str, Any], app: dict[str, Any], db: Database,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, name: None)
    apps.add_keywords(db, app["id"], ["selfie"], "us")
    saved = tools["listing_draft_save"](app="snap", fields={"name": "Snap Camera", "keywords": "camera, app"})
    assert saved["draft"] == {"name": "Snap Camera", "keywords": "camera, app"}
    assert "Spaces around commas" in saved["display"] and "selfie" in saved["display"]
    checked = tools["listing_check"](app="snap", fields={"subtitle": "Photo editor"})
    assert checked["checked"]["subtitle"] == "Photo editor" and checked["checked"]["name"] == "Snap Camera"
    assert tools["listing_check"](app="nope")["error"].startswith("No app matches")


def test_tool_plan_returns_display_and_note(tools: dict[str, Any], app: dict[str, Any], fake: FakeASC) -> None:
    fake.info("i2", "REJECTED")
    fake.info_loc("i2", "il2", "en-US", name="Snap")
    fake.ver_loc("v2", "vl2", "en-US", keywords="a")
    out = tools["plan_listing"](app="snap", fields={"name": "Snap Cam", "keywords": "camera,app"})
    assert out["plan_id"].startswith("listing-") and "ask before calling apply_plan" in out["next"]
    assert "| App info | en-US | name | Snap | Snap Cam |" in out["display"] and "'app' is wasted" in out["display"]
    st = tools["review_status"](app="snap")
    assert st["rejected"] and "Review submissions" in st["display"]
    rec = tools["rejection_record"](app="snap", text="Guideline 4.3 - Spam")
    assert rec["saved"]["guideline"] == "4.3" and "4.3" in rec["display"]


def test_ship_cli_asks_before_applying(db: Database, app: dict[str, Any], fake: FakeASC, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbmod, "Database", lambda: db)
    fake.versions[0]["attributes"]["appVersionState"] = "PREPARE_FOR_SUBMISSION"
    fake.build("b8", "1.1", "8")
    runner = CliRunner()
    res = runner.invoke(main, ["ship", "snap", "--step", "attach"], input="n\n")
    assert res.exit_code == 1 and "Apply this plan?" in res.output and "| Version 1.1 | all | build |" in res.output
    assert not fake.writes()
    res = runner.invoke(main, ["ship", "snap", "--step", "attach", "--yes"])
    assert res.exit_code == 0, res.output
    assert fake.attached["v2"] == "b8"
    assert db.value("SELECT count(*) FROM plans WHERE status = 'applied'") == 1
    res = runner.invoke(main, ["ship", "snap"])
    assert res.exit_code == 1 and "not ready" in res.output
    assert plans.get(db, db.value("SELECT id FROM plans WHERE status = 'pending'"), pending_only=False)
