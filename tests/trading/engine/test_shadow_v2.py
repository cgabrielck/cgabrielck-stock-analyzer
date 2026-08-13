"""Tests for ShadowTradingEngine 2.0 realistic fill simulation."""
import unittest
from unittest.mock import MagicMock

import pandas as pd

from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from backend.trading.engine.shadow import ShadowTradingEngine, DEFAULT_SLIPPAGE_BPS
from backend.trading.engine.order_manager import OrderManager


def _order(symbol="AAPL", side="buy", otype="limit", qty=10, limit=150.0):
    return Order(
        id="shadow_test",
        symbol=symbol,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        order_type=OrderType.LIMIT if otype == "limit" else OrderType.MARKET,
        quantity=qty,
        limit_price=limit,
        idempotency_key="shadow_key",
        status=OrderStatus.RISK_APPROVED,
    )


def _bars(opens, lows, highs, closes):
    return pd.DataFrame({
        "Open": opens, "Low": lows, "High": highs, "Close": closes,
    })


class TestShadowV2(unittest.TestCase):
    def setUp(self):
        self.mock_store = MagicMock()
        self.mock_broker = MagicMock()
        self.manager = OrderManager(broker=self.mock_broker, store=self.mock_store)
        self.engine = ShadowTradingEngine(self.manager, slippage_bps=DEFAULT_SLIPPAGE_BPS)

    def test_market_buy_pays_slippage_above_next_open(self):
        o = _order(otype="market", limit=None)
        bars = _bars([100.0, 101.0], [99.0, 100.5], [101.0, 102.0], [100.5, 101.5])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        expected = 100.0 * (1 + DEFAULT_SLIPPAGE_BPS / 10000.0)
        self.assertAlmostEqual(filled.filled_avg_price, expected, places=5)
        self.assertGreater(filled.slippage_pct, 0)

    def test_market_sell_pays_slippage_below_next_open(self):
        o = _order(side="sell", otype="market", limit=None)
        bars = _bars([100.0], [99.0], [101.0], [100.5])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        expected = 100.0 * (1 - DEFAULT_SLIPPAGE_BPS / 10000.0)
        self.assertAlmostEqual(filled.filled_avg_price, expected, places=5)

    def test_limit_buy_fills_when_low_crosses(self):
        o = _order(limit=100.0)
        bars = _bars([101.0, 99.5], [100.8, 99.0], [101.2, 99.8], [100.9, 99.2])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.filled_avg_price, 100.0)  # fills at limit, not better

    def test_limit_buy_not_touched_cancels(self):
        o = _order(limit=95.0)
        bars = _bars([100.0, 100.5], [99.0, 99.5], [101.0, 101.2], [100.5, 100.8])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        self.assertEqual(filled.status, OrderStatus.CANCELLED)
        self.assertIn("not touched", filled.error_message or "")

    def test_limit_sell_fills_when_high_crosses(self):
        o = _order(side="sell", limit=110.0)
        bars = _bars([109.0, 111.0], [108.5, 110.5], [109.8, 111.5], [109.5, 111.2])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.filled_avg_price, 110.0)

    def test_limit_sell_not_touched_cancels(self):
        o = _order(side="sell", limit=115.0)
        bars = _bars([109.0], [108.5], [109.8], [109.5])
        filled = self.engine.simulate_submission(o, next_bars=bars)
        self.assertEqual(filled.status, OrderStatus.CANCELLED)

    def test_partial_fill_respects_quantity_touched(self):
        o = _order(qty=10, limit=100.0)
        bars = _bars([101.0, 99.5], [100.8, 99.0], [101.2, 99.8], [100.9, 99.2])
        filled = self.engine.simulate_submission(o, next_bars=bars, quantity_touched=4)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.quantity, 4)

    def test_no_forward_bars_falls_back_to_instant_fill(self):
        o = _order(limit=150.0)
        filled = self.engine.simulate_submission(o, next_bars=None)
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.filled_avg_price, 150.0)
        self.assertEqual(filled.slippage_pct, 0.0)

    def test_wrong_state_ignored(self):
        o = _order()
        o.status = OrderStatus.DRAFT
        filled = self.engine.simulate_submission(o, next_bars=None)
        self.assertEqual(filled.status, OrderStatus.DRAFT)
        self.mock_store.save_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
