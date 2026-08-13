from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class DataProvenance(BaseModel):
    """
    Tracks the source, timestamp, and quality of data used in the system.
    """
    source_name: str = Field(..., description="Name of the data provider (e.g., 'Alpaca', 'SEC', 'LLM')")
    fetch_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When the data was retrieved")
    is_stale: bool = Field(default=False, description="Flag indicating if the data is considered stale based on business rules")
    quality_score: float = Field(default=1.0, description="0.0 to 1.0 score representing confidence in the data")
    raw_reference: Optional[str] = Field(None, description="A hash, ID, or URL pointing back to the exact raw data payload")
    assumptions: Optional[Dict[str, Any]] = Field(None, description="Any explicit assumptions made during data transformation or model generation")

class TraceableArtifact(BaseModel):
    """
    Base class for any research conclusion, signal, or model output that requires auditing.
    """
    artifact_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provenance_chain: List[DataProvenance] = Field(default_factory=list, description="Lineage of all data inputs that produced this artifact")
    is_llm_generated: bool = Field(default=False)
    llm_prompt_ref: Optional[str] = Field(None, description="Reference to the exact prompt used if LLM generated")

    def add_provenance(self, provenance: DataProvenance):
        self.provenance_chain.append(provenance)
        
    def check_staleness(self) -> bool:
        """Returns True if any critical dependency in the provenance chain is stale."""
        return any(p.is_stale for p in self.provenance_chain)
        
    def get_lowest_quality_score(self) -> float:
        if not self.provenance_chain:
            return 1.0
        return min(p.quality_score for p in self.provenance_chain)
