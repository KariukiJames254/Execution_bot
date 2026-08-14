import importlib.util
import pathlib
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

root = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
ui_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui_module)

app = ui_module.app


class PositionLookupTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        ui_module.pending_trades = {}
        ui_module.ea_state["close_request"] = None
        ui_module.ea_state["positions"] = {}
        ui_module.ea_state["last_seen"] = datetime.now().isoformat()

    def test_close_looks_up_position_by_ticket_not_symbol(self):
        with patch.object(ui_module, "mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = [MagicMock(ticket=99999, symbol="GBPUSD")]
            ui_module.ensure_connected = lambda: True
            ui_module.close_position = lambda ticket: MagicMock(
                retcode=10009, verified_closed=True, already_closed=False,
                symbol="GBPUSD", direction="SELL", close_price=1.2500, volume=0.1
            )
            ui_module._update_trade_closed = MagicMock()

            response = self.client.post("/api/close_position", json={"ticket": 99999})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["status"], "closed")
            self.assertEqual(payload["symbol"], "GBPUSD")

    def test_close_reports_already_closed_only_when_mt5_confirms(self):
        with patch.object(ui_module, "mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = []
            ui_module.ensure_connected = lambda: True

            response = self.client.post("/api/close_position", json={"ticket": 99999})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["status"], "already_closed")


class TradeHistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.module = ui_module
        self.module._save_pending_trades_to_disk = lambda: None
        self.module._save_trade_history_entry = MagicMock()

    def test_transition_to_open_saves_history(self):
        trade = {
            "trade_id": "EURUSD_20240101_BUY",
            "symbol": "EURUSD",
            "direction": "BUY",
            "status": "executing",
        }
        self.module._transition_state(trade, self.module.TRADE_STATE_OPEN, reason="position_confirmed")
        self.assertEqual(trade["status"], "open")
        self.module._save_trade_history_entry.assert_called_once()

    def test_transition_to_failed_saves_history(self):
        trade = {
            "trade_id": "EURUSD_20240101_BUY",
            "symbol": "EURUSD",
            "direction": "BUY",
            "status": "armed",
        }
        self.module._transition_state(trade, self.module.TRADE_STATE_FAILED, reason="ea_error")
        self.assertEqual(trade["status"], "failed")
        self.module._save_trade_history_entry.assert_called_once()

    def test_transition_to_non_final_does_not_save_history(self):
        trade = {
            "trade_id": "EURUSD_20240101_BUY",
            "symbol": "EURUSD",
            "direction": "BUY",
            "status": "armed",
        }
        self.module._transition_state(trade, "executing", reason="ea_reported")
        self.assertEqual(trade["status"], "executing")
        self.module._save_trade_history_entry.assert_not_called()


class MT5ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_reconcile_imports_missing_bot_trades(self):
        with patch.object(ui_module, "mt5") as mock_mt5, \
             patch.object(ui_module, "is_connected", return_value=True), \
             patch.object(ui_module, "_get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = None

            now = datetime.now()
            entry_deal = MagicMock()
            entry_deal.magic = 123456
            entry_deal.entry = ui_module.mt5.DEAL_ENTRY_IN
            entry_deal.position_id = 55555
            entry_deal.symbol = "EURUSD"
            entry_deal.time = now.timestamp()
            entry_deal.type = ui_module.mt5.DEAL_TYPE_BUY
            entry_deal.volume = 0.1
            entry_deal.price = 1.1000
            entry_deal.ticket = 111111

            exit_deal = MagicMock()
            exit_deal.magic = 123456
            exit_deal.entry = ui_module.mt5.DEAL_ENTRY_OUT
            exit_deal.position_id = 55555
            exit_deal.time = (now + __import__('datetime').timedelta(hours=1)).timestamp()
            exit_deal.price = 1.1100
            exit_deal.profit = 50.0
            exit_deal.ticket = 222222

            mock_mt5.history_deals_get.return_value = [entry_deal, exit_deal]

            ui_module._reconcile_mt5_history()

            insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO trades" in str(c)]
            self.assertTrue(len(insert_calls) > 0)

    def test_reconcile_does_not_duplicate_existing_trades(self):
        with patch.object(ui_module, "mt5") as mock_mt5, \
             patch.object(ui_module, "is_connected", return_value=True), \
             patch.object(ui_module, "_get_db") as mock_get_db:
            mock_conn = MagicMock()
            mock_get_db.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = MagicMock()

            now = datetime.now()
            entry_deal = MagicMock()
            entry_deal.magic = 123456
            entry_deal.entry = ui_module.mt5.DEAL_ENTRY_IN
            entry_deal.position_id = 55555
            entry_deal.symbol = "EURUSD"
            entry_deal.time = now.timestamp()
            entry_deal.type = ui_module.mt5.DEAL_TYPE_BUY
            entry_deal.volume = 0.1
            entry_deal.price = 1.1000
            entry_deal.ticket = 111111

            exit_deal = MagicMock()
            exit_deal.magic = 123456
            exit_deal.entry = ui_module.mt5.DEAL_ENTRY_OUT
            exit_deal.position_id = 55555
            exit_deal.time = (now + __import__('datetime').timedelta(hours=1)).timestamp()
            exit_deal.price = 1.1100
            exit_deal.profit = 50.0
            exit_deal.ticket = 222222

            mock_mt5.history_deals_get.return_value = [entry_deal, exit_deal]

            ui_module._reconcile_mt5_history()

            insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO trades" in str(c)]
            self.assertEqual(len(insert_calls), 0)


if __name__ == "__main__":
    unittest.main()
