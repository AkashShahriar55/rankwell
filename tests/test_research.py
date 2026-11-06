import datetime as dt

from rankwell import research


def fake_suggest(prefix: str, country: str) -> list[str]:
    table = {"pi": ["pinterest"], "pil": ["pill reminder", "pillow"], "pill": ["pill reminder"]}
    return table.get(prefix, [])


def app(name: str, ratings: int, stars: float = 4.7, released: str = "2020-01-01") -> dict:
    return {"trackName": name, "userRatingCount": ratings, "averageUserRating": stars, "releaseDate": released,
            "trackId": hash(name) % 10**9}


def test_popularity_counts_letters_typed() -> None:
    score, typed, pos = research.popularity("pill reminder", "us", fake_suggest)
    assert (typed, pos) == (3, 1)
    assert score == round(100 * (1 - 3 / 13) + 15)
    assert research.popularity("unknown term", "us", fake_suggest) == (0, None, None)


def test_difficulty_weights() -> None:
    top = [app("Pill Reminder Pro", 100_000)] * 10
    score, d = research.difficulty("pill reminder", top)
    assert d["title_match"] == 1.0
    assert score == 100
    assert research.difficulty("x", [])[0] == 0


def test_market_check_verdicts() -> None:
    today = dt.date(2026, 11, 7)
    giants = lambda *a, **k: [app(f"A{i}", 200_000) for i in range(10)]  # noqa: E731
    fresh = lambda *a, **k: [app(f"B{i}", 300, released="2026-06-01") for i in range(10)]  # noqa: E731
    assert research.market_check("x", search=giants, today=today)["verdict"] == "crowded by giants"
    assert research.market_check("x", search=fresh, today=today)["verdict"] == "free space"
    assert research.market_check("x", search=lambda *a, **k: [], today=today)["verdict"] == "no results"


def test_ideas_deduplicates() -> None:
    found = research.ideas("pil", "us", lambda p, c: ["pill reminder", "Pill Reminder"])
    assert [f["term"] for f in found] == ["pill reminder"]
