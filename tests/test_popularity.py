from rankwell import research


def suggests_after(term: str, typed: int, position: int = 1):
    """A fake autocomplete that suggests `term` once `typed` letters of it are typed, at `position`."""
    def suggest(prefix: str, country: str) -> list[str]:
        if term.startswith(prefix) and len(prefix) >= typed:
            return [f"filler {i}" for i in range(position - 1)] + [term]
        return ["something else"]
    return suggest


def test_long_phrase_is_not_rewarded_for_its_length() -> None:
    short, long = "unblur", "photo enhancer for blurry images"
    s, s_typed, _ = research.popularity(short, "us", suggests_after(short, 4))
    lg, l_typed, _ = research.popularity(long, "us", suggests_after(long, 8))
    assert (s_typed, l_typed) == (4, 8)
    # The old proxy (share of the term left untyped) ranked the long phrase far above the short word: 90 vs 48.
    assert round(100 * (1 - 8 / 32) + 15) > round(100 * (1 - 4 / 6) + 15) + 40
    assert s is not None and lg is not None and s > lg


def test_same_letters_typed_scores_the_same_whatever_the_length() -> None:
    assert research.popularity_value(5, 12, 1) == research.popularity_value(5, 30, 1)
    assert research.popularity_value(2, 20, 1) == 100
    values = [research.popularity_value(t, 30, 1) or 0 for t in (2, 3, 4, 5, 6, 8, 10, 13, 16, 20)]
    assert values == sorted(values, reverse=True) and values[-1] < 20


def test_suggested_only_when_fully_typed_is_the_floor() -> None:
    assert research.popularity_value(9, 9, 1) == research.MIN_SUGGESTED
    assert research.popularity_value(10, 14, 1) < research.popularity_value(10, 30, 1)  # type: ignore[operator]


def test_position_lowers_the_score() -> None:
    first, tenth = research.popularity_value(4, 20, 1), research.popularity_value(4, 20, 10)
    assert first is not None and tenth is not None and tenth < first and tenth >= 0.7 * first


def test_never_suggested_is_unknown_not_zero() -> None:
    assert research.popularity_value(None, 10, None) is None
    assert research.popularity("nothing", "us", lambda p, c: []) == (None, None, None)
    assert "unknown" in research.popularity_basis(None, 7, None)
    assert research.popularity_basis(4, 13, 2) == "App Store suggested it after 4 of 13 letters, #2"


def test_score_adds_basis_and_google_demand() -> None:
    term = "pill reminder"
    out = research.score(term, "us", search=lambda *a, **k: [], suggest=suggests_after(term, 3),
                         demand=lambda kw, c: {"typed": 6, "position": 2})
    assert out["popularity"] == research.popularity_value(3, 13, 1)
    assert out["popularity_basis"].startswith("App Store suggested it after 3 of 13")
    assert out["google_demand"] == research.popularity_value(6, 13, 2)
    assert out["google_basis"].startswith("Google suggested it after 6")
    offline = research.score(term, "us", search=lambda *a, **k: [], suggest=suggests_after(term, 3),
                             demand=lambda kw, c: None)
    assert offline["google_demand"] is None and offline["popularity"] == out["popularity"]
    never = research.score(term, "us", search=lambda *a, **k: [], suggest=suggests_after(term, 3),
                           demand=lambda kw, c: {"typed": None, "position": None})
    assert never["google_demand"] is None and "never suggested by Google" in never["google_basis"]
