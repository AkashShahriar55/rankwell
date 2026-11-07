import datetime as dt
import math

from rankwell import apps, estimates, gap
from rankwell.db import Database

OWN, COMP = 111, 222
GENRE = 6008


def day(i: int) -> str:
    return (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()


def seed(db: Database, ranks: list[int], a: float = 5000.0, b: float = -0.8, noise: float = 0.0,
         country: str = "us") -> dict:
    """Own app charting at `ranks` (one per day) with first downloads = a × rank^b (× a small wobble)."""
    app = apps.add(db, "Snap", store_id=OWN, countries=[country])
    apps.add_competitor(db, app["id"], COMP, "Rival Cam")
    rows, charts = [], []
    for i, r in enumerate(ranks):
        n = a * r ** b * math.exp(noise * (1 if i % 2 else -1))
        rows.append({"store_id": OWN, "report_name": "App Downloads Standard", "report_date": day(i),
                     "row_hash": f"h{i}", "data": {"date": day(i), "counts": f"{n:.3f}", "territory": country.upper(),
                                                   "download type": "First-time download"}})
        rows.append({"store_id": OWN, "report_name": "App Downloads Standard", "report_date": day(i),
                     "row_hash": f"r{i}", "data": {"date": day(i), "counts": "100000", "territory": country.upper(),
                                                   "download type": "Redownload"}})
        charts.append({"date": day(i), "country": country, "genre_id": GENRE, "chart": "free", "store_id": OWN,
                       "rank": r})
    db.upsert("asc_report_rows", rows, ["store_id", "report_name", "row_hash"])
    db.upsert("chart_positions", charts, ["date", "country", "genre_id", "chart", "store_id"])
    return app


def test_fit_recovers_a_power_law() -> None:
    pts = [(r, 5000 * r ** -0.8) for r in (3, 7, 12, 25, 40, 80)]
    c = estimates.fit(pts)
    assert c is not None and not c["slope_fixed"]
    assert abs(c["b"] + 0.8) < 1e-9 and abs(c["a"] - 5000) < 1e-6
    assert c["sigma"] == estimates.MIN_SIGMA_FIT  # a perfect fit still gets a floor on the range


def test_fit_falls_back_to_the_assumed_slope_when_thin_or_nonsense() -> None:
    assert estimates.fit([(5, 100), (6, 90)]) is None
    thin = estimates.fit([(5, 100), (6, 90), (7, 85)])
    assert thin is not None and thin["slope_fixed"] and thin["b"] == estimates.ASSUMED_SLOPE
    assert thin["sigma"] >= estimates.MIN_SIGMA_FIXED
    rising = estimates.fit([(r, 10 * r) for r in (2, 5, 10, 20, 50)])  # more downloads at worse ranks
    assert rising is not None and rising["slope_fixed"]


def test_fit_all_stores_chart_and_global_curves(db: Database) -> None:
    seed(db, [3, 7, 12, 25, 40, 80], noise=0.1)
    out = estimates.fit_all(db, "2026-11-07")
    assert out == {"points": 6, "curves": 2, "fitted_on": "2026-11-07"}
    rows = {r["key"]: r for r in db.all("SELECT * FROM download_curves")}
    chart = rows[f"free/{GENRE}/us"]
    assert abs(chart["b"] + 0.8) < 0.1 and chart["points"] == 6 and chart["rank_min"] == 3
    assert rows["global"]["sigma"] > chart["sigma"]  # pooled curve has the wider range


def test_redownloads_and_other_countries_do_not_count(db: Database) -> None:
    seed(db, [3, 7, 12, 25, 40, 80])
    pts = estimates.training_points(db)
    assert all(p["downloads"] < 100000 for p in pts) and {p["country"] for p in pts} == {"us"}


def test_download_estimate_for_a_competitor_is_labelled(db: Database) -> None:
    seed(db, [3, 7, 12, 25, 40, 80])
    db.upsert("chart_positions", [{"date": "2026-11-06", "country": "us", "genre_id": GENRE, "chart": "free",
                                   "store_id": COMP, "rank": 10}], ["date", "country", "genre_id", "chart", "store_id"])
    out = estimates.download_estimate(db, "rival", as_of="2026-11-07")
    e = out["estimates"][0]["estimate"]
    assert out["store_id"] == COMP and e["label"] == "estimate" and e["points"] == 6
    assert e["low"] < e["estimate"] < e["high"]
    assert abs(e["estimate"] - 5000 * 10 ** -0.8) < 1
    assert "estimate" in out["display"] and "6 days of your own downloads" in out["display"]
    chart = estimates.curves(db, "2026-11-07")[0]
    near, far = estimates.predict(chart, 4), estimates.predict(chart, 400)
    assert not near["extrapolated"] and far["extrapolated"] and "outside" in far["basis"]
    assert far["high"] / far["estimate"] > near["high"] / near["estimate"]


def test_thin_data_says_it_needs_more(db: Database) -> None:
    app = apps.add(db, "Snap", store_id=OWN, countries=["us"])
    apps.add_competitor(db, app["id"], COMP, "Rival Cam")
    db.upsert("asc_daily", [{"store_id": OWN, "date": day(i), "metric": "first_downloads", "source": "all",
                             "value": 2} for i in range(4)], ["store_id", "date", "metric", "source"])
    db.upsert("chart_positions", [{"date": "2026-11-06", "country": "us", "genre_id": GENRE, "chart": "free",
                                   "store_id": COMP, "rank": 10}], ["date", "country", "genre_id", "chart", "store_id"])
    out = estimates.download_estimate(db, str(COMP), as_of="2026-11-07")
    assert out["estimates"][0]["estimate"] is None
    assert out["data"] == {"own_apps_live": 1, "asc_download_days": 4, "own_chart_days": 0, "points": 0,
                           "needed": estimates.MIN_POINTS, "needed_for_slope": estimates.MIN_FIT}
    assert "need more data" in out["display"] and "4 app-days" in out["display"]
    db.upsert("snapshots", [{"store_id": COMP, "country": "us", "date": "2026-11-06", "title": "Rival Cam"}],
              ["store_id", "country", "date"])
    report = gap.report(db, "snap", as_of="2026-11-07")
    assert "**Downloads (estimate)**: Download estimates need more data" in report["display"]
    assert report["competitors"][0]["downloads_estimate"]["estimate"] is None


def test_not_charted_has_nothing_to_estimate(db: Database) -> None:
    seed(db, [3, 7, 12])
    out = estimates.download_estimate(db, "Rival Cam", as_of="2026-11-07")
    assert out["estimates"] == [] and "no free or paid category chart position" in out["display"]


def test_gap_report_shows_competitor_estimates(db: Database) -> None:
    seed(db, [3, 7, 12, 25, 40, 80])
    db.upsert("snapshots", [{"store_id": sid, "country": "us", "date": "2026-11-06", "title": name}
                            for sid, name in ((OWN, "Snap"), (COMP, "Rival Cam"))], ["store_id", "country", "date"])
    db.upsert("chart_positions", [
        {"date": "2026-11-06", "country": "us", "genre_id": GENRE, "chart": c, "store_id": COMP, "rank": r}
        for c, r in (("grossing", 2), ("free", 10))], ["date", "country", "genre_id", "chart", "store_id"])
    out = gap.report(db, "snap", as_of="2026-11-07")
    rival = next(x for x in out["competitors"] if x["store_id"] == COMP)
    d = rival["downloads_estimate"]
    assert d["chart"] == "free" and d["rank"] == 10 and d["estimate"]["label"] == "estimate"
    assert "Downloads (estimate, not App Store numbers" in out["display"]
    assert "#10 free" in out["display"] and "6 days of your own downloads" in out["display"]


def test_download_estimate_tool_is_registered() -> None:
    import asyncio

    from rankwell.mcp import server
    names = {t.name for t in asyncio.run(server.build().list_tools())}
    assert "download_estimate" in names
