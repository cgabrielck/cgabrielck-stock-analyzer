from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class Catalyst(BaseModel):
    id: str
    date: datetime
    event_type: str = Field(..., description="e.g., 'Earnings', 'FDA Approval', 'Macro', 'Product Launch'")
    description: str
    impact_direction: str = Field(..., description="'Positive', 'Negative', or 'Uncertain'")
    blackout_period_days: int = Field(0, description="Days before/after event where trading is restricted")

class InvestmentThesis(BaseModel):
    symbol: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    core_thesis: str = Field(..., description="Main argument for the investment")
    bull_case: str
    bear_case: str
    
    supporting_evidence: List[str] = Field(default_factory=list)
    disconfirming_evidence: List[str] = Field(default_factory=list)
    
    risks: List[str] = Field(default_factory=list)
    catalysts: List[Catalyst] = Field(default_factory=list)
    
    review_date: Optional[datetime] = None

    def add_evidence(self, evidence: str, supports: bool = True):
        if supports:
            self.supporting_evidence.append(evidence)
        else:
            self.disconfirming_evidence.append(evidence)
        self.updated_at = datetime.now(timezone.utc)

    def is_in_blackout_period(self, current_date: datetime) -> bool:
        """Checks if current_date falls within any catalyst blackout period."""
        for cat in self.catalysts:
            if cat.blackout_period_days > 0:
                delta = abs((current_date - cat.date).days)
                if delta <= cat.blackout_period_days:
                    return True
        return False
