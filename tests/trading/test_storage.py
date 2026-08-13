import unittest
import os
import json
from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from backend.trading.storage import JSONOrderStore

class TestJSONOrderStore(unittest.TestCase):
    def setUp(self):
        self.filepath = "test_orders.json"
        # Ensure clean state
        if os.path.exists(self.filepath):
            os.remove(self.filepath)
        self.store = JSONOrderStore(filepath=self.filepath)

    def tearDown(self):
        if os.path.exists(self.filepath):
            os.remove(self.filepath)
        if os.path.exists(self.filepath + ".tmp"):
             os.remove(self.filepath + ".tmp")

    def test_save_and_retrieve_order(self):
        order = Order(
            id="test_id_1",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            limit_price=150.0,
            idempotency_key="idem_1"
        )
        
        self.store.save_order(order)
        
        # Retrieve by ID
        retrieved_by_id = self.store.get_order("test_id_1")
        self.assertIsNotNone(retrieved_by_id)
        self.assertEqual(retrieved_by_id.symbol, "AAPL")
        
        # Retrieve by Idempotency Key
        retrieved_by_idem = self.store.get_order_by_idempotency_key("idem_1")
        self.assertIsNotNone(retrieved_by_idem)
        self.assertEqual(retrieved_by_idem.symbol, "AAPL")
        
        # Ensure it actually saved to disk
        with open(self.filepath, 'r') as f:
            data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['id'], "test_id_1")

    def test_persistence_across_instances(self):
        order = Order(
            id="test_id_2", symbol="MSFT", side=OrderSide.SELL,
            order_type=OrderType.MARKET, quantity=5, idempotency_key="idem_2"
        )
        self.store.save_order(order)
        
        # Create a new store instance pointing to the same file
        new_store = JSONOrderStore(filepath=self.filepath)
        
        retrieved = new_store.get_order("test_id_2")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.symbol, "MSFT")

    def test_get_recent_orders(self):
        for i in range(3):
            order = Order(
                id=f"test_id_{i}", symbol="TSLA", side=OrderSide.BUY,
                order_type=OrderType.MARKET, quantity=1, idempotency_key=f"idem_{i}"
            )
            self.store.save_order(order)
            
        recent = self.store.get_recent_orders(limit=2)
        self.assertEqual(len(recent), 2)

if __name__ == '__main__':
    unittest.main()
