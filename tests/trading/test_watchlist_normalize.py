from backend.api.app import DEFAULT_WATCHLIST, _normalize_watchlist


def test_lone_appl_typo_restores_default_five():
    assert _normalize_watchlist(["APPL"]) == DEFAULT_WATCHLIST


def test_appl_alias_in_mixed_list():
    assert _normalize_watchlist(["APPL", "NVDA"]) == ["AAPL", "NVDA"]


def test_empty_falls_back_to_default():
    assert _normalize_watchlist([]) == DEFAULT_WATCHLIST
