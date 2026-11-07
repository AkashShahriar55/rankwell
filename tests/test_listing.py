from typing import Any

import pytest
from asc_fake import APP, FakeASC, client

from rankwell import apps, listing, plans
from rankwell.db import Database
from rankwell.errors import PlanError, RankwellError


@pytest.fixture
def app(db: Database) -> dict[str, Any]:
    return apps.add(db, "Snap", store_id=int(APP), bundle_id="com.example.app")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    f = FakeASC()
    f.version("v2", "1.1", "PREPARE_FOR_SUBMISSION")
    f.version("v1", "1.0", "READY_FOR_SALE")
    f.info("i2", "PREPARE_FOR_SUBMISSION")
    f.info("i1", "READY_FOR_DISTRIBUTION")
    f.info_loc("i2", "il2", "en-US", name="Snap", subtitle="Photo fun", privacyPolicyUrl="https://x/p")
    f.info_loc("i1", "il1", "en-US", name="Snap", subtitle="Photo fun")
    f.ver_loc("v2", "vl2", "en-US", description="Old", keywords="camera,photo", promotionalText="Hi")
    f.ver_loc("v1", "vl1", "en-US", description="Old", keywords="camera,photo", promotionalText="Hi")
    c = client()
    monkeypatch.setattr(listing, "connect", lambda: c)
    with f:
        yield f


# ------------------------------------------------------------------ checks

def _msgs(result: dict[str, Any], kind: str) -> str:
    return " | ".join(f"{p['field']}: {p['message']}" for p in result[kind])


def test_check_limits_and_spaces() -> None:
    r = listing.check({"name": "x" * 31, "subtitle": " Fun ", "keywords": "a, b ,c"})
    text = _msgs(r, "problems")
    assert "31 characters; the limit is 30" in text
    assert "subtitle: Leading or trailing spaces" in text
    assert "Spaces around commas" in text and "(2×)" in text
    assert not r["ok"]
    assert {"field": "name", "chars": 31, "limit": 30, "left": -1} in r["lengths"]


def test_check_repeats_plurals_filler_category_and_coverage() -> None:
    fields = {"name": "Snap Camera", "subtitle": "Photo editor", "keywords": "camera,photos,app,editing,filter"}
    r = listing.check(fields, tracked_keywords=["photo filter", "selfie"], genre_id=6008)
    tips = _msgs(r, "tips")
    assert "'camera' is in name and keywords" in tips and "save 7 characters" in tips
    assert "'photo' and 'photos'" in tips
    assert "'app' is wasted" in tips
    assert "Category word" not in tips
    assert "Tracked keywords not covered" in tips and "selfie" in tips and "photo filter" not in tips
    assert r["uncovered"] == ["selfie"]
    assert r["ok"]


def test_check_category_word_and_duplicates() -> None:
    r = listing.check({"keywords": "photo,video,photo,,x"}, genre_id=6008)
    assert "Repeated in the keyword field: photo" in _msgs(r, "problems")
    assert "Empty entry" in _msgs(r, "problems")
    assert "Category word(s) photo, video" in _msgs(r, "tips")


def test_check_clean() -> None:
    r = listing.check({"name": "Snap", "subtitle": "Quick edits", "keywords": "camera,filter"}, ["camera"])
    assert r["ok"] and not r["tips"]


# ------------------------------------------------------------------ drafts

def test_drafts_merge_and_clear(db: Database, app: dict[str, Any]) -> None:
    listing.save_draft(db, app["id"], "en-US", {"name": "A", "subtitle": "B"})
    assert listing.save_draft(db, app["id"], "en-US", {"subtitle": "", "keywords": "k"}) == {"name": "A",
                                                                                            "keywords": "k"}
    assert listing.draft(db, app["id"], "de-DE") == {}
    with pytest.raises(RankwellError, match="Unknown listing field"):
        listing.save_draft(db, app["id"], "en-US", {"title": "x"})


# ------------------------------------------------------------------ live and plans

def test_live_reads_editable_and_live(fake: FakeASC) -> None:
    cur = listing.live(client(), int(APP), "en-US")
    assert cur["edit_version"]["version"] == "1.1" and cur["live_version"]["version"] == "1.0"
    assert cur["fields"]["name"] == "Snap" and cur["fields"]["privacy_url"] == "https://x/p"
    assert cur["ids"] == {"edit_info_loc": "il2", "live_info_loc": "il1", "edit_version_loc": "vl2",
                          "live_version_loc": "vl1"}


def test_plan_listing_current_to_new_and_apply(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "en-US", {
        "name": "Snap Cam", "keywords": "camera,photo", "promotional_text": "New!", "whats_new": "Fixes"})
    by = {(c["area"], c["field"]): c for c in p["changes"]}
    assert by[("App info", "name")]["current"] == "Snap" and by[("App info", "name")]["new"] == "Snap Cam"
    assert ("Version 1.1", "keywords") not in by  # unchanged
    assert by[("Version 1.1", "promotional_text")]["new"] == "New!"
    assert by[("Live version 1.0", "promotional_text")]["target"]["id"] == "vl1"
    assert by[("Version 1.1", "whats_new")]["current"] == ""
    assert any("without review" in n for n in p["notes"])
    shown = listing.present(p)
    assert shown["plan_id"] == p["id"] and "| App info | en-US | name | Snap | Snap Cam |" in shown["display"]
    assert "ask before calling apply_plan" in shown["next"]
    assert not fake.writes()

    result = plans.apply(db, p["id"])
    assert len(result["changed"]) == 4 and not result["errors"]
    patches = {path: body["data"]["attributes"] for m, path, body in fake.writes()}
    assert patches["/v1/appInfoLocalizations/il2"] == {"name": "Snap Cam"}
    assert patches["/v1/appStoreVersionLocalizations/vl2"] == {"promotionalText": "New!", "whatsNew": "Fixes"}
    assert patches["/v1/appStoreVersionLocalizations/vl1"] == {"promotionalText": "New!"}
    rows = db.all("SELECT * FROM audit_log WHERE plan_id = ? ORDER BY id", (p["id"],))
    assert (rows[0]["field"], rows[0]["old"], rows[0]["new"], rows[0]["status"]) == ("name", "Snap", "Snap Cam", "ok")
    assert len(rows) == 4


def test_applier_refuses_when_live_changed(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "en-US", {"subtitle": "Edit photos"})
    fake.info_locs["i2"][0]["attributes"]["subtitle"] = "Changed in App Store Connect"
    with pytest.raises(PlanError, match="changed since"):
        plans.apply(db, p["id"])
    assert not fake.writes()
    assert db.value("SELECT count(*) FROM audit_log") == 0


def test_plan_new_locale_posts_localization(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "de-DE", {"name": "Schnapp", "description": "Neu"})
    assert any("new language" in n for n in p["notes"])
    plans.apply(db, p["id"])
    posts = [(path, body["data"]) for m, path, body in fake.writes() if m == "POST"]
    assert posts[0][0] == "/v1/appInfoLocalizations"
    assert posts[0][1]["attributes"] == {"locale": "de-DE", "name": "Schnapp"}
    assert posts[0][1]["relationships"]["appInfo"]["data"]["id"] == "i2"
    assert posts[1][1]["relationships"]["appStoreVersion"]["data"]["id"] == "v2"


def test_plan_without_editable_version(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions = [v for v in fake.versions if v["id"] != "v2"]
    fake.infos = [i for i in fake.infos if i["id"] != "i2"]
    p = listing.plan_listing(db, listing.connect(), app, "en-US",
                             {"name": "New", "keywords": "x", "promotional_text": "Live now"})
    assert [(c["area"], c["field"]) for c in p["changes"]] == [("Live version 1.0", "promotional_text")]
    assert any("name" in n and "plan_create_version" in n for n in p["notes"])
    assert any("keywords skipped" in n for n in p["notes"])


def test_first_version_skips_whats_new(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions = [v for v in fake.versions if v["id"] != "v1"]
    with pytest.raises(RankwellError, match=r"Nothing to change.*first version"):
        listing.plan_listing(db, listing.connect(), app, "en-US", {"whats_new": "Hello"})


def test_plan_refuses_over_limit(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    with pytest.raises(RankwellError, match="subtitle is 31 characters"):
        listing.plan_listing(db, listing.connect(), app, "en-US", {"subtitle": "s" * 31})


def test_partial_failure_is_audited(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.fail[("PATCH", "/v1/appInfoLocalizations/il2")] = (409, "The version is not editable.")
    p = listing.plan_listing(db, listing.connect(), app, "en-US", {"name": "Snap Cam", "description": "New"})
    result = plans.apply(db, p["id"])
    assert result["changed"] == ["Version 1.1: description"]
    assert "not editable" in result["errors"][0]
    statuses = {r["field"]: r["status"] for r in db.all("SELECT * FROM audit_log")}
    assert statuses == {"name": "error", "description": "ok"}


def _apple_creates_version_loc(fake: FakeASC, monkeypatch: pytest.MonkeyPatch, **attrs: Any) -> None:
    """The version localization POST answers 409 because Apple just added that language itself."""
    import httpx

    real = fake._post_loc

    def post(b: dict[str, Any], p: Any, kind: str) -> Any:
        if kind != "appStoreVersionLocalizations":
            return real(b, p, kind)
        fake.ver_loc("v2", "vl-de", "de-DE", **attrs)
        return httpx.Response(409, json={"errors": [{"detail": "already exists"}]})

    monkeypatch.setattr(fake, "_post_loc", post)


def test_409_patches_an_empty_localization_apple_created(db: Database, app: dict[str, Any], fake: FakeASC,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "de-DE", {"description": "Neu"})
    _apple_creates_version_loc(fake, monkeypatch, description=None, keywords="kamera")
    assert plans.apply(db, p["id"])["changed"] == ["Version 1.1: description"]
    assert fake.writes()[-1][:2] == ("PATCH", "/v1/appStoreVersionLocalizations/vl-de")


def test_409_refuses_when_apple_filled_a_planned_field(db: Database, app: dict[str, Any], fake: FakeASC,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "de-DE", {"description": "Neu"})
    assert p["changes"][0]["current"] == ""
    _apple_creates_version_loc(fake, monkeypatch, description="Kopie der englischen Beschreibung")
    with pytest.raises(PlanError, match="created this localization meanwhile; make a new plan"):
        plans.apply(db, p["id"])
    assert not [w for w in fake.writes() if w[0] == "PATCH"]
    assert db.value("SELECT status FROM audit_log") == "error"


def test_vtuple_tolerates_odd_versions() -> None:
    assert listing._vtuple("1.2.3") == (1, 2, 3)
    assert listing._vtuple("1.2b") == (1,) and listing._vtuple("beta") == ()
    assert listing._vtuple("1.2.0") == listing._vtuple("1.2")
    assert listing._vtuple("1.10") > listing._vtuple("1.9")


def test_create_version_with_non_numeric_live_version(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions = [v for v in fake.versions if v["id"] != "v2"]
    fake.versions[0]["attributes"]["versionString"] = "1.0-beta"
    p = listing.plan_create_version(db, listing.connect(), app, "1.1")
    assert p["changes"][0]["current"] == "1.0-beta"


def test_bundle_id_lookup_before_release(db: Database, fake: FakeASC) -> None:
    pre = apps.add(db, "Pre", bundle_id="com.example.app")
    assert listing.asc_app_id(listing.connect(), pre) == int(APP)
    nope = apps.add(db, "Nope", bundle_id="com.example.none")
    with pytest.raises(RankwellError, match="No app with bundle id"):
        listing.asc_app_id(listing.connect(), nope)


def test_create_version(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    with pytest.raises(RankwellError, match="already in preparation"):
        listing.plan_create_version(db, listing.connect(), app, "1.2")
    fake.versions = [v for v in fake.versions if v["id"] != "v2"]
    with pytest.raises(RankwellError, match="higher than the live version"):
        listing.plan_create_version(db, listing.connect(), app, "1.0")
    p = listing.plan_create_version(db, listing.connect(), app, "1.2")
    assert p["changes"][0]["current"] == "1.0" and p["changes"][0]["new"] == "1.2"
    out = plans.apply(db, p["id"])
    assert out["version"] == "1.2"
    method, path, body = fake.writes()[0]
    assert (method, path) == ("POST", "/v1/appStoreVersions")
    assert body["data"]["attributes"] == {"platform": "IOS", "versionString": "1.2"}
    assert db.value("SELECT new FROM audit_log WHERE kind = 'create_version'") == "1.2"


def test_create_version_refused_when_versions_changed(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.versions = [v for v in fake.versions if v["id"] != "v2"]
    p = listing.plan_create_version(db, listing.connect(), app, "1.2")
    fake.version("v9", "1.2", "PREPARE_FOR_SUBMISSION")
    with pytest.raises(PlanError):
        plans.apply(db, p["id"])
    assert not fake.writes()


def test_plan_several_languages_in_one_plan(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.ver_loc("v2", "vl2de", "de-DE", description="Alt", keywords="kamera")
    p = listing.plan_listing_locales(db, listing.connect(), app, {
        "en-US": {"description": "New"}, "de-DE": {"description": "Neu", "keywords": "kamera,foto"}})
    by = {(c["locale"], c["field"]): c for c in p["changes"]}
    assert by[("en-US", "description")]["current"] == "Old" and by[("de-DE", "keywords")]["new"] == "kamera,foto"
    assert p["locales"] == ["en-US", "de-DE"] and not fake.writes()
    result = plans.apply(db, p["id"])
    assert sorted(result["changed"]) == ["Version 1.1: description (de-DE)", "Version 1.1: description (en-US)",
                                         "Version 1.1: keywords (de-DE)"]
    patches = {path: body["data"]["attributes"] for m, path, body in fake.writes()}
    assert patches["/v1/appStoreVersionLocalizations/vl2de"] == {"description": "Neu", "keywords": "kamera,foto"}


def test_several_languages_checked_before_any_read(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    with pytest.raises(RankwellError, match="de-DE: Too long"):
        listing.plan_listing_locales(db, listing.connect(), app, {"en-US": {"name": "ok"},
                                                                  "de-DE": {"name": "x" * 31}})


def test_several_languages_stale_one_refuses_all(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    fake.ver_loc("v2", "vl2de", "de-DE", description="Alt")
    p = listing.plan_listing_locales(db, listing.connect(), app, {"en-US": {"description": "New"},
                                                                  "de-DE": {"description": "Neu"}})
    fake.ver_locs["v2"][-1]["attributes"]["description"] = "Changed in App Store Connect"
    with pytest.raises(PlanError):
        plans.apply(db, p["id"])
    assert not fake.writes()


def test_check_uses_live_fields_for_coverage() -> None:
    live = {"name": "Snap: AI Photo Enhancer", "subtitle": "Restore old photos", "keywords": "retro,vintage"}
    promo_only = listing.check({"promotional_text": "New looks"}, ["ai photo enhancer", "retro filter"])
    assert promo_only["uncovered"] == []  # nothing searchable given: no false "uncovered" list
    r = listing.check({"promotional_text": "New looks"}, ["ai photo enhancer", "retro filter"], context=live)
    assert r["uncovered"] == ["retro filter"]
    assert [x["field"] for x in r["lengths"]] == ["promotional_text"]  # limits only for the changed field
    dup = listing.check({"keywords": "photo,sharpen"}, [], context=live)
    assert any("'photo' is in" in t["message"] for t in dup["tips"])


def test_plan_keeps_live_indexed_fields(db: Database, app: dict[str, Any], fake: FakeASC) -> None:
    p = listing.plan_listing(db, listing.connect(), app, "en-US", {"promotional_text": "New!"})
    assert p["live_indexed"]["name"] == "Snap" and p["live_indexed"]["keywords"] == "camera,photo"
