import importlib.util
import pathlib
import unittest
import math


class VerifyRiskSizing(unittest.TestCase):
    def _load_module(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ui_module", root / "ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_eurusd_310_risk_calculation(self):
        module = self._load_module()
        
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                self.skipTest("MT5 not available")
            info = mt5.symbol_info("EURUSD")
            if info is None:
                self.skipTest("EURUSD not available in MT5")
            tick = mt5.symbol_info_tick("EURUSD")
            mt5.shutdown()
        except Exception as e:
            self.skipTest(f"MT5 error: {e}")
        
        digits = info.digits
        point = info.point
        tick_value = info.trade_tick_value
        tick_size = info.trade_tick_size
        volume_min = info.volume_min
        volume_max = info.volume_max
        volume_step = info.volume_step
        
        entry = 1.09500
        manual_sl = 1.09000
        risk_amount = 310.0
        rr_ratio = 5.0
        
        risk_distance = abs(entry - manual_sl)
        tp = entry + risk_distance * rr_ratio
        
        if tick_size > 0 and tick_value > 0:
            distance_ticks = risk_distance / tick_size
            loss_per_lot = tick_value * distance_ticks
        elif point > 0 and tick_value > 0:
            distance_points = risk_distance / point
            loss_per_lot = tick_value * distance_points
        else:
            loss_per_lot = 0
        
        raw_lot = risk_amount / loss_per_lot if loss_per_lot > 0 else 0
        normalized_lot = math.floor(raw_lot / volume_step) * volume_step
        normalized_lot = max(normalized_lot, volume_min)
        normalized_lot = min(normalized_lot, volume_max)
        normalized_lot = round(normalized_lot, 2)
        
        estimated_loss = loss_per_lot * normalized_lot
        
        print(f"\n=== EURUSD Risk Calculation ===")
        print(f"Symbol: EURUSD")
        print(f"Entry: {entry}")
        print(f"Manual SL: {manual_sl}")
        print(f"Risk Distance: {risk_distance}")
        print(f"Tick Size: {tick_size}")
        print(f"Tick Value: {tick_value}")
        print(f"Volume Step: {volume_step}")
        print(f"Raw Calculated Volume: {raw_lot:.4f}")
        print(f"Normalized Volume: {normalized_lot:.2f}")
        print(f"Estimated Monetary Loss at SL: ${estimated_loss:.2f}")
        print(f"Requested Risk: ${risk_amount:.2f}")
        print(f"TP: {tp:.5f}")
        print(f"RR: {rr_ratio}")
        print(f"Loss deviation: ${abs(estimated_loss - risk_amount):.2f}")
        
        self.assertGreater(normalized_lot, 0, "Lot size must be positive")
        self.assertGreaterEqual(normalized_lot, volume_min, "Lot size must meet minimum")
        self.assertLessEqual(normalized_lot, volume_max, "Lot size must not exceed maximum")
        self.assertLess(abs(estimated_loss - risk_amount), 50, 
                       f"Estimated loss ${estimated_loss:.2f} too far from requested ${risk_amount:.2f}")

    def test_btcusd_310_risk_calculation(self):
        module = self._load_module()
        
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                self.skipTest("MT5 not available")
            info = mt5.symbol_info("BTCUSD")
            if info is None:
                self.skipTest("BTCUSD not available in this MT5 terminal")
            tick = mt5.symbol_info_tick("BTCUSD")
            mt5.shutdown()
        except Exception as e:
            self.skipTest(f"MT5 error: {e}")
        
        digits = info.digits
        point = info.point
        tick_value = info.trade_tick_value
        tick_size = info.trade_tick_size
        volume_min = info.volume_min
        volume_max = info.volume_max
        volume_step = info.volume_step
        
        if tick_value <= 0 or tick_size <= 0:
            self.skipTest("BTCUSD tick_value or tick_size is zero/invalid")
        
        entry = 1.09500
        manual_sl = 1.09000
        risk_amount = 310.0
        rr_ratio = 5.0
        
        risk_distance = abs(entry - manual_sl)
        tp = entry + risk_distance * rr_ratio
        
        distance_ticks = risk_distance / tick_size
        loss_per_lot = tick_value * distance_ticks
        
        raw_lot = risk_amount / loss_per_lot if loss_per_lot > 0 else 0
        normalized_lot = math.floor(raw_lot / volume_step) * volume_step
        normalized_lot = max(normalized_lot, volume_min)
        normalized_lot = min(normalized_lot, volume_max)
        normalized_lot = round(normalized_lot, 2)
        
        estimated_loss = loss_per_lot * normalized_lot
        
        print(f"\n=== BTCUSD Risk Calculation ===")
        print(f"Symbol: BTCUSD")
        print(f"Entry: {entry}")
        print(f"Manual SL: {manual_sl}")
        print(f"Risk Distance: {risk_distance}")
        print(f"Tick Size: {tick_size}")
        print(f"Tick Value: {tick_value}")
        print(f"Volume Step: {volume_step}")
        print(f"Raw Calculated Volume: {raw_lot:.4f}")
        print(f"Normalized Volume: {normalized_lot:.2f}")
        print(f"Estimated Monetary Loss at SL: ${estimated_loss:.2f}")
        print(f"Requested Risk: ${risk_amount:.2f}")
        print(f"TP: {tp:.5f}")
        print(f"RR: {rr_ratio}")
        print(f"Loss deviation: ${abs(estimated_loss - risk_amount):.2f}")
        
        self.assertGreater(normalized_lot, 0, "Lot size must be positive")
        self.assertGreaterEqual(normalized_lot, volume_min, "Lot size must meet minimum")
        self.assertLessEqual(normalized_lot, volume_max, "Lot size must not exceed maximum")
        self.assertLess(abs(estimated_loss - risk_amount), 50,
                       f"Estimated loss ${estimated_loss:.2f} too far from requested ${risk_amount:.2f}")


if __name__ == "__main__":
    unittest.main()
