from backend.utils.provenance.trade_log import (
    from_audit_event,
    from_order,
    journal_provenance,
    looks_like_verified_fake,
    merge_trade_logs,
    normalize_trade_log,
    reason_codes_from_text,
)


def test_legacy_portfolio_row_is_note_not_fill():
    entry = normalize_trade_log({
        "date": "2026-10-01T00:00:00+00:00",
        "ticker": "AAPL",
        "action": "buy",
        "price": 190.0,
        "shares": 10,
        "reason": "Portfolio allocation 5.0%",
    })
    assert entry is not None
    assert entry["schema_version"] == "trade_log.v0"
    assert entry["symbol"] == "AAPL"
    assert entry["status"] == "proposed"
    assert entry["fills"] == []
    assert entry["kind"] == "note"
    assert entry["provenance"]["verified"] is False
    assert entry["provenance"]["live"] is False
    assert entry["provenance"]["source"] == "trade_journal"


def test_risk_reject_order_has_reason_codes():
    entry = from_order({
        "id": "ord-1",
        "symbol": "NVDA",
        "side": "buy",
        "quantity": 4,
        "status": "rejected",
        "error_message": "Risk Gate Rejected: Order notional $12,000 exceeds limit $10,000.",
        "created_at": "2026-10-21T12:00:00+00:00",
        "updated_at": "2026-10-21T12:00:00+00:00",
    })
    assert entry is not None
    assert entry["status"] == "risk_rejected"
    assert entry["kind"] == "reject"
    assert "risk_rejected" in entry["reason_codes"]
    assert "notional_cap" in entry["reason_codes"]
    assert entry["fills"] == []
    assert entry["order_id"] == "ord-1"
    assert entry["provenance"]["source"] == "order_store"
    assert entry["provenance"]["verified"] is False


def test_submitted_order_does_not_invent_fill():
    entry = from_order({
        "id": "ord-2",
        "symbol": "MSFT",
        "side": "buy",
        "quantity": 1,
        "status": "submitted",
        "limit_price": 400.0,
        "filled_avg_price": None,
        "created_at": "2026-10-21T12:00:00+00:00",
    })
    assert entry is not None
    assert entry["status"] == "submitted"
    assert entry["fills"] == []


def test_filled_only_when_price_recorded():
    missing = from_order({
        "id": "ord-3",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 2,
        "status": "filled",
        "filled_avg_price": None,
    })
    assert missing is not None
    assert missing["status"] == "filled"
    assert missing["fills"] == []

    real = from_order({
        "id": "ord-4",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 2,
        "status": "filled",
        "filled_avg_price": 189.5,
    })
    assert real["fills"] == [{"price": 189.5, "qty": 2.0}]


def test_audit_risk_reject_becomes_journal_row():
    entry = from_audit_event({
        "schema_version": "audit.v0",
        "event_id": "aud-9",
        "ts": "2026-10-21T13:00:00+00:00",
        "kind": "risk_reject",
        "event_type": "risk_decision",
        "symbol": "TSLA",
        "side": "buy",
        "quantity": 3,
        "approved": False,
        "reason": "Insufficient buying power. Need $8000, have $100.",
    })
    assert entry is not None
    assert entry["status"] == "risk_rejected"
    assert "buying_power" in entry["reason_codes"]
    assert entry["links"]["audit_ref"] == "/api/journal/audit/aud-9"
    assert entry["provenance"]["source"] == "audit_jsonl"


def test_kill_heartbeat_without_reason_skipped():
    assert from_audit_event({
        "event_type": "kill_switch_check",
        "halted": False,
        "ts": "2026-10-21T13:00:00+00:00",
    }) is None


def test_merge_dedupes_order_and_audit():
    rows = merge_trade_logs(
        [],
        [{"id": "ord-5", "symbol": "META", "side": "buy", "quantity": 1, "status": "rejected",
          "error_message": "Risk Gate Rejected: Too large", "updated_at": "2026-10-21T14:00:00+00:00"}],
        [{"event_id": "aud-5", "kind": "risk_reject", "symbol": "META", "order_id": "ord-5",
          "approved": False, "reason": "Too large", "ts": "2026-10-21T14:00:01+00:00"}],
        limit=20,
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "META"


def test_empty_merge_is_empty():
    assert merge_trade_logs([], [], [], limit=10) == []


def test_reason_codes_from_text():
    codes = reason_codes_from_text("Risk Gate Rejected: Daily loss halt triggered", extra=["risk_rejected"])
    assert codes[0] == "risk_rejected"
    assert "daily_loss" in codes


def test_provenance_never_claims_verified_for_local_sources():
    p = journal_provenance(source="audit_jsonl", as_of="2026-10-21T00:00:00+00:00")
    assert p["verified"] is False
    assert p["live"] is False
    assert p["vendor"] is None
    assert looks_like_verified_fake({"source": "order_store", "verified": True}) is True
    assert looks_like_verified_fake(p) is False
