"""
ShadowTradingEngine 2.0 — realistic simulated fills using next-bar market data.

Upgrade from 1.0 (instant fill at limit price):
  - Market orders fill at the NEXT bar's open price plus directional slippage.
  - Limit buy fills only if a later bar's Low <= limit price.
  - Limit sell fills only if a later bar's High >= limit price.
  - Records filled_avg_price and slippage_pct on the Order for later P&L review.
  - Supports partial fills via the quantity_touched parameter (useful for backtest reuse).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from backend.trading.models import Order, OrderStatus
from backend.trading.engine.order_manager import OrderManager

logger = logging.getLogger(__name__)

# Default market-impact model: buy above next-open, sell below next-open.
# For liquid large-caps 5 bps each side is conservative but realistic.
DEFAULT_SLIPPAGE_BPS = 5.0


class ShadowTradingEngine:
    """
    Simulates the execution of orders without submitting them to a real broker.

    The engine evaluates the order against forward-looking price bars (supplied
    by the caller), so a limit order only fills when the market actually trades
    through the limit price, and a market order pays a modelled spread/slippage.
    """

    def __init__(self, order_manager: Optional[OrderManager] = None,
                 slippage_bps: float = DEFAULT_SLIPPAGE_BPS):
        self.order_manager = order_manager
        self.slippage_bps = slippage_bps

    def simulate_submission(
        self,
        order: Order,
        next_bars: Optional[pd.DataFrame] = None,
        quantity_touched: Optional[float] = None,
    ) -> Order:
        """
        Simulates broker submission and filling for *order*.

        Args:
            order:           A RISK_APPROVED order to simulate.
            next_bars:       OHLCV DataFrame of bars AFTER the signal bar.
                             Columns must include Open, High, Low, Close (and
                             optionally Time). If None, falls back to an
                             immediate fill at limit_price (legacy behavior).
            quantity_touched: If provided, only fill this many shares (partial).
                             Defaults to the full order quantity.

        Returns:
            The same Order object with its status/fill fields updated.
        """
        if order.status != OrderStatus.RISK_APPROVED:
            logger.warning("Shadow engine expects RISK_APPROVED orders. Got: %s", order.status)
            return order

        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now(timezone.utc)
        self._save(order)

        # No forward bars -> legacy instant fill at limit price.
        if next_bars is None or len(next_bars) == 0:
            self._mark_filled(order, fill_price=order.limit_price or 0.0, slippage=0.0)
            logger.info("[SHADOW] Instant fill %s @ %s (no forward bars)",
                        order.symbol, order.limit_price)
            return order

        fill_price = self._resolve_fill_price(order, next_bars)
        if fill_price is None:
            # Limit never crossed within the look-ahead window.
            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.now(timezone.utc)
            order.error_message = "Shadow: limit price not touched within provided bars."
            self._save(order)
            logger.info("[SHADOW] %s order NOT filled for %s (limit %s never crossed)",
                        order.symbol, order.side, order.limit_price)
            return order

        qty = quantity_touched if quantity_touched is not None else order.quantity
        reference = self._reference_price(order, next_bars)
        slippage = 0.0
        if reference and reference > 0:
            slippage = abs(fill_price - reference) / reference

        self._mark_filled(order, fill_price=fill_price, slippage=slippage, quantity=qty)
        logger.info(
            "[SHADOW] %s %s ×%.2f @ %.4f (ref %.4f, slip %.2f%%)",
            order.side, order.symbol, qty, fill_price,
            reference or fill_price, slippage * 100.0,
        )
        return order

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mark_filled(self, order: Order, fill_price: float, slippage: float,
                     quantity: Optional[float] = None) -> None:
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.now(timezone.utc)
        order.filled_avg_price = round(float(fill_price), 6)
        order.slippage_pct = round(float(slippage), 6)
        if quantity is not None and quantity != order.quantity:
            order.quantity = float(quantity)
        self._save(order)

    def _save(self, order: Order) -> None:
        """Persist via the optional order manager; no-op in standalone use."""
        if self.order_manager is not None and getattr(self.order_manager, "store", None) is not None:
            self.order_manager.store.save_order(order)

    def _reference_price(self, order: Order, next_bars: pd.DataFrame) -> Optional[float]:
        """Best reference price: previous close if available, else next open."""
        if "Close" in next_bars.columns and len(next_bars["Close"]) > 1:
            return float(next_bars["Close"].iloc[-2])
        if "Open" in next_bars.columns:
            return float(next_bars["Open"].iloc[0])
        return order.limit_price

    def _resolve_fill_price(self, order: Order, next_bars: pd.DataFrame) -> Optional[float]:
        """
        Returns the fill price for the order, or None if a limit order is not touched.

        - MARKET:   fill at next bar Open with directional slippage applied.
        - LIMIT buy:  first bar whose Low <= limit_price; fill at min(open, limit)
                      (aggressive) or limit (passive). We use limit price to stay
                      conservative (never assume better than our limit).
        - LIMIT sell: first bar whose High >= limit_price; fill at limit price.
        """
        first_open = float(next_bars["Open"].iloc[0]) if "Open" in next_bars.columns else 0.0

        if order.order_type.value == "market" or order.order_type.value == "stop":
            slip = self.slippage_bps / 10000.0
            if order.side.value == "buy":
                return first_open * (1.0 + slip) if first_open else order.limit_price
            return first_open * (1.0 - slip) if first_open else order.limit_price

        # Limit orders
        if order.limit_price is None:
            return first_open or None

        if "Low" in next_bars.columns and order.side.value == "buy":
            for low in next_bars["Low"]:
                if float(low) <= order.limit_price:
                    return order.limit_price
            return None

        if "High" in next_bars.columns and order.side.value == "sell":
            for high in next_bars["High"]:
                if float(high) >= order.limit_price:
                    return order.limit_price
            return None

        return None
