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


class CloseStateReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        ui_module.pending_trades = {}
        ui_module.ea_state["close_request"] = None
        ui_module.ea_state["positions"] = {}
        ui_module.ea_state["last_seen"] = datetime.now().isoformat()

    def test_reconcile_clears_close_request_when_position_gone(self):
        ui_module.ea_state["close_request"] = {
            "ticket": 12345,
            "symbol": "EURUSD",
            "status": "processing",
        }
        ui_module.mt5 = MagicMock()
        ui_module.mt5.positions_get.return_value = []

        ui_module._reconcile_close_state()

        self.assertIsNone(ui_module.ea_state.get("close_request"))

    def test_reconcile_keeps_close_request_when_position_exists(self):
        ui_module.ea_state["close_request"] = {
            "ticket": 12345,
            "symbol": "EURUSD",
            "status": "processing",
        }
        mock_pos = MagicMock()
        mock_pos.ticket = 12345
        ui_module.mt5 = MagicMock()
        ui_module.mt5.positions_get.return_value = [mock_pos]

        ui_module._reconcile_close_state()

        self.assertIsNotNone(ui_module.ea_state.get("close_request"))

    def test_reconcile_does_nothing_when_no_close_request(self):
        ui_module.ea_state["close_request"] = None
        ui_module.mt5 = MagicMock()

        ui_module._reconcile_close_state()

        ui_module.mt5.positions_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
