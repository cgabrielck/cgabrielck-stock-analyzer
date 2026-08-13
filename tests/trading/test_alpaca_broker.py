import unittest
from unittest.mock import MagicMock, patch
import os
from datetime import datetime, timezone

# Set dummy env vars for testing before importing the broker
os.environ["APCA_API_KEY_ID"] = "test_key"
os.environ["APCA_API_SECRET_KEY"] = "test_secret"

from backend.trading.alpaca_broker import AlpacaBroker
from backend.trading.models import Order, OrderSide, OrderType, OrderStatus
from alpaca.common.exceptions import APIError


# Helper to create mock Alpaca objects with attributes
def create_mock_alpaca_object(attributes):
    mock_obj = MagicMock()
    for key, value in attributes.items():
        if isinstance(value, datetime):
            setattr(mock_obj, key, value.replace(tzinfo=timezone.utc))
        else:
            setattr(mock_obj, key, value)
    return mock_obj


class TestAlpacaBroker(unittest.TestCase):

    @patch('backend.trading.alpaca_broker.TradingClient')
    def setUp(self, MockTradingClient):
        """Set up a mock Alpaca API for each test."""
        mock_account = MagicMock()
        mock_account.trading_blocked = False
        mock_account.cash = '100000'
        mock_account.buying_power = '200000'
        mock_account.portfolio_value = '125000'

        self.mock_api = MockTradingClient.return_value
        self.mock_api.get_account.return_value = mock_account

        self.broker = AlpacaBroker()
        self.assertEqual(self.broker.api, self.mock_api)

    def test_initialization_success(self):
        """Test successful initialization with environment variables."""
        self.assertIsInstance(self.broker.api, MagicMock)
        self.broker.api.get_account.assert_called_once()
        self.assertTrue(self.broker.is_paper)

    @patch('backend.trading.alpaca_broker.TradingClient')
    def test_initialization_no_credentials(self, MockTradingClient):
        """Test initialization failure when credentials are not set."""
        if "APCA_API_KEY_ID" in os.environ: del os.environ["APCA_API_KEY_ID"]
        if "APCA_API_SECRET_KEY" in os.environ: del os.environ["APCA_API_SECRET_KEY"]

        with self.assertRaisesRegex(ValueError, "Alpaca API credentials"):
            AlpacaBroker()

        os.environ["APCA_API_KEY_ID"] = "test_key"
        os.environ["APCA_API_SECRET_KEY"] = "test_secret"

    def test_get_account_summary(self):
        """Test retrieving and parsing account summary."""
        mock_positions = [
            create_mock_alpaca_object({'asset_id': 'pos1', 'symbol': 'AAPL', 'qty': '10', 'avg_entry_price': '150.0'}),
            create_mock_alpaca_object({'asset_id': 'pos2', 'symbol': 'GOOG', 'qty': '5', 'avg_entry_price': '2800.0'}),
        ]
        self.broker.api.get_all_positions.return_value = mock_positions

        summary = self.broker.get_account_summary()

        self.assertEqual(summary.cash, 100000.0)
        self.assertEqual(summary.buying_power, 200000.0)
        self.assertEqual(summary.portfolio_value, 125000.0)
        self.assertEqual(len(summary.positions), 2)
        self.assertEqual(summary.positions[0].symbol, 'AAPL')
        self.assertEqual(summary.positions[0].quantity, 10.0)

    def test_submit_order_success(self):
        """Test successful order submission."""
        order_to_submit = Order(
            id="internal_id_123",
            symbol='TSLA',
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            limit_price=200.0,
            idempotency_key="idem_key_1"
        )

        mock_alpaca_order = create_mock_alpaca_object({
            'id': 'broker_order_id_456',
            'status': 'new',
            'submitted_at': datetime.now()
        })
        self.broker.api.submit_order.return_value = mock_alpaca_order

        result_order = self.broker.submit_order(order_to_submit)

        self.broker.api.submit_order.assert_called_once()
        _, kwargs = self.broker.api.submit_order.call_args
        request = kwargs['order_data']
        self.assertEqual(request.symbol, 'TSLA')
        self.assertEqual(request.qty, 10)
        self.assertEqual(request.limit_price, 200.0)
        self.assertEqual(request.client_order_id, 'idem_key_1')

        self.assertEqual(result_order.status, OrderStatus.SUBMITTED)
        self.assertEqual(result_order.id, 'broker_order_id_456')
        self.assertIsNotNone(result_order.submitted_at)

    def test_submit_order_api_error(self):
        """Test order submission failure due to an API error."""
        order_to_submit = Order(
            id="internal_id_123",
            symbol='TSLA',
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
            idempotency_key="idem_key_2"
        )

        self.broker.api.submit_order.side_effect = APIError('{"message": "Insufficient funds"}')

        result_order = self.broker.submit_order(order_to_submit)

        self.assertEqual(result_order.status, OrderStatus.REJECTED)
        self.assertIn("Insufficient funds", result_order.error_message)

    def test_get_order_found(self):
        """Test retrieving an existing order."""
        now = datetime.now()
        mock_alpaca_order = create_mock_alpaca_object({
            'id': 'broker_order_id_789',
            'client_order_id': 'idem_key_3',
            'symbol': 'MSFT',
            'side': 'sell',
            'type': 'limit',
            'qty': '100',
            'status': 'filled',
            'limit_price': '300.50',
            'stop_price': None,
            'submitted_at': now,
            'filled_at': now,
            'canceled_at': None,
            'failed_at': None
        })
        self.broker.api.get_order_by_id.return_value = mock_alpaca_order

        order = self.broker.get_order('broker_order_id_789')

        self.assertIsNotNone(order)
        self.assertEqual(order.id, 'broker_order_id_789')
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.symbol, 'MSFT')

    def test_get_order_not_found(self):
        """Test retrieving a non-existent order."""
        self.broker.api.get_order_by_id.side_effect = APIError('{"message": "Order not found"}')

        order = self.broker.get_order('non_existent_id')

        self.assertIsNone(order)

    def test_cancel_order_success(self):
        """Test successful order cancellation."""
        mock_cancelled_order = create_mock_alpaca_object({
            'id': 'order_to_cancel',
            'status': 'canceled',
            'client_order_id': 'idem_key_4',
            'symbol': 'AMD',
            'side': 'buy',
            'type': 'limit',
            'qty': '50',
            'limit_price': '100',
            'stop_price': None,
            'submitted_at': datetime.now(),
            'filled_at': None,
            'canceled_at': datetime.now(),
            'failed_at': None,
        })
        self.broker.api.get_order_by_id.return_value = mock_cancelled_order
        self.broker.api.cancel_order_by_id.return_value = None

        result = self.broker.cancel_order('order_to_cancel')

        self.broker.api.cancel_order_by_id.assert_called_once_with('order_to_cancel')
        self.broker.api.get_order_by_id.assert_called_once_with('order_to_cancel')
        self.assertEqual(result.status, OrderStatus.CANCELLED)


if __name__ == '__main__':
    unittest.main()
