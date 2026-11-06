import json

import pytest

from rankwell import apps, stats
from rankwell.db import Database
from rankwell.errors import RankwellError
from rankwell.util import days_ago

END = "2026-11-06"
AS_OF = "2026-11-07"
OWN = 111


def own_ranks(term: str, i: int) -> int | None:
    """i = days before END; each rank changes once, 7 days before END."""
    old = i >= 7
    return {"pill reminder": 30 if old else 12, "medication tracker": 5 if old else 60,
            "pill tracker": None if old else 80, "med alarm": 50 if old else None}[term]


def seed(db: Database) -> dict:
    a = apps.add(db, "Pill Pal", store_id=OWN, countries=["us"])
    apps.add_keywords(db, a["id"], ["pill reminder", "medication tracker", "pill tracker", "med alarm"], "us")
    db.upsert("keyword_scores", [
        {"country": "us", "term": "pill reminder", "date": "2026-11-01", "popularity": 60, "difficulty": 70},
        {"country": "us", "term": "medication tracker", "date": "2026-10-01", "popularity": 10, "difficulty": 50},
        {"country": "us", "term": "medication tracker", "date": "2026-11-01", "popularity": 40, "difficulty": 55},
        {"country": "us", "term": "med alarm", "date": "2026-11-01", "popularity": 0, "difficulty": 20},
    ], ["country", "term", "date"])
    rows = []
    for i in range(14):
        d = days_ago(i, END)
        for t in ("pill reminder", "medication tracker", "pill tracker", "med alarm"):
            rows.append({"date": d, "country": "us", "term": t, "store_id": OWN, "rank": own_ranks(t, i)})
    db.upsert("ranks", rows, ["date", "country", "term", "store_id"])
    db.upsert("snapshots", [
        {"store_id": OWN, "country": "us", "date": days_ago(i, END), "title": "Pill Pal", "rating_count": 40 + 13 - i,
         "rating_avg": 4.2 if i >= 7 else 4.0, "screenshots": json.dumps(["a", "b"])} for i in range(14)
    ], ["store_id", "country", "date"])
    return a


def asc(db: Database, with_all: bool = True) -> None:
    rows = []
    for i in range(14):
        d = days_ago(i, "2026-11-03")
        downloads = 10 if i < 7 else 20
        for src, share in (("App Store search", 0.7), ("App Store browse", 0.3)):
            for metric, v in (("impressions", 1000), ("page_views", 100), ("first_downloads", downloads)):
                rows.append({"store_id": OWN, "date": d, "metric": metric, "source": src, "value": round(v * share)})
        if with_all:
            for metric, v in (("impressions", 1000), ("page_views", 100), ("first_downloads", downloads)):
                rows.append({"store_id": OWN, "date": d, "metric": metric, "source": "all", "value": v})
    db.upsert("asc_daily", rows, ["store_id", "date", "metric", "source"])


def test_bucket_edges() -> None:
    assert [stats.bucket(r) for r in (1, 2, 3, 4, 10, 11, 20, 50, 51, 200, None)] == [
        1.0, 0.7, 0.7, 0.5, 0.3, 0.15, 0.15, 0.05, 0.01, 0.01, 0.0]
    assert stats.keyword_weight(None) == 50
    assert stats.keyword_weight({"popularity": 0}) == 5
    assert stats.keyword_weight({"popularity": 72}) == 72


def test_visibility_math(db: Database) -> None:
    seed(db)
    v = stats.visibility(db, "Pill Pal", as_of=AS_OF)
    # weights: pill reminder 60, medication tracker 40 (newest score), pill tracker 50 (unscored), med alarm 5 (0)
    assert v["weights"] == {"pill reminder": 60, "medication tracker": 40, "pill tracker": 50, "med alarm": 5}
    first, last = v["series"][0], v["series"][-1]
    assert first["score"] == round(100 * (60 * 0.05 + 40 * 0.5 + 5 * 0.05) / 155, 1) == 15.0
    assert last["score"] == round(100 * (60 * 0.15 + 40 * 0.01 + 50 * 0.01) / 155, 1) == 6.4
    assert v["trend"]["change"] == pytest.approx(-8.6)
    assert v["change_7d"]["previous"] == 15.0
    assert "**6.4** / 100" in v["display"]


def test_rank_history_moves(db: Database) -> None:
    seed(db)
    h = stats.rank_history(db, "pill pal", days=30, as_of=AS_OF)
    by = {k["term"]: k for k in h["keywords"]}
    assert by["pill reminder"]["change_7d"] == 18 and by["pill reminder"]["status_7d"] == "up"
    assert by["pill reminder"]["best"] == 12 and by["pill reminder"]["popularity"] == 60
    assert by["medication tracker"]["change_7d"] == -55 and by["medication tracker"]["best"] == 5
    assert by["pill tracker"]["status_7d"] == "new" and by["pill tracker"]["change_7d"] is None
    assert by["med alarm"]["status_7d"] == "lost" and by["med alarm"]["rank"] is None
    assert len(by["pill reminder"]["spark"]) == 14
    assert h["keywords"][0]["term"] == "pill reminder"  # best current rank first
    assert "top 200 for 3 of 4" in h["display"]


def test_rank_history_without_data(db: Database) -> None:
    a = apps.add(db, "Lonely", store_id=999)
    assert "No keywords tracked" in stats.rank_history(db, a)["display"]
    apps.add_keywords(db, a["id"], ["x"], "us")
    r = stats.rank_history(db, a)
    assert r["keywords"] == [] and "rankwell collect" in r["display"]
    assert "No ranks collected" in stats.visibility(db, a)["display"]


def test_funnel_without_asc(db: Database) -> None:
    seed(db)
    f = stats.funnel(db, "Pill Pal", 7, as_of=AS_OF)
    assert f["available"] is False
    assert "No App Store Connect data yet" in f["display"] and "0" not in f["display"].split("yet")[0]


def test_funnel_with_asc(db: Database) -> None:
    seed(db)
    asc(db)
    f = stats.funnel(db, "Pill Pal", 7, as_of=AS_OF)
    assert f["period"] == {"from": "2026-10-28", "to": "2026-11-03", "days_with_data": 7}
    assert f["current"]["impressions"] == 7000 and f["current"]["first_downloads"] == 70
    assert f["current"]["page_view_rate"] == 10.0 and f["current"]["download_rate"] == 10.0
    assert f["current"]["conversion"] == 1.0
    assert f["previous"]["first_downloads"] == 140
    assert f["changes"]["first_downloads"]["change_pct"] == -50.0
    assert [s["source"] for s in f["by_source"]] == ["App Store search", "App Store browse"]
    assert f["by_source"][0]["first_downloads"] == 49
    assert len(f["daily_first_downloads"]) == 7
    assert "By source" in f["display"] and "First downloads" in f["display"]


def test_funnel_sums_sources_without_all_and_no_previous(db: Database) -> None:
    seed(db)
    asc(db, with_all=False)
    f = stats.funnel(db, "Pill Pal", 14, as_of=AS_OF)
    assert f["current"]["impressions"] == 14000
    assert f["previous"] is None and f["changes"]["impressions"]["change_pct"] is None
    assert "no comparison" in f["display"]


def test_ratings_trend(db: Database) -> None:
    seed(db)
    r = stats.ratings(db, "Pill Pal", days=8, as_of=AS_OF)
    assert r["from"] == "2026-10-31" and r["to"] == END
    assert r["count"]["current"] == 53 and r["count"]["change"] == 6
    assert r["average"]["change"] == 0
    full = stats.ratings(db, "Pill Pal", days=30, as_of=AS_OF)
    assert full["average"]["change"] == pytest.approx(-0.2)


def test_charts(db: Database) -> None:
    seed(db)
    assert "No category charts collected" in stats.charts(db, "Pill Pal", as_of=AS_OF)["display"]
    rows = [{"date": days_ago(i, END), "country": "us", "genre_id": 6013, "chart": "free", "store_id": 5, "rank": 1}
            for i in range(5)]
    rows += [{"date": days_ago(i, END), "country": "us", "genre_id": 6013, "chart": "free", "store_id": OWN,
              "rank": 40 - i} for i in (1, 2, 3)]
    db.upsert("chart_positions", rows, ["date", "country", "genre_id", "chart", "store_id"])
    c = stats.charts(db, "Pill Pal", as_of=AS_OF)["charts"][0]
    assert c["rank"] is None and c["best"] == 37 and c["days_in_chart"] == 3 and c["days_collected"] == 5
    assert c["series"] == [None, 37, 38, 39, None]


def test_compare_and_windows() -> None:
    assert stats.compare(150, 100) == {"current": 150, "previous": 100, "change": 50, "change_pct": 50.0}
    assert stats.compare(5, 0)["change_pct"] is None
    assert stats.compare(None, 3)["change"] is None
    assert stats.windows(7, "2026-11-07") == (("2026-11-01", "2026-11-07"), ("2026-10-25", "2026-10-31"))


def test_metric_dispatch_and_concept_app(db: Database) -> None:
    a = apps.add(db, "Concept")
    for m in stats.METRICS:
        out = stats.metric(db, a, m)
        assert "not on the App Store yet" in out["display"]
    with pytest.raises(RankwellError, match="Unknown metric"):
        stats.metric(db, a, "downloads")
