"""Structured LLM parse: Instructor when enabled+installed, else Pydantic+json."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from backend.lab.flags import instructor_enabled
from backend.lab.schemas import ScanStockLLMOut
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_METRICS_PATH = Path(DATA_DIR) / "lab_schema_metrics.json"


def _load_metrics() -> Dict[str, Any]:
    if not _METRICS_PATH.exists():
        return {"attempts": 0, "ok": 0, "fail": 0, "mode": {}}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"attempts": 0, "ok": 0, "fail": 0, "mode": {}}


def _save_metrics(data: Dict[str, Any]) -> None:
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _METRICS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("lab schema metrics write failed: %s", exc)


def record_parse(ok: bool, mode: str) -> None:
    m = _load_metrics()
    m["attempts"] = int(m.get("attempts") or 0) + 1
    if ok:
        m["ok"] = int(m.get("ok") or 0) + 1
    else:
        m["fail"] = int(m.get("fail") or 0) + 1
    modes = dict(m.get("mode") or {})
    row = dict(modes.get(mode) or {"ok": 0, "fail": 0})
    row["ok" if ok else "fail"] = int(row.get("ok" if ok else "fail") or 0) + 1
    modes[mode] = row
    m["mode"] = modes
    attempts = max(1, int(m["attempts"]))
    m["success_rate"] = round(int(m.get("ok") or 0) / attempts, 4)
    _save_metrics(m)


def schema_metrics() -> Dict[str, Any]:
    return _load_metrics()


def parse_model(raw: str | dict, model: Type[T], *, mode: str = "pydantic") -> Optional[T]:
    """Validate *raw* into *model*. Records success/fail for A/B metrics."""
    try:
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw
        obj = model.model_validate(data)
        record_parse(True, mode)
        return obj
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        logger.debug("lab parse failed (%s): %s", mode, exc)
        record_parse(False, mode)
        return None


def parse_scan_llm_text(text: str) -> Dict[str, Any]:
    """Parse Scan LLM JSON into a dict compatible with analyze_stock return."""
    mode = "instructor" if instructor_enabled() else "pydantic"
    # Instructor path: still validate with Pydantic (works without the package).
    # Full instructor.client.chat wrapping is optional when package+flag present.
    if instructor_enabled():
        try:
            import instructor  # noqa: F401

            mode = "instructor"
        except Exception:
            mode = "pydantic_fallback"

    parsed = parse_model(text, ScanStockLLMOut, mode=mode)
    if parsed is None:
        # Legacy best-effort
        try:
            data = json.loads(text)
            record_parse(False, "legacy_partial")
            return {
                "final_score": data.get("final_score"),
                "reasoning": data.get("reasoning", ""),
                "technical_summary": data.get("technical_summary", ""),
                "key_signal": data.get("key_signal", "neutral"),
                "lab_schema_ok": False,
            }
        except Exception:
            return {
                "final_score": None,
                "reasoning": "",
                "technical_summary": "",
                "key_signal": "neutral",
                "lab_schema_ok": False,
            }
    return {
        "final_score": parsed.final_score,
        "reasoning": parsed.reasoning,
        "technical_summary": parsed.technical_summary,
        "key_signal": parsed.key_signal,
        "lab_schema_ok": True,
        "lab_parse_mode": mode,
    }
