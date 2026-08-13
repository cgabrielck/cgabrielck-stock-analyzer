from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

class OrderStatus(str, Enum):
    DRAFT = "draft"
    RISK_APPROVED = "risk_approved"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    RECONCILIATION_ERROR = "reconciliation_error"

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"

class AuditableModel(BaseModel):
    id: str = Field(..., description="Unique identifier")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp in UTC")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp in UTC")

class Order(AuditableModel):
    symbol: str = Field(..., description="The symbol of the asset to trade")
    side: OrderSide = Field(..., description="The side of the order (buy or sell)")
    order_type: OrderType = Field(..., description="The type of the order")
    quantity: float = Field(..., description="The number of shares to trade")
    limit_price: Optional[float] = Field(None, description="The limit price for a limit order")
    stop_price: Optional[float] = Field(None, description="The stop price for a stop order")
    status: OrderStatus = Field(OrderStatus.DRAFT, description="The current status of the order")
    submitted_at: Optional[datetime] = Field(None, description="Timestamp when the order was submitted to the broker")
    filled_at: Optional[datetime] = Field(None, description="Timestamp when the order was fully filled")
    cancelled_at: Optional[datetime] = Field(None, description="Timestamp when the order was cancelled")
    failed_at: Optional[datetime] = Field(None, description="Timestamp when the order failed")
    error_message: Optional[str] = Field(None, description="Error message if the order failed")
    idempotency_key: str = Field(..., description="Idempotency key to prevent duplicate orders")

class Fill(AuditableModel):
    order_id: str = Field(..., description="The ID of the order this fill belongs to")
    price: float = Field(..., description="The price at which the shares were filled")
    quantity: float = Field(..., description="The number of shares that were filled")
    timestamp: datetime = Field(..., description="The timestamp of the fill")
    commission: float = Field(0.0, description="The commission paid for this fill")

class Position(AuditableModel):
    symbol: str = Field(..., description="The symbol of the asset")
    quantity: float = Field(..., description="The number of shares held")
    average_entry_price: float = Field(..., description="The average price at which the shares were acquired")
    
class AccountSummary(BaseModel):
    cash: float
    buying_power: float
    portfolio_value: float
    positions: List[Position]
