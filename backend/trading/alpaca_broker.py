import logging
import os
from typing import Any, List, Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import TimeInForce, OrderClass
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.sticky_idempotency import (
    is_acceptance_unknown_exception,
    is_client_order_id_conflict,
    mark_acceptance_unknown,
)
from backend.trading.fill_honesty import apply_recorded_fill, recorded_fill_price
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)


def disable_unsafe_place_retries(client: Any) -> None:
    """Stop alpaca-py from auto-POSTing a place again after HTTP 504.

    ``TradingClient`` retries ``DEFAULT_RETRY_EXCEPTION_CODES`` (429 and 504)
    up to ``DEFAULT_RETRY_ATTEMPTS`` (3). ``retry_attempts=0`` is falsy, so
    the constructor cannot disable that. A 504 POST may already have landed;
    the retry then returns 422 ``client_order_id must be unique`` before our
    code ever sees the timeout, and sticky never pins. 429 stays so
    rate-limit retries still work.
    """
    codes = getattr(client, "_retry_codes", None)
    if not isinstance(codes, list):
        return
    client._retry_codes = [code for code in codes if int(code) != 504]


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


def round_us_equity_price(price: Optional[float]) -> Optional[float]:
    """Alpaca rejects sub-penny increments on names priced at $1+."""
    if price is None:
        return None
    p = float(price)
    if p >= 1.0:
        return round(p, 2)
    return round(p, 4)


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
        # Before any place: a 504 retry can turn into 422 unique and skip the pin.
        disable_unsafe_place_retries(self._api)

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

        def _f(value, default=None):
            if value is None:
                return default
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        positions = []
        unrealized_total = 0.0
        long_mv = 0.0
        for p in positions_raw:
            qty = _f(p.qty, 0.0) or 0.0
            avg = _f(p.avg_entry_price, 0.0) or 0.0
            mark = _f(getattr(p, "current_price", None))
            mkt = _f(getattr(p, "market_value", None))
            upl = _f(getattr(p, "unrealized_pl", None))
            uplpc = _f(getattr(p, "unrealized_plpc", None))
            cost = _f(getattr(p, "cost_basis", None), qty * avg)
            if mkt is None and mark is not None:
                mkt = mark * qty
            if upl is None and mkt is not None and cost is not None:
                upl = mkt - cost
            if upl:
                unrealized_total += upl
            if mkt:
                long_mv += mkt
            positions.append(
                Position(
                    id=str(p.asset_id),
                    symbol=p.symbol,
                    quantity=qty,
                    average_entry_price=avg,
                    current_price=mark,
                    market_value=mkt,
                    unrealized_pl=upl,
                    unrealized_plpc=uplpc,
                    cost_basis=cost,
                )
            )

        equity = _f(getattr(account, "equity", None), float(account.portfolio_value))
        last_equity = _f(getattr(account, "last_equity", None), equity)
        day_pnl = None
        if equity is not None and last_equity is not None:
            day_pnl = equity - last_equity

        return AccountSummary(
            cash=float(account.cash),
            buying_power=float(account.buying_power),
            portfolio_value=float(account.portfolio_value),
            positions=positions,
            equity=equity,
            last_equity=last_equity,
            day_pnl=day_pnl,
            unrealized_pl=unrealized_total,
            long_market_value=long_mv,
            paper=self.is_paper,
        )

    def _map_status(self, alpaca_status) -> OrderStatus:
        """Maps Alpaca status strings/enums to our internal OrderStatus enum."""
        status_value = getattr(alpaca_status, "value", alpaca_status)
        return _STATUS_MAP.get(status_value, OrderStatus.DRAFT)  # Default/fallback

    def _bracket_kwargs(self, order: Order) -> dict:
        """Attach Alpaca bracket legs when stop/take-profit are present."""
        wants_bracket = (
            str(getattr(order, "order_class", "simple")).lower() == "bracket"
            and order.take_profit_price is not None
            and (order.stop_loss_price is not None or order.stop_price is not None)
        )
        if not wants_bracket:
            return {}
        stop = order.stop_loss_price if order.stop_loss_price is not None else order.stop_price
        return {
            "order_class": OrderClass.BRACKET,
            "take_profit": TakeProfitRequest(limit_price=round_us_equity_price(order.take_profit_price)),
            "stop_loss": StopLossRequest(stop_price=round_us_equity_price(stop)),
            # Alpaca equity brackets typically require DAY TIF on the parent.
            "time_in_force": TimeInForce.DAY,
        }

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
        common.update(self._bracket_kwargs(order))
        if order.order_type == OrderType.MARKET:
            return MarketOrderRequest(**common)
        if order.order_type == OrderType.LIMIT:
            return LimitOrderRequest(limit_price=round_us_equity_price(order.limit_price), **common)
        if order.order_type == OrderType.STOP:
            return StopOrderRequest(stop_price=round_us_equity_price(order.stop_price), **common)
        if order.order_type == OrderType.STOP_LIMIT:
            return StopLimitOrderRequest(
                limit_price=round_us_equity_price(order.limit_price),
                stop_price=round_us_equity_price(order.stop_price),
                **common,
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
            apply_recorded_fill(order, alpaca_order)
        except APIError as e:
            if is_client_order_id_conflict(e):
                # Includes a 422 that alpaca-py surfaced after its own 504 retry.
                return self._pin_unique_client_order(order, e)
            if is_acceptance_unknown_exception(e):
                # 502/504/timeout: do not pretend the broker rejected it.
                mark_acceptance_unknown(order, e)
                return order
            order.status = OrderStatus.REJECTED
            order.idempotency_sticky = False
            order.error_message = str(e)
        except Exception as e:
            if is_client_order_id_conflict(e) or is_acceptance_unknown_exception(e):
                if is_client_order_id_conflict(e):
                    return self._pin_unique_client_order(order, e)
                mark_acceptance_unknown(order, e)
                return order
            raise
        return order

    def _pin_unique_client_order(self, order: Order, exc: BaseException) -> Order:
        """422 unique: the client_order_id landed. Do not clear sticky.

        Lookup may copy the broker id for later sync. Status stays
        acceptance_unknown — copying FILLED would let a desk 200 path
        clear the pin and allow a fresh-key second submit.
        """
        mark_acceptance_unknown(order, exc)
        found = self._lookup_order_by_client_id(order.idempotency_key)
        broker_id = getattr(found, "id", None) if found is not None else None
        if broker_id:
            order.id = str(broker_id)
        order.status = OrderStatus.ACCEPTANCE_UNKNOWN
        order.idempotency_sticky = True
        order.filled_at = None
        order.filled_avg_price = None
        return order

    def _lookup_order_by_client_id(self, client_order_id: str):
        client = self._api
        lookup = getattr(client, "get_order_by_client_id", None) if client is not None else None
        if not callable(lookup):
            return None
        try:
            return lookup(client_order_id)
        except Exception:
            logger.exception(
                "Could not recover Alpaca order for client_order_id=%s",
                client_order_id,
            )
            return None

    def get_order(self, order_id: str) -> Optional[Order]:
        """Retrieves a specific order by its broker-assigned ID."""
        try:
            alpaca_order = self.api.get_order_by_id(order_id)

            # This is a simplified mapping. A real implementation would need
            # a way to link back to the original internal `Order` object to
            # retrieve all its details like idempotency_key.
            fill_px = recorded_fill_price(alpaca_order)
            status = self._map_status(alpaca_order.status)
            order = Order(
                id=str(alpaca_order.id),
                idempotency_key=alpaca_order.client_order_id,
                symbol=alpaca_order.symbol,
                side=OrderSide(getattr(alpaca_order.side, "value", alpaca_order.side)),
                order_type=OrderType(getattr(alpaca_order.type, "value", alpaca_order.type)),
                quantity=float(alpaca_order.qty),
                status=status,
                limit_price=float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
                stop_price=float(alpaca_order.stop_price) if alpaca_order.stop_price else None,
                submitted_at=alpaca_order.submitted_at,
                filled_at=alpaca_order.filled_at,
                cancelled_at=alpaca_order.canceled_at,
                failed_at=alpaca_order.failed_at,
                filled_avg_price=fill_px,
            )
            apply_recorded_fill(order, alpaca_order)
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
