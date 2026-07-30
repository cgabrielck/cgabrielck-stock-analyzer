import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import data_fetcher
from datetime import date, datetime, timedelta, timezone
import pandas as pd


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self):
        return {
            "quoteSummary": {
                "result": [{"financialData": {"debtToEquity": {"raw": 150.0}}}]
            }
        }


def test_yahoo_debt_equity_is_normalized_to_ratio(monkeypatch) -> None:
    monkeypatch.setattr(data_fetcher._REQUEST_SESSION, "get", lambda *args, **kwargs: _Response())

    result = data_fetcher._fetch_yahoo_fundamentals("TEST")

    assert result["debt_equity"] == 1.5


def test_extract_news_supports_new_yahoo_schema() -> None:
    result = data_fetcher._extract_news({"content": {
        "id": "article-1", "title": "Earnings beat", "summary": "Revenue increased",
        "pubDate": "2026-07-22T10:00:00Z", "provider": {"displayName": "Reuters"},
        "canonicalUrl": {"url": "https://example.com/new"}, "contentType": "STORY",
    }})

    assert result["id"] == "article-1"
    assert result["publisher"] == "Reuters"
    assert result["published_at"].startswith("2026-07-22T10:00:00")


def test_extract_news_supports_legacy_yahoo_schema() -> None:
    result = data_fetcher._extract_news({
        "uuid": "legacy", "title": "Legacy story", "publisher": "AP",
        "link": "https://example.com/legacy", "providerPublishTime": 1784714400,
    })

    assert result["id"] == "legacy"
    assert result["publisher"] == "AP"
    assert result["link"].endswith("legacy")
    assert result["published_at"] is not None


def test_calendar_dates_support_current_dict_shape() -> None:
    future = date.today() + timedelta(days=10)

    dates = data_fetcher._calendar_earnings_dates({"Earnings Date": [future]})

    assert dates == [future]


def test_news_relevance_rejects_unrelated_company_story() -> None:
    assert data_fetcher._news_is_relevant("AAPL", "Apple raises guidance", "") is True
    assert data_fetcher._news_is_relevant("AAPL", "Zhongji seeks Hong Kong listing", "China optical company") is False


def test_zero_metrics_count_as_available_except_invalid_peg() -> None:
    metrics = {"revenue_growth": 0, "eps_growth": 0, "profit_margin": 0, "roe": 0, "debt_equity": 0, "peg": 0}

    assert data_fetcher._count_available_metrics(metrics) == 5


def test_seed_as_of_preserves_historical_snapshot_time() -> None:
    value = data_fetcher._seed_as_of({"fetched_at": "2026-07-20 15:23:12"})

    assert value.startswith("2026-07-20T15:23:12")


def test_short_ticker_requires_company_name_or_structured_symbol() -> None:
    assert data_fetcher._news_is_relevant("V", "Visa raises guidance", "") is True
    assert data_fetcher._news_is_relevant("V", "Version V launches", "") is False
    assert data_fetcher._news_is_relevant("V", "$V raises guidance", "") is True
    assert data_fetcher._news_is_relevant("MA", "John Ma joins board", "") is False
    assert data_fetcher._news_is_relevant("MA", "Mastercard expands network", "") is True


def test_news_cutoff_filters_old_missing_and_future_articles(monkeypatch) -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)

    class FakeTicker:
        @property
        def news(self):
            return [
                {"content": {"id": "fresh", "title": "Apple fresh update", "pubDate": "2026-07-21T12:00:00Z"}},
                {"content": {"id": "old", "title": "Apple old update", "pubDate": "2026-07-01T12:00:00Z"}},
                {"content": {"id": "missing", "title": "Apple missing time"}},
                {"content": {"id": "future", "title": "Apple future update", "pubDate": "2026-07-23T12:00:00Z"}},
            ]

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: FakeTicker())
    result = data_fetcher.fetch_news("AAPL", force_refresh=True, now=now)

    assert [item["id"] for item in result] == ["fresh"]


def test_earnings_aware_timestamp_uses_eastern_calendar_date() -> None:
    value = data_fetcher._earnings_date_et(pd.Timestamp("2026-07-23 02:00", tz="UTC"))

    assert value.isoformat() == "2026-07-22"


def test_earnings_today_is_included_with_zero_days() -> None:
    today = date(2026, 7, 22)

    result = data_fetcher._earnings_result([today], "test", today=today)

    assert result["days_until"] == 0


def test_options_cache_and_force_refresh(monkeypatch) -> None:
    calls = []
    frame = pd.DataFrame([{
        "contractSymbol": "TEST260821C00100000", "strike": 100.0, "bid": 2.0, "ask": 2.2,
        "lastPrice": 2.1, "volume": 100, "openInterest": 500,
        "impliedVolatility": 0.25, "inTheMoney": False,
    }])

    class Chain:
        calls = frame
        puts = frame

    class FakeTicker:
        options = ["2026-08-21"]

        def option_chain(self, expiry):
            calls.append(expiry)
            return Chain()

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: FakeTicker())
    data_fetcher.cache.delete("options_v2_TEST_100.0", "info")

    data_fetcher.fetch_options_chain("TEST", 100)
    data_fetcher.fetch_options_chain("TEST", 100)
    data_fetcher.fetch_options_chain("TEST", 100, force_refresh=True)

    assert len(calls) == 2


def test_options_rate_limit_starts_cooldown_even_for_force_refresh(monkeypatch) -> None:
    calls = []

    class RateLimitedTicker:
        @property
        def options(self):
            calls.append("options")
            raise RuntimeError("Too Many Requests. Rate limited. Try after a while.")

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: RateLimitedTicker())
    monkeypatch.setattr(data_fetcher, "fetch_cboe_options_chain", lambda *args: {
        "error": "provider_unavailable", "error_code": "provider_error", "provider_reason": "failed",
    })
    data_fetcher.cache.delete("options_provider_cooldown_v2_LIMITED", "info")
    data_fetcher.cache.delete("options_v2_LIMITED_100.0", "info")

    first = data_fetcher.fetch_options_chain("LIMITED", 100)
    second = data_fetcher.fetch_options_chain("LIMITED", 100)
    forced = data_fetcher.fetch_options_chain("LIMITED", 100, force_refresh=True)

    assert first["error_code"] == "rate_limit"
    assert first["provider_reason"] == "yahoo_rate_limit;cboe_failed"
    assert first["retry_after_seconds"] == 300
    assert second["from_cache"] is True
    assert forced["from_cache"] is True
    assert calls == ["options"]


def test_options_provider_error_does_not_expose_raw_exception(monkeypatch) -> None:
    class BrokenTicker:
        @property
        def options(self):
            raise RuntimeError("secret provider detail")

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: BrokenTicker())
    monkeypatch.setattr(data_fetcher, "fetch_cboe_options_chain", lambda *args: {
        "error": "provider_unavailable", "error_code": "provider_error", "provider_reason": "failed",
    })
    data_fetcher.cache.delete("options_provider_cooldown_v2_BROKEN", "info")
    data_fetcher.cache.delete("options_v2_BROKEN_100.0", "info")

    result = data_fetcher.fetch_options_chain("BROKEN", 100)

    assert result["error"] == "provider_unavailable"
    assert result["error_code"] == "provider_error"
    assert result["provider_reason"] == "yahoo_request_failed;cboe_failed"
    assert "secret" not in str(result)


def test_empty_option_expirations_are_provider_incomplete_and_cached(monkeypatch) -> None:
    calls = []

    class EmptyTicker:
        @property
        def options(self):
            calls.append("options")
            return ()

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: EmptyTicker())
    monkeypatch.setattr(data_fetcher, "fetch_cboe_options_chain", lambda *args: {
        "error": "provider_unavailable", "error_code": "provider_error", "provider_reason": "failed",
    })
    data_fetcher.cache.delete("options_provider_cooldown_v2_EMPTY", "info")
    data_fetcher.cache.delete("options_v2_EMPTY_100.0", "info")

    first = data_fetcher.fetch_options_chain("EMPTY", 100)
    second = data_fetcher.fetch_options_chain("EMPTY", 100, force_refresh=True)

    assert first["error_code"] == "provider_error"
    assert first["provider_reason"] == "empty_expirations;cboe_failed"
    assert second["from_cache"] is True
    assert calls == ["options"]


def test_empty_option_chain_is_provider_incomplete(monkeypatch) -> None:
    class EmptyChain:
        calls = pd.DataFrame()
        puts = pd.DataFrame()

    class EmptyChainTicker:
        options = ["2026-08-21"]

        def option_chain(self, expiry):
            return EmptyChain()

    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: EmptyChainTicker())
    monkeypatch.setattr(data_fetcher, "fetch_cboe_options_chain", lambda *args: {
        "error": "provider_unavailable", "error_code": "provider_error", "provider_reason": "failed",
    })
    data_fetcher.cache.delete("options_provider_cooldown_v2_EMPTYCHAIN", "info")
    data_fetcher.cache.delete("options_v2_EMPTYCHAIN_100.0", "info")

    result = data_fetcher.fetch_options_chain("EMPTYCHAIN", 100)

    assert result["error_code"] == "provider_error"
    assert result["provider_reason"] == "empty_chain;cboe_failed"


def test_empty_yahoo_options_fall_back_to_cboe(monkeypatch) -> None:
    class EmptyTicker:
        options = ()

    cboe = {
        "ticker": "TSLA", "calls": [{"contract_symbol": "TSLA_CALL"}], "puts": [],
        "source": "cboe_delayed_options", "from_cache": False,
    }
    monkeypatch.setattr(data_fetcher.yf, "Ticker", lambda *args, **kwargs: EmptyTicker())
    monkeypatch.setattr(data_fetcher, "fetch_cboe_options_chain", lambda *args: dict(cboe))
    data_fetcher.cache.delete("options_provider_cooldown_v2_TSLA_FALLBACK", "info")
    data_fetcher.cache.delete("options_v2_TSLA_FALLBACK_100.0", "info")

    result = data_fetcher.fetch_options_chain("TSLA_FALLBACK", 100)

    assert result["source"] == "cboe_delayed_options"
    assert result["fallback_from"] == "yfinance_options"
    assert result["fallback_reason"] == "empty_expirations"
