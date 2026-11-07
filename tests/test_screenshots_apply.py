import asyncio
from pathlib import Path
from typing import Any

import pytest
from asc_fake import APP, client
from asc_media_fake import MediaASC
from shot_files import png

from rankwell import apps, listing, plans, screenshots
from rankwell.db import Database
from rankwell.errors import PlanError, RankwellError


@pytest.fixture
def app(db: Database) -> dict[str, Any]:
    return apps.add(db, "Snap", store_id=int(APP), bundle_id="com.example.app")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    f = MediaASC(part_size=64)
    f.version("v2", "1.1", "PREPARE_FOR_SUBMISSION")
    f.version("v1", "1.0", "READY_FOR_SALE")
    f.ver_loc("v2", "vl2", "en-US")
    f.ver_loc("v1", "vl1", "en-US")
    f.shot_set("vl2", "set67", "APP_IPHONE_67", [("o1", "old-1.png"), ("o2", "old-2.png")])
    f.shot_set("vl2", "setpad", "APP_IPAD_PRO_3GEN_129", [("p1", "pad.png")])
    f.shot_set("vl1", "live67", "APP_IPHONE_67", [("l1", "live.png")])
    c = client()
    monkeypatch.setattr(listing, "connect", lambda: c)
    monkeypatch.setattr(screenshots, "sleep", lambda s: None)
    with f:
        yield f


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "shots"
    png(d / "en-US" / "02-share.png", 1320, 2868, size=150)
    png(d / "en-US" / "01-edit.png", 1320, 2868, size=150)
    png(d / "en-US" / "03-export.png", 1290, 2796, size=150)
    return d


def _new_names(fake: MediaASC) -> list[str]:
    return fake.names("set67")


def test_live_lists_sets_of_version_in_preparation(app: dict[str, Any], fake: MediaASC) -> None:
    cur = screenshots.live(client(), int(APP), "en-US")
    assert cur["editable"] and cur["version"]["version"] == "1.1"
    by = {s["display"]: s for s in cur["sets"]}
    assert by["APP_IPHONE_67"]["count"] == 2
    assert [x["file"] for x in by["APP_IPHONE_67"]["screenshots"]] == ["old-1.png", "old-2.png"]
    assert by["APP_IPHONE_67"]["screenshots"][0]["state"] == "COMPLETE"
    assert "old-1.png" in screenshots.live_display(cur)
    assert "has no ja localization" in screenshots.live_display(screenshots.live(client(), int(APP), "ja"))


def test_replace_uploads_in_parts_then_deletes_old_and_orders(db: Database, app: dict[str, Any], fake: MediaASC,
                                                              folder: Path) -> None:
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    shown = screenshots.present(plan)
    assert [c["field"] for c in plan["changes"]] == ['APP_IPHONE_67 (iPhone 6.9"/6.7" (Dynamic Island, large))']
    assert plan["changes"][0]["current"] == "2: old-1.png, old-2.png"
    assert "replace with 3: 01-edit.png, 02-share.png, 03-export.png" in plan["changes"][0]["new"]
    notes = " ".join(plan["notes"])
    assert "deletes the current screenshots of APP_IPHONE_67 (2)" in notes and "live version is not touched" in notes
    assert "APP_IPAD_PRO_3GEN_129" in notes  # untouched set is named
    assert "rankwell screenshots upload snap" in notes
    assert "01-edit.png" in shown["display"]
    assert not fake.writes()

    result = plans.apply(db, plan["id"])
    assert result["ok"], result
    assert _new_names(fake) == ["01-edit.png", "02-share.png", "03-export.png"]
    assert [d["file"] for d in result["deleted"]] == ["old-1.png", "old-2.png"]
    assert fake.names("setpad") == ["pad.png"] and fake.names("live67") == ["live.png"]

    # 150 bytes of body + headers → several 64-byte parts per file, each with Apple's headers.
    size = (folder / "en-US" / "01-edit.png").stat().st_size
    first = next(s["id"] for s in fake.set_shots["set67"] if s["attributes"]["fileName"] == "01-edit.png")
    offsets = sorted(off for sid, off, _ in fake.puts if sid == first)
    assert offsets == list(range(0, size, 64)) and len(offsets) > 1
    assert all(h == {"Content-Type": "image/png"} for _, _, h in fake.puts)
    reserve = next(c for c in fake.calls if c[:2] == ("POST", "/v1/appScreenshots"))
    assert reserve[2]["data"]["attributes"] == {"fileName": "01-edit.png", "fileSize": size}
    assert reserve[2]["data"]["relationships"]["appScreenshotSet"]["data"] == {"type": "appScreenshotSets",
                                                                               "id": "set67"}

    # Deletes come only after every new screenshot was COMPLETE.
    methods = [(m, p) for m, p, _ in fake.calls]
    first_delete = next(i for i, (m, _) in enumerate(methods) if m == "DELETE")
    last_poll = max(i for i, (m, p) in enumerate(methods) if m == "GET" and p.startswith("/v1/appScreenshots/"))
    last_commit = max(i for i, (m, p) in enumerate(methods) if m == "PATCH" and p.startswith("/v1/appScreenshots/"))
    assert last_commit < last_poll < first_delete
    assert {p for m, p in methods if m == "DELETE"} == {"/v1/appScreenshots/o1", "/v1/appScreenshots/o2"}

    log = db.all("SELECT * FROM audit_log WHERE plan_id = ? ORDER BY id", (plan["id"],))
    assert [r["status"] for r in log] == ["ok"] * len(log)
    assert sum("uploaded" in (r["new"] or "") for r in log) == 3
    assert sum(r["old"] in ("old-1.png", "old-2.png") and r["new"] is None for r in log) == 2


def test_append_keeps_old_and_puts_new_after(db: Database, app: dict[str, Any], fake: MediaASC,
                                             folder: Path) -> None:
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "append")
    assert "add 3: " in plan["changes"][0]["new"] and "→ 5 total" in plan["changes"][0]["new"]
    assert not any("deletes" in n for n in plan["notes"])
    result = plans.apply(db, plan["id"])
    assert result["ok"] and not result["deleted"]
    assert _new_names(fake) == ["old-1.png", "old-2.png", "01-edit.png", "02-share.png", "03-export.png"]
    assert not [c for c in fake.calls if c[0] == "DELETE"]


def test_append_over_ten_refused(db: Database, app: dict[str, Any], fake: MediaASC, tmp_path: Path) -> None:
    for i in range(9):
        png(tmp_path / f"{i}.png", 1320, 2868)
    with pytest.raises(RankwellError, match="goes over 10"):
        screenshots.plan_screenshots(db, client(), app, "en-US", tmp_path, "append")


def test_new_set_is_created(db: Database, app: dict[str, Any], fake: MediaASC, tmp_path: Path) -> None:
    png(tmp_path / "en-US" / "APP_IPHONE_65" / "1.png", 1242, 2688)
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", tmp_path)
    assert "APP_IPHONE_65: a new screenshot set will be added." in plan["notes"]
    assert plan["changes"][0]["current"] == "(none)"
    result = plans.apply(db, plan["id"])
    assert result["ok"]
    new_set = next(s for s in fake.sets["vl2"] if s["attributes"]["screenshotDisplayType"] == "APP_IPHONE_65")
    assert fake.names(new_set["id"]) == ["1.png"]
    post = next(c for c in fake.calls if c[:2] == ("POST", "/v1/appScreenshotSets"))
    assert post[2]["data"]["relationships"]["appStoreVersionLocalization"]["data"]["id"] == "vl2"


def _full_set(fake: MediaASC, tmp_path: Path, n_old: int, n_new: int) -> Path:
    fake.set_shots["set67"] = [fake._shot(f"o{i}", f"old-{i}.png", f"c{i}", "COMPLETE") for i in range(n_old)]
    for i in range(n_new):
        png(tmp_path / f"{i + 1:02}-new.png", 1320, 2868)
    return tmp_path


def test_replace_full_set_uploads_what_fits_first(db: Database, app: dict[str, Any], fake: MediaASC,
                                                  tmp_path: Path) -> None:
    folder = _full_set(fake, tmp_path, 8, 4)
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    assert any("2 new screenshot(s) upload first, then the old ones are deleted" in n for n in plan["notes"])
    result = plans.apply(db, plan["id"])
    assert result["ok"], result
    assert fake.names("set67") == ["01-new.png", "02-new.png", "03-new.png", "04-new.png"]
    methods = [(m, p) for m, p, _ in fake.calls]
    reserves = [i for i, (m, p) in enumerate(methods) if (m, p) == ("POST", "/v1/appScreenshots")]
    deletes = [i for i, (m, _) in enumerate(methods) if m == "DELETE"]
    assert reserves[1] < deletes[0] and deletes[-1] < reserves[2]


def test_replace_full_set_phase_one_failure_keeps_old(db: Database, app: dict[str, Any], fake: MediaASC,
                                                      tmp_path: Path) -> None:
    folder = _full_set(fake, tmp_path, 10, 3)
    fake.fail_files.add("02-new.png")
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    assert any("is full (10): its last screenshot (old-9.png) is deleted first" in n for n in plan["notes"])
    result = plans.apply(db, plan["id"])  # 01 makes it, then 02 fails in the second round
    assert result["ok"] is False and [f["file"] for f in result["failed"]] == ["02-new.png"]
    fake.set_shots["set67"] = [fake._shot(f"o{i}", f"old-{i}.png", f"c{i}", "COMPLETE") for i in range(10)]
    fake.fail_files = {"01-new.png"}
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    with pytest.raises(RankwellError, match="Only 1 old screenshot"):
        plans.apply(db, plan["id"])
    left = fake.names("set67")
    assert left[:9] == [f"old-{i}.png" for i in range(9)] and "old-9.png" not in left


def test_replace_full_set_late_failure_is_reported(db: Database, app: dict[str, Any], fake: MediaASC,
                                                   tmp_path: Path) -> None:
    folder = _full_set(fake, tmp_path, 9, 3)
    fake.fail_files.add("03-new.png")
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    result = plans.apply(db, plan["id"])
    assert result["ok"] is False
    assert len(result["deleted"]) == 9
    assert [f["file"] for f in result["failed"]] == ["03-new.png"]
    assert "failed after the old ones were deleted" in result["message"]
    assert fake.names("set67")[:2] == ["01-new.png", "02-new.png"]


def test_partial_failure_keeps_old_and_reports(db: Database, app: dict[str, Any], fake: MediaASC,
                                               folder: Path) -> None:
    fake.fail_files.add("02-share.png")
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    result = plans.apply(db, plan["id"])
    assert result["ok"] is False
    assert [u["file"] for u in result["uploaded"]] == ["01-edit.png", "03-export.png"]
    assert result["failed"][0]["file"] == "02-share.png" and "dimensions are wrong" in result["failed"][0]["error"]
    assert "Nothing was deleted" in result["message"] and not result["deleted"]
    assert not [c for c in fake.calls if c[0] == "DELETE"]
    assert not [c for c in fake.calls if "relationships/appScreenshots" in c[1]]
    assert _new_names(fake)[:2] == ["old-1.png", "old-2.png"]  # old ones still there, first
    errors = db.all("SELECT * FROM audit_log WHERE plan_id = ? AND status = 'error'", (plan["id"],))
    assert len(errors) == 1 and "FAILED" in errors[0]["error"]
    assert "02-share.png" in result["display"]


def test_put_failure_and_processing_timeout_keep_old(db: Database, app: dict[str, Any], fake: MediaASC,
                                                     folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.fail_put.add("01-edit.png")
    fake.stuck_files.add("03-export.png")
    monkeypatch.setattr(screenshots, "WAIT_TIMEOUT", 0.0)
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    result = plans.apply(db, plan["id"])
    assert result["ok"] is False
    failed = {f["file"]: f["error"] for f in result["failed"]}
    assert "403" in failed["01-edit.png"]
    assert "UPLOAD_COMPLETE" in failed["03-export.png"]
    assert [u["file"] for u in result["uploaded"]] == ["02-share.png"]
    assert not [c for c in fake.calls if c[0] == "DELETE"]


def test_nothing_uploaded_raises_and_keeps_old(db: Database, app: dict[str, Any], fake: MediaASC,
                                               folder: Path) -> None:
    fake.fail_files.update({"01-edit.png", "02-share.png", "03-export.png"})
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder, "replace")
    with pytest.raises(RankwellError, match="0 of 3"):
        plans.apply(db, plan["id"])
    assert not [c for c in fake.calls if c[0] == "DELETE"]
    assert db.one("SELECT status FROM plans WHERE id = ?", (plan["id"],))["status"] == "failed"


def test_refuses_without_editable_version(db: Database, app: dict[str, Any], fake: MediaASC, folder: Path) -> None:
    fake.versions = [v for v in fake.versions if v["id"] != "v2"]
    with pytest.raises(RankwellError, match="version in preparation"):
        screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    fake.version("v3", "1.1", "WAITING_FOR_REVIEW")
    with pytest.raises(RankwellError, match="WAITING_FOR_REVIEW"):
        screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    assert not fake.writes()


def test_refuses_missing_localization_and_bad_files(db: Database, app: dict[str, Any], fake: MediaASC,
                                                    tmp_path: Path) -> None:
    png(tmp_path / "1.png", 1320, 2868)
    with pytest.raises(RankwellError, match="no de-DE localization"):
        screenshots.plan_screenshots(db, client(), app, "de-DE", tmp_path)
    png(tmp_path / "2.png", 1320, 2868, color=6)
    with pytest.raises(RankwellError, match="alpha"):
        screenshots.plan_screenshots(db, client(), app, "en-US", tmp_path)
    with pytest.raises(RankwellError, match="mode"):
        screenshots.plan_screenshots(db, client(), app, "en-US", tmp_path, "merge")


def test_stale_state_refused(db: Database, app: dict[str, Any], fake: MediaASC, folder: Path) -> None:
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    fake.set_shots["set67"].append(fake._shot("o3", "added-meanwhile.png", "abc", "COMPLETE"))
    with pytest.raises(PlanError, match="changed since this plan"):
        plans.apply(db, plan["id"])
    assert not fake.writes()


def test_changed_file_refused(db: Database, app: dict[str, Any], fake: MediaASC, folder: Path) -> None:
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    png(folder / "en-US" / "01-edit.png", 1320, 2868, size=300)
    with pytest.raises(PlanError, match=r"01-edit\.png changed"):
        plans.apply(db, plan["id"])
    assert not fake.writes()


def test_same_files_already_there(db: Database, app: dict[str, Any], fake: MediaASC, folder: Path) -> None:
    plans.apply(db, screenshots.plan_screenshots(db, client(), app, "en-US", folder)["id"])
    with pytest.raises(RankwellError, match="already has these screenshots"):
        screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    # Renaming changes the order: that is a reorder by replace.
    (folder / "en-US" / "01-edit.png").rename(folder / "en-US" / "04-edit.png")
    plan = screenshots.plan_screenshots(db, client(), app, "en-US", folder)
    plans.apply(db, plan["id"])
    assert fake.names("set67") == ["02-share.png", "03-export.png", "04-edit.png"]


def test_mcp_tools_registered() -> None:
    from rankwell.mcp import server

    s = server.build()
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert {"screenshots_live", "screenshots_scan", "plan_screenshots"} <= names


def test_mcp_plan_tool(app: dict[str, Any], fake: MediaASC, folder: Path, monkeypatch: pytest.MonkeyPatch,
                       db: Database) -> None:
    from mcp.server.mcpserver import MCPServer

    from rankwell.mcp import tools_screenshots

    monkeypatch.setattr(tools_screenshots, "db", lambda: db)
    s = MCPServer("t")
    tools_screenshots.register(s)

    def call(name: str, **args: Any) -> str:
        return str(asyncio.run(s.call_tool(name, args)))

    out = call("plan_screenshots", app="snap", folder=str(folder))
    assert "screenshots-" in out and "01-edit.png" in out and "apply_plan" in out
    assert "APP_IPHONE_67" in call("screenshots_scan", folder=str(folder))
    assert "old-1.png" in call("screenshots_live", app="snap")
    assert "No folder" in call("screenshots_scan", folder=str(folder / "missing"))
    assert not fake.writes()


def test_cli_upload(app: dict[str, Any], fake: MediaASC, folder: Path, monkeypatch: pytest.MonkeyPatch,
                    db: Database) -> None:
    from click.testing import CliRunner

    from rankwell import db as dbmod
    from rankwell.cli import main

    monkeypatch.setattr(dbmod, "Database", lambda *a, **k: db)
    no = CliRunner().invoke(main, ["screenshots", "upload", "snap", "--folder", str(folder)], input="n\n")
    assert no.exit_code == 1 and "01-edit.png" in no.output and not fake.writes()
    yes = CliRunner().invoke(main, ["screenshots", "upload", "snap", "--folder", str(folder), "--yes"])
    assert yes.exit_code == 0, yes.output
    assert "uploading 01-edit.png" in yes.output and "3 screenshot(s) uploaded" in yes.output
    assert fake.names("set67") == ["01-edit.png", "02-share.png", "03-export.png"]
    scan = CliRunner().invoke(main, ["screenshots", "scan", str(folder)])
    assert scan.exit_code == 0 and "APP_IPHONE_67" in scan.output
