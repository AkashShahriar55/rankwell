import asyncio
import json

from mcp.server.mcpserver import MCPServer

from rankwell import apps, today
from rankwell.db import Database
from rankwell.mcp import tools_insights
from rankwell.util import days_ago

END = "2026-11-06"
AS_OF = "2026-11-07"
OWN, MEDI, ROUND = 111, 222, 333
TERMS = ("pill reminder", "medication tracker", "pill tracker", "med alarm")


def seed(db: Database) -> dict:
    a = apps.add(db, "Pill Pal", store_id=OWN, countries=["us"])
    apps.add_keywords(db, a["id"], list(TERMS), "us")
    apps.add_competitor(db, a["id"], MEDI, "Medisafe")
    apps.add_competitor(db, a["id"], ROUND, "Round Health")
    db.upsert("keyword_scores", [{"country": "us", "term": "pill reminder", "date": "2026-11-01", "popularity": 60,
                                  "difficulty": 70}], ["country", "term", "date"])
    ranks = []
    for i in range(14):
        old = i >= 7
        own = {"pill reminder": 30 if old else 12, "medication tracker": 5 if old else 60,
               "pill tracker": None if old else 80, "med alarm": 50 if old else None}
        for t in TERMS:
            ranks.append({"date": days_ago(i, END), "country": "us", "term": t, "store_id": OWN, "rank": own[t]})
    db.upsert("ranks", ranks, ["date", "country", "term", "store_id"])
    db.upsert("snapshots", [{"store_id": OWN, "country": "us", "date": "2026-08-01", "title": "Pill Pal",
                             "rating_count": 30, "rating_avg": 4.3}] + [
        {"store_id": OWN, "country": "us", "date": days_ago(i, END), "title": "Pill Pal",
         "rating_count": 40 + 13 - i, "rating_avg": 4.2 if i >= 7 else 4.0} for i in range(14)
    ], ["store_id", "country", "date"])
    db.upsert("changes", [
        {"store_id": MEDI, "country": "us", "date": "2026-11-03", "field": "title", "old": "Medisafe Meds",
         "new": "Medisafe Pill Reminder"},
        {"store_id": MEDI, "country": "us", "date": "2026-11-04", "field": "rating_count", "old": "100", "new": "130"},
        {"store_id": ROUND, "country": "us", "date": "2026-11-05", "field": "screenshots",
         "old": json.dumps(["a"] * 8), "new": json.dumps(["b"] * 10)},
        {"store_id": ROUND, "country": "us", "date": "2026-10-01", "field": "version", "old": "3.0", "new": "3.1"},
    ], ["store_id", "country", "date", "field"])
    db.upsert("reviews", [
        {"id": "r1", "store_id": OWN, "country": "us", "rating": 2, "title": "Crashes on launch",
         "content": "The app crashes on launch every morning.", "version": "1.0",
         "reviewed_at": "2026-11-04T08:00:00-07:00"},
        {"id": "r2", "store_id": OWN, "country": "us", "rating": 3, "title": "Reminder never fires",
         "content": "Never fires when locked.", "version": "1.0", "reviewed_at": "2026-11-02T08:00:00-07:00"},
        {"id": "r3", "store_id": OWN, "country": "us", "rating": 5, "title": "Great", "content": "Love it",
         "reviewed_at": "2026-11-05T08:00:00-07:00"},
        {"id": "r4", "store_id": OWN, "country": "us", "rating": 1, "title": "Old", "content": "Too old to show",
         "reviewed_at": "2026-09-01T08:00:00-07:00"},
        {"id": "r5", "store_id": MEDI, "country": "us", "rating": 1, "title": "Ads", "content": "Too many ads",
         "reviewed_at": "2026-11-01T08:00:00-07:00"},
    ], ["id"])
    return a


def asc(db: Database) -> None:
    rows = []
    for i in range(14):
        for metric, v in (("impressions", 1000), ("page_views", 100), ("first_downloads", 10 if i < 7 else 20)):
            rows.append({"store_id": OWN, "date": days_ago(i, "2026-11-03"), "metric": metric, "source": "all",
                         "value": v})
    db.upsert("asc_daily", rows, ["store_id", "date", "metric", "source"])


def test_briefing_movers_changes_reviews(db: Database) -> None:
    seed(db)
    b = today.briefing(db, "Pill Pal", as_of=AS_OF)
    assert b["freshness"]["stale"] is False and b["freshness"]["last_rank"] == END
    assert [m["term"] for m in b["movers"]["up"]] == ["pill reminder", "pill tracker"]
    assert [m["term"] for m in b["movers"]["down"]] == ["medication tracker", "med alarm"]
    assert b["keywords"] == {"tracked": 4, "ranked": 3, "top10": 0, "top50": 1}
    sentences = [c["sentence"] for c in b["competitor_changes"]]
    assert sentences == ["Round Health updated its screenshots (8 → 10)",
                         "Medisafe changed its title: “Medisafe Meds” → “Medisafe Pill Reminder”"]
    assert [r["id"] for r in b["low_reviews"]] == ["r1", "r2"]
    assert b["ratings"]["count"]["change"] == 6
    assert b["listing"]["lower_bound"] is True and b["listing"]["days"] == 98
    todo = [t["do"] for t in b["todo"]]
    assert todo[0].startswith("Reply to the 2 review(s)")
    assert todo[1].startswith("Look into “medication tracker”")
    assert "Medisafe changed its title" in b["todo"][1]["why"]
    assert any(t.startswith("Plan a metadata update") for t in todo)
    assert any(t.startswith("Connect App Store Connect") for t in todo)
    assert all(t["why"] for t in b["todo"]) and len(b["todo"]) <= today.MAX_TODO
    assert "No App Store Connect data yet" in b["display"] and "**What to do**" in b["display"]


def test_briefing_funnel_drop(db: Database) -> None:
    seed(db)
    asc(db)
    b = today.briefing(db, "Pill Pal", as_of=AS_OF)
    assert b["funnel"]["changes"]["first_downloads"]["change_pct"] == -50.0
    drop = next(t for t in b["todo"] if t["do"].startswith("Find the funnel step"))
    assert "70 vs 140" in drop["why"] and "10.0% vs 20.0%" in drop["why"]
    assert "downloads 70 (▼ -50.0% ⚠)" in b["display"]


def test_stale_data_warning(db: Database) -> None:
    seed(db)
    b = today.briefing(db, "Pill Pal", as_of="2026-11-20")
    assert b["freshness"]["stale"] is True and b["freshness"]["age_days"] == 14
    assert b["todo"][0]["do"].startswith("Run `rankwell collect`")
    assert "14 days old" in b["todo"][0]["why"]
    assert "Data is stale" in b["display"]


def test_never_collected(db: Database) -> None:
    a = apps.add(db, "Fresh", store_id=5)
    b = today.briefing(db, a, as_of=AS_OF)
    assert b["freshness"]["never"] is True
    assert b["todo"][0]["do"] == "Run the first collection: `rankwell collect`."
    assert any("Track 10-20 keywords" in t["do"] for t in b["todo"])
    assert any("competitors" in t["do"] for t in b["todo"])


def test_concept_app(db: Database) -> None:
    a = apps.add(db, "Quran Path", countries=["us", "gb"])
    apps.add_keywords(db, a["id"], ["learn quran", "noorani qaida"], "us")
    db.upsert("keyword_scores", [{"country": "us", "term": "learn quran", "date": "2026-11-01", "popularity": 45,
                                  "difficulty": 30}], ["country", "term", "date"])
    apps.add_competitor(db, a["id"], 444, "Quran Pro")
    db.upsert("changes", [{"store_id": 444, "country": "gb", "date": "2026-11-05", "field": "version", "old": "2.0",
                           "new": "2.1"}], ["store_id", "country", "date", "field"])
    b = today.briefing(db, a, as_of=AS_OF)
    assert b["stage"] == "prelaunch"
    us = b["keyword_plan"]["countries"][0]
    assert us["tracked"] == 2 and us["scored"] == 1 and us["unscored"] == ["noorani qaida"]
    assert b["competitor_changes"][0]["sentence"] == "Quran Pro shipped version 2.1 (was 2.0)"
    assert b["todo"][0]["do"].startswith("Score the 1 unscored")
    assert "Keyword plan" in b["display"]


def test_overview(db: Database) -> None:
    assert "No apps yet" in today.overview(db)["display"]
    seed(db)
    apps.add(db, "Concept")
    o = today.overview(db, as_of=AS_OF)
    assert [r["app"] for r in o["rows"]] == ["Pill Pal", "Concept"]
    assert o["rows"][0]["movers"] == "▲2 ▼2" and o["rows"][0]["low_reviews"] == 2
    assert "Most important per app" in o["display"]


def test_reviews_list(db: Database) -> None:
    seed(db)
    own = today.reviews(db, "Pill Pal", 5, 30, as_of=AS_OF)
    assert own["count"] == 3 and own["by_star"]["5"] == 1
    comp = today.reviews(db, "Pill Pal", 3, 30, competitors=True, as_of=AS_OF)
    assert [r["app"] for r in comp["reviews"]] == ["Medisafe"]
    assert "No reviews of 1★ or less" in today.reviews(db, "Pill Pal", 1, 5, as_of=AS_OF)["display"]


def test_competitor_changes_dedupes_countries(db: Database) -> None:
    a = apps.add(db, "Multi", store_id=1, countries=["us", "gb"])
    apps.add_competitor(db, a["id"], 2, "Rival")
    db.upsert("changes", [{"store_id": 2, "country": c, "date": "2026-11-05", "field": "subtitle", "old": None,
                           "new": "Track pills"} for c in ("us", "gb")], ["store_id", "country", "date", "field"])
    out = today.competitor_changes(db, a, as_of=AS_OF)
    assert len(out["changes"]) == 1 and out["changes"][0]["countries"] == ["us", "gb"]
    assert "(US, GB)" in out["display"]
    assert "no competitors" in today.competitor_changes(db, apps.add(db, "Alone"))["display"]


def test_change_sentences() -> None:
    assert today.change_sentence("X", "price", "0", "2.99") == "X changed its price from free to 2.99"
    assert today.change_sentence("X", "description", "abc", "abcdef") == "X rewrote its description (3 → 6 characters)"
    assert today.change_sentence("X", "screenshots", None, '["a"]') == "X updated its screenshots"


def test_mcp_tools_registered_and_safe_on_empty_db() -> None:
    server = MCPServer("test")
    tools_insights.register(server)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"app_today", "app_stats", "keyword_ranks", "gap_report", "reviews_list", "competitor_changes"}
    result = asyncio.run(server.call_tool("app_today", {}))
    assert "No apps yet" in json.dumps(result.model_dump(), ensure_ascii=False)
    result = asyncio.run(server.call_tool("gap_report", {"app": "nothing"}))
    assert "No app matches" in json.dumps(result.model_dump(), ensure_ascii=False)
