import gzip
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from rankwell import apps, collect, secrets
from rankwell.db import Database
from rankwell.errors import StoreError

OWN, COMP, OTHER = 111, 222, 333


@pytest.fixture(autouse=True)
def no_asc_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "has_asc_key", lambda: False)


class FakeStore:
    """The public App Store endpoints, answered from in-memory state."""

    def __init__(self) -> None:
        self.listings: dict[int, dict[str, Any]] = {
            OWN: {"trackId": OWN, "trackName": "Alpha", "version": "1.0", "price": 0.0, "primaryGenreId": 6007,
                  "description": "Plan your day.", "screenshotUrls": ["https://is1-ssl.mzstatic.com/a/1.png"],
                  "userRatingCount": 10, "averageUserRating": 4.5},
            COMP: {"trackId": COMP, "trackName": "Beta", "version": "2.0", "price": 0.0, "primaryGenreId": 6007,
                   "description": "Focus.", "screenshotUrls": [], "userRatingCount": 900},
        }
        self.subtitles: dict[int, str | None] = {OWN: "Daily planner", COMP: "Focus timer"}
        self.search: dict[str, list[int]] = {"planner": [OTHER, OWN, 444], "focus": [COMP, OTHER]}
        self.charts: dict[str, list[int]] = {"topfreeapplications": [OTHER, COMP, OWN],
                                             "topgrossingapplications": [COMP]}
        self.fail_lookup: set[int] = set()
        self.fail_search: set[str] = set()

    def install(self, router: respx.Router) -> None:
        router.get("https://itunes.apple.com/lookup").mock(side_effect=self._lookup)
        router.get("https://itunes.apple.com/search").mock(side_effect=self._search)
        router.get(url__regex=r"https://apps\.apple\.com/\w+/app/id\d+").mock(side_effect=self._page)
        router.get(url__regex=r"https://itunes\.apple\.com/\w+/rss/customerreviews/.*").mock(side_effect=self._reviews)
        router.get(url__regex=r"https://itunes\.apple\.com/\w+/rss/top\w+/.*").mock(side_effect=self._chart)

    def _lookup(self, request: httpx.Request) -> httpx.Response:
        ids = [int(i) for i in request.url.params["id"].split(",")]
        if self.fail_lookup & set(ids):
            return httpx.Response(404)
        return httpx.Response(200, json={"results": [self.listings[i] for i in ids if i in self.listings]})

    def _search(self, request: httpx.Request) -> httpx.Response:
        term = request.url.params["term"]
        if term in self.fail_search:
            return httpx.Response(404)
        return httpx.Response(200, json={"results": [
            {"trackId": i, "trackName": f"App {i}", "userRatingCount": i} for i in self.search.get(term, [])]})

    def _page(self, request: httpx.Request) -> httpx.Response:
        sid = int(str(request.url).rsplit("id", 1)[1])
        sub = self.subtitles.get(sid)
        if sub is None:
            return httpx.Response(404)
        return httpx.Response(200, text=f'<h1>App</h1><p class="subtitle">{sub}</p>')

    def _reviews(self, request: httpx.Request) -> httpx.Response:
        sid = int(str(request.url).split("/id=")[1].split("/")[0])
        if sid in self.fail_lookup:
            return httpx.Response(404)
        entries = [{"id": {"label": f"r{sid}-{n}"}, "im:rating": {"label": str(n)}, "title": {"label": "t"},
                    "content": {"label": "c"}, "author": {"name": {"label": "a"}}, "im:version": {"label": "1.0"},
                    "updated": {"label": "2026-11-01T00:00:00-07:00"}} for n in (4, 5)]
        return httpx.Response(200, json={"feed": {"entry": entries}})

    def _chart(self, request: httpx.Request) -> httpx.Response:
        kind = str(request.url).split("/rss/")[1].split("/")[0]
        entries = [{"id": {"attributes": {"im:id": str(i)}}, "im:name": {"label": f"App {i}"}}
                   for i in self.charts.get(kind, [])]
        return httpx.Response(200, json={"feed": {"entry": entries}})


@pytest.fixture
def store() -> Any:
    fake = FakeStore()
    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        yield fake


def _app(db: Database, name: str = "Alpha", store_id: int | None = OWN, terms: tuple[str, ...] = ("planner", "focus"),
         competitor: int | None = COMP) -> apps.App:
    a = apps.add(db, name, store_id=store_id, countries=["us"])
    apps.add_keywords(db, a["id"], list(terms), "us")
    if competitor:
        apps.add_competitor(db, a["id"], competitor, "Beta")
    return a


def _count(db: Database, table: str) -> int:
    return int(db.value(f"SELECT count(*) FROM {table}"))


def test_snapshots_and_changes(db: Database, store: FakeStore) -> None:
    _app(db)
    first = collect.run(db, on="2026-11-01", asc=lambda: None)
    assert first["status"] == "ok"
    assert first["apps"]["alpha"]["snapshots"] == 2 and first["apps"]["alpha"]["changes"] == 0
    snap = db.one("SELECT * FROM snapshots WHERE store_id = ? AND date = '2026-11-01'", (OWN,))
    assert snap and snap["subtitle"] == "Daily planner" and json.loads(snap["screenshots"])[0].endswith("1.png")

    store.listings[OWN].update(trackName="Alpha Pro", version="1.1",
                               screenshotUrls=["https://is4-ssl.mzstatic.com/a/1.png"])  # same image, other CDN host
    store.subtitles[OWN] = None  # page scrape failed: unknown, not a change
    second = collect.run(db, on="2026-11-02", asc=lambda: None)
    changes = {r["field"]: r for r in db.all("SELECT * FROM changes WHERE date = '2026-11-02'")}
    assert set(changes) == {"title", "version"}
    assert changes["title"]["old"] == "Alpha" and changes["title"]["new"] == "Alpha Pro"
    assert second["apps"]["alpha"]["changes"] == 2

    store.subtitles[OWN] = "Plan better"
    collect.run(db, on="2026-11-03", asc=lambda: None)
    sub = db.one("SELECT * FROM changes WHERE date = '2026-11-03' AND field = 'subtitle'")
    assert sub and sub["old"] == "Daily planner" and sub["new"] == "Plan better"


def test_genre_filled_from_lookup(db: Database, store: FakeStore) -> None:
    a = _app(db)
    collect.run(db, on="2026-11-01", asc=lambda: None)
    assert apps.get(db, a["id"])["genre_id"] == 6007


def test_ranks_include_null_and_top10(db: Database, store: FakeStore) -> None:
    _app(db)
    collect.run(db, on="2026-11-01", asc=lambda: None)
    ranks = {(r["term"], r["store_id"]): r["rank"] for r in db.all("SELECT * FROM ranks")}
    assert ranks == {("planner", OWN): 2, ("planner", COMP): None, ("focus", OWN): None, ("focus", COMP): 1}
    top = db.all("SELECT * FROM keyword_top WHERE term = 'planner' ORDER BY position")
    assert [t["store_id"] for t in top] == [OTHER, OWN, 444] and top[0]["name"] == f"App {OTHER}"


def test_reviews_and_charts(db: Database, store: FakeStore) -> None:
    _app(db)
    out = collect.run(db, on="2026-11-01", asc=lambda: None)["apps"]["alpha"]
    assert out["reviews"] == 4 and _count(db, "reviews") == 4
    charts = {(r["chart"], r["store_id"]): r["rank"] for r in db.all("SELECT * FROM chart_positions")}
    assert charts == {("free", COMP): 2, ("free", OWN): 3, ("grossing", COMP): 1}
    assert out["charts"] == 3


def test_same_day_twice_is_idempotent(db: Database, store: FakeStore) -> None:
    _app(db)
    collect.run(db, on="2026-11-01", asc=lambda: None)
    store.listings[OWN]["trackName"] = "Alpha 2"
    collect.run(db, on="2026-11-02", asc=lambda: None)
    tables = ("snapshots", "changes", "ranks", "keyword_top", "reviews", "chart_positions")
    before = {t: _count(db, t) for t in tables}
    store.charts["topgrossingapplications"] = []  # dropped out of a chart between the two runs
    collect.run(db, on="2026-11-02", asc=lambda: None)
    after = {t: _count(db, t) for t in tables}
    assert after == {**before, "chart_positions": before["chart_positions"] - 1}
    assert _count(db, "runs") == 3


def test_reverted_change_is_removed_on_rerun(db: Database, store: FakeStore) -> None:
    _app(db)
    collect.run(db, on="2026-11-01", asc=lambda: None)
    store.listings[OWN]["version"] = "1.1"
    collect.run(db, on="2026-11-02", asc=lambda: None)
    store.listings[OWN]["version"] = "1.0"
    collect.run(db, on="2026-11-02", asc=lambda: None)
    assert _count(db, "changes") == 0


def test_failing_app_does_not_stop_others(db: Database, store: FakeStore) -> None:
    _app(db, "Broken", store_id=999, terms=("broken",), competitor=None)
    _app(db, "Alpha")
    store.fail_lookup.add(999)
    store.fail_search.add("broken")
    out = collect.run(db, on="2026-11-01", asc=lambda: None)
    assert out["status"] == "partial"
    assert out["apps"]["broken"]["status"] == "failed" and out["apps"]["broken"]["errors"]
    assert out["apps"]["alpha"]["status"] == "ok" and out["apps"]["alpha"]["snapshots"] == 2
    row = db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    assert row and row["status"] == "partial" and json.loads(row["summary"])["apps"]["broken"]["errors"]


def test_failing_step_is_recorded_and_rest_continues(db: Database, store: FakeStore) -> None:
    _app(db)
    store.fail_search.add("focus")
    out = collect.run(db, on="2026-11-01", asc=lambda: None)["apps"]["alpha"]
    assert out["status"] == "partial" and out["keywords"] == 1 and out["reviews"] == 4
    assert any("rank 'focus'" in e for e in out["errors"])


def test_unexpected_crash_in_one_app(db: Database, store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _app(db, "Alpha")
    _app(db, "Gamma", store_id=None, terms=("planner",), competitor=None)
    real = apps.tracked_store_ids

    def boom(d: Database, app: apps.App) -> list[int]:
        if app["slug"] == "alpha":
            raise RuntimeError("bug")
        return real(d, app)

    monkeypatch.setattr(apps, "tracked_store_ids", boom)
    out = collect.run(db, on="2026-11-01", asc=lambda: None)
    assert out["apps"]["alpha"]["status"] == "failed"
    assert out["apps"]["gamma"]["status"] == "ok" and out["apps"]["gamma"]["keywords"] == 1
    assert _count(db, "keyword_top") == 3  # pre-launch app still records who owns the term


def test_catch_up_and_lock(db: Database, store: FakeStore, tmp_path: Path) -> None:
    _app(db)
    lock = tmp_path / "collect.lock"
    first = collect.catch_up(db, lock_file=lock, asc=lambda: None)
    assert first["status"] == "ok" and not collect.is_locked(lock)
    assert "already collected" in collect.catch_up(db, lock_file=lock, asc=lambda: None)["skipped"]

    db.execute("DELETE FROM runs")
    with collect.lock(lock):  # a live collector holds the lock
        assert collect.catch_up(db, lock_file=lock, asc=lambda: None)["skipped"] == "another collection is running"
    lock.write_text("123 2026-01-01T00:00:00Z\n")  # a file left behind by a crash holds no lock
    assert collect.catch_up(db, lock_file=lock, asc=lambda: None)["status"] == "ok"
    assert lock.exists()  # never deleted, only unlocked


def test_lock_raises_when_busy(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with collect.lock(lock):
        assert collect.is_locked(lock)
        assert collect.lock_holder(lock) == os.getpid()
        with pytest.raises(collect.CollectBusy), collect.lock(lock):
            pass
        assert collect.is_locked(lock)  # the failed attempt did not release or delete the holder's lock
    assert not collect.is_locked(lock) and collect.lock_holder(lock) is None


def test_lock_held_by_another_process(db: Database, tmp_path: Path) -> None:
    import subprocess
    import sys

    lock = tmp_path / "x.lock"
    code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT); "
            "fcntl.flock(fd, fcntl.LOCK_EX); os.write(fd, str(os.getpid()).encode()); print('ok', flush=True); "
            "sys.stdin.read()")
    proc = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    try:
        assert proc.stdout and proc.stdout.readline().strip() == "ok"
        assert collect.is_locked(lock) and collect.lock_holder(lock) == proc.pid
        with pytest.raises(collect.CollectBusy), collect.lock(lock):
            pass
        db.execute("INSERT INTO runs (kind, started_at, status) VALUES ('collect', '2026-11-01T10:00:00Z', 'running')")
        assert collect.mark_stale_runs(db, lock) == 0  # the other process is alive: its row stays running
        assert db.value("SELECT status FROM runs") == "running"
    finally:
        proc.communicate("")
    assert not collect.is_locked(lock)  # the kernel released it when the process exited
    with collect.lock(lock):
        pass


def test_stale_running_row_is_marked_failed(db: Database, store: FakeStore, tmp_path: Path) -> None:
    _app(db)
    lock = tmp_path / "collect.lock"
    db.execute("INSERT INTO runs (kind, started_at, status) VALUES ('collect', '2026-11-01T10:00:00Z', 'running')")
    assert collect.mark_stale_runs(db, lock) == 1  # nobody holds the lock: the row was left by a crash
    row = db.one("SELECT * FROM runs WHERE id = 1")
    assert row and row["status"] == "failed" and row["finished_at"]
    assert "crashed" in json.loads(row["summary"])["note"]
    db.execute("UPDATE runs SET status = 'running', summary = NULL")
    out = collect.catch_up(db, lock_file=lock, asc=lambda: None)
    assert out["status"] == "ok"
    assert [r["status"] for r in db.all("SELECT status FROM runs ORDER BY id")] == ["failed", "ok"]


def test_missed_days(db: Database) -> None:
    assert collect.missed_days(db, until="2026-11-07") == []
    for day, status in (("2026-11-01", "ok"), ("2026-11-02", "failed"), ("2026-11-04", "partial")):
        db.execute("INSERT INTO runs (kind, started_at, status, summary) VALUES ('collect', ?, ?, ?)",
                   (day + "T10:00:00Z", status, json.dumps({"date": day})))
    assert collect.missed_days(db, until="2026-11-07") == ["2026-11-02", "2026-11-03", "2026-11-05", "2026-11-06"]
    assert collect.collected_on(db, "2026-11-04") and not collect.collected_on(db, "2026-11-02")


def test_estimate(db: Database) -> None:
    a = _app(db)
    assert collect.estimate_seconds(db, [apps.get(db, a["id"])]) > 2 * 3


def test_no_apps(db: Database) -> None:
    out = collect.run(db, on="2026-11-01", asc=lambda: None)
    assert out["status"] == "ok" and out["apps"] == {}


# App Store Connect analytics

DISCOVERY = "App Store Discovery and Engagement Standard"
DOWNLOADS = "App Downloads Standard"


def test_aggregate() -> None:
    rows = [
        {"Date": "2026-11-01", "Event": "Impression", "Source Type": "App Store search", "Counts": "10"},
        {"Date": "2026-11-01", "Event": "Impression", "Source Type": "App Store browse", "Counts": "5"},
        {"Date": "2026-11-01", "Event": "Page view", "Source Type": "App Store search", "Counts": "3"},
        {"Date": "2026-11-01", "Event": "Other", "Source Type": "x", "Counts": "99"},
        {"Date": "", "Event": "Tap", "Counts": "1"},
        {"Date": "2026-11-01", "Event": "Tap", "Counts": "n/a"},
    ]
    agg = collect.aggregate(DISCOVERY, rows)
    assert agg[("2026-11-01", "impressions", "all")] == 15
    assert agg[("2026-11-01", "impressions", "App Store search")] == 10
    assert agg[("2026-11-01", "page_views", "all")] == 3 and len(agg) == 5
    dl = collect.aggregate(DOWNLOADS, [{"Date": "2026-11-01", "Download Type": "First-time download",
                                        "Source Type": "Web referrer", "Counts": "2"},
                                       {"Date": "2026-11-01", "Download Type": "Redownload", "Counts": "1"}])
    assert dl[("2026-11-01", "first_downloads", "all")] == 2 and dl[("2026-11-01", "redownloads", "unknown")] == 1


def test_report_rows_dedupe_and_daily_recompute(db: Database) -> None:
    rows = [{"Date": "2026-11-01", "Event": "Impression", "Source Type": "App Store search", "Counts": "10"},
            {"Date": "2026-11-02", "Event": "Impression", "Source Type": "App Store search", "Counts": "7"}]
    dates = collect.save_report_rows(db, OWN, DISCOVERY, rows, None)
    collect.save_report_rows(db, OWN, DISCOVERY, rows[:1], None)  # the same day again in another file
    assert dates == {"2026-11-01", "2026-11-02"} and _count(db, "asc_report_rows") == 2
    collect.recompute_daily(db, OWN, dates)
    collect.recompute_daily(db, OWN, dates)
    rows = db.all("SELECT * FROM asc_daily WHERE metric = 'impressions'")
    daily = {(r["date"], r["source"]): r["value"] for r in rows}
    assert daily == {("2026-11-01", "all"): 10, ("2026-11-01", "App Store search"): 10,
                     ("2026-11-02", "all"): 7, ("2026-11-02", "App Store search"): 7}


def test_row_hash_matches_tracker() -> None:
    import hashlib

    data = {"date": "2026-11-01", "counts": "3"}
    expected = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
    assert collect.row_hash(data) == expected


class FakeASC:
    """Just enough of the App Store Connect client for the analytics flow."""

    def __init__(self, in_account: bool = True) -> None:
        self.in_account = in_account
        self.posts: list[dict[str, Any]] = []
        self.requests = [{"id": "req-ongoing", "attributes": {"accessType": "ONGOING"}},
                         {"id": "req-old", "attributes": {"accessType": "ONE_TIME_SNAPSHOT",
                                                          "stoppedDueToInactivity": True}}]

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.in_account:
            raise StoreError("App Store Connect GET → 404", 404)
        return {"data": {"id": str(OWN)}}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append(body)
        created = {"id": f"req-{len(self.posts)}", "attributes": body["data"]["attributes"]}
        self.requests.append(created)
        return {"data": created}

    def get_all(self, path: str, **params: Any) -> list[dict[str, Any]]:
        if path.endswith("/analyticsReportRequests"):
            return list(self.requests)
        if path.endswith("/reports"):
            if "req-ongoing" not in path:
                return []
            return [{"id": "rep-1", "attributes": {"name": DISCOVERY}},
                    {"id": "rep-2", "attributes": {"name": "App Sessions Standard"}}]
        if path.endswith("/instances"):
            assert params["filter[granularity]"] == "DAILY"
            return [{"id": "inst-1", "attributes": {"processingDate": "2026-11-02"}}]
        if path.endswith("/segments"):
            return [{"attributes": {"url": "https://example-bucket.s3.amazonaws.com/seg1"}}]
        raise AssertionError(path)


def test_sync_asc(db: Database) -> None:
    tsv = "Date\tEvent\tSource Type\tCounts\n2026-11-01\tImpression\tApp Store search\t12\n" \
          "2026-11-01\tTap\tApp Store search\t2\n"
    fake = FakeASC()
    with respx.mock:
        seg = respx.get("https://example-bucket.s3.amazonaws.com/seg1").mock(
            return_value=httpx.Response(200, content=gzip.compress(tsv.encode())))
        msg = collect.sync_asc(db, fake, OWN)  # type: ignore[arg-type]
        assert "1 new report files, 2 rows" in msg and "ONE_TIME_SNAPSHOT" in msg
        assert collect.sync_asc(db, fake, OWN).startswith("0 new")  # type: ignore[arg-type]
        assert seg.call_count == 1
    assert [p["data"]["attributes"]["accessType"] for p in fake.posts] == ["ONE_TIME_SNAPSHOT"]
    assert db.value("SELECT value FROM asc_daily WHERE metric = 'impressions' AND source = 'all'") == 12
    assert db.value("SELECT rows FROM asc_instances WHERE id = 'inst-1'") == 2
    audit = db.one("SELECT * FROM audit_log")
    assert audit and audit["kind"] == "asc_report_request" and audit["new"] == "ONE_TIME_SNAPSHOT"


def test_sync_asc_app_not_in_account(db: Database) -> None:
    assert collect.sync_asc(db, FakeASC(in_account=False), OWN).startswith("skipped")  # type: ignore[arg-type]


def test_run_uses_asc_for_live_apps(db: Database, store: FakeStore) -> None:
    _app(db)
    _app(db, "Gamma", store_id=None, terms=(), competitor=COMP)
    calls: list[int] = []

    def fake_sync(d: Database, client: Any, store_id: int) -> str:
        calls.append(store_id)
        return "0 new report files, 0 rows"

    made: list[int] = []

    def factory() -> Any:
        made.append(1)
        return object()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(collect, "sync_asc", fake_sync)
        out = collect.run(db, on="2026-11-01", asc=factory)
    assert calls == [OWN] and made == [1]
    assert out["apps"]["alpha"]["asc"].startswith("0 new") and out["apps"]["gamma"]["asc"] is None


class TwoInstanceASC(FakeASC):
    """Two new report files; the second one's segment download fails."""

    def get_all(self, path: str, **params: Any) -> list[dict[str, Any]]:
        if path.endswith("/instances"):
            return [{"id": "inst-1", "attributes": {"processingDate": "2026-11-02"}},
                    {"id": "inst-2", "attributes": {"processingDate": "2026-11-03"}}]
        if path.endswith("/segments"):
            n = path.split("/")[-2].rsplit("-", 1)[1]
            return [{"attributes": {"url": f"https://example-bucket.s3.amazonaws.com/seg{n}"}}]
        return super().get_all(path, **params)


def test_sync_asc_partial_run_keeps_daily_totals(db: Database) -> None:
    tsv = "Date\tEvent\tSource Type\tCounts\n2026-11-01\tImpression\tApp Store search\t12\n"
    with respx.mock:
        respx.get("https://example-bucket.s3.amazonaws.com/seg1").mock(
            return_value=httpx.Response(200, content=gzip.compress(tsv.encode())))
        respx.get("https://example-bucket.s3.amazonaws.com/seg2").mock(return_value=httpx.Response(403))
        with pytest.raises(StoreError):
            collect.sync_asc(db, TwoInstanceASC(), OWN)  # type: ignore[arg-type]
    # inst-1 is marked done, so the next run skips it: its day must already be in asc_daily
    assert db.value("SELECT count(*) FROM asc_instances") == 1
    assert db.value("SELECT value FROM asc_daily WHERE date = '2026-11-01' AND metric = 'impressions' "
                    "AND source = 'all'") == 12
