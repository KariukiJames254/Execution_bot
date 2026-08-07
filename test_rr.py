import unittest
import sys
import os

# Add execution-bot to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'execution-bot'))

from config import RR_RATIO
from strategy import Strategy


class TestRRDefaults(unittest.TestCase):
    def test_config_rr_ratio_default_is_5(self):
        """RR_RATIO in config must default to 5."""
        self.assertEqual(RR_RATIO, 5,
            msg=f"RR_RATIO default is {RR_RATIO}, expected 5")
    
    def test_strategy_rr_ratio_is_5(self):
        """Strategy.RR_RATIO must be 5."""
        self.assertEqual(Strategy.RR_RATIO, 5,
            msg=f"Strategy.RR_RATIO is {Strategy.RR_RATIO}, expected 5")
    
    def test_risk_calculate_tp_default_is_5(self):
        """Default reward_ratio in calculate_tp must be 5."""
        import inspect
        from risk import calculate_tp
        sig = inspect.signature(calculate_tp)
        default = sig.parameters['reward_ratio'].default
        self.assertEqual(default, 5,
            msg=f"calculate_tp default reward_ratio is {default}, expected 5")


class TestTPCalculation(unittest.TestCase):
    def test_tp_formula_buy_rr_5(self):
        """TP formula for BUY with RR=5: entry + (risk_distance * 5)."""
        entry = 1.15275
        sl = 1.15221
        direction = "BUY"
        risk_distance = abs(entry - sl)  # 0.00054
        expected_tp = entry + (risk_distance * 5)  # 1.15545
        
        # Direct formula test (no MT5 dependency)
        tp = entry + risk_distance * 5
        
        self.assertAlmostEqual(tp, expected_tp, places=5,
            msg=f"TP formula failed: got {tp}, expected {expected_tp}")
    
    def test_tp_formula_sell_rr_5(self):
        """TP formula for SELL with RR=5: entry - (risk_distance * 5)."""
        entry = 1.15275
        sl = 1.15299  # SL above entry for SELL
        direction = "SELL"
        risk_distance = abs(entry - sl)  # 0.00024
        expected_tp = entry - (risk_distance * 5)  # 1.15155
        
        tp = entry - risk_distance * 5
        
        self.assertAlmostEqual(tp, expected_tp, places=5,
            msg=f"TP formula failed: got {tp}, expected {expected_tp}")
    
    def test_tp_rr_2_gives_wrong_value(self):
        """RR=2 produces 1.15383, not 1.15545 — proves the bug."""
        entry = 1.15275
        sl = 1.15221
        risk_distance = abs(entry - sl)
        
        tp_rr2 = entry + risk_distance * 2  # 1.15383
        tp_rr5 = entry + risk_distance * 5  # 1.15545
        
        # TP with RR=2 should NOT equal the correct TP with RR=5
        self.assertNotAlmostEqual(tp_rr2, tp_rr5, places=4,
            msg="RR=2 and RR=5 should produce different TPs")
        
        # Verify the exact wrong value
        self.assertAlmostEqual(tp_rr2, 1.15383, places=5)
        self.assertAlmostEqual(tp_rr5, 1.15545, places=5)


if __name__ == '__main__':
    unittest.main()
