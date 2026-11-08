import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from play_fake import PEM, PKG, FakePlay, key

from rankwell import apps, plans, play
from rankwell import db as dbmod
from rankwell.db import Database
from rankwell.errors import PlanError, RankwellError, StoreError
from rankwell.mcp import server
from rankwell.stores.play import Play

AAB = b"PK\x03\x04 fake bundle bytes"
NOW = 1_800_000_000  # 2027-01-15


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RANKWELL_PLAY_KEY_PATH", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    out = r / "build/app/outputs/bundle/release"
    out.mkdir(parents=True)
    (out / "app-release.aab").write_bytes(AAB)
    (r / "pubspec.yaml").write_text("name: snap\nversion: 1.2.0+12\n")
    return r


@pytest.fixture
def app(db: Database, repo: Path) -> dict[str, Any]:
    return apps.add(db, "Snap", play_package=PKG, repo_path=str(repo))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    f = FakePlay()
    f.listing("en-US", title="Snap", shortDescription="Quick photo edits", fullDescription="Edit photos fast.")
    f.track("internal", {"name": "1.1.0", "versionCodes": ["9"], "status": "completed"})
    f.track("production", {"name": "1.0.0", "versionCodes": ["8"], "status": "inProgress", "userFraction": 0.5})
    f.bundle(8)
    f.bundle(9)
    client = Play(key())
    monkeypatch.setattr(play, "connect", lambda: client)
    with f:
        yield f


def _no_secret(value: Any) -> None:
    text = json.dumps(value, default=str)
    assert not any(ln in text for ln in PEM.splitlines() if len(ln) > 8)
    assert "tok-" not in text


# ------------------------------------------------------------------ apps

def test_play_package_on_apps(db: Database) -> None:
    a = apps.add(db, "Alpha", play_package="com.a")
    assert a["play_package"] == "com.a"
    assert apps.update(db, a["id"], play_package="com.b")["play_package"] == "com.b"
    with pytest.raises(RankwellError, match="no Google Play package"):
        play.package_of(apps.add(db, "Beta"))


# ------------------------------------------------------------------ listing

def test_check_limits_promo_and_coverage() -> None:
    r = play.check({"title": "Best Photo Editor FREE 🎉 xxxxxxxxxx", "short_description": " x ",
                    "full_description": "Edit photos."}, ["photo editor", "collage"])
    problems = " | ".join(p["message"] for p in r["problems"])
    tips = " | ".join(p["message"] for p in r["tips"])
    assert "the limit is 30" in problems and "Leading or trailing spaces" in problems
    assert "'best, free'" in tips and "Emoji" in tips
    assert "collage" in tips and r["uncovered"] == ["collage"]
    assert play.check({"title": "Snap", "short_description": "Photo editor"}, ["photo editor"])["tips"] == []


def test_listing_live(app: dict[str, Any], fake: FakePlay) -> None:
    cur = play.live(play.connect(), PKG, "en-US")
    assert cur["exists"] and cur["fields"]["title"] == "Snap" and cur["languages"] == ["en-US"]
    assert "Quick photo edits" in play.live_display(cur)
    assert fake.edits == {}  # the read edit was deleted


def test_listing_plan_and_apply_through_one_edit(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    plan = play.plan_listing(db, play.connect(), app, "en-US",
                             {"title": "Snap Photo Editor", "short_description": "Quick photo edits"})
    assert [(c["field"], c["current"], c["new"]) for c in plan["changes"]] == [
        ("title", "Snap", "Snap Photo Editor")]
    fake.calls.clear()
    result = plans.apply(db, plan["id"])
    assert result["changed"] == ["title"]
    assert [(m, p.split("/edits")[-1]) for m, p, _ in fake.writes()] == [
        ("POST", ""), ("PUT", "/e2/listings/en-US"), ("POST", "/e2:validate"), ("POST", "/e2:commit")]
    put = next(b for m, _, b in fake.writes() if m == "PUT")
    assert put == {"title": "Snap Photo Editor", "shortDescription": "Quick photo edits",
                   "fullDescription": "Edit photos fast.", "language": "en-US"}
    assert fake.state["listings"]["en-US"]["title"] == "Snap Photo Editor"
    assert fake.edits == {}
    log = db.all("SELECT * FROM audit_log")
    assert [(r["kind"], r["field"], r["old"], r["new"]) for r in log] == [
        ("play_listing", "title", "Snap", "Snap Photo Editor")]


def test_listing_apply_refused_when_play_changed(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    plan = play.plan_listing(db, play.connect(), app, "en-US", {"title": "Snap Pro"})
    fake.listing("en-US", title="Changed in Console")
    with pytest.raises(PlanError, match="changed since this plan"):
        plans.apply(db, plan["id"])
    assert not any(m == "PUT" or p.endswith(":commit") for m, p, _ in fake.writes())
    assert fake.edits == {} and fake.state["listings"]["en-US"]["title"] == "Changed in Console"
    assert db.value("SELECT status FROM plans WHERE id = ?", (plan["id"],)) == "failed"


def test_listing_plan_rules(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    with pytest.raises(RankwellError, match="title is 31 characters"):
        play.plan_listing(db, play.connect(), app, "en-US", {"title": "x" * 31})
    with pytest.raises(RankwellError, match="Unknown Play listing field"):
        play.plan_listing(db, play.connect(), app, "en-US", {"subtitle": "x"})
    with pytest.raises(RankwellError, match="Nothing to change"):
        play.plan_listing(db, play.connect(), app, "en-US", {"title": "Snap"})
    with pytest.raises(RankwellError, match="needs a title"):
        play.plan_listing(db, play.connect(), app, "de-DE", {"short_description": "Schnell"})
    plan = play.plan_listing(db, play.connect(), app, "de-DE", {"title": "Snap", "short_description": "Schnell"})
    assert any("new language" in n for n in plan["notes"])
    plans.apply(db, plan["id"])
    assert fake.state["listings"]["de-DE"] == {"title": "Snap", "shortDescription": "Schnell", "language": "de-DE"}


# ------------------------------------------------------------------ tracks and upload

def test_tracks(app: dict[str, Any], fake: FakePlay) -> None:
    rows = play.tracks(play.connect(), PKG)
    assert [(r["track"], r["version_codes"], r["status"], r["rollout"]) for r in rows] == [
        ("internal", "9", "completed", "100%"), ("production", "8", "inProgress", "50%")]
    assert "| production |" in play.tracks_display(rows)


def test_upload_plan_and_apply(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    plan = play.plan_upload(db, play.connect(), app, track="production", rollout=0.2,
                            release_notes={"en-US": "Faster export."})
    assert plan["release_status"] == "inProgress" and plan["fraction"] == 0.2
    fields = [(c["area"], c["field"]) for c in plan["changes"]]
    assert fields == [("Bundles", "upload"), ("Track production", "release"), ("Track production", "release notes")]
    assert "version code 12 (1.2.0, from pubspec.yaml)" in plan["changes"][0]["new"]
    assert plan["changes"][1]["current"] == "inProgress 8" and plan["changes"][1]["new"] == "inProgress · 20% of users"
    assert any("Google's review" in n for n in plan["notes"])
    fake.next_code = 12
    result = plans.apply(db, plan["id"])
    assert result["version_code"] == 12 and result["sha256"] == hashlib.sha256(AAB).hexdigest()
    assert fake.uploaded == [AAB]
    assert fake.state["tracks"]["production"] == [{"versionCodes": ["12"], "status": "inProgress",
                                                   "userFraction": 0.2,
                                                   "releaseNotes": [{"language": "en-US", "text": "Faster export."}]}]
    assert [b["versionCode"] for b in fake.state["bundles"]] == [8, 9, 12]
    assert fake.edits == {}
    assert [r["field"] for r in db.all("SELECT field FROM audit_log WHERE kind = 'play_upload'")] == [
        "versionCode", "release"]


def test_upload_refuses_duplicates(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    fake.bundle(12)
    with pytest.raises(RankwellError, match=r"Version code 12 .* Raise the \+N"):
        play.plan_upload(db, play.connect(), app)
    fake.bundle(5, hashlib.sha256(AAB).hexdigest())
    with pytest.raises(RankwellError, match="already on Play as version code 5"):
        play.plan_upload(db, play.connect(), app)


def test_upload_options_are_checked(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    c = play.connect()
    with pytest.raises(RankwellError, match="track must be"):
        play.plan_upload(db, c, app, track="nightly")
    with pytest.raises(RankwellError, match="between 0 and 1"):
        play.plan_upload(db, c, app, rollout=1.5)
    with pytest.raises(RankwellError, match="needs status inProgress"):
        play.plan_upload(db, c, app, rollout=0.5, status="draft")
    with pytest.raises(RankwellError, match="needs a rollout"):
        play.plan_upload(db, c, app, status="inProgress")
    with pytest.raises(RankwellError, match="over 500"):
        play.plan_upload(db, c, app, release_notes={"en-US": "x" * 501})
    with pytest.raises(RankwellError, match=r"No \.aab at"):
        play.plan_upload(db, c, app, aab="missing.aab")
    draft = play.plan_upload(db, c, app, status="draft", rollout=1.0)
    assert draft["release_status"] == "draft" and draft["fraction"] is None


def test_upload_refused_when_track_changed(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    plan = play.plan_upload(db, play.connect(), app)
    fake.track("internal", {"versionCodes": ["11"], "status": "completed"})
    with pytest.raises(PlanError):
        plans.apply(db, plan["id"])
    assert fake.uploaded == [] and fake.edits == {}


def test_upload_failure_deletes_the_edit(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    plan = play.plan_upload(db, play.connect(), app)
    fake.fail[("POST", r":commit$")] = (400, "Version code 10 has already been used.")
    with pytest.raises(StoreError, match="already been used"):
        plans.apply(db, plan["id"])
    assert fake.edits == {} and [b["versionCode"] for b in fake.state["bundles"]] == [8, 9]
    assert db.value("SELECT count(*) FROM audit_log") == 0


# ------------------------------------------------------------------ reviews

def _reviews(fake: FakePlay) -> None:
    fake.review("gp:AAA111", 1, "Crashes on export", NOW - 3600)
    fake.review("gp:BBB222", 5, "Love it", NOW - 7200, reply="Thanks!")
    fake.review("gp:CCC333", 3, "Needs dark mode", NOW - 9000)


def test_reviews_fetched_paged_and_kept(db: Database, app: dict[str, Any], fake: FakePlay,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _reviews(fake)
    assert play.fetch_reviews(db, play.connect(), app) == 3
    assert sum(1 for m, p, _ in fake.calls if p.endswith("/reviews")) == 2  # two pages
    rows = play.reviews(db, app, days=10_000)
    assert [r["id"] for r in rows] == ["gp:AAA111", "gp:BBB222", "gp:CCC333"]
    assert rows[1]["reply"] == "Thanks!" and rows[0]["rating"] == 1 and rows[0]["version"] == "1.0"
    assert [r["id"] for r in play.reviews(db, app, days=10_000, unanswered=True)] == ["gp:AAA111", "gp:CCC333"]
    fake.reviews.clear()  # gone from the API after 7 days, still in Rankwell
    play.fetch_reviews(db, play.connect(), app)
    assert len(play.reviews(db, app, days=10_000)) == 3
    assert "Crashes on export" in play.reviews_display(rows)


def test_review_reply(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    _reviews(fake)
    play.fetch_reviews(db, play.connect(), app)
    with pytest.raises(RankwellError, match="351 characters; Google Play allows 350"):
        play.plan_review_reply(db, play.connect(), app, "gp:AAA111", "x" * 351)
    with pytest.raises(RankwellError, match="empty"):
        play.plan_review_reply(db, play.connect(), app, "gp:AAA111", "  ")
    plan = play.plan_review_reply(db, play.connect(), app, "AAA111", "Sorry! Fixed in 1.2, please update.")
    assert plan["review_id"] == "gp:AAA111"
    assert plan["changes"][0]["current"] == "" and plan["changes"][0]["area"] == "Review ★ by Ann"
    text = "Sorry! Fixed in 1.2, please update."
    assert plans.apply(db, plan["id"]) == {"replied": "gp:AAA111", "text": text}
    assert db.value("SELECT reply FROM play_reviews WHERE id = 'gp:AAA111'") == text
    assert db.value("SELECT new FROM audit_log WHERE kind = 'play_review_reply'") == text


def test_review_reply_refused_when_reply_changed(db: Database, app: dict[str, Any], fake: FakePlay) -> None:
    _reviews(fake)
    plan = play.plan_review_reply(db, play.connect(), app, "gp:CCC333", "Dark mode is coming.")
    fake.reviews[2]["comments"].append({"developerComment": {"text": "Replied in Console"}})
    with pytest.raises(PlanError):
        plans.apply(db, plan["id"])
    assert not any(p.endswith(":reply") for _, p, _ in fake.calls)


# ------------------------------------------------------------------ MCP tools

def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = asyncio.run(server.build().call_tool(name, args)).structured_content
    assert isinstance(out, dict)
    return out


def test_mcp_tools_show_plans_and_hide_secrets(repo: Path, fake: FakePlay) -> None:
    apps.add(dbmod.get(), "Snap", play_package=PKG, repo_path=str(repo))
    _reviews(fake)
    live = _call("play_listing_live", {"app": "snap"})
    assert "Quick photo edits" in live["display"]
    checked = _call("play_listing_check", {"app": "snap", "fields": {"title": "Top Snap"}})
    assert "'top'" in checked["display"]
    planned = _call("play_plan_listing", {"app": "snap", "fields": {"title": "Snap Photo"}})
    assert planned["plan_id"].startswith("play_listing-") and "Snap Photo" in planned["display"]
    upload = _call("play_plan_upload", {"app": "snap", "track": "beta"})
    assert "Track beta" in upload["display"]
    assert "| internal |" in _call("play_tracks", {"app": "snap"})["display"]
    revs = _call("play_reviews", {"app": "snap", "days": 10_000})
    assert revs["fetched"] == 3 and "Love it" in revs["display"]
    reply = _call("play_plan_review_reply", {"app": "snap", "review_id": "gp:AAA111", "text": "x" * 400})
    assert reply == {"error": "The reply is 400 characters; Google Play allows 350."}
    for out in (live, checked, planned, upload, revs):
        _no_secret(out)
    _no_secret(dbmod.get().all("SELECT * FROM plans"))


def test_mcp_without_key_says_what_to_do(fake: FakePlay, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(play, "connect", lambda: Play())
    apps.add(dbmod.get(), "Snap", play_package=PKG)
    out = _call("play_tracks", {"app": "snap"})
    assert "rankwell play init" in out["error"]


# ------------------------------------------------------------------ CLI

def test_cli_init_and_upload(tmp_path: Path, repo: Path, fake: FakePlay) -> None:
    from click.testing import CliRunner
    from play_fake import key_json

    from rankwell import secrets
    from rankwell.cli import main

    f = tmp_path / "sa.json"
    f.write_text(key_json())
    r = CliRunner().invoke(main, ["play", "init", "--key", str(f)])
    assert r.exit_code == 0 and "saved in the system keyring" in r.output and secrets.has_play_key()
    assert "BEGIN" not in r.output
    apps.add(dbmod.Database(), "Snap", play_package=PKG, repo_path=str(repo))
    r = CliRunner().invoke(main, ["play", "upload", "snap", "--track", "beta"], input="n\n")
    assert r.exit_code == 1 and "Track beta" in r.output and fake.uploaded == []
    r = CliRunner().invoke(main, ["play", "upload", "snap", "--track", "beta", "--notes", "en-US=Fixes", "--yes"])
    assert r.exit_code == 0, r.output
    assert "version code 10 on beta (completed)" in r.output and fake.uploaded == [AAB]
    assert fake.state["tracks"]["beta"][0]["releaseNotes"] == [{"language": "en-US", "text": "Fixes"}]
    r = CliRunner().invoke(main, ["play", "upload", "snap", "--notes", "oops"])
    assert r.exit_code == 2 and "LANGUAGE=TEXT" in r.output
    r = CliRunner().invoke(main, ["play", "init", "--remove"])
    assert r.exit_code == 0 and not secrets.has_play_key()
