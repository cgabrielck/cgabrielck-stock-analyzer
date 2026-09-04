import os
from typing import List, Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from backend.trading.broker import BrokerAdapter
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType, Position

# Alpaca's public status strings -> our internal OrderStatus enum.
_STATUS_MAP = {
    "new": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.ACCEPTED,
    "pending_cancel": OrderStatus.SUBMITTED,
    "pending_replace": OrderStatus.SUBMITTED,
}


class AlpacaBroker(BrokerAdapter):
    """
    Broker adapter for Alpaca paper and live trading, backed by the
    `alpaca-py` SDK (the maintained successor to `alpaca-trade-api`).
    """

    def __init__(self):
        # Read configuration only. No network I/O in the constructor so the
        # adapter can be instantiated in tests and non-trading contexts without
        # valid credentials. The client is created lazily on first use.
        self.api_key_id = os.getenv("APCA_API_KEY_ID")
        self.api_secret_key = os.getenv("APCA_API_SECRET_KEY")
        self.is_paper = os.getenv("APCA_PAPER", "true").lower() == "true"
        self._api: Optional[TradingClient] = None
        self._verified = False

    def _ensure_connected(self) -> None:
        """Lazily create the Alpaca client and verify the account, once.

        Credential validation and the account-status check are deferred here so
        constructing the adapter never touches the network. Raises if creds are
        missing or the account is blocked from trading.
        """
        if self._verified:
            return

        if not self.api_key_id or not self.api_secret_key:
            raise ValueError("Alpaca API credentials (APCA_API_KEY_ID, APCA_API_SECRET_KEY) are not set.")

        if self._api is None:
            self._api = TradingClient(
                api_key=self.api_key_id,
                secret_key=self.api_secret_key,
                paper=self.is_paper,
            )

        account = self._api.get_account()
        if account.trading_blocked:
            raise Exception("Alpaca account is currently blocked from trading.")
        self._verified = True

    @property
    def api(self) -> TradingClient:
        """The Alpaca client, connecting/verifying lazily on first access."""
        self._ensure_connected()
        assert self._api is not None  # guaranteed by _ensure_connected
        return self._api

    def get_account_summary(self) -> AccountSummary:
        """Retrieves the current account summary from Alpaca."""
        account = self.api.get_account()
        positions_raw = self.api.get_all_positions()

        positions = [
            Position(
                id=p.asset_id,
                symbol=p.symbol,
                quantity=float(p.qty),
                average_entry_price=float(p.avg_entry_price),
            )
            for p in positions_raw
        ]

        return AccountSummary(
            cash=float(account.cash),
            buying_power=float(account.buying_power),
            portfolio_value=float(account.portfolio_value),
            positions=positions,
        )

    def _map_status(self, alpaca_status) -> OrderStatus:
        """Maps Alpaca status strings/enums to our internal OrderStatus enum."""
        status_value = getattr(alpaca_status, "value", alpaca_status)
        return _STATUS_MAP.get(status_value, OrderStatus.DRAFT)  # Default/fallback

    def _build_order_request(self, order: Order):
        """Builds the alpaca-py order-request object matching the order type."""
        side = AlpacaOrderSide(order.side.value)
        common = {
            "symbol": order.symbol,
            "qty": order.quantity,
            "side": side,
            "time_in_force": TimeInForce.GTC,
            "client_order_id": order.idempotency_key,
        }
        if order.order_type == OrderType.MARKET:
            return MarketOrderRequest(**common)
        if order.order_type == OrderType.LIMIT:
            return LimitOrderRequest(limit_price=order.limit_price, **common)
        if order.order_type == OrderType.STOP:
            return StopOrderRequest(stop_price=order.stop_price, **common)
        if order.order_type == OrderType.STOP_LIMIT:
            return StopLimitOrderRequest(
                limit_price=order.limit_price, stop_price=order.stop_price, **common
            )
        raise ValueError(f"Unsupported order type: {order.order_type}")

    def submit_order(self, order: Order) -> Order:
        """Submits an order to Alpaca."""
        try:
            order_request = self._build_order_request(order)
            alpaca_order = self.api.submit_order(order_data=order_request)
            order.status = self._map_status(alpaca_order.status)
            order.submitted_at = alpaca_order.submitted_at
            # Store the broker's order ID for future reference
            order.id = str(alpaca_order.id)
        except APIError as e:
            order.status = OrderStatus.REJECTED
            order.error_message = str(e)
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        """Retrieves a specific order by its broker-assigned ID."""
        try:
            alpaca_order = self.api.get_order_by_id(order_id)

            # This is a simplified mapping. A real implementation would need
            # a way to link back to the original internal `Order` object to
            # retrieve all its details like idempotency_key.
            order = Order(
                id=str(alpaca_order.id),
                idempotency_key=alpaca_order.client_order_id,
                symbol=alpaca_order.symbol,
                side=OrderSide(getattr(alpaca_order.side, "value", alpaca_order.side)),
                order_type=OrderType(getattr(alpaca_order.type, "value", alpaca_order.type)),
                quantity=float(alpaca_order.qty),
                status=self._map_status(alpaca_order.status),
                limit_price=float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
                stop_price=float(alpaca_order.stop_price) if alpaca_order.stop_price else None,
                submitted_at=alpaca_order.submitted_at,
                filled_at=alpaca_order.filled_at,
                cancelled_at=alpaca_order.canceled_at,
                failed_at=alpaca_order.failed_at,
            )
            return order
        except APIError:
            return None  # Order not found

    def cancel_order(self, order_id: str) -> Order:
        """Cancels an existing order by its broker-assigned ID."""
        try:
            self.api.cancel_order_by_id(order_id)
            # Fetch the updated order to confirm cancellation status
            updated_order = self.get_order(order_id)
            if updated_order:
                return updated_order
            else:
                # This case is unlikely if cancel_order succeeds but get_order fails
                raise Exception("Failed to retrieve order after cancellation request.")

        except APIError as e:
            # If cancellation fails, fetch the current state and return it
            current_order = self.get_order(order_id)
            if current_order:
                if not current_order.error_message:
                    current_order.error_message = f"Cancellation failed: {str(e)}"
                return current_order
            raise e  # Re-raise if we can't even get the current status

    def list_positions(self) -> List[dict]:
        """Lists all open positions in a raw dictionary format."""
        positions = self.api.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "quantity": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
            }
            for p in positions
        ]
