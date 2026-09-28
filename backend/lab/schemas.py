"""Pydantic schemas for Scan/Deep LLM handoff (lab Instructor path)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ScanStockLLMOut(BaseModel):
    final_score: Optional[float] = Field(None, ge=0, le=100)
    reasoning: str = ""
    technical_summary: str = ""
    key_signal: Literal["bullish", "bearish", "neutral"] = "neutral"

    @field_validator("key_signal", mode="before")
    @classmethod
    def _norm_signal(cls, v: object) -> str:
        s = str(v or "neutral").strip().lower()
        if s in ("bullish", "buy", "long", "positive"):
            return "bullish"
        if s in ("bearish", "sell", "short", "negative"):
            return "bearish"
        return "neutral"


class DeepStrategyLLMOut(BaseModel):
    stance: str = "neutral"
    action: str = "hold"
    entry_zone: Optional[str] = None
    stop_loss: Optional[float] = None
    targets: list[float] = Field(default_factory=list)
    rationale: str = ""
    risks: list[str] = Field(default_factory=list)


class AdvisoryRoleOut(BaseModel):
    role: Literal["bull", "bear", "risk"]
    ticker: str
    thesis: str
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    key_points: list[str] = Field(default_factory=list)
    # Explicit: advisory never requests an order
    places_order: Literal[False] = False
