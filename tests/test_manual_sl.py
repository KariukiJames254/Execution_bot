import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone


class ManualSlTests(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _fresh_time(self):
        return datetime.now(timezone.utc).isoformat()

    def setUp(self):
        self.module = self._load_module()
        self.module.ensure_connected = lambda: True
        self.module._execution_get_open_positions = lambda symbol=None: []
        self.module.calculate_lot_from_risk = lambda *args, **kwargs: 0.1
        self.module._preflight_checks = lambda symbol, data=None: [
            {"name": "EA Connected", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Account Info Received", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Symbol Info", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Candle Data Available", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Stop Loss Valid", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Lot Size Valid", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Take Profit Valid", "passed": True, "status": "passed", "message": "OK"},
        ]
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
            "series_synced": 1,
        }
        self.module.ea_state["account"] = {"login": 12345, "balance": 50000}
        self.module.ea_state["last_seen"] = datetime.now(timezone.utc).isoformat()
        self.module.mt5 = None

        from symbol_store import set_symbol_info, set_candle
        set_symbol_info("EURUSD", self.module.ea_state["symbols"]["EURUSD"])
        set_candle("EURUSD", "M15", {"time": datetime.now(timezone.utc).timestamp(), "open": 1.095, "high": 1.100, "low": 1.090, "close": 1.095})

        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["dashboard_authenticated"] = True

        conn = self.module._get_db()
        try:
            conn.execute("DELETE FROM trades WHERE symbol='EURUSD'")
            conn.commit()
        finally:
            conn.close()

    def test_prepare_trade_accepts_manual_sl(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": self._fresh_time(),
                "manual_sl": 1.09200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "armed")
        self.assertEqual(payload["sl"], 1.09200)
        self.assertNotEqual(payload["sl"], 1.09000)

    def test_prepare_trade_rejects_invalid_buy_sl(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": self._fresh_time(),
                "manual_sl": 1.09600,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("Invalid SL for BUY", payload["error"])

    def test_prepare_trade_rejects_invalid_sell_sl(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "SELL",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": self._fresh_time(),
                "manual_sl": 1.09300,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("Invalid SL for SELL", payload["error"])

    def test_preview_trade_recalculates_from_manual_sl(self):
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }
        self.module.calculate_lot_from_risk = lambda entry, sl, risk, symbol=None: 0.2

        response = self.client.post(
            "/api/preview_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "close": 1.09500,
                "manual_sl": 1.09200,
                "time": self._fresh_time(),
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sl"], 1.09200)
        self.assertEqual(payload["lot"], 0.2)
        self.assertEqual(payload["risk_amount"], 310.0)

    def test_max_total_open_risk_check(self):
        self.module._get_total_open_risk = lambda: 5110.0

        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2026-08-09T21:28:18.028670+00:00",
                "manual_sl": 1.09200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("Max total open risk exceeded", payload["error"])


if __name__ == "__main__":
    unittest.main()
