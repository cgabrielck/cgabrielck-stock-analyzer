"""Tests for durable order stores."""
import os
import tempfile
import unittest

from backend.trading.models import Order, OrderSide, OrderType
from backend.trading.storage import SQLiteOrderStore, create_order_store, JSONOrderStore


class TestSQLiteOrderStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = SQLiteOrderStore(filepath=self.tmp.name)

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_save_and_retrieve(self):
        order = Order(
            id="sqlite-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=3,
            limit_price=100.0,
            idempotency_key="idem-sqlite-1",
            take_profit_price=110.0,
            stop_loss_price=95.0,
            order_class="bracket",
        )
        self.store.save_order(order)
        loaded = self.store.get_order("sqlite-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.symbol, "AAPL")
        self.assertEqual(loaded.order_class, "bracket")
        by_key = self.store.get_order_by_idempotency_key("idem-sqlite-1")
        self.assertEqual(by_key.id, "sqlite-1")
        recent = self.store.get_recent_orders(limit=5)
        self.assertEqual(len(recent), 1)

    def test_factory_defaults_to_sqlite(self):
        path = self.tmp.name + ".factory"
        store = create_order_store(backend="sqlite", filepath=path)
        self.assertIsInstance(store, SQLiteOrderStore)
        os.unlink(path) if os.path.exists(path) else None

    def test_factory_json(self):
        path = self.tmp.name + ".json"
        store = create_order_store(backend="json", filepath=path)
        self.assertIsInstance(store, JSONOrderStore)
        if os.path.exists(path):
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
