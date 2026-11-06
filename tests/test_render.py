from rankwell import render


def test_sparkline_gaps_and_invert() -> None:
    assert render.sparkline([1, None, 8]) == "▁·█"
    assert render.sparkline([1, 8], invert=True) == "█▁"
    assert render.sparkline([None, None]) == "··"


def test_delta() -> None:
    assert render.delta(3) == "▲ +3"
    assert render.delta(-3, good_when_up=False) == "▼ -3"
    assert render.delta(3, good_when_up=False) == "▲ +3 ⚠"
    assert render.delta(0) == "– 0"
    assert render.delta(None) == "–"


def test_table_escapes_pipes() -> None:
    out = render.table([{"a": "x|y", "b": None}], align_right=["b"])
    assert out.splitlines() == ["| a | b |", "|---|---:|", "| x\\|y | – |"]


def test_number() -> None:
    assert [render.number(n) for n in (5, 1234, 12_345, 2_500_000, None)] == ["5", "1.2k", "12k", "2.5M", "–"]


def test_funnel_conversion() -> None:
    out = render.funnel([("Impressions", 1000), ("Page views", 50), ("Downloads", 5)])
    assert "(5.0%)" in out and "(10.0%)" in out
