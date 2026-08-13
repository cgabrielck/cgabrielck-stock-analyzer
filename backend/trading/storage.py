import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from backend.trading.models import Order, OrderStatus
from backend.trading.engine.order_manager import OrderStore
from backend.utils.constants import DATA_DIR

class JSONOrderStore(OrderStore):
    """
    Durable JSON-file backed store for trading orders.
    Matches the simplicity of other local storage in the app (like portfolio.json).
    """
    def __init__(self, filepath=None):
        if filepath is None:
            data_path = Path(DATA_DIR)
            data_path.mkdir(parents=True, exist_ok=True)
            self.filepath = str(data_path / "trading_orders.json")
        else:
            self.filepath = str(filepath)
            
        self._lock = threading.RLock()
        self._orders_by_id: Dict[str, Order] = {}
        self._orders_by_idem_key: Dict[str, Order] = {}
        self._load()

    def _load(self):
        with self._lock:
            if not os.path.exists(self.filepath):
                return
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                    for item in data:
                        order = Order.model_validate(item)
                        self._orders_by_id[order.id] = order
                        self._orders_by_idem_key[order.idempotency_key] = order
            except json.JSONDecodeError:
                # Handle corrupted file gracefully, maybe backup and start fresh
                pass

    def _save(self):
        with self._lock:
            data = [order.model_dump(mode='json') for order in self._orders_by_id.values()]
            
            # Write to temp file then rename for atomic save
            temp_file = str(self.filepath) + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.filepath)

    def save_order(self, order: Order) -> None:
        with self._lock:
            self._orders_by_id[order.id] = order
            self._orders_by_idem_key[order.idempotency_key] = order
            self._save()

    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]:
        with self._lock:
            return self._orders_by_idem_key.get(key)

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            return self._orders_by_id.get(order_id)
            
    def get_recent_orders(self, limit: int = 50) -> List[Order]:
        with self._lock:
            sorted_orders = sorted(self._orders_by_id.values(), key=lambda o: o.created_at, reverse=True)
            return sorted_orders[:limit]
