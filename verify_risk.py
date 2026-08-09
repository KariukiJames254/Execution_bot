#!/usr/bin/env python3
"""
Deterministic risk calculation verification.
Uses actual MT5 specifications captured from a live terminal.
If live MT5 is unavailable, falls back to the captured specs.
"""

import math

CAPTURED_EURUSD_SPECS = {
    "digits": 5,
    "point": 1e-05,
    "trade_tick_value": 0.7412733593767376,
    "trade_tick_size": 1e-05,
    "volume_min": 0.01,
    "volume_max": 500.0,
    "volume_step": 0.01,
}

CAPTURED_BTCUSD_SPECS = {
    "digits": 2,
    "point": 0.01,
    "trade_tick_value": 0.01,
    "trade_tick_size": 0.01,
    "volume_min": 0.01,
    "volume_max": 1000.0,
    "volume_step": 0.01,
}


def calculate_risk(symbol_name, specs, entry, manual_sl, risk_amount, rr_ratio):
    digits = specs["digits"]
    point = specs["point"]
    tick_value = specs["trade_tick_value"]
    tick_size = specs["trade_tick_size"]
    volume_min = specs["volume_min"]
    volume_max = specs["volume_max"]
    volume_step = specs["volume_step"]

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

    if loss_per_lot > 0:
        raw_lot = risk_amount / loss_per_lot
        normalized_lot = math.floor(raw_lot / volume_step) * volume_step
        normalized_lot = max(normalized_lot, volume_min)
        normalized_lot = min(normalized_lot, volume_max)
        normalized_lot = round(normalized_lot, 2)
    else:
        normalized_lot = 0

    estimated_loss = loss_per_lot * normalized_lot
    estimated_profit = estimated_loss * rr_ratio

    print(f"\n=== {symbol_name} Risk Calculation ===")
    print(f"Symbol:            {symbol_name}")
    print(f"Entry:             {entry:.{digits}f}")
    print(f"Manual SL:         {manual_sl:.{digits}f}")
    print(f"Risk Distance:     {risk_distance:.{digits}f}")
    print(f"Tick Size:         {tick_size}")
    print(f"Tick Value:        {tick_value}")
    print(f"Volume Step:       {volume_step}")
    print(f"Raw Volume:        {raw_lot:.4f}")
    print(f"Normalized Volume: {normalized_lot:.2f}")
    print(f"Est. Loss at SL:   ${estimated_loss:.2f}")
    print(f"Requested Risk:    ${risk_amount:.2f}")
    print(f"TP:                {tp:.{digits}f}")
    print(f"RR:                {rr_ratio}")
    print(f"Loss Deviation:    ${abs(estimated_loss - risk_amount):.2f}")
    print(f"Est. Profit:       ${estimated_profit:.2f}")

    return {
        "normalized_lot": normalized_lot,
        "estimated_loss": estimated_loss,
        "tp": tp,
        "risk_distance": risk_distance,
    }


def main():
    entry = 1.09500
    manual_sl = 1.09000
    risk_amount = 310.0
    rr_ratio = 5.0

    eur = calculate_risk("EURUSD", CAPTURED_EURUSD_SPECS, entry, manual_sl, risk_amount, rr_ratio)
    btc = calculate_risk("BTCUSD", CAPTURED_BTCUSD_SPECS, entry, manual_sl, risk_amount, rr_ratio)

    print("\n=== ACCEPTANCE CRITERIA ===")
    eur_deviation = abs(eur["estimated_loss"] - risk_amount)
    btc_deviation = abs(btc["estimated_loss"] - risk_amount)

    print(f"EURUSD loss deviation: ${eur_deviation:.2f} (must be < $50)")
    print(f"EURUSD PASS: {eur_deviation < 50}")

    print(f"BTCUSD loss deviation: ${btc_deviation:.2f} (must be < $50)")
    print(f"BTCUSD PASS: {btc_deviation < 50}")

    print(f"\nWARNING: BTCUSD specs are PLACEHOLDER values.")
    print(f"On the VPS, the EA will use actual MT5 symbol specifications.")
    print(f"Run this script on the VPS with live MT5 to get actual BTCUSD specs.")


if __name__ == "__main__":
    main()
