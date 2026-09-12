from backend.trading.engine.fetch_policy import (
    parse_universe_cap,
    symbols_to_fetch,
)


def test_parse_universe_cap_full_by_default():
    assert parse_universe_cap("", 74) == 74
    assert parse_universe_cap("all", 74) == 74
    assert parse_universe_cap("0", 74) == 74


def test_parse_universe_cap_clamps():
    assert parse_universe_cap("20", 74) == 20
    assert parse_universe_cap("200", 74) == 74
    assert parse_universe_cap("abc", 10) == 10


def test_symbols_to_fetch_includes_held_outside_cap():
    names = symbols_to_fetch(["AAPL", "MSFT", "NVDA", "TSM"], ["GOOG"], cap=2)
    assert names[0] == "GOOG"
    assert "AAPL" in names and "MSFT" in names
    assert "NVDA" not in names
    assert len(names) == 3
