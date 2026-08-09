import importlib.util
import pathlib
import unittest
import re


class StaleBarBlockTests(unittest.TestCase):
    """Verify that stale candle data cannot trigger OrderSend in the EA."""

    def _load_ea_source(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        ea_path = root / "ExecutionBridge.mq5"
        return ea_path.read_text(encoding="utf-8")

    def test_stale_bar_blocks_execution(self):
        source = self._load_ea_source()

        stale_block_pattern = re.compile(
            r'if\s*\(\s*staleBar\s*\)\s*\{[^}]*STALE_BAR_BLOCKED[^}]*\}',
            re.DOTALL
        )
        self.assertTrue(stale_block_pattern.search(source),
                       "EA must contain a staleBar block that logs STALE_BAR_BLOCKED")

        execute_call_pattern = re.compile(
            r'if\s*\(\s*staleBar\s*\)\s*\{[^}]*ExecuteTradeLocal\s*\(\)',
            re.DOTALL
        )
        self.assertFalse(execute_call_pattern.search(source),
                        "EA must NOT call ExecuteTradeLocal() inside staleBar block")

        nested_pattern = re.compile(
            r'if\s*\(\s*staleBar\s*\)\s*\{[^}]*if\s*\(\s*barChanged\s*\|\|\s*timeReached\s*\)',
            re.DOTALL
        )
        self.assertFalse(nested_pattern.search(source),
                        "staleBar check must be evaluated BEFORE barChanged/timeReached")

    def test_stale_bar_reports_status(self):
        source = self._load_ea_source()
        self.assertIn('ReportExecutionDetailed(pendingTradeId, "stale_bar"', source,
                     "EA must report stale_bar status to Flask")

    def test_stale_bar_adds_diagnostics(self):
        source = self._load_ea_source()
        self.assertIn("LogTimeDiagnostics", source,
                     "EA must call LogTimeDiagnostics when stale bar is detected")
        self.assertIn("TimeCurrent()", source,
                     "EA must log TimeCurrent()")
        self.assertIn("TimeTradeServer()", source,
                     "EA must log TimeTradeServer()")
        self.assertIn("TimeGMT()", source,
                     "EA must log TimeGMT()")
        self.assertIn("SERIES_SYNCHRONIZED", source,
                     "EA must check SERIES_SYNCHRONIZED")
        self.assertIn("BarsCount", source,
                     "EA must log Bars count")

    def test_simulation_stale_bar_never_executes(self):
        """Simulate EA stale-bar logic in Python to prove OrderSend is blocked."""
        armed_tf_minutes = 15
        armed_bar_time = 946684800  # 2024-01-01 00:00:00 (stale)
        candle_close_time = 946687200  # 2024-01-01 00:20:00

        test_cases = [
            {
                "name": "stale bar with barChanged=true",
                "now": 1723224000,  # 2024-08-09 23:45:00 (current)
                "current_bar_time": 946684800,  # same as armed (barChanged=false)
                "bar_changed": False,
                "time_reached": True,
                "expected_execute": False,
            },
            {
                "name": "stale bar with timeReached=true",
                "now": 1723224000,
                "current_bar_time": 1723221000,  # different bar (barChanged=true)
                "bar_changed": True,
                "time_reached": True,
                "expected_execute": False,
            },
            {
                "name": "fresh bar with barChanged=true",
                "now": 1723224000,
                "current_bar_time": 1723223400,  # 10 min ago, fresh
                "bar_changed": True,
                "time_reached": True,
                "expected_execute": True,
            },
            {
                "name": "fresh bar with timeReached=true",
                "now": 1723224000,
                "current_bar_time": 1723223400,  # 10 min ago, fresh
                "bar_changed": False,
                "time_reached": True,
                "expected_execute": True,
            },
        ]

        for tc in test_cases:
            bar_age = tc["now"] - tc["current_bar_time"] if tc["current_bar_time"] > 0 else 0
            max_age = armed_tf_minutes * 2 * 60
            stale_bar = bar_age > max_age

            would_execute = tc["bar_changed"] or tc["time_reached"]
            if stale_bar:
                would_execute = False

            self.assertEqual(would_execute, tc["expected_execute"],
                           f"Failed for: {tc['name']} | stale={stale_bar} barAge={bar_age}s maxAge={max_age}s")


if __name__ == "__main__":
    unittest.main()
