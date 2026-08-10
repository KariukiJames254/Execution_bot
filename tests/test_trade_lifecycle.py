import importlib.util
import pathlib
import unittest
from datetime import datetime, timezone
from urllib.parse import quote
from unittest.mock import patch

import execution


class TradeLifecycleTests(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _fresh_time(self):
        return datetime.now(timezone.utc).isoformat()

    def _encoded_trade_id(self, trade_id):
        return quote(trade_id, safe='')

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
        self.module._preflight_has_failures = lambda checks: any(c["status"] == "failed" for c in checks)
        self.module._preflight_all_passed = lambda checks: all(c["status"] == "passed" for c in checks)
        self.module._get_open_positions_impl = lambda symbol=None: []
        self.module._get_total_open_risk = lambda: 0.0
        self.module._validate_candle_freshness = lambda symbol, timeframe, candle_data: (True, "Fresh candle")
        self.module._time_to_close = lambda ct, tf: 60
        self.module._compute_candle_close_unix = lambda trade: 9999999999
        self.module.ea_state["market"]["EURUSD"] = {"bid": 1.15558, "ask": 1.15580}
        self.module.ea_state["positions"] = {}
        self.client = self.module.app.test_client()

    def test_arm_trade_appears_in_status(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(status_response.status_code, 200)
        data = status_response.get_json()
        self.assertIsNotNone(data.get("pending_trade"))
        self.assertEqual(data["pending_trade"]["trade_id"], trade_id)
        self.assertEqual(data["pending_trade"]["status"], "armed")

    def test_arm_trade_ea_pending_returns_trade(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        pending_response = self.client.get(f"/api/ea/pending?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(pending_response.status_code, 200)
        data = pending_response.get_json()
        self.assertEqual(data["trade_id"], trade_id)
        self.assertEqual(data["status"], "armed")

    def test_empty_trade_id_polling_does_not_remove_trade(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        for _ in range(3):
            pending_response = self.client.get("/api/ea/pending?trade_id=")
            self.assertEqual(pending_response.status_code, 200)

        status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(status_response.status_code, 200)
        data = status_response.get_json()
        self.assertIsNotNone(data.get("pending_trade"))
        self.assertEqual(data["pending_trade"]["trade_id"], trade_id)

    def test_trade_remains_armed_across_multiple_polls(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        for _ in range(5):
            pending_response = self.client.get(f"/api/ea/pending?trade_id={self._encoded_trade_id(trade_id)}")
            self.assertEqual(pending_response.status_code, 200)
            data = pending_response.get_json()
            self.assertEqual(data["status"], "armed")

        status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(status_response.status_code, 200)
        data = status_response.get_json()
        self.assertIsNotNone(data.get("pending_trade"))

    def test_explicit_cancel_removes_trade(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        cancel_response = self.client.post(
            "/api/cancel_trade",
            json={"trade_id": trade_id},
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.get_json()["status"], "cancelled")

        status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(status_response.status_code, 200)
        data = status_response.get_json()
        self.assertIsNone(data.get("pending_trade"))

    def test_successful_execution_removes_pending_state(self):
        self.module.get_current_price = lambda symbol: (1.15558, 1.15580)
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 500.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }

        class FakeResult:
            retcode = 10009
            order = 123456
            deal = 654321
            comment = "Done"

        with patch.object(execution, 'execute_buy', return_value=FakeResult()), \
             patch.object(execution, 'execute_sell', return_value=FakeResult()):
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
                    "manual_sl": 1.08800,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200)
            trade_id = response.get_json()["trade_id"]

            exec_response = self.client.get(
                f"/api/execute_trade?trade_id={self._encoded_trade_id(trade_id)}&symbol=EURUSD&direction=BUY"
            )
            self.assertEqual(exec_response.status_code, 200)
            self.assertEqual(exec_response.get_json()["status"], "executed")

            status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
            self.assertEqual(status_response.status_code, 200)
            data = status_response.get_json()
            self.assertIsNone(data.get("pending_trade"))

    def test_ea_report_execution_removes_pending_state(self):
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
                "manual_sl": 1.08800,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        trade_id = response.get_json()["trade_id"]

        report_response = self.client.post(
            "/api/ea/report_execution",
            json={
                "trade_id": trade_id,
                "status": "executed",
                "retcode": 10009,
                "comment": "Done",
                "order": 123456,
                "deal": 654321,
                "entry": 1.09500,
                "slippage": 0.0,
                "spread": 0.0,
            },
        )
        self.assertEqual(report_response.status_code, 200)

        status_response = self.client.get(f"/api/status?trade_id={self._encoded_trade_id(trade_id)}")
        self.assertEqual(status_response.status_code, 200)
        data = status_response.get_json()
        self.assertIsNone(data.get("pending_trade"))


class ManualSlExecutionTests(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _fresh_time(self):
        return datetime.now(timezone.utc).isoformat()

    def _encoded_trade_id(self, trade_id):
        return quote(trade_id, safe='')

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
        self.module._preflight_has_failures = lambda checks: any(c["status"] == "failed" for c in checks)
        self.module._preflight_all_passed = lambda checks: all(c["status"] == "passed" for c in checks)
        self.module._get_open_positions_impl = lambda symbol=None: []
        self.module._get_total_open_risk = lambda: 0.0
        self.module._validate_candle_freshness = lambda symbol, timeframe, candle_data: (True, "Fresh candle")
        self.module._time_to_close = lambda ct, tf: 60
        self.module._compute_candle_close_unix = lambda trade: 9999999999
        self.module.ea_state["market"]["EURUSD"] = {"bid": 1.15558, "ask": 1.15580}
        self.module.ea_state["positions"] = {}
        self.client = self.module.app.test_client()

    def test_buy_manual_sl_not_replaced_by_candle_low(self):
        self.module.get_current_price = lambda symbol: (1.15558, 1.15580)
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 500.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }

        class FakeResult:
            retcode = 10009
            order = 123456
            deal = 654321
            comment = "Done"

        with patch.object(execution, 'execute_buy', return_value=FakeResult()), \
             patch.object(execution, 'execute_sell', return_value=FakeResult()):
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
                    "manual_sl": 1.08800,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200)
            trade_id = response.get_json()["trade_id"]

            exec_response = self.client.get(
                f"/api/execute_trade?trade_id={self._encoded_trade_id(trade_id)}&symbol=EURUSD&direction=BUY"
            )
            self.assertEqual(exec_response.status_code, 200)
            data = exec_response.get_json()
            self.assertEqual(data["status"], "executed")
            self.assertEqual(data["sl"], 1.08800)

    def test_sell_manual_sl_not_replaced_by_candle_high(self):
        self.module.get_current_price = lambda symbol: (1.15558, 1.15580)
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 500.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }

        class FakeResult:
            retcode = 10009
            order = 123456
            deal = 654321
            comment = "Done"

        with patch.object(execution, 'execute_buy', return_value=FakeResult()), \
             patch.object(execution, 'execute_sell', return_value=FakeResult()):
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
                    "manual_sl": 1.15600,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200)
            trade_id = response.get_json()["trade_id"]

            exec_response = self.client.get(
                f"/api/execute_trade?trade_id={self._encoded_trade_id(trade_id)}&symbol=EURUSD&direction=SELL"
            )
            self.assertEqual(exec_response.status_code, 200)
            data = exec_response.get_json()
            self.assertEqual(data["status"], "executed")
            self.assertEqual(data["sl"], 1.15600)

    def test_invalid_manual_sl_below_entry_for_buy_is_rejected(self):
        self.module.ea_state["symbols"]["EURUSD"] = {
            "symbol": "EURUSD",
            "volume_min": 0.01,
            "volume_max": 500.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }
        original_get_current_price = self.module.get_current_price
        self.module.get_current_price = lambda symbol: (1.08800, 1.08820)

        class FakeResult:
            retcode = 10009
            order = 123456
            deal = 654321
            comment = "Done"

        try:
            with patch.object(execution, 'execute_buy', return_value=FakeResult()), \
                 patch.object(execution, 'execute_sell', return_value=FakeResult()):
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
                        "manual_sl": 1.08825,
                        "risk_amount": 310,
                        "rr_ratio": 5,
                    },
                )
                self.assertEqual(response.status_code, 200)
                trade_id = response.get_json()["trade_id"]

                exec_response = self.client.get(
                    f"/api/execute_trade?trade_id={self._encoded_trade_id(trade_id)}&symbol=EURUSD&direction=BUY"
                )
                self.assertEqual(exec_response.status_code, 200)
                data = exec_response.get_json()
                self.assertEqual(data["status"], "error")
                self.assertIn("Invalid manual SL", data["comment"])
        finally:
            self.module.get_current_price = original_get_current_price


if __name__ == '__main__':
    unittest.main()
