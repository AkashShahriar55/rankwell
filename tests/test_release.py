import json
import os
import plistlib
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest
from asc_fake import APP, FakeASC, client, key

from rankwell import apps, listing, plans, release, review
from rankwell.db import Database
from rankwell.errors import PlanError, RankwellError

# ------------------------------------------------------------------ release.toml


def test_defaults_per_kind() -> None:
    f = release.parse({})
    assert (f.build_kind, f.build_command, f.ipa, f.upload_kind, f.release_type, f.phased) == (
        "flutter", "flutter build ipa --release", "build/ios/ipa/*.ipa", "altool", "after-approval", False)
    f = release.parse({"build": {"kind": "flutter", "export_options": "ios/Export Options.plist"}})
    assert f.build_command == "flutter build ipa --release --export-options-plist='ios/Export Options.plist'"
    x = release.parse({"build": {"kind": "xcode", "scheme": "App", "workspace": "App.xcworkspace",
                                 "export_options": "ExportOptions.plist"}})
    assert x.build_command.startswith("xcodebuild archive -workspace App.xcworkspace -scheme App -configuration "
                                      "Release")
    assert "&& xcodebuild -exportArchive -archivePath build/rankwell/App.xcarchive" in x.build_command
    assert "-exportOptionsPlist ExportOptions.plist" in x.build_command and x.ipa == "build/rankwell/export/*.ipa"
    fl = release.parse({"build": {"kind": "fastlane", "lane": "build_release"},
                        "upload": {"kind": "fastlane", "lane": "upload_testflight"},
                        "release": {"type": "manual", "phased": True}})
    assert (fl.build_command, fl.upload_command, fl.release_type, fl.phased) == (
        "fastlane build_release", "fastlane upload_testflight", "manual", True)
    c = release.parse({"build": {"kind": "custom", "command": "make ipa", "ipa": "out/*.ipa"},
                       "upload": {"kind": "custom", "command": "upload.sh {ipa}"}})
    assert (c.build_command, c.ipa, c.upload_command) == ("make ipa", "out/*.ipa", "upload.sh {ipa}")
    assert release.parse({"build": {"command": "flutter build ipa --flavor prod"}}).build_command == \
        "flutter build ipa --flavor prod"


@pytest.mark.parametrize(("data", "message"), [
    ({"build": {"kind": "gradle"}}, "kind must be one of flutter, xcode"),
    ({"build": {"kind": "xcode", "scheme": "A"}}, "needs export_options"),
    ({"build": {"kind": "xcode", "export_options": "E.plist"}}, "needs scheme"),
    ({"build": {"kind": "fastlane"}}, "needs lane"),
    ({"build": {"kind": "custom"}}, "needs command"),
    ({"build": {"comand": "x"}}, "unknown key 'comand' in \\[build\\]"),
    ({"deploy": {}}, "unknown section \\[deploy\\]"),
    ({"upload": {"kind": "transporter"}}, "\\[upload\\] kind must be one of altool"),
    ({"upload": {"kind": "custom"}}, "needs command"),
    ({"upload": {"kind": "altool", "command": "x"}}, "only for kind"),
    ({"release": {"type": "now"}}, "after-approval or manual"),
    ({"release": {"phased": "yes"}}, "phased must be true or false"),
    ({"build": {"kind": 3}}, "kind must be a string"),
])
def test_invalid_config(data: dict[str, Any], message: str) -> None:
    with pytest.raises(RankwellError, match=message):
        release.parse(data)


def test_load(tmp_path: Path) -> None:
    with pytest.raises(RankwellError, match=r"No .*release\.toml"):
        release.load(tmp_path)
    (tmp_path / "pubspec.yaml").write_text("name: x\n")
    assert release.load(tmp_path).build_kind == "flutter"
    (tmp_path / ".aso").mkdir()
    (tmp_path / ".aso" / "release.toml").write_text("[build\nkind=")
    with pytest.raises(RankwellError, match="not valid TOML"):
        release.load(tmp_path)
    (tmp_path / ".aso" / "release.toml").write_text('[build]\nkind = "custom"\ncommand = "make"\n')
    assert release.load(tmp_path).build_command == "make"


# ------------------------------------------------------------------ ipa, altool


def make_ipa(path: Path, version: str = "1.1", build: str = "7", bundle: str = "com.example.app") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"CFBundleShortVersionString": version, "CFBundleVersion": build, "CFBundleIdentifier": bundle}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Payload/Runner.app/Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
        z.writestr("Payload/Runner.app/Frameworks/X.framework/Info.plist", plistlib.dumps({"CFBundleVersion": "0"}))
    return path


def test_ipa_info(tmp_path: Path) -> None:
    info = release.ipa_info(make_ipa(tmp_path / "a.ipa"))
    assert (info["version"], info["build"], info["bundle_id"]) == ("1.1", "7", "com.example.app")
    assert info["size"] > 0
    (tmp_path / "bad.ipa").write_text("nope")
    with pytest.raises(RankwellError, match=r"not a readable \.ipa"):
        release.ipa_info(tmp_path / "bad.ipa")
    with zipfile.ZipFile(tmp_path / "empty.ipa", "w") as z:
        z.writestr("x.txt", "x")
    with pytest.raises(RankwellError, match="no Payload"):
        release.ipa_info(tmp_path / "empty.ipa")


def test_find_newest_ipa(tmp_path: Path) -> None:
    cfg = release.parse({})
    with pytest.raises(RankwellError, match="Build first"):
        release.find_ipa(tmp_path, cfg)
    old = make_ipa(tmp_path / "build/ios/ipa/old.ipa")
    new = make_ipa(tmp_path / "build/ios/ipa/new.ipa")
    os.utime(old, (1, 1))
    assert release.find_ipa(tmp_path, cfg) == new
    assert release.find_ipa(tmp_path, cfg, "build/ios/ipa/old.ipa") == old


def test_find_ipa_absolute_glob(tmp_path: Path) -> None:
    out = tmp_path / "elsewhere [x]"
    ipa = make_ipa(out / "dist" / "App.ipa")
    cfg = release.parse({"build": {"kind": "custom", "command": "make", "ipa": f"{tmp_path}/elsewhere*/dist/*.ipa"}})
    assert release.find_ipa(tmp_path / "repo", cfg) == ipa
    repo = tmp_path / "re[po]"  # glob characters in the repo path are taken literally
    nested = make_ipa(repo / "out" / "a" / "b" / "N.ipa")
    cfg = release.parse({"build": {"kind": "custom", "command": "make", "ipa": "out/**/*.ipa"}})
    assert release.find_ipa(repo, cfg) == nested


def test_altool_command_and_temp_key_cleanup(tmp_path: Path) -> None:
    k = key()
    ipa = make_ipa(tmp_path / "a.ipa")
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str]:
        f = cwd / "private_keys" / f"AuthKey_{k.key_id}.p8"
        seen.update(cmd=cmd, cwd=cwd, key=f.read_text(), mode=stat.S_IMODE(f.stat().st_mode),
                    dir_mode=stat.S_IMODE(cwd.stat().st_mode))
        return 0, "No errors uploading.\n" + k.private_key.splitlines()[1]

    code, out = release.upload_altool(ipa, k, fake_run)
    assert code == 0
    assert seen["cmd"] == ["xcrun", "altool", "--upload-package", str(ipa.resolve()), "--api-key", "KEY123",
                           "--api-issuer", "issuer-1", "--p8-file-path",
                           str(seen["cwd"] / "private_keys" / "AuthKey_KEY123.p8")]
    assert not any("PRIVATE KEY" in a for a in seen["cmd"])
    assert seen["key"] == k.private_key and seen["mode"] == 0o600 and seen["dir_mode"] == 0o700
    assert not seen["cwd"].exists()
    assert k.private_key.splitlines()[1] not in out and "<hidden>" in out


def test_temp_key_removed_when_runner_fails(tmp_path: Path) -> None:
    seen: list[Path] = []

    def boom(cmd: list[str], cwd: Path) -> tuple[int, str]:
        seen.append(cwd)
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        release.upload_altool(make_ipa(tmp_path / "a.ipa"), key(), boom)
    assert seen and not seen[0].exists()


# ------------------------------------------------------------------ App Store Connect fixtures


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".aso").mkdir(parents=True)
    (r / ".aso" / "release.toml").write_text('[build]\nkind = "flutter"\n\n[release]\ntype = "manual"\n')
    return r


@pytest.fixture
def app(db: Database, repo: Path) -> dict[str, Any]:
    return apps.add(db, "Snap", store_id=int(APP), bundle_id="com.example.app", repo_path=str(repo))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    f = FakeASC()
    f.version("v2", "1.1", "PREPARE_FOR_SUBMISSION", releaseType="AFTER_APPROVAL", copyright="2026 Me")
    f.version("v1", "1.0", "READY_FOR_SALE")
    f.info("i2", "PREPARE_FOR_SUBMISSION")
    f.info_loc("i2", "il2", "en-US", name="Snap", privacyPolicyUrl="https://x/p")
    f.ver_loc("v2", "vl2", "en-US", description="D", keywords="k", supportUrl="https://x/s", whatsNew="Fixes")
    f.shots["vl2"] = {"APP_IPHONE_67": 3}
    f.age["i2"] = {"type": "ageRatingDeclarations", "id": "age", "attributes": {"violenceCartoonOrFantasy": "NONE"}}
    f.build("b6", "1.1", "6", uploaded="2026-11-01T10:00:00Z")
    f.build("b7", "1.1", "7", uploaded="2026-11-02T10:00:00Z")
    f.attached["v2"] = "b7"
    f.review["v2"] = {"type": "appStoreReviewDetails", "id": "rd1", "attributes": {
        "contactFirstName": "A", "contactLastName": "B", "contactPhone": "+1 555", "contactEmail": "a@b.c"}}
    c = client()
    monkeypatch.setattr(listing, "connect", lambda: c)
    with f:
        yield f


# ------------------------------------------------------------------ check


def test_check_ready(app: dict[str, Any], fake: FakeASC) -> None:
    r = release.check(listing.connect(), app)
    assert r["version"] == "1.1" and r["ready"], r["items"]
    assert "✓ Build 7 attached" in release.check_display(r)


def test_check_lists_blockers(app: dict[str, Any], fake: FakeASC) -> None:
    fake.shots["vl2"] = {"APP_IPHONE_61": 2, "APP_IPAD_PRO_3GEN_11": 1}
    del fake.attached["v2"]
    fake.review["v2"]["attributes"]["contactPhone"] = None
    fake.app_attrs["contentRightsDeclaration"] = None
    fake.age.clear()
    fake.ver_locs["v2"][0]["attributes"]["whatsNew"] = None
    fake.iaps = [{"type": "inAppPurchases", "id": "p1",
                  "attributes": {"productId": "pro", "state": "MISSING_METADATA"}}]
    r = release.check(listing.connect(), app)
    bad = " | ".join(i["item"] for i in r["items"] if i["ok"] is False)
    assert not r["ready"]
    assert "What's New" in bad and 'iPhone 6.9"/6.5" screenshots' in bad and 'iPad 13" screenshots' in bad
    assert "No build attached" in bad and "App Review contact incomplete" in bad
    assert "Content rights not declared" in bad and "Age rating not set" in bad and "pro: MISSING_METADATA" in bad


def test_check_without_version(app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions[0]["attributes"]["appVersionState"] = "WAITING_FOR_REVIEW"
    r = release.check(listing.connect(), app)
    assert r["items"][0]["ok"] is None and "WAITING_FOR_REVIEW" in r["items"][0]["item"]


# ------------------------------------------------------------------ build


def test_build_start_and_status(db: Database, app: dict[str, Any], repo: Path) -> None:
    started: dict[str, Any] = {}

    def spawn(script: str, cwd: Path, log: Path) -> int:
        started.update(script=script, cwd=cwd, log=log)
        return os.getpid()  # alive while the test runs

    job = release.build_start(db, app, spawn, confirmed=True)
    assert started["cwd"] == repo and started["script"].startswith("flutter build ipa --release\n")
    assert "build-snap-" in job["log_path"] and job["status"] == "running"
    assert Path(job["log_path"]).parent == Path(os.environ["RANKWELL_HOME"]) / "logs"
    with pytest.raises(RankwellError, match="already running"):
        release.build_start(db, app, spawn, confirmed=True)
    st = release.build_status(db, app)
    assert st["status"] == "running" and st["log_tail"][-1].startswith("$ flutter build ipa")

    make_ipa(repo / "build/ios/ipa/Snap.ipa")
    with Path(job["log_path"]).open("a") as f:
        f.write("Built build/ios/ipa/Snap.ipa\n[rankwell] exit 0\n")
    st = release.build_status(db, app)
    assert st["status"] == "ok" and st["job"]["exit_code"] == 0 and st["ipa"].endswith("Snap.ipa")
    assert db.value("SELECT status FROM build_jobs") == "ok"


def test_build_start_without_confirm_runs_nothing(db: Database, app: dict[str, Any], repo: Path) -> None:
    def spawn(script: str, cwd: Path, log: Path) -> int:
        raise AssertionError("must not run before the user confirms")

    preview = release.build_start(db, app, spawn)
    assert preview["started"] is False and preview["command"] == "flutter build ipa --release"
    assert preview["cwd"] == str(repo) and "build-snap-" in preview["log_path"]
    assert not Path(preview["log_path"]).exists() and db.value("SELECT count(*) FROM build_jobs") == 0


def test_build_start_mcp_tool_asks_first(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from mcp.server.mcpserver import MCPServer

    from rankwell import db as dbmod
    from rankwell.mcp import tools_release

    a = apps.add(dbmod.get(), "Snap", store_id=int(APP), bundle_id="com.example.app", repo_path=str(repo))
    started: list[str] = []
    monkeypatch.setattr(release, "_spawn", lambda script, cwd, log: started.append(script) or os.getpid())
    server = MCPServer("t")
    tools_release.register(server)
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "build_start")
    assert "confirmed" in tool.input_schema["properties"] and "ask" in (tool.description or "").lower()
    first = str(asyncio.run(server.call_tool("build_start", {"app": a["slug"]})))
    assert started == [] and "flutter build ipa --release" in first and str(repo) in first
    assert "confirmed=True" in first
    second = str(asyncio.run(server.call_tool("build_start", {"app": a["slug"], "confirmed": True})))
    assert len(started) == 1 and started[0].startswith("flutter build ipa --release") and "Build started" in second


def test_ship_build_shows_command_and_asks(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from click.testing import CliRunner

    from rankwell import db as dbmod
    from rankwell.cli import main

    apps.add(dbmod.get(), "Snap", store_id=int(APP), repo_path=str(repo))
    ran: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a) or subprocess.CompletedProcess(a, 0))
    no = CliRunner().invoke(main, ["ship", "snap", "--step", "build"], input="n\n")
    assert no.exit_code != 0 and "flutter build ipa --release" in no.output and str(repo) in no.output
    assert "Run this build?" in no.output and ran == []
    yes = CliRunner().invoke(main, ["ship", "snap", "--step", "build", "--yes"])
    assert yes.exit_code == 0 and "Run this build?" not in yes.output and len(ran) == 1


def test_build_failed_and_stopped(db: Database, app: dict[str, Any]) -> None:
    job = release.build_start(db, app, lambda s, c, log: 999_999_99, confirmed=True)
    with Path(job["log_path"]).open("a") as f:
        f.write("error: signing\n[rankwell] exit 65\n")
    assert release.build_status(db, app)["job"]["exit_code"] == 65
    release.build_start(db, app, lambda s, c, log: 999_999_99, confirmed=True)
    assert release.build_status(db, app)["status"] == "stopped"


def test_real_spawn_runs_detached(db: Database, app: dict[str, Any], repo: Path) -> None:
    (repo / ".aso" / "release.toml").write_text('[build]\nkind = "custom"\ncommand = "echo hello"\n')
    job = release.build_start(db, app, confirmed=True)
    release._procs[job["pid"]].wait(timeout=10)
    st = release.build_status(db, app)
    assert st["status"] == "ok" and "hello" in st["log_tail"]


# ------------------------------------------------------------------ upload


def test_plan_upload_and_apply(db: Database, app: dict[str, Any], repo: Path, fake: FakeASC,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    make_ipa(repo / "build/ios/ipa/Snap.ipa", build="8")
    p = release.plan_upload(db, listing.connect(), app)
    assert p["changes"][0]["new"].startswith("1.1 (8)") and p["changes"][0]["current"] == "6, 7"
    ran: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str]:
        ran.append(cmd)
        assert (cwd / "private_keys" / "AuthKey_KEY123.p8").exists()
        return 0, "UPLOAD SUCCEEDED"

    monkeypatch.setattr(release, "runner", fake_run)
    out = plans.apply(db, p["id"])
    assert out["uploaded"] == "1.1 (8)" and ran[0][:3] == ["xcrun", "altool", "--upload-package"]
    row = db.one("SELECT * FROM audit_log WHERE kind = 'upload'")
    assert row is not None and row["new"] == "1.1 (8)" and row["status"] == "ok"
    assert not fake.writes()


def test_upload_failure_is_audited(db: Database, app: dict[str, Any], repo: Path, fake: FakeASC,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    make_ipa(repo / "build/ios/ipa/Snap.ipa", build="8")
    p = release.plan_upload(db, listing.connect(), app)
    monkeypatch.setattr(release, "runner", lambda cmd, cwd: (1, "ERROR ITMS-90189: Redundant Binary Upload"))
    with pytest.raises(RankwellError, match="Upload failed \\(exit 1\\)"):
        plans.apply(db, p["id"])
    assert db.value("SELECT status FROM audit_log") == "error"
    assert plans.get(db, p["id"], pending_only=False)["status"] == "failed"


def test_upload_refusals(db: Database, app: dict[str, Any], repo: Path, fake: FakeASC) -> None:
    ipa = make_ipa(repo / "build/ios/ipa/Snap.ipa", build="7")
    with pytest.raises(RankwellError, match="already in App Store Connect"):
        release.plan_upload(db, listing.connect(), app)
    make_ipa(ipa, build="9", bundle="com.other")
    with pytest.raises(RankwellError, match=r"is com\.other"):
        release.plan_upload(db, listing.connect(), app)
    make_ipa(ipa, build="9")
    with pytest.raises(RankwellError, match="needs the App Store Connect key"):
        release.plan_upload(db, None, app)
    p = release.plan_upload(db, listing.connect(), app)
    make_ipa(ipa, build="10")
    os.utime(ipa, (5, 5))
    with pytest.raises(PlanError, match="changed since"):
        plans.apply(db, p["id"])


def test_custom_upload_without_key(db: Database, app: dict[str, Any], repo: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / ".aso" / "release.toml").write_text('[upload]\nkind = "custom"\ncommand = "up.sh {ipa}"\n')
    (repo / "pubspec.yaml").write_text("name: x\n")
    ipa = make_ipa(repo / "build/ios/ipa/My App.ipa")
    p = release.plan_upload(db, None, app)
    ran: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(release, "runner", lambda cmd, cwd: (ran.append((cmd, cwd)), (0, "ok"))[1])
    plans.apply(db, p["id"])
    assert ran == [(["/bin/sh", "-c", f"up.sh '{ipa}'"], repo)]


@pytest.mark.parametrize("template", ['up.sh {ipa}', 'up.sh "{ipa}"', "up.sh '{ipa}'"])
def test_upload_command_quotes_ipa_once(template: str, tmp_path: Path) -> None:
    import shlex
    import subprocess

    ipa = tmp_path / "My \"odd\" App's.ipa"
    cmd = release.upload_shell_command(template, ipa)
    assert shlex.split(cmd) == ["up.sh", str(ipa)]
    out = subprocess.run(["/bin/sh", "-c", cmd.replace("up.sh", "printf '%s\\n'", 1)], capture_output=True,
                         text=True, check=True).stdout
    assert out == f"{ipa}\n"


class PagedBuilds:
    """/v1/builds in pages of two, newest first; records the query parameters."""

    def __init__(self, n: int) -> None:
        self.rows = [{"id": f"b{i}", "attributes": {"version": str(i), "processingState": "VALID"},
                      "relationships": {"preReleaseVersion": {"data": {"id": "pre-old" if i < 5 else "pre-new"}}}}
                     for i in range(n, 0, -1)]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.calls.append((path, params))
        page = int(path.split("page=")[1]) if "page=" in path else 0
        rows = self.rows[page * 2: page * 2 + 2]
        included = [{"id": "pre-old", "attributes": {"version": "1.0"}},
                    {"id": "pre-new", "attributes": {"version": "1.1"}}]
        nxt = f"https://api.example/v1/builds?page={page + 1}" if page * 2 + 2 < len(self.rows) else None
        return {"data": rows, "included": included, "links": {"next": nxt} if nxt else {}}


def test_builds_reads_every_page() -> None:
    fake = PagedBuilds(9)
    old = release.builds(fake, 1, "1.0")  # type: ignore[arg-type]
    assert [b["build"] for b in old] == ["4", "3", "2", "1"]  # on pages 3 and 5, past the first page
    assert fake.calls[0][1]["filter[preReleaseVersion.version]"] == "1.0" and len(fake.calls) == 5
    assert len(release.builds(fake, 1)) == 9  # type: ignore[arg-type]
    capped = PagedBuilds(40)
    assert len(release.builds(capped, 1, max_pages=3)) == 6  # type: ignore[arg-type]


def test_wait_for_processing(fake: FakeASC) -> None:
    fake.build("b8", "1.1", "8", state="PROCESSING")
    t = [0.0]
    states: list[str] = []

    def sleep(s: float) -> None:
        t[0] += s
        if t[0] >= 60:
            fake.builds[0]["attributes"]["processingState"] = "VALID"

    b = release.wait_for_processing(listing.connect(), int(APP), "1.1", "8", interval=30, sleep=sleep,
                                    clock=lambda: t[0], progress=states.append)
    assert b["state"] == "VALID" and states == ["1.1 (8): PROCESSING"] * 2 + ["1.1 (8): VALID"]
    with pytest.raises(RankwellError, match="still not visible yet"):
        release.wait_for_processing(listing.connect(), int(APP), "1.1", "99", timeout=60, interval=30, sleep=sleep,
                                    clock=lambda: t[0])


# ------------------------------------------------------------------ attach build, review info


def test_attach_build(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    with pytest.raises(RankwellError, match="already attached"):
        release.plan_attach_build(db, listing.connect(), app)
    fake.build("b8", "1.1", "8", encryption=None)
    fake.build("b9", "1.1", "9", state="PROCESSING")
    p = release.plan_attach_build(db, listing.connect(), app)
    assert p["changes"][0]["current"] == "7" and p["changes"][0]["new"].startswith("8 ")
    assert any("Export compliance" in n for n in p["notes"])
    plans.apply(db, p["id"])
    assert fake.attached["v2"] == "b8"
    assert fake.writes() == [("PATCH", "/v1/appStoreVersions/v2/relationships/build",
                              {"data": {"type": "builds", "id": "b8"}})]
    assert db.value("SELECT new FROM audit_log WHERE kind = 'attach_build'") == "8"
    with pytest.raises(RankwellError, match="No processed build 42"):
        release.plan_attach_build(db, listing.connect(), app, "42")


def test_attach_refused_when_attached_changed(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.build("b8", "1.1", "8")
    p = release.plan_attach_build(db, listing.connect(), app, "8")
    fake.attached["v2"] = "b6"
    with pytest.raises(PlanError):
        plans.apply(db, p["id"])
    assert not fake.writes()


def test_review_info_hides_password(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = release.plan_review_info(db, listing.connect(), app, {
        "first_name": "A", "phone": "+1 999", "demo_account_required": True, "demo_account_name": "demo",
        "demo_account_password": "s3cret-pass"})
    assert [c["field"] for c in p["changes"]] == ["phone", "demo_account_required", "demo_account_name",
                                                  "demo_account_password"]
    shown = listing.present(p)
    assert "s3cret-pass" not in json.dumps(shown) and "hidden" in shown["display"]
    plans.apply(db, p["id"])
    attrs = fake.writes()[0][2]["data"]["attributes"]
    assert attrs == {"contactPhone": "+1 999", "demoAccountRequired": True, "demoAccountName": "demo",
                     "demoAccountPassword": "s3cret-pass"}
    assert "s3cret-pass" not in json.dumps(db.all("SELECT * FROM audit_log"))
    assert "s3cret-pass" not in str(db.value("SELECT payload FROM plans WHERE id = ?", (p["id"],)))
    with pytest.raises(RankwellError, match="not an email"):
        release.plan_review_info(db, listing.connect(), app, {"email": "nope"})


def test_review_info_hash_leaves_out_the_password(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    attrs = fake.review["v2"]["attributes"]
    attrs.update(demoAccountRequired=True, demoAccountName="demo", demoAccountPassword="old-secret")
    p = release.plan_review_info(db, listing.connect(), app, {"notes": "Tap Start."})
    hashed = {**attrs, "demoAccountPassword": True}
    assert p["live_hash"] == plans.state_hash({"version": "v2", "detail": "rd1", "attrs": hashed})
    assert p["live_hash"] != plans.state_hash({"version": "v2", "detail": "rd1", "attrs": attrs})
    attrs["demoAccountPassword"] = "rotated"  # still set: the plan still applies
    plans.apply(db, p["id"])
    p = release.plan_review_info(db, listing.connect(), app, {"notes": "Tap Go."})
    attrs["demoAccountPassword"] = None  # cleared in App Store Connect: refuse
    with pytest.raises(PlanError):
        plans.apply(db, p["id"])


def test_review_info_creates_detail(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    del fake.review["v2"]
    p = release.plan_review_info(db, listing.connect(), app, {"email": "me@x.io", "notes": "Tap Start."})
    plans.apply(db, p["id"])
    method, path, body = fake.writes()[0]
    assert (method, path) == ("POST", "/v1/appStoreReviewDetails")
    assert body["data"]["relationships"]["appStoreVersion"]["data"]["id"] == "v2"


# ------------------------------------------------------------------ submit / resubmit


def test_submit_creates_submission(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = release.plan_submit(db, listing.connect(), app)
    assert [c["step"] for c in p["changes"]] == ["release_type", "create", "add_item", "submit"]
    assert p["changes"][0]["new"] == "MANUAL"
    out = plans.apply(db, p["id"])
    assert out["review_submission"] == "sub-new1" and not out["resubmit"]
    paths = [(m, path) for m, path, _ in fake.writes()]
    assert paths == [("PATCH", "/v1/appStoreVersions/v2"), ("POST", "/v1/reviewSubmissions"),
                     ("POST", "/v1/reviewSubmissionItems"), ("PATCH", "/v1/reviewSubmissions/sub-new1")]
    assert fake.submissions[0]["attributes"]["state"] == "WAITING_FOR_REVIEW"
    assert db.value("SELECT count(*) FROM audit_log WHERE kind = 'submit'") == 4


def test_submit_reuses_open_submission(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions[0]["attributes"]["releaseType"] = "MANUAL"
    fake.submission("s1", "READY_FOR_REVIEW", [("it-iap", "READY_FOR_REVIEW", None)])
    p = release.plan_submit(db, listing.connect(), app)
    assert [c["step"] for c in p["changes"]] == ["add_item", "submit"]
    plans.apply(db, p["id"])
    assert [i["relationships"]["appStoreVersion"]["data"] for i in fake.items["s1"]][1]["id"] == "v2"


def test_resubmit_after_rejection(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions[0]["attributes"].update(appVersionState="REJECTED", releaseType="MANUAL")
    fake.submission("s1", "COMPLETE", [("old", "ACCEPTED", "v1")])
    fake.submission("s2", "UNRESOLVED_ISSUES", [("it1", "REJECTED", "v2"), ("it2", "ACCEPTED", None)])
    review.record_rejection(db, app["id"], "Guideline 2.1 - Performance", version="1.1")
    p = release.plan_submit(db, listing.connect(), app)
    assert [(c["step"], c.get("submission"), c.get("item")) for c in p["changes"]] == [
        ("resolve", "s2", "it1"), ("submit", "s2", None)]
    assert any("existing review submission" in n for n in p["notes"])
    out = plans.apply(db, p["id"])
    assert out == {"submitted": "1.1", "review_submission": "s2", "resubmit": True}
    assert [(m, path, b["data"]["attributes"]) for m, path, b in fake.writes()] == [
        ("PATCH", "/v1/reviewSubmissionItems/it1", {"resolved": True}),
        ("PATCH", "/v1/reviewSubmissions/s2", {"submitted": True})]
    assert not any(path == "/v1/reviewSubmissions" for _, path, _ in fake.writes())
    assert review.rejections(db, app["id"])[0]["resolved_at"]


def test_resubmit_without_resolve_is_what_apple_refuses(fake: FakeASC) -> None:
    fake.submission("s2", "UNRESOLVED_ISSUES", [("it1", "REJECTED", "v2")])
    with pytest.raises(RankwellError, match="409"):
        listing.connect().patch("/v1/reviewSubmissions/s2", {"data": {"type": "reviewSubmissions", "id": "s2",
                                                                      "attributes": {"submitted": True}}})


def test_submit_refusals(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.submission("s3", "WAITING_FOR_REVIEW", [("it", "READY_FOR_REVIEW", "v2")])
    with pytest.raises(RankwellError, match="already submitted"):
        release.plan_submit(db, listing.connect(), app)
    fake.submissions.clear()
    del fake.attached["v2"]
    with pytest.raises(RankwellError, match="Not ready to submit:\n- No build attached"):
        release.plan_submit(db, listing.connect(), app)
    p = release.plan_submit(db, listing.connect(), app, force=True)
    assert any("Submitting despite" in n for n in p["notes"])


def test_submit_refused_when_state_changed(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = release.plan_submit(db, listing.connect(), app)
    fake.submission("s9", "READY_FOR_REVIEW", [])
    with pytest.raises(PlanError):
        plans.apply(db, p["id"])
    assert not fake.writes()


def test_phased_release_from_config(db: Database, app: dict[str, Any], repo: Path, fake: FakeASC) -> None:
    (repo / ".aso" / "release.toml").write_text('[release]\ntype = "after-approval"\nphased = true\n')
    p = release.plan_submit(db, listing.connect(), app)
    assert p["changes"][0]["step"] == "phased_on"
    plans.apply(db, p["id"])
    assert fake.writes()[0][1] == "/v1/appStoreVersionPhasedReleases"
