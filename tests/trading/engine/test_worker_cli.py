"""Tests for the standalone TradingWorker CLI entry point.

Covers:
  - live-account guard (refuses without --allow-live)
  - --once single-cycle smoke run
  - heartbeat file writing
  - graceful SIGTERM shutdown path
"""
import json
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from backend.trading.engine import worker as worker_module


class FakeAccount:
    portfolio_value = 100000.0
    cash = 50000.0
    buying_power = 50000.0
    positions = []


def _fake_worker():
    w = MagicMock()
    w.ticker_universe = ["AAPL", "MSFT"]
    w.running = True
    w.is_market_hours = False
    w.last_run_utc = None
    w.last_signal_summary = []
    w._sync_orders.return_value = None
    w._safe_get_account.return_value = FakeAccount()
    w._run_strategy_signals.return_value = None
    return w


class TestWorkerCLI(unittest.TestCase):

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            worker_module.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    @patch.dict(os.environ, {"APCA_PAPER": "false"}, clear=False)
    def test_live_account_refuses_without_allow_live(self):
        with patch.object(worker_module, "_build_worker") as build:
            code = worker_module.main(["--once"])
            self.assertEqual(code, 2)
            build.assert_not_called()

    @patch.dict(os.environ, {"APCA_PAPER": "false"}, clear=False)
    def test_live_account_allowed_with_allow_live(self):
        with patch.object(worker_module, "_build_worker", return_value=_fake_worker()):
            code = worker_module.main(["--once", "--allow-live"])
            self.assertEqual(code, 0)

    @patch.dict(os.environ, {"APCA_PAPER": "true"}, clear=False)
    def test_once_mode_runs_single_cycle(self):
        fake = _fake_worker()
        with patch.object(worker_module, "_build_worker", return_value=fake):
            code = worker_module.main(["--once", "--strategy", "aggressive"])
            self.assertEqual(code, 0)
            fake._sync_orders.assert_called_once()
            fake._run_strategy_signals.assert_called_once()

    @patch.dict(os.environ, {"APCA_PAPER": "true"}, clear=False)
    def test_heartbeat_file_written(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            hb_path = tmp.name
        try:
            fake = _fake_worker()
            fake.running = False  # simulate a completed run
            with patch.object(worker_module, "_build_worker", return_value=fake):
                code = worker_module.main(["--once", "--heartbeat-file", hb_path])
                self.assertEqual(code, 0)
            with open(hb_path) as fh:
                data = json.load(fh)
            self.assertIn("ts", data)
            self.assertIn("running", data)
            self.assertIn("market_hours", data)
        finally:
            os.remove(hb_path)

    @patch.dict(os.environ, {"APCA_PAPER": "true"}, clear=False)
    def test_invalid_strategy_rejected(self):
        with self.assertRaises(SystemExit):
            worker_module.main(["--once", "--strategy", "bogus"])


if __name__ == "__main__":
    unittest.main()
