import json

import httpx
import respx

from rankwell import db as dbmod
from rankwell import demand, research
from rankwell.stores import google

SUGGEST = "https://suggestqueries.google.com/complete/search"


def answer(table: dict[str, list[str]]):
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        return httpx.Response(200, json=[q, table.get(q, [])])
    return handler


def test_suggest_parses_and_caches() -> None:
    with respx.mock:
        route = respx.get(SUGGEST).mock(side_effect=answer({"pill": ["pill identifier", "pill reminder app"]}))
        assert google.suggest("Pill", "us") == ["pill identifier", "pill reminder app"]
        assert google.suggest("pill", "us") == ["pill identifier", "pill reminder app"]
        assert route.call_count == 1
        assert route.calls[0].request.url.params["gl"] == "us"


def test_failures_return_none() -> None:
    with respx.mock:
        respx.get(SUGGEST).mock(side_effect=httpx.ConnectError("offline"))
        assert google.suggest("a", "us") is None
    with respx.mock:
        respx.get(SUGGEST).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
        assert google.suggest("b", "us") is None
    with respx.mock:
        respx.get(SUGGEST).mock(return_value=httpx.Response(500))
        assert google.suggest("c", "us") is None
    assert not google.blocked("suggest")


def test_rate_limit_blocks_the_endpoint_for_a_while() -> None:
    with respx.mock:
        route = respx.get(SUGGEST).mock(return_value=httpx.Response(429))
        assert google.suggest("d", "us") is None
        assert google.blocked("suggest")
        assert google.suggest("e", "us") is None
        assert route.call_count == 1


def test_unrouted_request_never_raises() -> None:
    with respx.mock:  # no route at all: respx raises inside the transport
        assert google.suggest("f", "us") is None
        assert google.first_suggested("pill reminder", "us") is None


def test_first_suggested_finds_the_shortest_prefix() -> None:
    table = {"pill reminder"[:n]: ["pill reminder app"] for n in range(6, 14)}
    with respx.mock:
        respx.get(SUGGEST).mock(side_effect=answer(table))
        assert google.first_suggested("pill reminder", "us") == {"typed": 6, "position": 1}
    with respx.mock:
        respx.get(SUGGEST).mock(side_effect=answer({}))
        assert google.first_suggested("zzz qqq", "us") == {"typed": None, "position": None}
    plural = {"passport photo"[:n]: ["passport renewal", "passport photos near me"] for n in range(5, 15)}
    with respx.mock:
        respx.get(SUGGEST).mock(side_effect=answer(plural))
        assert google.first_suggested("passport photo", "us") == {"typed": 5, "position": 2}


def test_google_failure_keeps_scoring() -> None:
    with respx.mock:
        respx.get(SUGGEST).mock(return_value=httpx.Response(503))
        out = research.score("pill reminder", "us", search=lambda *a, **k: [],
                             suggest=lambda p, c: ["pill reminder"] if len(p) >= 4 else [])
    assert out["google_demand"] is None and out["popularity"] == research.popularity_value(4, 13, 1)
    assert "did not answer" in out["google_basis"]


EXPLORE = {"widgets": [{"id": "TIMESERIES", "token": "tok", "request": {"time": "today 12-m"}}]}


def test_interest_reads_trends_and_summarises_direction() -> None:
    weeks = [{"value": [10]} for _ in range(39)] + [{"value": [20]} for _ in range(13)]
    with respx.mock:
        respx.get(google.EXPLORE_URL).mock(return_value=httpx.Response(200, text=")]}'\n" + json.dumps(EXPLORE)))
        multi = respx.get(google.MULTILINE_URL).mock(return_value=httpx.Response(
            200, text=")]}',\n" + json.dumps({"default": {"timelineData": weeks}})))
        out = google.interest("pill reminder", "us")
        assert out == {"weeks": 52, "low_volume": False, "change_pct": 100.0}
        assert multi.calls[0].request.url.params["token"] == "tok"
        assert google.interest("pill reminder", "us") == out  # cached
        assert multi.call_count == 1
    assert google.trend_summary([0, 0, 0]) == {"weeks": 3, "low_volume": True, "change_pct": None}


def test_interest_failure_is_none_and_blocks_after_429() -> None:
    with respx.mock:
        respx.get(google.EXPLORE_URL).mock(return_value=httpx.Response(200, text=")]}'\n{\"widgets\": []}"))
        assert google.interest("a", "us") is None
    with respx.mock:
        respx.get(google.EXPLORE_URL).mock(return_value=httpx.Response(429))
        assert google.interest("b", "us") is None
        assert google.blocked("trends")


def test_weekly_collection_stores_rows_and_skips_recent(db: dbmod.Database) -> None:
    from rankwell import apps
    a = apps.add(db, "Pill Pal", countries=["us"])
    apps.add_keywords(db, a["id"], ["pill reminder", "med alarm", "pill tracker"], "us")
    probe = {"pill reminder": {"typed": 3, "position": 1}, "med alarm": {"typed": None, "position": None},
             "pill tracker": None}
    out = demand.collect(db, "2026-11-07", probe=lambda t, c: probe[t],
                         trends=lambda t, c: {"low_volume": False, "change_pct": 12.5})
    assert out == {"checked": 2, "failed": 1, "due": 1}
    rows = {r["term"]: r for r in db.all("SELECT * FROM google_demand")}
    assert rows["pill reminder"]["demand"] == research.popularity_value(3, 13, 1)
    assert rows["pill reminder"]["trend_change_pct"] == 12.5 and rows["pill reminder"]["trend_low_volume"] == 0
    assert rows["med alarm"]["demand"] is None and rows["med alarm"]["suggested_after_chars"] is None
    assert demand.due(db, "2026-11-10") == [("us", "pill tracker")]
    assert len(demand.due(db, "2026-11-15")) == 3


def test_collection_run_records_extras_and_never_fails_on_google(db: dbmod.Database) -> None:
    from rankwell import apps, collect
    a = apps.add(db, "Concept", countries=["us"])
    apps.add_keywords(db, a["id"], ["pill reminder"], "us")
    with respx.mock:  # App Store search answers; Google is not routed at all
        respx.get("https://itunes.apple.com/search").mock(return_value=httpx.Response(200, json={"results": []}))
        out = collect.run(db, on="2026-11-07", asc=lambda: None)
    assert out["status"] == "ok"
    summary = json.loads(db.value("SELECT summary FROM runs"))
    assert summary["extras"]["google_demand"] == {"checked": 0, "failed": 1, "due": 1}
    assert summary["extras"]["download_curves"] == {"points": 0, "curves": 0, "fitted_on": "2026-11-07"}
    assert demand.due(db, "2026-11-07", []) == []
