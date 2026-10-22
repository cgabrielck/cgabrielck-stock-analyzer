"""TradeLogEntry / AuditEvent v0 helpers (MODULAR_ARCHITECTURE §5.2 / §5.4).

Materializes existing journal JSON, order-store rows, and risk audit jsonl
into one user-visible shape. Never invents fills. Provenance is honest:
local ledger sources are never marked verified or live.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

TRADE_LOG_SCHEMA = "trade_log.v0"
AUDIT_SCHEMA = "audit.v0"

TRADE_STATUSES = (
    "proposed",
    "risk_rejected",
    "vetoed",
    "submitted",
    "filled",
    "canceled",
    "shadow",
)

AUDIT_KINDS = (
    "risk_reject",
    "risk_allow",
    "kill",
    "resume",
    "mandate_deny",
    "order_submit",
    "order_fill",
    "config_change",
)

# Heartbeat kill checks are too noisy for the desk journal.
JOURNAL_AUDIT_KINDS = frozenset(
    {"risk_reject", "mandate_deny", "order_submit", "order_fill", "kill", "resume"}
)

_REASON_PATTERNS: Sequence[Tuple[str, str]] = (
    ("cool-down", "cooldown"),
    ("cooldown", "cooldown"),
    ("notional", "notional_cap"),
    ("buying power", "buying_power"),
    ("concentration", "concentration"),
    ("exposure", "exposure_cap"),
    ("already holding", "max_positions"),
    ("daily loss", "daily_loss"),
    ("correlated", "correlation"),
    ("correlation", "correlation"),
    ("kill", "kill_switch"),
    ("mandate", "mandate"),
    ("portfolio value is zero", "invalid_equity"),
    ("reference price", "no_price"),
    ("risk gate", "risk_rejected"),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal_provenance(
    *,
    source: str,
    as_of: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    vendor: Optional[str] = None,
) -> Dict[str, Any]:
    """Honest journal/audit provenance. Never claims verified or live."""
    src = str(source or "unknown").strip() or "unknown"
    default_note = {
        "trade_journal": "Local trade_journal.json — operator/research note, not a broker fill.",
        "order_store": "Local order ledger — status as stored; not a live confirmation.",
        "audit_jsonl": "Risk/safety audit jsonl shared with M8 — not a fill.",
        "unknown": "Source unknown — not verified.",
    }.get(src, "Local desk ledger — not a live broker confirmation.")
    payload: Dict[str, Any] = {
        "source": src,
        "vendor": vendor,  # None unless a real vendor was recorded on this event
        "as_of": as_of,
        "verified": False,
        "live": False,
        "note": note or default_note,
    }
    if extra:
        for key, value in extra.items():
            if key in {"verified", "live", "source", "vendor"}:
                continue
            if value is not None:
                payload[key] = value
    return payload


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    return dict(getattr(obj, "__dict__", {}) or {})


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    data = obj if isinstance(obj, dict) else None
    for name in names:
        if data is not None and data.get(name) not in (None, ""):
            return data.get(name)
        if not isinstance(obj, dict) and hasattr(obj, name):
            val = getattr(obj, name)
            if val not in (None, ""):
                return val.value if hasattr(val, "value") else val
    return default


def _iso(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value)
    return text or None


def new_log_id() -> str:
    return str(uuid.uuid4())


def stable_id(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"tl-{digest}"


def display_kind(status: str) -> str:
    if status in ("risk_rejected", "vetoed"):
        return "reject"
    if status == "filled":
        return "fill"
    if status in ("submitted", "canceled", "shadow"):
        return "order"
    return "note"


def reason_codes_from_text(reason: Optional[str], *, extra: Optional[Iterable[str]] = None) -> List[str]:
    codes: List[str] = []
    seen: Set[str] = set()

    def add(code: Optional[str]) -> None:
        token = str(code or "").strip()
        if not token or token in seen:
            return
        seen.add(token)
        codes.append(token)

    for code in extra or []:
        add(code)
    text = str(reason or "").strip()
    if not text:
        return codes
    low = text.lower()
    for needle, code in _REASON_PATTERNS:
        if needle in low:
            add(code)
    return codes


def _status_from_order(order: Any) -> str:
    raw = str(_field(order, "status", default="") or "").lower()
    error = str(_field(order, "error_message", "error", default="") or "")
    low_err = error.lower()
    if raw == "rejected" or "risk gate rejected" in low_err:
        if "risk" in low_err or "mandate" in low_err or "kill" in low_err:
            return "risk_rejected"
        return "canceled"
    if raw == "filled":
        return "filled"
    if raw in ("cancelled", "canceled", "expired"):
        return "canceled"
    if raw in ("submitted", "accepted", "partially_filled", "risk_approved"):
        return "submitted"
    if raw in TRADE_STATUSES:
        return raw
    return "proposed"


def _recorded_fill_price(*sources: Any) -> Optional[float]:
    """Return a real fill price, or None. Limit/mark prices do not count."""
    for source in sources:
        if source is None:
            continue
        for key in ("filled_avg_price", "fill_price", "filled_price"):
            raw = _field(source, key)
            if raw in (None, "", 0, 0.0):
                continue
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
    return None


def _fills_from_price(price: Optional[float], qty: Any) -> List[Dict[str, Any]]:
    """Only materialize a fill when a recorded price exists. Never invent."""
    if price is None:
        return []
    try:
        return [{"price": float(price), "qty": float(qty or 0)}]
    except (TypeError, ValueError):
        return []


def _filled_status(fill_price: Optional[float], *, fallback: str = "submitted") -> str:
    return "filled" if fill_price is not None else fallback


def empty_trade_log(
    *,
    log_id: Optional[str] = None,
    ts: Optional[str] = None,
    symbol: str = "",
    status: str = "proposed",
) -> Dict[str, Any]:
    return {
        "schema_version": TRADE_LOG_SCHEMA,
        "log_id": log_id or new_log_id(),
        "ts": ts or utc_now_iso(),
        "symbol": str(symbol or "").upper(),
        "card_id": None,
        "strategy_id": None,
        "intent_id": None,
        "side": None,
        "qty": 0,
        "order_id": None,
        "status": status if status in TRADE_STATUSES else "proposed",
        "risk_decision_id": None,
        "veto_codes": [],
        "conviction_tier": None,
        "fills": [],
        "r_multiple": None,
        "user_visible_summary": "",
        "links": {"audit_ref": "", "signal_ref": "", "intent_ref": ""},
        "reason_codes": [],
        "kind": display_kind(status if status in TRADE_STATUSES else "proposed"),
        "provenance": journal_provenance(source="unknown"),
    }


def normalize_trade_log(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce a journal file row (v0 or legacy note / portfolio row) into TradeLogEntry."""
    if not isinstance(raw, dict):
        return None
    if raw.get("schema_version") == TRADE_LOG_SCHEMA and raw.get("log_id") and raw.get("symbol"):
        entry = empty_trade_log(
            log_id=str(raw["log_id"]),
            ts=_iso(raw.get("ts")) or utc_now_iso(),
            symbol=str(raw.get("symbol") or ""),
            status=str(raw.get("status") or "proposed"),
        )
        entry.update(
            {
                "card_id": raw.get("card_id"),
                "strategy_id": raw.get("strategy_id"),
                "intent_id": raw.get("intent_id"),
                "side": _side(raw.get("side")),
                "qty": _qty(raw.get("qty") if raw.get("qty") is not None else raw.get("quantity")),
                "order_id": raw.get("order_id"),
                "risk_decision_id": raw.get("risk_decision_id"),
                "veto_codes": list(raw.get("veto_codes") or []),
                "conviction_tier": raw.get("conviction_tier"),
                "fills": list(raw.get("fills") or []),
                "r_multiple": raw.get("r_multiple"),
                "user_visible_summary": str(
                    raw.get("user_visible_summary") or raw.get("note") or raw.get("reason") or ""
                ),
                "links": {
                    "audit_ref": (raw.get("links") or {}).get("audit_ref") or "",
                    "signal_ref": (raw.get("links") or {}).get("signal_ref") or "",
                    "intent_ref": (raw.get("links") or {}).get("intent_ref") or "",
                },
                "reason_codes": list(raw.get("reason_codes") or []),
            }
        )
        entry["kind"] = display_kind(entry["status"])
        prov = raw.get("provenance")
        entry["provenance"] = _honest_provenance(prov, source="trade_journal", as_of=entry["ts"])
        if entry["status"] != "filled":
            entry["fills"] = []
        return entry

    symbol = str(raw.get("symbol") or raw.get("ticker") or "").upper()
    if not symbol:
        return None
    ts = _iso(raw.get("ts") or raw.get("date") or raw.get("created_at")) or utc_now_iso()
    action = str(raw.get("action") or raw.get("side") or "").lower()
    side = _side(action if action in ("buy", "sell") else raw.get("side"))
    note = str(raw.get("note") or raw.get("reason") or raw.get("user_visible_summary") or "").strip()
    qty = _qty(raw.get("qty") if raw.get("qty") is not None else raw.get("shares") or raw.get("quantity"))
    # Legacy portfolio_manager rows are research allocations, not broker fills.
    status = "proposed"
    entry = empty_trade_log(
        log_id=str(raw.get("id") or raw.get("log_id") or stable_id("note", symbol, ts, note)),
        ts=ts,
        symbol=symbol,
        status=status,
    )
    entry.update(
        {
            "side": side,
            "qty": qty,
            "user_visible_summary": note or f"Journal note for {symbol}",
            "reason_codes": list(raw.get("reason_codes") or []),
            "provenance": journal_provenance(
                source="trade_journal",
                as_of=ts,
                note="Research/portfolio note — not a broker fill.",
            ),
        }
    )
    entry["kind"] = "note"
    return entry


def from_order(
    order: Any,
    *,
    audit_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    link_intent: bool = True,
    provenance_extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    data = _as_dict(order)
    if not data and order is None:
        return None
    symbol = str(_field(order, "symbol", default="") or "").upper()
    if not symbol:
        return None
    status = _status_from_order(order)
    ts = (
        _iso(_field(order, "updated_at", "submitted_at", "filled_at", "created_at", "failed_at"))
        or utc_now_iso()
    )
    order_id = str(_field(order, "id", "order_id", default="") or "") or None
    error = str(_field(order, "error_message", "error", default="") or "")
    side = _side(_field(order, "side"))
    qty = _qty(_field(order, "quantity", "qty", default=0))
    fill_px = _recorded_fill_price(order, data)
    if status == "filled":
        status = _filled_status(fill_px)
    extra_codes = ["risk_rejected"] if status == "risk_rejected" else []
    codes = reason_codes_from_text(error, extra=extra_codes)
    summary = error or _order_summary(symbol, side, qty, status)
    entry = empty_trade_log(
        log_id=stable_id("order", order_id or symbol, status, ts),
        ts=ts,
        symbol=symbol,
        status=status,
    )
    audit_ref = f"/api/journal/audit/{audit_id}" if audit_id else ""
    linked_intent = intent_id or _field(order, "intent_id")
    # H1 journals link to IntentStore. Desk PATH stamps a journal-only
    # correlation id and must not invent /api/intents/{id} (that document
    # was never written — Intent v0 forbids stuffing this L2 order in).
    intent_ref = (
        f"/api/intents/{linked_intent}" if linked_intent and link_intent else ""
    )
    extra: Dict[str, Any] = {}
    if order_id:
        extra["order_id"] = order_id
    if provenance_extra:
        extra.update(provenance_extra)
    entry.update(
        {
            "strategy_id": strategy_id or _field(order, "strategy_id"),
            "intent_id": linked_intent,
            "side": side,
            "qty": qty,
            "order_id": order_id,
            "risk_decision_id": audit_id,
            "fills": _fills_from_price(fill_px, qty) if status == "filled" else [],
            "user_visible_summary": summary,
            "links": {"audit_ref": audit_ref, "signal_ref": "", "intent_ref": intent_ref},
            "reason_codes": codes,
            "kind": display_kind(status),
            "provenance": journal_provenance(
                source="order_store",
                as_of=ts,
                extra=extra or None,
            ),
        }
    )
    return entry


def from_audit_event(event: Any) -> Optional[Dict[str, Any]]:
    data = _as_dict(event)
    if not data:
        return None
    kind = str(data.get("kind") or infer_audit_kind(data) or "")
    if kind not in JOURNAL_AUDIT_KINDS:
        return None
    # Recurring heartbeat checks must not flood the journal.
    if data.get("event_type") == "kill_switch_check" and kind == "kill":
        if data.get("halted") is not True and not data.get("engaged"):
            return None
        # Still skip routine "halted=false" ticks; only surface an engaged halt.
        if data.get("halted") is True and data.get("event_type") == "kill_switch_check":
            # Worker logs this every tick while halted — keep one shape, but require a reason.
            if not data.get("reason"):
                return None
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    symbol = str(
        data.get("symbol") or payload.get("symbol") or ""
    ).upper()
    if not symbol and kind not in ("kill", "resume"):
        return None
    if not symbol:
        symbol = "SYSTEM"
    approved = data.get("approved")
    if approved is None:
        approved = payload.get("approved")
    reason = data.get("reason") or payload.get("reason") or data.get("error_message") or ""
    extra: List[str] = []
    status = "proposed"
    fill_px = _recorded_fill_price(data, payload)
    if kind == "risk_reject":
        status = "risk_rejected"
        extra.append("risk_rejected")
    elif kind == "mandate_deny":
        status = "risk_rejected"
        extra.append("mandate")
    elif kind == "kill":
        status = "risk_rejected"
        extra.append("kill_switch")
    elif kind == "order_fill":
        # Same rule as order_submit: kind=order_fill is not a fill without a price.
        status = _filled_status(fill_px)
    elif kind == "order_submit":
        raw_status = str(data.get("status") or payload.get("status") or "submitted").lower()
        if raw_status in ("rejected", "canceled", "cancelled"):
            status = "canceled"
        elif raw_status == "filled":
            status = _filled_status(fill_px)
        else:
            status = "submitted"
    elif kind == "resume":
        status = "proposed"
    event_id = str(data.get("event_id") or stable_id("audit", data.get("ts"), kind, symbol, reason))
    ts = _iso(data.get("ts")) or utc_now_iso()
    side = _side(data.get("side") or payload.get("side"))
    qty = _qty(data.get("quantity") if data.get("quantity") is not None else payload.get("quantity"))
    order_id = data.get("order_id") or payload.get("order_id")
    codes = reason_codes_from_text(str(reason), extra=extra)
    summary = str(reason).strip() or f"{kind} {symbol}".strip()
    entry = empty_trade_log(log_id=stable_id("audit", event_id), ts=ts, symbol=symbol, status=status)
    entry.update(
        {
            "side": side,
            "qty": qty,
            "order_id": str(order_id) if order_id else None,
            "risk_decision_id": event_id,
            "fills": _fills_from_price(fill_px, qty) if status == "filled" else [],
            "user_visible_summary": summary,
            "links": {"audit_ref": f"/api/journal/audit/{event_id}", "signal_ref": ""},
            "reason_codes": codes,
            "kind": display_kind(status) if kind != "resume" else "note",
            "provenance": journal_provenance(source="audit_jsonl", as_of=ts, extra={"event_id": event_id}),
        }
    )
    if approved is False and not entry["reason_codes"]:
        entry["reason_codes"] = extra or ["rejected"]
    return entry


def infer_audit_kind(raw: Dict[str, Any]) -> Optional[str]:
    if raw.get("kind") in AUDIT_KINDS:
        return str(raw["kind"])
    event_type = str(raw.get("event_type") or "")
    approved = raw.get("approved")
    if event_type == "risk_decision":
        return "risk_allow" if approved is True else "risk_reject"
    if event_type == "mandate_decision":
        return "risk_allow" if approved is True else "mandate_deny"
    if event_type == "order_submission":
        return "order_submit"
    if event_type in ("order_fill", "fill"):
        return "order_fill"
    if event_type in ("kill_switch_check", "kill_switch_engage", "kill"):
        return "kill"
    if event_type in ("resume", "kill_switch_resume"):
        return "resume"
    return None


def normalize_audit_event(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = infer_audit_kind(raw) or str(raw.get("kind") or raw.get("event_type") or "config_change")
    if kind not in AUDIT_KINDS:
        # Preserve unknown historical event_type as payload, mapped to config_change
        # only when we still want a followable record. Skip heartbeats.
        if raw.get("event_type") == "kill_switch_check":
            kind = "kill"
        else:
            kind = "config_change"
    ts = _iso(raw.get("ts")) or utc_now_iso()
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {
        k: v for k, v in raw.items()
        if k not in {"schema_version", "event_id", "ts", "actor", "kind", "payload", "account_id"}
    }
    event_id = str(raw.get("event_id") or stable_id("audit", ts, kind, payload.get("symbol"), payload.get("reason")))
    return {
        "schema_version": AUDIT_SCHEMA,
        "event_id": event_id,
        "ts": ts,
        "actor": raw.get("actor") or "worker",
        "kind": kind,
        "payload": payload,
        "account_id": raw.get("account_id"),
        # Backward-compatible flattened fields for existing readers/tests.
        "event_type": raw.get("event_type") or kind,
        **{k: v for k, v in payload.items() if k not in {"schema_version"}},
    }


def merge_trade_logs(
    journal_rows: Iterable[Any],
    orders: Iterable[Any] = (),
    audit_events: Iterable[Any] = (),
    *,
    limit: int = 100,
    kind: str = "all",
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    index: Dict[str, Dict[str, Any]] = {}

    def accept(entry: Optional[Dict[str, Any]]) -> None:
        if not entry:
            return
        keys = _dedupe_keys(entry)
        existing = next((index[k] for k in keys if k in index), None)
        if existing:
            _enrich_entry(existing, entry)
            for k in keys:
                seen.add(k)
                index[k] = existing
            return
        for k in keys:
            seen.add(k)
            index[k] = entry
        entries.append(entry)

    for raw in journal_rows or []:
        accept(normalize_trade_log(raw))
    for order in orders or []:
        accept(from_order(order))
    for event in audit_events or []:
        accept(from_audit_event(event))

    entries.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    if kind and kind != "all":
        entries = [e for e in entries if e.get("kind") == kind]
    return entries[: max(1, int(limit))] if entries else []


def make_note_entry(symbol: str, note: str, side: Optional[str] = None) -> Dict[str, Any]:
    symbol_u = str(symbol or "").upper().strip()
    ts = utc_now_iso()
    entry = empty_trade_log(log_id=new_log_id(), ts=ts, symbol=symbol_u, status="proposed")
    entry.update(
        {
            "side": _side(side),
            "user_visible_summary": str(note or "").strip(),
            "kind": "note",
            "provenance": journal_provenance(
                source="trade_journal",
                as_of=ts,
                note="Operator note — not a broker fill.",
            ),
        }
    )
    return entry


def _dedupe_keys(entry: Dict[str, Any]) -> List[str]:
    keys = [f"log:{entry.get('log_id')}"]
    if entry.get("order_id"):
        keys.append(f"order:{entry['order_id']}:{entry.get('status')}")
    if entry.get("risk_decision_id"):
        keys.append(f"audit:{entry['risk_decision_id']}")
    summary = str(entry.get("user_visible_summary") or "").strip()
    if entry.get("symbol") and entry.get("status") in {"risk_rejected", "vetoed"} and summary:
        keys.append(f"twin:{entry.get('symbol')}:{entry.get('status')}:{summary}")
    return keys


def _enrich_entry(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """Fold a later twin (audit vs order) into the row already shown."""
    if not base.get("order_id") and incoming.get("order_id"):
        base["order_id"] = incoming["order_id"]
    if not base.get("risk_decision_id") and incoming.get("risk_decision_id"):
        base["risk_decision_id"] = incoming["risk_decision_id"]
    incoming_ref = (incoming.get("links") or {}).get("audit_ref") or ""
    if incoming_ref and not (base.get("links") or {}).get("audit_ref"):
        base.setdefault("links", {})["audit_ref"] = incoming_ref
    if incoming.get("intent_id") and not base.get("intent_id"):
        base["intent_id"] = incoming["intent_id"]
    incoming_intent = (incoming.get("links") or {}).get("intent_ref") or ""
    if incoming_intent and not (base.get("links") or {}).get("intent_ref"):
        base.setdefault("links", {})["intent_ref"] = incoming_intent
    codes = list(base.get("reason_codes") or [])
    for code in incoming.get("reason_codes") or []:
        if code not in codes:
            codes.append(code)
    base["reason_codes"] = codes
    if incoming.get("qty") and not base.get("qty"):
        base["qty"] = incoming["qty"]
    if incoming.get("side") and not base.get("side"):
        base["side"] = incoming["side"]


def _side(value: Any) -> Optional[str]:
    text = str(value or "").lower().strip()
    if text in ("buy", "sell"):
        return text
    return None


def _qty(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _order_summary(symbol: str, side: Optional[str], qty: float, status: str) -> str:
    side_s = side or "order"
    qty_s = f"{qty:g}" if qty else ""
    return " ".join(p for p in (status, side_s, qty_s, symbol) if p)


def _honest_provenance(raw: Any, *, source: str, as_of: Optional[str]) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return journal_provenance(source=source, as_of=as_of)
    # Never let an empty or inherited chip look verified.
    return journal_provenance(
        source=str(raw.get("source") or source),
        as_of=raw.get("as_of") or as_of,
        extra={k: v for k, v in raw.items() if k not in {"verified", "live", "source", "as_of", "note", "vendor"}},
        note=raw.get("note"),
        vendor=raw.get("vendor"),
    )


def looks_like_verified_fake(provenance: Optional[Dict[str, Any]]) -> bool:
    """Code-review helper: empty or local sources must not claim verified/live."""
    if not provenance:
        return False
    if provenance.get("verified") or provenance.get("live"):
        source = str(provenance.get("source") or provenance.get("vendor") or "").lower()
        if source in ("", "unknown", "trade_journal", "order_store", "audit_jsonl", "local"):
            return True
        if not provenance.get("source") and not provenance.get("vendor"):
            return True
    return False
