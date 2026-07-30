from typing import Any, Dict, List


def evaluate_option_risk(quote: Dict[str, Any], rule_data: Dict[str, Any]) -> Dict[str, Any]:
    """Produce deterministic risk gates and an auditable agent-stage result."""
    checks = {
        "quote_available": bool(quote.get("available")),
        "quote_fresh": not bool(quote.get("stale")),
        "not_delayed": not bool(quote.get("delayed")),
        "spread_acceptable": (
            quote.get("spread_pct") is not None and float(quote["spread_pct"]) <= 20
        ),
        "open_interest_acceptable": int(quote.get("open_interest") or 0) >= 100,
        "volume_acceptable": int(quote.get("volume") or 0) >= 25,
        "contract_matches": quote.get("contract_symbol") == rule_data.get("monitor_symbol"),
    }
    violations: List[str] = [name for name in ("quote_available", "quote_fresh", "not_delayed", "spread_acceptable", "contract_matches") if not checks[name]]
    warnings: List[str] = []
    if not checks["open_interest_acceptable"] and not checks["volume_acceptable"]:
        warnings.append("low_option_liquidity")
    status = "rejected" if violations else "approved_with_warnings" if warnings else "approved"
    return {
        "agent": "options_risk_judge",
        "status": status,
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
        "hard_gate_passed": not violations,
    }


def build_option_agent_trace(
    contract: Dict[str, Any], expiry: Any, entry: float, stop: float, targets: List[float],
    underlying_invalidation: Any,
) -> List[Dict[str, Any]]:
    """Build a deterministic stage trace for the proposed option plan."""
    spread = contract.get("spread_pct")
    open_interest = int(contract.get("open_interest") or 0)
    volume = int(contract.get("volume") or 0)
    iv = contract.get("implied_volatility")
    quote_time = contract.get("last_trade_time")
    source = contract.get("source") or "yfinance_option_chain"
    delayed = bool(contract.get("delayed"))
    liquidity_warnings = []
    if spread is None or float(spread) > 20:
        liquidity_warnings.append("spread_unacceptable")
    if open_interest < 100 and volume < 25:
        liquidity_warnings.append("low_option_liquidity")
    max_loss = round(entry * 100, 2)
    breakeven = None
    strike = contract.get("strike")
    option_type = contract.get("option_type")
    if strike is not None:
        breakeven = round(float(strike) + entry, 2) if option_type == "call" else round(float(strike) - entry, 2)

    hard_violations = list(liquidity_warnings)
    if delayed:
        hard_violations.append("delayed_data_not_actionable")
    return [
        {
            "stage": "option_data",
            "status": "done" if quote_time else "warning",
            "as_of": quote_time,
            "source": source,
            "data_gaps": [] if quote_time else ["contract_quote_time_unavailable"],
            "warnings": ["delayed_option_data"] if delayed else [],
        },
        {
            "stage": "liquidity_agent",
            "status": "warning" if liquidity_warnings else "done",
            "result": {"spread_pct": spread, "open_interest": open_interest, "volume": volume},
            "warnings": liquidity_warnings,
        },
        {
            "stage": "volatility_agent",
            "status": "done" if iv is not None else "skipped",
            "result": {"implied_volatility": iv},
            "data_gaps": [] if iv is not None else ["implied_volatility_unavailable"],
        },
        {
            "stage": "payoff_agent",
            "status": "done",
            "result": {
                "max_loss_per_contract": max_loss,
                "entry": entry,
                "stop": stop,
                "targets": targets,
                "breakeven_at_expiry": breakeven,
                "underlying_invalidation": underlying_invalidation,
            },
        },
        {
            "stage": "event_risk_agent",
            "status": "skipped",
            "result": {"expiry": expiry},
            "data_gaps": ["verified_earnings_calendar_unavailable"],
        },
        {
            "stage": "risk_judge",
            "status": "rejected" if hard_violations else "approved_with_warnings" if not quote_time else "approved",
            "hard_gate_passed": not hard_violations,
            "violations": hard_violations,
            "warnings": ["manual_entry_confirmation_required"],
        },
    ]
