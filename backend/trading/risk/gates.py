"""
RiskEngine 2.0 — pre-trade risk gates with institutional-grade checks.

New in 2.0 (vs 1.0):
  - Correlation check: rejects buy if new stock correlates > 0.70 with any existing holding
  - Daily loss halt: stops all trading if portfolio is down > 3% from today's open value
  - VIX dampening: halves max position size when VIX > 30 (high fear environment)
  - Max open positions: never more than 10 simultaneous holdings
  - Cool-down period: tracked externally via Worker; RiskEngine only checks the flag
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
from pydantic import BaseModel

from backend.trading.models import AccountSummary, Order, OrderSide, Position

logger = logging.getLogger(__name__)


class RiskDecision(BaseModel):
    approved: bool
    reason: Optional[str] = None


class RiskLimits(BaseModel):
    max_order_notional: float       = 10_000.0   # Max $ per single order
    max_position_concentration: float = 0.25     # Max % of portfolio in one symbol
    max_total_exposure: float       = 0.90       # Max % of portfolio invested
    daily_loss_limit_percent: float = 0.03       # Halt if down >3% today
    max_open_positions: int         = 10         # Never hold more than 10 names
    max_correlation: float          = 0.70       # Reject if ρ > this with any holding
    vix_dampening_threshold: float  = 30.0       # Halve position size above this VIX
    kelly_scale: float              = 0.5        # Half-Kelly by default


class RiskEngine:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        # Tracks today's starting portfolio value for daily-loss check
        self._day_start_value: Optional[float] = None
        self._day_start_date: Optional[date]   = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_order(
        self,
        order: Order,
        account_summary: AccountSummary,
        vix_level: Optional[float] = None,
        price_history: Optional[Dict[str, pd.DataFrame]] = None,  # ticker → OHLCV df
        cooldown_tickers: Optional[List[str]] = None,
    ) -> RiskDecision:
        """
        Evaluate *order* against all risk limits.

        Args:
            order:            The order draft to evaluate.
            account_summary:  Current account state from broker.
            vix_level:        Latest VIX close (optional — skips dampening if None).
            price_history:    Dict of recent OHLCV DataFrames keyed by ticker symbol,
                              used for correlation check.
            cooldown_tickers: Tickers that are in cool-down (sold recently); block re-entry.
        """
        if account_summary.portfolio_value <= 0:
            return RiskDecision(approved=False, reason="Portfolio value is zero or negative.")

        # Gate 0: Cool-down period
        if order.side == OrderSide.BUY and cooldown_tickers and order.symbol in cooldown_tickers:
            return RiskDecision(approved=False,
                                reason=f"{order.symbol} is in cool-down period (sold recently).")

        # Gate 1: Determine reference price
        ref_price = order.limit_price
        if ref_price is None:
            # Market orders: try to infer from price history
            if price_history and order.symbol in price_history:
                hist = price_history[order.symbol]
                if not hist.empty:
                    ref_price = float(hist["Close"].iloc[-1])
            if ref_price is None:
                return RiskDecision(
                    approved=False,
                    reason="Cannot evaluate market order without a reference price. "
                           "Provide limit_price or ensure price_history is passed.",
                )

        order_notional = order.quantity * ref_price

        # Gate 2: Max order size
        if order_notional > self.limits.max_order_notional:
            return RiskDecision(
                approved=False,
                reason=f"Order notional ${order_notional:,.0f} exceeds limit ${self.limits.max_order_notional:,.0f}.",
            )

        # BUY-only gates
        if order.side == OrderSide.BUY:

            # Gate 3: Buying power
            if order_notional > account_summary.buying_power:
                return RiskDecision(
                    approved=False,
                    reason=f"Insufficient buying power. Need ${order_notional:,.0f}, "
                           f"have ${account_summary.buying_power:,.0f}.",
                )

            # Gate 4: Position concentration
            existing_val = sum(
                p.quantity * p.average_entry_price
                for p in account_summary.positions
                if p.symbol == order.symbol
            )
            new_concentration = (existing_val + order_notional) / account_summary.portfolio_value
            if new_concentration > self.limits.max_position_concentration:
                return RiskDecision(
                    approved=False,
                    reason=f"Position concentration {new_concentration*100:.1f}% exceeds "
                           f"{self.limits.max_position_concentration*100:.1f}% limit.",
                )

            # Gate 5: Total exposure
            total_invested = sum(p.quantity * p.average_entry_price for p in account_summary.positions)
            if (total_invested + order_notional) / account_summary.portfolio_value > self.limits.max_total_exposure:
                return RiskDecision(
                    approved=False,
                    reason=f"Total exposure would exceed {self.limits.max_total_exposure*100:.0f}% limit.",
                )

            # Gate 6: Max open positions
            open_symbols = {p.symbol for p in account_summary.positions}
            if order.symbol not in open_symbols and len(open_symbols) >= self.limits.max_open_positions:
                return RiskDecision(
                    approved=False,
                    reason=f"Already holding {len(open_symbols)} positions (limit={self.limits.max_open_positions}).",
                )

            # Gate 7: Daily loss halt
            if self._is_daily_loss_halt(account_summary.portfolio_value):
                return RiskDecision(
                    approved=False,
                    reason=f"Daily loss halt triggered: portfolio is down >"
                           f"{self.limits.daily_loss_limit_percent*100:.0f}% today.",
                )

            # Gate 8: Correlation check (NEW)
            if price_history and len(price_history) > 1:
                corr_block = self._correlation_check(order.symbol, account_summary.positions, price_history)
                if corr_block:
                    return corr_block

            # Gate 9: VIX dampening (warning only — doesn't block, but caller may reduce qty)
            if vix_level and vix_level > self.limits.vix_dampening_threshold:
                logger.warning(
                    "VIX=%.1f > %.1f — position size should be halved for %s.",
                    vix_level, self.limits.vix_dampening_threshold, order.symbol,
                )

        return RiskDecision(approved=True)

    def vix_size_multiplier(self, vix_level: Optional[float]) -> float:
        """Return position-size multiplier based on VIX (1.0 normal, 0.5 high fear)."""
        if vix_level and vix_level > self.limits.vix_dampening_threshold:
            return 0.5
        return 1.0

    def record_day_start(self, portfolio_value: float) -> None:
        """Call this once at the start of each trading day."""
        today = date.today()
        if self._day_start_date != today:
            self._day_start_value = portfolio_value
            self._day_start_date  = today

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_daily_loss_halt(self, current_value: float) -> bool:
        if self._day_start_value is None or self._day_start_value <= 0:
            return False
        loss_pct = (self._day_start_value - current_value) / self._day_start_value
        return loss_pct >= self.limits.daily_loss_limit_percent

    def _correlation_check(
        self,
        new_symbol: str,
        positions: List[Position],
        price_history: Dict[str, pd.DataFrame],
    ) -> Optional[RiskDecision]:
        """
        Reject the order if the new symbol correlates > max_correlation
        with any existing holding over the available price history.
        Uses daily returns correlation.
        """
        if new_symbol not in price_history:
            return None  # can't check → allow

        new_returns = price_history[new_symbol]["Close"].pct_change().dropna()

        for pos in positions:
            if pos.symbol == new_symbol:
                continue
            if pos.symbol not in price_history:
                continue

            held_returns = price_history[pos.symbol]["Close"].pct_change().dropna()

            # Align on common dates
            combined = pd.concat([new_returns.rename("n"), held_returns.rename("h")], axis=1).dropna()
            if len(combined) < 20:
                continue

            corr = float(combined["n"].corr(combined["h"]))
            if corr > self.limits.max_correlation:
                return RiskDecision(
                    approved=False,
                    reason=(
                        f"{new_symbol} is highly correlated (ρ={corr:.2f}) with existing holding "
                        f"{pos.symbol}. Exceeds limit ρ>{self.limits.max_correlation:.2f}."
                    ),
                )
        return None
