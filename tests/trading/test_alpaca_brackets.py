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
