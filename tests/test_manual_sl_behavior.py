import importlib.util
import pathlib
import unittest


class ManualSlBehaviorTests(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        self.module = self._load_module()
        self.module.ensure_connected = lambda: True
        self.module._execution_get_open_positions = lambda symbol=None: []
        self.module.calculate_lot_from_risk = lambda *args, **kwargs: 0.1
        self.module._preflight_checks = lambda symbol, data=None: [
            {"name": "EA Connected", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Account Info Received", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Symbol Info", "passed": True, "status": "passed", "message": "OK"},
            {"name": "Candle Data", "passed": True, "status": "passed", "message": "OK"},
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
        }
        self.module.ea_state["account"] = {"login": 12345, "balance": 50000}
        self.module.ea_state["last_seen"] = "2024-01-01T00:00:00"
        self.module.mt5 = None

        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["dashboard_authenticated"] = True

        conn = self.module._get_db()
        try:
            conn.execute("DELETE FROM trades WHERE symbol='EURUSD'")
            conn.commit()
        finally:
            conn.close()

    def test_buy_manual_sl_overrides_candle_low(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2024-01-01T00:00:00",
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sl"], 1.08800)
        self.assertNotEqual(payload["sl"], 1.09000)

    def test_sell_manual_sl_overrides_candle_high(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "SELL",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2024-01-01T00:00:00",
                "manual_sl": 1.10200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sl"], 1.10200)
        self.assertNotEqual(payload["sl"], 1.10000)

    def test_manual_sl_change_recalculates_lot_and_tp(self):
        self.module.calculate_lot_from_risk = lambda entry, sl, risk, symbol=None: 0.5 if abs(entry - sl) > 0.005 else 0.2

        response1 = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2024-01-01T00:00:00",
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response1.status_code, 200)
        payload1 = response1.get_json()
        self.assertEqual(payload1["lot"], 0.5)
        self.assertEqual(payload1["tp"], 1.13000)

        response2 = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2024-01-01T00:00:00",
                "manual_sl": 1.09200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response2.status_code, 200)
        payload2 = response2.get_json()
        self.assertEqual(payload2["lot"], 0.2)
        self.assertEqual(payload2["tp"], 1.11000)

    def test_ea_pending_sends_manual_sl_and_risk_fields(self):
        response = self.client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.10000,
                "low": 1.09000,
                "close": 1.09500,
                "open": 1.09800,
                "time": "2024-01-01T00:00:00",
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        pending_response = self.client.get(f"/api/ea/pending?trade_id={trade_id}")
        self.assertEqual(pending_response.status_code, 200)
        payload = pending_response.get_json()
        self.assertEqual(payload["manual_sl"], 1.08800)
        self.assertEqual(payload["risk_amount"], 310.0)
        self.assertEqual(payload["rr_ratio"], 5.0)


if __name__ == "__main__":
    unittest.main()
