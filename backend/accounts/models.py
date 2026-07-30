from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class User:
    id: str
    username: str


@dataclass(frozen=True)
class AuthContext:
    user: Optional[User] = None
    session_token: Optional[str] = None

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None


@dataclass(frozen=True)
class UserPreferences:
    language: str = "zh_tw"
    portfolio_capital: int = 100_000
    risk_budget_pct: float = 1.0
    include_ai_news: bool = True
    schema_version: int = 1


@dataclass(frozen=True)
class SavedPlanVersion:
    plan_id: str
    ticker: str
    version: int
    plan_data: Dict[str, Any]
    analysis_timestamp: str
    created_at: Optional[str] = None


@dataclass(frozen=True)
class AlertRule:
    id: str
    saved_plan_id: str
    plan_version: int
    event_type: str
    rule_data: Dict[str, Any]
    monitoring_enabled: bool = False


@dataclass(frozen=True)
class AlertEvent:
    id: str
    ticker: str
    event_type: str
    price: float
    quote_time: str
    created_at: str
    read_at: Optional[str] = None


@dataclass(frozen=True)
class OptionPosition:
    id: str
    saved_plan_id: str
    plan_version: int
    underlying_ticker: str
    contract_symbol: str
    lifecycle_state: str
    planned_entry: float
    confirmed_entry: Optional[float] = None
    quantity: Optional[int] = None
    option_type: Optional[str] = None
    expiry: Optional[str] = None
    strike: Optional[float] = None
    entry_alerted_at: Optional[str] = None
    entered_at: Optional[str] = None


@dataclass(frozen=True)
class SavedPlanOutcome:
    id: str
    saved_plan_id: str
    plan_version: int
    horizon_days: int
    calculation_version: int
    outcome_data: Dict[str, Any]
