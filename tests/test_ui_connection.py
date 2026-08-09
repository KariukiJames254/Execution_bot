import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone


class UiConnectionTests(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_is_connected_helper_exists(self):
        module = self._load_module()

        self.assertTrue(callable(getattr(module, "is_connected", None)))
        self.assertIsInstance(module.is_connected(), bool)

    def test_candle_data_route_returns_json_error(self):
        module = self._load_module()
        module.is_connected = lambda: False
        module.get_latest_candle = lambda symbol: None

        client = module.app.test_client()
        with client.session_transaction() as session:
            session["dashboard_authenticated"] = True

        response = client.get("/api/candle_data", headers={"X-Symbol": "EURUSD"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["error"], "No candle data")

    def test_status_endpoint_includes_login_and_account_values(self):
        module = self._load_module()
        module.ea_state["account"] = {"login": 12345, "balance": 1000.25, "equity": 1100.5, "server": "Test Server"}
        module.ea_state["market"] = {}
        module.ea_state["positions"] = {}

        client = module.app.test_client()
        with client.session_transaction() as session:
            session["dashboard_authenticated"] = True

        response = client.get("/api/status", headers={"X-Symbol": "EURUSD"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["login"], 12345)
        self.assertEqual(payload["balance"], 1000.25)
        self.assertEqual(payload["equity"], 1100.5)
        self.assertEqual(payload["broker"], "Test Server")

    def test_status_endpoint_works_without_dashboard_session(self):
        module = self._load_module()

        client = module.app.test_client()
        response = client.get("/api/status", headers={"X-Symbol": "EURUSD"})

        self.assertEqual(response.status_code, 200)

    def test_execution_rejection_returns_json_payload(self):
        module = self._load_module()
        module.pending_trades = {"test_trade": {"symbol": "EURUSD", "direction": "BUY", "status": "armed"}}
        module.ensure_connected = lambda: False

        client = module.app.test_client()
        response = client.post(
            "/api/execute_trade",
            json={"trade_id": "test_trade"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["retcode"], 0)
        self.assertIn("comment", payload)

    def test_prepare_trade_returns_json_on_internal_error(self):
        module = self._load_module()
        module.ensure_connected = lambda: True
        module.get_open_positions = lambda symbol: []
        module.calculate_lot_from_risk = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))

        client = module.app.test_client()
        response = client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.1000,
                "low": 1.0900,
                "close": 1.0950,
                "open": 1.0980,
                "time": datetime.now(timezone.utc).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertIn("error", payload)
        self.assertEqual(payload["error"], "boom")

    def test_prepare_trade_uses_fallback_when_open_positions_helper_missing(self):
        module = self._load_module()
        module.ensure_connected = lambda: True
        module.calculate_lot_from_risk = lambda *args, **kwargs: 0.01
        if hasattr(module, "get_open_positions"):
            delattr(module, "get_open_positions")

        client = module.app.test_client()
        response = client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.1000,
                "low": 1.0900,
                "close": 1.0950,
                "open": 1.0980,
                "time": datetime.now(timezone.utc).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "armed")
        self.assertIn("trade_id", payload)

    def test_ea_symbol_info_report_is_stored(self):
        module = self._load_module()
        client = module.app.test_client()
        response = client.post(
            "/api/ea/report_symbol_info",
            json={
                "symbol": "EURUSD",
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "digits": 5,
                "point": 0.00001,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(module.ea_state["symbols"]["EURUSD"]["volume_max"], 100.0)

    def test_prepare_trade_uses_ea_symbol_info(self):
        module = self._load_module()
        module.ensure_connected = lambda: True
        module.calculate_lot_from_risk = lambda *args, **kwargs: 0.01
        module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "digits": 3,
            "point": 0.001,
        }
        module.mt5 = None

        client = module.app.test_client()
        response = client.post(
            "/api/prepare_trade",
            json={
                "symbol": "EURUSD",
                "direction": "BUY",
                "high": 1.1000,
                "low": 1.0900,
                "close": 1.0950,
                "open": 1.0980,
                "time": datetime.now(timezone.utc).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "armed")
        self.assertEqual(payload["entry"], 1.095)


if __name__ == "__main__":
    unittest.main()
