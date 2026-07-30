import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import cboe_options


def _payload():
    return {
        "timestamp": "2026-07-30 09:46:35",
        "symbol": "TSLA",
        "data": {
            "current_price": 300.62,
            "last_trade_time": "2026-07-30T09:46:34",
            "bid": 300.58,
            "ask": 300.68,
            "options": [
                {
                    "option": "TSLA260821C00300000", "bid": 10.0, "ask": 10.5,
                    "last_trade_price": 10.2, "last_trade_time": "2026-07-30T09:45:00",
                    "volume": 100.0, "open_interest": 500.0, "iv": 0.45,
                    "delta": 0.55, "gamma": 0.02, "theta": -0.1, "vega": 0.2,
                    "rho": 0.05, "theo": 10.25,
                },
                {
                    "option": "TSLA260821P00300000", "bid": 9.5, "ask": 10.0,
                    "last_trade_price": 9.8, "last_trade_time": "2026-07-30T09:44:00",
                    "volume": 50.0, "open_interest": 400.0, "iv": 0.5,
                    "delta": -0.45, "gamma": 0.02, "theta": -0.09, "vega": 0.2,
                    "rho": -0.04, "theo": 9.75,
                },
            ],
        },
    }


def test_cboe_chain_normalizes_occ_contracts_and_greeks(monkeypatch) -> None:
    monkeypatch.setattr(cboe_options, "_fetch_payload", lambda symbol, deadline=None: _payload())

    result = cboe_options.fetch_cboe_options_chain("tsla", 300)

    assert result["source"] == "cboe_delayed_options"
    assert result["delayed"] is True
    assert result["selected_expiry"] == "2026-08-21"
    assert result["atm_strike"] == 300
    assert result["calls"][0]["contract_symbol"] == "TSLA260821C00300000"
    assert result["calls"][0]["option_type"] == "call"
    assert result["calls"][0]["delta"] == 0.55
    assert result["puts"][0]["option_type"] == "put"
    assert result["put_call_ratio"] == 1.0
    assert result["put_call_volume_ratio"] == 0.5
    assert result["provider_timestamp"] == "2026-07-30 09:46:35"
    assert result["fetched_at"].endswith("+00:00")


def test_cboe_single_contract_lookup(monkeypatch) -> None:
    monkeypatch.setattr(cboe_options, "_fetch_payload", lambda symbol: _payload())

    result = cboe_options.fetch_cboe_option_contract("TSLA", "TSLA260821P00300000")

    assert result["bid"] == 9.5
    assert result["ask"] == 10.0
    assert result["timestamp_timezone"] == "America/New_York_assumed"


def test_cboe_missing_contract_is_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(cboe_options, "_fetch_payload", lambda symbol: _payload())

    result = cboe_options.fetch_cboe_option_contract("TSLA", "MISSING")

    assert result["error_code"] == "provider_incomplete"
