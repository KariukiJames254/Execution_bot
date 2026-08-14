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


class ExecutionReportingTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        ui_module.pending_trades = {}
        ui_module.ea_state["close_request"] = None
        ui_module.ea_state["positions"] = {}
        ui_module.ea_state["last_seen"] = datetime.now().isoformat()

    def test_ea_report_executed_transitions_to_executing(self):
        trade = {
            "trade_id": "EURUSD_20240101_BUY",
            "symbol": "EURUSD",
            "direction": "BUY",
            "status": "armed",
            "lot": 0.1,
            "entry": 1.1000,
            "sl": 1.0900,
            "tp": 1.1100,
        }
        ui_module.pending_trades[trade["trade_id"]] = trade

        with patch.object(ui_module, "_confirm_position") as mock_confirm, \
             patch.object(ui_module, "_notify_trade_opened") as mock_notify:
            mock_confirm.return_value = MagicMock(ticket=12345, symbol="EURUSD", price_open=1.1000, volume=0.1)

            response = self.client.post(
                "/api/ea/report_execution",
                json={
                    "trade_id": trade["trade_id"],
                    "status": "executed",
                    "retcode": 10009,
                    "comment": "OK",
                    "order": 12345,
                    "deal": 67890,
                    "entry": 1.1000,
                    "slippage": 0.0,
                    "spread": 0.0,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(trade["status"], "open")
            mock_confirm.assert_called_once()

    def test_ea_report_error_does_not_send_failure_if_position_confirmed(self):
        trade = {
            "trade_id": "EURUSD_20240101_SELL",
            "symbol": "EURUSD",
            "direction": "SELL",
            "status": "armed",
            "lot": 0.1,
            "entry": 1.1541,
            "sl": 1.15414,
            "tp": 1.1539,
        }
        ui_module.pending_trades[trade["trade_id"]] = trade

        with patch.object(ui_module, "_confirm_position") as mock_confirm, \
             patch.object(ui_module, "_notify_trade_opened") as mock_opened, \
             patch.object(ui_module, "_notify_execution_failed") as mock_failed:
            mock_confirm.return_value = MagicMock(ticket=2145988314, symbol="EURUSD", price_open=1.15407, volume=104.62)

            response = self.client.post(
                "/api/ea/report_execution",
                json={
                    "trade_id": trade["trade_id"],
                    "status": "error",
                    "retcode": 4756,
                    "comment": "OrderSend failed",
                    "order": 0,
                    "deal": 0,
                    "entry": 1.15407,
                    "slippage": 0.0,
                    "spread": 0.0,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(trade["status"], "open")
            mock_failed.assert_not_called()
            mock_opened.assert_called_once()

    def test_ea_report_error_sends_failure_when_position_not_confirmed(self):
        trade = {
            "trade_id": "EURUSD_20240101_BUY",
            "symbol": "EURUSD",
            "direction": "BUY",
            "status": "armed",
            "lot": 0.1,
            "entry": 1.1000,
            "sl": 1.0900,
            "tp": 1.1100,
        }
        ui_module.pending_trades[trade["trade_id"]] = trade

        with patch.object(ui_module, "_confirm_position") as mock_confirm, \
             patch.object(ui_module, "_notify_trade_opened") as mock_opened, \
             patch.object(ui_module, "_notify_execution_failed") as mock_failed:
            mock_confirm.return_value = None

            response = self.client.post(
                "/api/ea/report_execution",
                json={
                    "trade_id": trade["trade_id"],
                    "status": "error",
                    "retcode": 4756,
                    "comment": "OrderSend failed",
                    "order": 0,
                    "deal": 0,
                    "entry": 1.1000,
                    "slippage": 0.0,
                    "spread": 0.0,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(trade["status"], "failed")
            mock_failed.assert_called_once()
            mock_opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
