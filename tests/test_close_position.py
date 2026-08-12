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


class ClosePositionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        ui_module.pending_trades = {}
        ui_module.ea_state["close_request"] = None
        ui_module.ea_state["positions"] = {}
        ui_module.ea_state["last_seen"] = datetime.now().isoformat()

    def test_close_position_missing_ticket(self):
        response = self.client.post("/api/close_position", json={})
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("error", payload)

    def test_close_position_duplicate_protection(self):
        ui_module.ea_state["close_request"] = {
            "ticket": 12345,
            "symbol": "EURUSD",
            "status": "processing",
        }
        response = self.client.post("/api/close_position", json={"ticket": 12345})
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["status"], "already_processing")

    def test_close_position_already_closed_via_api(self):
        ui_module.ensure_connected = lambda: True
        ui_module.get_open_positions = lambda symbol=None: []
        ui_module.close_position = lambda ticket: None

        response = self.client.post("/api/close_position", json={"ticket": 99999})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "already_closed")

    def test_close_position_queued_for_ea(self):
        class FakePos:
            def __init__(self):
                self.ticket = 12345
        ui_module.ensure_connected = lambda: True
        ui_module.get_open_positions = lambda symbol=None: [FakePos()]
        ui_module.close_position = lambda ticket: None

        response = self.client.post("/api/close_position", json={"ticket": 12345})
        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["status"], "queued_for_ea")
        self.assertIsNotNone(ui_module.ea_state.get("close_request"))

    def test_close_position_success_via_direct(self):
        class FakePos:
            def __init__(self):
                self.ticket = 12345
        mock_result = MagicMock()
        mock_result.retcode = 10009
        mock_result.comment = "Success"
        mock_result.order = 55555
        mock_result.deal = 66666
        mock_result.verified_closed = True
        mock_result.already_closed = False
        mock_result.volume = 0.1

        ui_module.ensure_connected = lambda: True
        ui_module.get_open_positions = lambda symbol=None: [FakePos()]
        ui_module.close_position = lambda ticket: mock_result
        ui_module._update_trade_closed = MagicMock()

        response = self.client.post("/api/close_position", json={"ticket": 12345})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "closed")

    def test_ea_report_close_handles_statuses(self):
        with patch("ui.notify") as mock_notify, patch.object(ui_module, "_update_trade_closed") as mock_closed, patch.object(ui_module, "_update_trade_failed") as mock_failed:
            response = self.client.post(
                "/api/ea/report_close",
                json={"ticket": 12345, "status": "closed", "symbol": "EURUSD", "direction": "BUY", "volume": 0.1, "price": 1.1000, "pnl": 50.0}
            )
            self.assertEqual(response.status_code, 200)
            mock_closed.assert_called_once()

        ui_module.ea_state["close_request"] = {"ticket": 12345}

        with patch("ui.notify") as mock_notify, patch.object(ui_module, "_update_trade_failed") as mock_failed:
            response = self.client.post(
                "/api/ea/report_close",
                json={"ticket": 12345, "status": "failed", "retcode": 10004, "comment": "Market closed"}
            )
            self.assertEqual(response.status_code, 200)
            mock_failed.assert_called_once_with(12345, 10004, "Market closed")

        ui_module.ea_state["close_request"] = {"ticket": 12345}

        with patch("ui.notify") as mock_notify, patch.object(ui_module, "_update_trade_closed") as mock_closed:
            response = self.client.post(
                "/api/ea/report_close",
                json={"ticket": 12345, "status": "already_closed"}
            )
            self.assertEqual(response.status_code, 200)
            mock_closed.assert_called_once_with(12345)


if __name__ == "__main__":
    unittest.main()
