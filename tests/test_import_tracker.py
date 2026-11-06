import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from rankwell import apps
from rankwell.db import Database
from rankwell.errors import NotConfigured
from rankwell.importers import tracker

URL = "https://abc.supabase.co"
OWN, COMP, OTHER = 6759825215, 222, 333


class FakeSource:
    """Tracker tables as PostgREST would return them (only the in.() filter is applied)."""

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.reads: list[str] = []

    def pages(self, table: str, order: str = "id.asc", **filters: str) -> Iterator[list[dict[str, Any]]]:
        self.reads.append(table)
        rows = self.tables.get(table, [])
        for col, flt in filters.items():
            if flt.startswith("in.("):
                wanted = {int(v) for v in flt[4:-1].split(",") if v}
                rows = [r for r in rows if r[col] in wanted]
            elif flt.startswith("eq."):
                rows = [r for r in rows if str(r[col]) == flt[3:]]
        if rows:
            yield rows

    def rows(self, table: str, order: str = "id.asc", **filters: str) -> list[dict[str, Any]]:
        return [r for p in self.pages(table, order, **filters) for r in p]


def fixture_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "apps": [{"id": OWN, "name": "PassFirst", "is_mine": True}, {"id": COMP, "name": "Rival", "is_mine": False},
                 {"id": OTHER, "name": "Gone", "is_mine": False}],
        "projects": [
            {"id": 1, "slug": "passfirst", "name": "PassFirst", "app_id": OWN, "countries": ["us", "GB"],
             "active": True, "bundle_id": "com.x.pass"},
            {"id": 2, "slug": "quran-path", "name": "Quran Path", "app_id": None, "countries": ["us"],
             "active": True},
            {"id": 3, "slug": "old-idea", "name": "Old Idea", "app_id": None, "countries": ["us"], "active": False},
        ],
        "project_apps": [{"project_id": 1, "app_id": OWN, "role": "mine"},
                         {"project_id": 1, "app_id": COMP, "role": "competitor"},
                         {"project_id": 2, "app_id": COMP, "role": "competitor"}],
        "keywords": [
            {"id": 10, "term": "Driving Test", "country": "us", "project_id": 1, "active": True},
            {"id": 11, "term": "theory test", "country": "gb", "project_id": 1, "active": True},
            {"id": 12, "term": "dmv", "country": "us", "project_id": 1, "active": False},
            {"id": 13, "term": "quran", "country": "us", "project_id": 2, "active": True},
            {"id": 14, "term": "driving test", "country": "us", "project_id": 2, "active": True},
        ],
        "rank_snapshots": [
            {"id": 1, "run_date": "2026-10-01", "app_id": OWN, "keyword_id": 10, "rank": 7},
            {"id": 2, "run_date": "2026-10-01", "app_id": COMP, "keyword_id": 10, "rank": None},
            {"id": 3, "run_date": "2026-10-01", "app_id": OWN, "keyword_id": 12, "rank": 40},
            {"id": 4, "run_date": "2026-10-01", "app_id": COMP, "keyword_id": 14, "rank": None},
            {"id": 5, "run_date": "2026-10-01", "app_id": OWN, "keyword_id": 999, "rank": 1},
        ],
        "app_snapshots": [{"id": 1, "run_date": "2026-10-01", "app_id": OWN, "country": "us", "title": "PassFirst",
                           "subtitle": "Pass first time", "version": "1.2", "rating_count": 12, "rating_avg": 4.8,
                           "price": 0, "description": "Learn.", "screenshot_urls": ["https://x/1.png"],
                           "description_hash": "h", "release_notes": "Fixes"}],
        "listing_changes": [{"id": 1, "run_date": "2026-10-02", "app_id": OWN, "country": "us", "field": "version",
                             "old_value": "1.1", "new_value": "1.2"}],
        "reviews": [{"id": "r1", "app_id": OWN, "country": "us", "rating": 5, "title": "Great", "content": "Yes",
                     "author": "a", "version": "1.2", "reviewed_at": "2026-10-01T07:00:00+00:00"},
                    {"id": "r2", "app_id": OWN, "country": "us", "rating": None}],
        "keyword_scores": [{"id": 1, "run_date": "2026-10-01", "keyword_id": 10, "popularity_proxy": 41,
                            "difficulty": 63, "median_top10_ratings": 1200, "title_match_top10": "0.40"}],
        "keyword_top10": [{"id": 1, "run_date": "2026-10-01", "keyword_id": 10, "rank": 1, "app_id": COMP,
                           "app_name": "Rival", "ratings": 5000}],
        "chart_snapshots": [
            {"id": 1, "run_date": "2026-10-01", "country": "us", "chart": "topfree-6017", "rank": 33, "app_id": OWN},
            {"id": 2, "run_date": "2026-10-01", "country": "us", "chart": "topfree-6017", "rank": 1, "app_id": 42},
            {"id": 3, "run_date": "2026-10-01", "country": "us", "chart": "weird", "rank": 2, "app_id": COMP},
        ],
        "asc_daily": [{"id": 1, "app_id": OWN, "report_date": "2026-10-01", "metric": "impressions", "source": "all",
                       "value": 120},
                      {"id": 2, "app_id": None, "report_date": "2026-09-01", "metric": "impressions",
                       "source": "all", "value": 1}],
        "asc_report_rows": [{"id": 1, "app_id": OWN, "report_name": "App Downloads Standard",
                             "report_date": "2026-10-01", "row_hash": "abc", "data": {"date": "2026-10-01"}}],
    }


def test_import_mapping(db: Database) -> None:
    source = FakeSource(fixture_tables())
    out = tracker.run(db, source)  # type: ignore[arg-type]
    c = out["counts"]
    assert out["status"] == "ok"
    assert c["apps"] == 3 and c["competitors"] == 2 and c["keywords"] == 4
    assert c["ranks"] == 3  # unknown keyword id skipped; two projects sharing a term merge into one row
    assert c["reviews"] == 1 and c["chart_positions"] == 1 and c["asc_daily"] == 1

    own = apps.get(db, "passfirst")
    assert own["store_id"] == OWN and own["bundle_id"] == "com.x.pass" and own["countries"] == ["us", "gb"]
    assert {(k["country"], k["term"]) for k in apps.keywords(db, own["id"])} == {
        ("us", "driving test"), ("gb", "theory test")}  # inactive 'dmv' is not tracked
    assert [x["name"] for x in apps.competitors(db, own["id"])] == ["Rival"]
    pre = apps.get(db, "quran-path")
    assert pre["stage"] == "prelaunch" and [x["store_id"] for x in apps.competitors(db, pre["id"])] == [COMP]
    old = db.one("SELECT * FROM apps WHERE slug = 'old-idea'")
    assert old and old["archived_at"]

    assert db.value("SELECT rank FROM ranks WHERE term = 'dmv'") == 40  # history kept for inactive keywords
    assert db.value("SELECT rank FROM ranks WHERE term = 'driving test' AND store_id = ?", (OWN,)) == 7
    snap = db.one("SELECT * FROM snapshots")
    assert snap and snap["subtitle"] == "Pass first time" and json.loads(snap["screenshots"]) == ["https://x/1.png"]
    assert db.one("SELECT * FROM changes")["new"] == "1.2"  # type: ignore[index]
    score = db.one("SELECT * FROM keyword_scores")
    assert score and score["term"] == "driving test" and score["title_match"] == 0.4 and score["popularity"] == 41
    chart = db.one("SELECT * FROM chart_positions")
    assert chart and chart["genre_id"] == 6017 and chart["chart"] == "free" and chart["rank"] == 33
    assert db.one("SELECT * FROM keyword_top")["store_id"] == COMP  # type: ignore[index]
    assert json.loads(db.value("SELECT data FROM asc_report_rows")) == {"date": "2026-10-01"}
    run = db.one("SELECT * FROM runs WHERE kind = 'import'")
    assert run and run["status"] == "ok"


def test_import_twice_updates_in_place(db: Database) -> None:
    tables = fixture_tables()
    tracker.run(db, FakeSource(tables))  # type: ignore[arg-type]
    counts = {t: db.value(f"SELECT count(*) FROM {t}") for t in (
        "apps", "keywords", "competitors", "ranks", "snapshots", "changes", "reviews", "asc_daily")}
    tables["projects"][0]["name"] = "PassFirst Pro"
    tables["rank_snapshots"][0]["rank"] = 5
    tracker.run(db, FakeSource(tables))  # type: ignore[arg-type]
    assert counts == {t: db.value(f"SELECT count(*) FROM {t}") for t in counts}
    assert apps.get(db, "passfirst")["name"] == "PassFirst Pro"
    assert db.value("SELECT rank FROM ranks WHERE term = 'driving test' AND store_id = ?", (OWN,)) == 5


def test_import_matches_existing_app_by_store_id(db: Database) -> None:
    mine = apps.add(db, "My Pass App", store_id=OWN, countries=["de"], repo_path="/code/pass")
    tracker.run(db, FakeSource(fixture_tables()))  # type: ignore[arg-type]
    a = apps.get(db, mine["id"])
    assert a["repo_path"] == "/code/pass" and a["countries"] == ["de", "us", "gb"]
    assert db.value("SELECT count(*) FROM apps WHERE store_id = ?", (OWN,)) == 1


def test_read_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nSUPABASE_URL = https://abc.supabase.co\nSUPABASE_SERVICE_KEY= 'sb_secret_x'\n"
                   "EMPTY=\nexport OTHER=1\n")
    assert tracker.read_env(env) == {"SUPABASE_URL": URL, "SUPABASE_SERVICE_KEY": "sb_secret_x", "EMPTY": "",
                                     "OTHER": "1"}
    with pytest.raises(NotConfigured):
        tracker.read_env(tmp_path / "missing")
    env.write_text("SUPABASE_URL=x\n")
    with pytest.raises(NotConfigured):
        tracker.connect(env)


def test_supabase_client_paginates_with_get_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracker, "PAGE", 2)
    client = tracker.Supabase(URL, "eyJhbGciOi.fake.jwt")
    with respx.mock:
        route = respx.get(f"{URL}/rest/v1/apps").mock(side_effect=[
            httpx.Response(200, json=[{"id": 1}, {"id": 2}]), httpx.Response(200, json=[{"id": 3}])])
        respx.get(f"{URL}/rest/v1/nope").mock(return_value=httpx.Response(404, json={"code": "42P01"}))
        assert [r["id"] for r in client.rows("apps")] == [1, 2, 3]
        assert client.rows("nope") == []
    first, second = (c.request for c in route.calls)
    assert first.method == "GET" and first.url.params["offset"] == "0" and second.url.params["offset"] == "2"
    assert first.url.params["order"] == "id.asc" and first.headers["apikey"] == "eyJhbGciOi.fake.jwt"
    assert first.headers["Authorization"] == "Bearer eyJhbGciOi.fake.jwt"
    assert "Authorization" not in tracker.Supabase(URL, "sb_secret_x").headers
