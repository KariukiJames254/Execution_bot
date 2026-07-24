import os
from flask import Flask, render_template, request, jsonify
from datetime import datetime
from config import SYMBOL, TIMEFRAME, SL_PIPS, DEFAULT_RISK_AMOUNT
from broker import initialize, login, shutdown as broker_shutdown, is_connected, get_account_details
from market import get_current_price, get_previous_candle
from execution import get_open_positions, close_position, set_break_even
from risk import calculate_lot_from_risk, calculate_sl, calculate_tp
from logger import setup_logger

logger = setup_logger("ui")
app = Flask(__name__, template_folder="templates")

bot_running = False
bot_thread = None
last_log = []


def add_log(level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    last_log.append({"time": ts, "level": "log-" + level, "message": message})
    if len(last_log) > 100:
        last_log.pop(0)


@app.route("/")
def dashboard():
    if not is_connected():
        initialize()
        from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
        login(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)

    connected = is_connected()
    account = get_account_details() if connected else None
    bid, ask = get_current_price(SYMBOL) if connected else (None, None)
    positions = get_open_positions(SYMBOL) if connected else []
    return render_template(
        "dashboard.html",
        connected=connected,
        account_login=account["login"] if account else None,
        account_balance=account["balance"] if account else None,
        account_equity=account["equity"] if account else None,
        symbol=SYMBOL,
        bid=bid,
        ask=ask,
        default_risk=DEFAULT_RISK_AMOUNT,
        log_entries=reversed(last_log[-50:]),
    )


@app.route("/api/status")
def api_status():
    connected = is_connected()
    account = get_account_details() if connected else None
    bid, ask = get_current_price(SYMBOL) if connected else (None, None)
    return jsonify({
        "connected": connected,
        "balance": account["balance"] if account else None,
        "equity": account["equity"] if account else None,
        "bid": bid,
        "ask": ask,
    })


@app.route("/api/positions")
def api_positions():
    if not is_connected():
        return jsonify([])
    positions = get_open_positions(SYMBOL)
    return jsonify([
        {
            "ticket": p.ticket,
            "type": p.type,
            "symbol": p.symbol,
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
        }
        for p in positions
    ])


@app.route("/api/calc_lot", methods=["POST"])
def api_calc_lot():
    data = request.get_json()
    entry = data.get("entry")
    sl = data.get("sl")
    risk_amount = data.get("risk_amount", DEFAULT_RISK_AMOUNT)
    risk_mode = data.get("risk_mode", "amt")

    try:
        entry = float(entry)
        sl = float(sl)
        risk_amount = float(risk_amount)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid input"}), 400

    if risk_mode == "pct":
        acct = get_account_details() if is_connected() else None
        if acct:
            risk_amount = acct["balance"] * (risk_amount / 100.0)
        else:
            return jsonify({"error": "Not connected to MT5"}), 400

    lot = calculate_lot_from_risk(entry, sl, risk_amount)
    balance = 0
    acct = get_account_details() if is_connected() else None
    if acct:
        balance = acct["balance"]

    return jsonify({"lot": lot, "balance": balance})


@app.route("/api/trade", methods=["POST"])
def api_trade():
    try:
        data = request.get_json()
        direction = data.get("direction", "BUY")
        lot = data.get("lot", 0.01)
        entry = data.get("entry")
        sl_price = data.get("sl_price")

        if not is_connected():
            return jsonify({"message": "Not connected to MT5"}), 400

        if len(get_open_positions(SYMBOL)) > 0:
            return jsonify({"message": "Position already open"}), 400

        if not entry or not sl_price:
            return jsonify({"message": "Missing entry or stop loss price"}), 400

        try:
            entry = float(entry)
            sl_price = float(sl_price)
        except (TypeError, ValueError):
            return jsonify({"message": "Invalid price values"}), 400

        if entry == sl_price:
            return jsonify({"message": "Stop Loss cannot equal Entry price"}), 400

        sl = calculate_stop_loss(entry, calculate_pips(entry, sl_price), direction)
        tp = calculate_take_profit(entry, calculate_pips(entry, sl_price), direction)

        from execution import execute_buy, execute_sell
        if direction == "BUY":
            result = execute_buy(SYMBOL, lot, sl, tp, comment="UI Trade")
        else:
            result = execute_sell(SYMBOL, lot, sl, tp, comment="UI Trade")

        if result and result.retcode == 0:
            add_log("success", f"{direction} {lot} {SYMBOL} @ {entry} SL={sl} TP={tp}")
            return jsonify({"message": f"{direction} trade executed successfully"})
        else:
            add_log("error", f"{direction} trade failed")
            return jsonify({"message": "Trade failed"}), 500
    except Exception as e:
        logger.error(f"Trade error: {e}")
        return jsonify({"message": "Error: " + str(e)}), 500


def calculate_pips(entry, sl):
    diff = abs(entry - sl)
    if diff >= 0.01:
        return round(diff / 0.01, 2)
    elif diff >= 0.001:
        return round(diff / 0.001, 1)
    return round(diff / 0.0001, 0)


def run_ui(host="0.0.0.0", port=5000):
    add_log("info", "UI server starting...")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_ui()