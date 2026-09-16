"""Alpaca bracket order request construction."""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APCA_API_KEY_ID", "test_key")
os.environ.setdefault("APCA_API_SECRET_KEY", "test_secret")

from backend.trading.alpaca_broker import AlpacaBroker
from backend.trading.models import Order, OrderSide, OrderType


class TestAlpacaBracketOrders(unittest.TestCase):
    def setUp(self):
        patcher = patch("backend.trading.alpaca_broker.TradingClient")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.broker = AlpacaBroker()
        self.broker._api = MagicMock()
        self.broker._verified = True

    def test_bracket_kwargs_attached_for_bracket_orders(self):
        order = Order(
            id="1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=2,
            limit_price=100.0,
            take_profit_price=110.0,
            stop_loss_price=90.0,
            order_class="bracket",
            idempotency_key="br-1",
        )
        req = self.broker._build_order_request(order)
        self.assertTrue(hasattr(req, "order_class"))
        self.assertIsNotNone(req.take_profit)
        self.assertIsNotNone(req.stop_loss)

    def test_bracket_prices_round_to_penny_for_names_over_one_dollar(self):
        order = Order(
            id="3",
            symbol="GOOGL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=28,
            limit_price=349.3901,
            take_profit_price=384.329,
            stop_loss_price=328.4266,
            order_class="bracket",
            idempotency_key="br-googl",
        )
        req = self.broker._build_order_request(order)
        self.assertEqual(req.limit_price, 349.39)
        self.assertEqual(float(req.take_profit.limit_price), 384.33)
        self.assertEqual(float(req.stop_loss.stop_price), 328.43)

    def test_simple_orders_have_no_bracket_legs(self):
        order = Order(
            id="2",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            idempotency_key="simple-1",
            order_class="simple",
        )
        req = self.broker._build_order_request(order)
        # MarketOrderRequest may still expose order_class default; ensure no TP/SL
        self.assertTrue(getattr(req, "take_profit", None) in (None, ))
        self.assertTrue(getattr(req, "stop_loss", None) in (None, ))


if __name__ == "__main__":
    unittest.main()
