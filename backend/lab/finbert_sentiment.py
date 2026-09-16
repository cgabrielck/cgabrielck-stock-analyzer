"""FinBERT soft sentiment — lab only. Falls back to VADER. Never places orders."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.lab.flags import finbert_enabled
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

_METRICS_PATH = Path(DATA_DIR) / "lab_sentiment_metrics.json"
_PIPE = None
_PIPE_TRIED = False


def _load_metrics() -> Dict[str, Any]:
    if not _METRICS_PATH.exists():
        return {"compared": 0, "agree": 0, "finbert_calls": 0, "vader_fallback": 0}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"compared": 0, "agree": 0, "finbert_calls": 0, "vader_fallback": 0}


def _save_metrics(data: Dict[str, Any]) -> None:
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = dict(data)
        compared = max(1, int(data.get("compared") or 0))
        data["agreement_rate"] = round(int(data.get("agree") or 0) / compared, 4) if int(data.get("compared") or 0) else None
        _METRICS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("lab sentiment metrics write failed: %s", exc)


def sentiment_metrics() -> Dict[str, Any]:
    return _load_metrics()


def _vader_label(text: str) -> str:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        analyzer = SentimentIntensityAnalyzer()
        compound = analyzer.polarity_scores(text)["compound"]
        if compound >= 0.15:
            return "positive"
        if compound <= -0.15:
            return "negative"
        return "neutral"
    except Exception:
        return "neutral"


def _get_finbert_pipe():
    global _PIPE, _PIPE_TRIED
    if _PIPE_TRIED:
        return _PIPE
    _PIPE_TRIED = True
    try:
        from transformers import pipeline

        _PIPE = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
    except Exception as exc:
        logger.info("FinBERT unavailable, using VADER: %s", exc)
        _PIPE = None
    return _PIPE


def _finbert_label(text: str) -> Optional[str]:
    pipe = _get_finbert_pipe()
    if pipe is None:
        return None
    try:
        out = pipe(text[:512])[0]
        label = str(out.get("label") or "").strip().lower()
        # FinBERT: positive / negative / neutral
        if label in ("positive", "negative", "neutral"):
            return label
        if "pos" in label:
            return "positive"
        if "neg" in label:
            return "negative"
        return "neutral"
    except Exception as exc:
        logger.debug("FinBERT infer failed: %s", exc)
        return None


def classify_news_sentiment(title: str, summary: str = "") -> Tuple[str, Dict[str, Any]]:
    """Return (label, meta). Soft layer only — does not buy/sell."""
    text = f"{title} {summary}".strip()
    vader = _vader_label(text)
    meta: Dict[str, Any] = {"vader": vader, "engine": "vader", "lab_finbert": False}

    if not finbert_enabled():
        return vader, meta

    m = _load_metrics()
    fb = _finbert_label(text)
    if fb is None:
        m["vader_fallback"] = int(m.get("vader_fallback") or 0) + 1
        _save_metrics(m)
        meta["engine"] = "vader_fallback"
        return vader, meta

    m["finbert_calls"] = int(m.get("finbert_calls") or 0) + 1
    m["compared"] = int(m.get("compared") or 0) + 1
    if fb == vader:
        m["agree"] = int(m.get("agree") or 0) + 1
    _save_metrics(m)

    meta.update({"finbert": fb, "engine": "finbert", "lab_finbert": True, "agree_with_vader": fb == vader})
    return fb, meta
