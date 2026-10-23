"""Doc honesty locks for charts / reference signals / audit Confirmed 1–5.

MUST M21–M23 + 質疑者 DOC-D7 / SIG-NOORDER-02. Tests only — no status upgrades.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHARTS = ROOT / "docs/CHARTS_INDICATORS_DESIGN.md"
SIGNALS = ROOT / "docs/REFERENCE_SIGNALS_DESIGN.md"
AUDIT = ROOT / "docs/AUDIT_REMEDIATION_PLAN_2026-10-21.md"


def _require_draft_invariants(text: str, *, path: Path) -> None:
    assert "**Status:** Draft" in text, path
    assert "**Status:** Accepted" not in text, path
    assert "未 Accepted" in text or "not yet Accepted" in text or "Draft ≠ Accepted" in text
    assert "Merge ≠ Accepted" in text
    assert "Issue #10 stays OPEN" in text
    assert "ADR-002 is VOID" in text or ("ADR-002" in text and "VOID" in text)
    assert "closes #10" not in text.lower()
    assert "fixes #10" not in text.lower()
    assert "Confirmed item 6" not in text or "not Confirmed item 6" in text or "唔係** Confirmed item 6" in text or "本頁**唔係** Confirmed item 6" in text


def test_charts_design_honesty_invariants():
    """M21: CHARTS design page stays Draft; #10 OPEN; ADR-002 VOID; not Confirmed 6."""
    text = CHARTS.read_text(encoding="utf-8")
    _require_draft_invariants(text, path=CHARTS)
    assert "未 Accepted" in text
    assert "Confirmed" in text
    assert "1–5" in text or "1-5" in text
    assert "本頁**唔係** Confirmed item 6" in text or "唔係** Confirmed item 6" in text
    assert "Confirmed item 6" not in text.replace("唔係** Confirmed item 6", "").replace("not Confirmed item 6", "")


def test_reference_signals_design_remains_draft():
    """M22: REFERENCE signals design stays Draft and does not stamp verified/live."""
    text = SIGNALS.read_text(encoding="utf-8")
    _require_draft_invariants(text, path=SIGNALS)
    assert "Draft ≠ Accepted" in text
    assert "唔 stamp" in text or "verified" in text
    assert "live" in text
    assert "Confirmed items 1–5 **only**" in text or "Confirmed items 1–5" in text


def test_audit_confirmed_remediation_is_items_1_to_5_only():
    """M23: AUDIT Confirmed remediation remains items 1–5; charts/signals not item 6."""
    text = AUDIT.read_text(encoding="utf-8")
    assert "## Confirmed remediation" in text
    start = text.find("## Confirmed remediation")
    end = text.find("## Explicit non-goals", start)
    section = text[start:end]
    numbered = re.findall(r"^(\d+)\.\s", section, flags=re.M)
    assert numbered == ["1", "2", "3", "4", "5"], numbered
    assert "6." not in section.split("**Draft")[0]
    assert "CHARTS_INDICATORS_DESIGN.md" in text
    assert "REFERENCE_SIGNALS_DESIGN.md" in text
    assert (
        "not Confirmed item 6" in text
        or "**not** Confirmed item 6" in text
        or "not a Confirmed remediation item" in text
    )
    assert "Issue #10 stays OPEN" in text
    assert "No resurrecting ADR-002" in text or "唔復活 ADR-002" in text


def test_d7_schema_planning_distinct_from_d7_design_dirs():
    """DOC-D7: ADR-003 D7 schema stays planning; D7-DESIGN ≠ that schema."""
    text = SIGNALS.read_text(encoding="utf-8")
    assert "planning" in text
    assert "D7" in text
    assert "同名刀 ≠" in text or "≠" in text and "D7-DESIGN" in text
    assert "D7-DESIGN" in text
    assert "仍 planning" in text or "still planning" in text.lower() or "細節仍 planning" in text
    charts = CHARTS.read_text(encoding="utf-8")
    assert "D7-DESIGN" in charts or "參考訊號仍 planning" in charts


def test_signals_design_forbids_order_and_verified_claims():
    """SIG-NOORDER-02: reference signals must not self-order or stamp verified/live."""
    text = SIGNALS.read_text(encoding="utf-8")
    assert "**唔**自行落單" in text or "唔自行落單" in text
    assert "stamp `verified`" in text or "stamp verified" in text.lower()
    assert "MUST NEVER" in text
    assert "place_desk_order" in text
    assert "backend/signals/" in text
    assert "無 `backend/signals/`" in text or "無** `backend/signals/`" in text or "**無** `backend/signals/`" in text
    assert "/api/signals" in text
