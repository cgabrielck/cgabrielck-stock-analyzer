"""A/B metrics aggregator: main (flags off) vs lab (flags on) — not SPY bakeoff."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from backend.lab.advisory_debate import advisory_metrics
from backend.lab.finbert_sentiment import sentiment_metrics
from backend.lab.flags import lab_status
from backend.lab.instructor_parse import schema_metrics
from backend.utils.constants import DATA_DIR


def collect_ab_report() -> Dict[str, Any]:
    tear_html = Path(DATA_DIR) / "lab_quantstats_tear.html"
    tear_stub = Path(DATA_DIR) / "lab_quantstats_stub.json"
    tear_complete = False
    tear_path = None
    if tear_html.exists() and tear_html.stat().st_size > 100:
        tear_complete = True
        tear_path = str(tear_html)
    elif tear_stub.exists():
        tear_complete = True
        tear_path = str(tear_stub)

    schema = schema_metrics()
    sentiment = sentiment_metrics()
    advisory = advisory_metrics()

    return {
        "purpose": "Compare schema/sentiment/advisory/tear completeness — NOT 12-name SPY bakeoff alpha",
        "lab_status": lab_status(),
        "schema": {
            "success_rate": schema.get("success_rate"),
            "attempts": schema.get("attempts"),
            "ok": schema.get("ok"),
            "fail": schema.get("fail"),
            "by_mode": schema.get("mode"),
        },
        "sentiment": {
            "agreement_rate": sentiment.get("agreement_rate"),
            "compared": sentiment.get("compared"),
            "finbert_calls": sentiment.get("finbert_calls"),
            "vader_fallback": sentiment.get("vader_fallback"),
        },
        "advisory": {
            "runs": advisory.get("runs"),
            "rewrites": advisory.get("rewrites"),
            "last": advisory.get("last"),
            "note": "top-N rewrite is suggestion-only; worker ignores for entries",
        },
        "tear": {
            "complete": tear_complete,
            "path": tear_path,
        },
        "refused_as_trading_brain": lab_status().get("refused"),
    }


def write_ab_report(path: Path | None = None) -> Path:
    path = path or (Path(DATA_DIR) / "lab_ab_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collect_ab_report(), indent=2), encoding="utf-8")
    return path
