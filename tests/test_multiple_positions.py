import importlib.util
import pathlib
import unittest


class MultiplePositionsTests(unittest.TestCase):
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
        self.module.ea_state["positions"] = {}
        self.module.mt5 = None

        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["dashboard_authenticated"] = True

        conn = self.module._get_db()
        try:
            conn.execute("DELETE FROM trades WHERE symbol='EURUSD'")
            conn.execute("DELETE FROM trades WHERE symbol='GBPUSD'")
            conn.commit()
        finally:
            conn.close()

    def test_max_positions_5_allowed(self):
        for i in range(5):
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
                    "manual_sl": 1.09200,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200, f"Trade {i+1} failed: {response.get_json()}")

    def test_sixth_position_rejected(self):
        call_count = [0]
        def mock_get_positions(symbol=None):
            call_count[0] += 1
            if call_count[0] <= 5:
                return [{"ticket": 1000 + i, "symbol": "EURUSD", "volume": 0.1} for i in range(call_count[0] - 1)]
            return [{"ticket": 1000 + i, "symbol": "EURUSD", "volume": 0.1} for i in range(5)]
        
        self.module._execution_get_open_positions = mock_get_positions

        for i in range(5):
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
                    "manual_sl": 1.09200,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200, f"Trade {i+1} failed: {response.get_json()}")

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
                "manual_sl": 1.09200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Max positions reached", response.get_json()["error"])

    def test_max_total_open_risk_blocks_new_trade(self):
        conn = self.module._get_db()
        try:
            for i in range(5):
                conn.execute(
                    "INSERT INTO trades (time, symbol, direction, lot, entry, sl, tp, status, risk_amount, rr_ratio, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("2024-01-01T00:00:00", "EURUSD", "BUY", 0.1, 1.095, 1.090, 1.120, "Executed", 310.0, 5.0, "2024-01-01T00:00:00"),
                )
            conn.commit()
        finally:
            conn.close()

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
                "manual_sl": 1.09200,
                "risk_amount": 310,
                "rr_ratio": 5,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Max total open risk exceeded", response.get_json()["error"])

    def test_multiple_symbols_and_directions(self):
        self.module.ea_state["symbols"]["GBPUSD"] = {
            "symbol": "GBPUSD",
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "digits": 5,
            "point": 0.00001,
        }

        symbols_dirs = [
            ("EURUSD", "BUY", 1.09500, 1.09000),
            ("EURUSD", "SELL", 1.09500, 1.10000),
            ("GBPUSD", "BUY", 1.26500, 1.26000),
            ("GBPUSD", "SELL", 1.26500, 1.27000),
            ("EURUSD", "BUY", 1.09600, 1.09100),
        ]

        for symbol, direction, close, sl in symbols_dirs:
            high = close + 0.005 if direction == "BUY" else close + 0.005
            low = close - 0.005 if direction == "BUY" else close - 0.005
            if direction == "SELL":
                high = close + 0.005
                low = close - 0.005
            response = self.client.post(
                "/api/prepare_trade",
                json={
                    "symbol": symbol,
                    "direction": direction,
                    "high": high,
                    "low": low,
                    "close": close,
                    "open": close - 0.001,
                    "time": "2024-01-01T00:00:00",
                    "manual_sl": sl,
                    "risk_amount": 310,
                    "rr_ratio": 5,
                },
            )
            self.assertEqual(response.status_code, 200, f"Failed for {symbol} {direction}: {response.get_json()}")

    def test_get_total_open_risk_sums_correctly(self):
        conn = self.module._get_db()
        try:
            conn.execute(
                "INSERT INTO trades (time, symbol, direction, lot, entry, sl, tp, status, risk_amount, rr_ratio, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2024-01-01T00:00:00", "EURUSD", "BUY", 0.1, 1.095, 1.090, 1.120, "Executed", 310.0, 5.0, "2024-01-01T00:00:00"),
            )
            conn.execute(
                "INSERT INTO trades (time, symbol, direction, lot, entry, sl, tp, status, risk_amount, rr_ratio, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2024-01-01T00:00:00", "GBPUSD", "SELL", 0.1, 1.265, 1.270, 1.240, "Executed", 310.0, 5.0, "2024-01-01T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        total = self.module._get_total_open_risk()
        self.assertAlmostEqual(total, 620.0, places=2)


if __name__ == "__main__":
    unittest.main()
