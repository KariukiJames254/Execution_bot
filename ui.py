import os
import MetaTrader5 as mt5
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
from config import SYMBOL, TIMEFRAME, SL_PIPS, DEFAULT_RISK_AMOUNT, RR_RATIO, BE_ENABLED, BE_RR, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
from broker import initialize, login, ensure_connected, shutdown as broker_shutdown, is_connected, get_account_details
from market import get_current_price, get_previous_candle, get_latest_candle
from execution import get_open_positions, close_position, set_break_even, execute_buy, execute_sell, validate_min_stop_distance
from risk import calculate_lot_from_risk, calculate_sl, calculate_tp
from logger import setup_logger

logger = setup_logger("ui")
app = Flask(__name__, template_folder="templates")

bot_running = False
bot_thread = None
last_log = []

AVAILABLE_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
    "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "US30",
    "NAS100", "SPX500", "US100", "UK100", "GER40",
]

TF_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300,
    "M6": 360, "M10": 600, "M12": 720, "M15": 900,
    "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200,
    "H3": 10800, "H4": 14400, "H6": 21600, "H8": 28800,
    "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}

pending_trades = {}
trade_history = []

def add_log(level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    last_log.append({"time": ts, "level": "log-" + str(level), "message": str(message)})
    if len(last_log) > 100:
        last_log.pop(0)


def _current_symbol():
    return request.headers.get("X-Symbol") or request.args.get("symbol") or SYMBOL


def _get_risk_amount(data, symbol):
    risk_mode = data.get("risk_mode", "amt")
    risk_amount = float(data.get("risk_amount", DEFAULT_RISK_AMOUNT))
    if risk_mode == "pct":
        acct = get_account_details() if is_connected() else None
        if acct:
            risk_amount = acct["balance"] * (risk_amount / 100.0)
    return risk_amount


def _time_to_close(candle_time_str, timeframe):
    try:
        candle_time = datetime.fromisoformat(candle_time_str.replace("Z", "+00:00"))
        tf_seconds = TF_SECONDS.get(timeframe.upper(), 900)
        close_time = candle_time + timedelta(seconds=tf_seconds)
        now = datetime.now(candle_time.tzinfo)
        remaining = (close_time - now).total_seconds()
        return max(0, int(remaining))
    except Exception:
        return 0


def _format_countdown(seconds):
    if seconds <= 0:
        return "00:00"
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


@app.route("/")
def dashboard():
    if not ensure_connected():
        add_log("error", "Failed to connect to MT5 terminal")

    connected = is_connected()
    account = get_account_details() if connected else None
    current = SYMBOL
    bid, ask = get_current_price(current) if connected else (None, None)
    positions = get_open_positions(current) if connected else []
    terminal = mt5.terminal_info() if connected else None
    broker_name = terminal.name if terminal else "---"

    symbols_to_show = AVAILABLE_SYMBOLS
    if connected:
        try:
            mt5_symbols = mt5.symbols_get()
            if mt5_symbols:
                names = sorted({s.name for s in mt5_symbols if s.visible})
                curated = set(AVAILABLE_SYMBOLS)
                popular = [s for s in names if any(s.endswith(suffix) for suffix in AVAILABLE_SYMBOLS)]
                merged = curated.union(popular)
                if merged:
                    symbols_to_show = sorted(merged)
        except Exception as e:
            logger.warning(f"Failed to fetch MT5 symbols for dashboard: {e}")

    trade_id = request.args.get("trade_id")
    pending = pending_trades.get(trade_id) if trade_id else None
    if pending and pending.get("candle_time"):
        pending["time_remaining"] = _time_to_close(pending["candle_time"], pending.get("timeframe", TIMEFRAME))
        pending["countdown"] = _format_countdown(pending["time_remaining"])

    return render_template(
        "dashboard.html",
        connected=connected,
        account_login=account["login"] if account else None,
        account_balance=account["balance"] if account else None,
        account_equity=account["equity"] if account else None,
        symbol=current,
        broker=broker_name,
        bid=bid,
        ask=ask,
        available_symbols=symbols_to_show,
        default_risk=DEFAULT_RISK_AMOUNT,
        default_rr=RR_RATIO,
        be_enabled=BE_ENABLED,
        be_rr=BE_RR,
        pending_trade=pending,
        trade_history=trade_history[-50:],
        log_entries=reversed(last_log[-50:]),
    )


@app.route("/api/symbols")
def api_symbols():
    try:
        if is_connected():
            mt5_symbols = mt5.symbols_get()
            if mt5_symbols:
                names = sorted({s.name for s in mt5_symbols if s.visible})
                curated = set(AVAILABLE_SYMBOLS)
                popular = [s for s in names if any(s.endswith(suffix) for suffix in AVAILABLE_SYMBOLS)]
                selected = curated.union(popular)
                return jsonify(sorted(selected))
    except Exception as e:
        logger.warning(f"Failed to fetch MT5 symbols: {e}")
    return jsonify(AVAILABLE_SYMBOLS)


@app.route("/api/status")
def api_status():
    current = _current_symbol()
    connected = is_connected()
    account = get_account_details() if connected else None
    bid, ask = get_current_price(current) if connected else (None, None)
    
    trade_id = request.args.get("trade_id")
    pending = pending_trades.get(trade_id) if trade_id else None
    countdown = 0
    stages = []
    if pending:
        countdown = _time_to_close(pending.get("candle_time", ""), pending.get("timeframe", TIMEFRAME))
        stages = pending.get("stages", [])
    
    return jsonify({
        "connected": connected,
        "balance": account["balance"] if account else None,
        "equity": account["equity"] if account else None,
        "bid": bid,
        "ask": ask,
        "symbol": current,
        "pending_trade": pending,
        "countdown": countdown,
        "countdown_str": _format_countdown(countdown),
        "stages": stages,
    })


@app.route("/api/history")
def api_history():
    return jsonify(list(reversed(trade_history[-50:])))


@app.route("/api/ea/pending", methods=["GET", "POST"])
def api_ea_pending():
    symbol = _current_symbol()
    if not is_connected():
        return jsonify({"status": "error", "message": "Not connected"}), 200

    trade_id = request.args.get("trade_id")
    if trade_id and trade_id in pending_trades:
        trade = pending_trades[trade_id]
        return jsonify({
            "trade_id": trade["trade_id"],
            "status": trade["status"],
            "direction": trade["direction"],
            "symbol": trade["symbol"],
            "candle_time": trade.get("candle_time", ""),
            "timeframe": trade.get("timeframe", TIMEFRAME),
            "entry": trade.get("entry", 0),
            "sl": trade.get("sl", 0),
            "tp": trade.get("tp", 0),
            "lot": trade.get("lot", 0),
            "error": trade.get("error", ""),
        })

    if pending_trades:
        latest_id = list(pending_trades.keys())[-1]
        trade = pending_trades[latest_id]
        return jsonify({
            "trade_id": trade["trade_id"],
            "status": trade["status"],
            "direction": trade["direction"],
            "symbol": trade["symbol"],
            "candle_time": trade.get("candle_time", ""),
            "timeframe": trade.get("timeframe", TIMEFRAME),
            "entry": trade.get("entry", 0),
            "sl": trade.get("sl", 0),
            "tp": trade.get("tp", 0),
            "lot": trade.get("lot", 0),
            "error": trade.get("error", ""),
        })

    return jsonify({"status": "idle"})


@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    success = ensure_connected()
    connected = is_connected()
    account = get_account_details() if connected else None
    return jsonify({
        "connected": connected,
        "balance": account["balance"] if account else None,
        "equity": account["equity"] if account else None,
    })


@app.route("/api/settings", methods=["POST"])
def api_settings():
    global DEFAULT_RISK_AMOUNT, RR_RATIO, BE_RR
    data = request.get_json() or {}
    if "risk_amount" in data:
        DEFAULT_RISK_AMOUNT = float(data["risk_amount"])
    if "rr_ratio" in data:
        RR_RATIO = float(data["rr_ratio"])
    if "be_rr" in data:
        BE_RR = float(data["be_rr"])
    return jsonify({
        "risk_amount": DEFAULT_RISK_AMOUNT,
        "rr_ratio": RR_RATIO,
        "be_rr": BE_RR,
    })


@app.route("/api/candle_data")
def api_candle_data():
    current = _current_symbol()
    if not is_connected():
        return jsonify({"error": "Not connected"}), 400
    
    candle = get_latest_candle(current)
    if not candle:
        return jsonify({"error": "No candle data"}), 400
    
    return jsonify({
        "symbol": current,
        "time": candle["time"].isoformat(),
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
    })


@app.route("/api/price")
def api_price():
    current = _current_symbol()
    bid, ask = get_current_price(current)
    return jsonify({"bid": bid, "ask": ask})


@app.route("/api/logs")
def api_logs():
    return jsonify(last_log[-50:])


@app.route("/api/positions")
def api_positions():
    current = _current_symbol()
    if not is_connected():
        return jsonify([])
    positions = get_open_positions(current)
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


@app.route("/api/prepare_trade", methods=["POST"])
def api_prepare_trade():
    data = request.get_json()
    symbol = data.get("symbol") or _current_symbol()
    direction = data.get("direction", "BUY")
    timeframe = data.get("timeframe", TIMEFRAME)
    high = float(data.get("high", 0))
    low = float(data.get("low", 0))
    close = float(data.get("close", 0))
    open_ = float(data.get("open", 0))
    candle_time_str = data.get("time", "")
    
    if not ensure_connected():
        return jsonify({"error": "Not connected"}), 400

    if direction == "BUY":
        sl = low
        entry = close
        if entry <= sl:
            sl = entry - 0.0001
    else:
        sl = high
        entry = close
        if entry >= sl:
            sl = entry + 0.0001

    if sl == 0 or entry == 0:
        return jsonify({"error": "Invalid candle data"}), 400

    risk_amount = _get_risk_amount(data, symbol)
    rr_ratio = float(data.get("rr_ratio", RR_RATIO))
    be_rr = float(data.get("be_rr", BE_RR))
    
    lot = calculate_lot_from_risk(entry, sl, risk_amount, symbol=symbol)
    
    diff = abs(entry - sl)
    if direction == "BUY":
        tp = entry + diff * rr_ratio
    else:
        tp = entry - diff * rr_ratio
    
    info = mt5.symbol_info(symbol)
    if info:
        sl = round(sl, info.digits)
        tp = round(tp, info.digits)
        entry = round(entry, info.digits)
    
    dist_points = abs(entry - sl) / info.point if info else 0
    dist_pips = dist_points / 10.0
    
    be_trigger = entry + diff * be_rr if direction == "BUY" else entry - diff * be_rr
    
    trade_id = f"{symbol}_{candle_time_str}_{direction}"
    
    stages = [
        {"name": "Candle Selected", "done": True},
        {"name": "Candle Data Received", "done": True},
        {"name": "Stop Loss Calculated", "done": True},
        {"name": "Lot Size Calculated", "done": True},
        {"name": "Take Profit Calculated", "done": True},
        {"name": "Waiting For Candle Close", "done": True},
        {"name": "Executing Trade", "done": False},
        {"name": "Trade Opened", "done": False},
    ]
    
    pending_trades[trade_id] = {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "timeframe": timeframe,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "lot": lot,
        "risk_amount": risk_amount,
        "rr_ratio": rr_ratio,
        "be_rr": be_rr,
        "be_trigger": round(be_trigger, info.digits) if info else be_trigger,
        "distance_pips": round(dist_pips, 2),
        "candle_time": candle_time_str,
        "candle_open": open_,
        "candle_high": high,
        "candle_low": low,
        "candle_close": close,
        "status": "armed",
        "stages": stages,
        "time_remaining": _time_to_close(candle_time_str, timeframe),
    }
    
    add_log("info", f"Prepared {direction} {symbol}: entry={entry}, SL={sl}, TP={tp}, lot={lot}")
    
    return jsonify({
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "lot": lot,
        "risk_amount": risk_amount,
        "distance_pips": round(dist_pips, 2),
        "rr_ratio": rr_ratio,
        "be_rr": be_rr,
        "be_trigger": round(be_trigger, info.digits) if info else be_trigger,
        "status": "armed",
        "candle_time": candle_time_str,
        "candle_open": open_,
        "candle_high": high,
        "candle_low": low,
        "candle_close": close,
        "stages": stages,
    })


@app.route("/api/execute_trade", methods=["GET", "POST"])
def api_execute_trade():
    test_mode = request.args.get("test_mode") == "1"
    if test_mode:
        symbol = request.args.get("symbol") or _current_symbol()
        direction = request.args.get("direction", "BUY")
        return jsonify({
            "status": "executed",
            "ticket": 999999,
            "deal": 888888,
            "entry": 1.00000,
            "slippage": 0.0,
            "sl": 1.00000,
            "tp": 1.00100,
            "lot": 0.01,
            "test": True,
        })

    if request.method == "GET":
        raw_id = request.args.get("trade_id")
    else:
        data = request.get_json(silent=True) or {}
        raw_id = data.get("trade_id")

    trade_id = raw_id
    if trade_id:
        try:
            from urllib.parse import unquote
            decoded = unquote(trade_id)
            if decoded in pending_trades:
                trade_id = decoded
        except Exception:
            pass

    symbol = request.args.get("symbol") or _current_symbol()
    direction = request.args.get("direction")

    if not trade_id or trade_id not in pending_trades:
        candidates = [(tid, t) for tid, t in pending_trades.items()
                      if t.get("symbol") == symbol and t.get("status") == "armed"]
        if len(candidates) == 1:
            trade_id = candidates[0][0]
        elif len(candidates) > 1 and direction:
            for tid, t in candidates:
                if t.get("direction") == direction.upper():
                    trade_id = tid
                    break

    if not trade_id or trade_id not in pending_trades:
        print(f"TRADE_ID mismatch: raw={raw_id!r} pending={list(pending_trades.keys())}")
        return jsonify({
            "error": "Invalid trade_id",
            "received_trade_id": trade_id,
            "pending": list(pending_trades.keys()),
        }), 400

    trade = pending_trades[trade_id]
    symbol = trade["symbol"]
    direction = trade["direction"]
    lot = float(trade["lot"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])
    planned_entry = float(trade["entry"])

    if not ensure_connected():
        return jsonify({"error": "Not connected"}), 400

    if len(get_open_positions(symbol)) > 0:
        pending_trades[trade_id]["status"] = "error"
        pending_trades[trade_id]["error"] = "Position already open"
        return jsonify({"error": "Position already open"}), 400

    trade["stages"] = [
        {"name": "Candle Selected", "done": True},
        {"name": "Candle Data Received", "done": True},
        {"name": "Stop Loss Calculated", "done": True},
        {"name": "Lot Size Calculated", "done": True},
        {"name": "Take Profit Calculated", "done": True},
        {"name": "Waiting For Candle Close", "done": True},
        {"name": "Executing Trade", "done": True},
        {"name": "Trade Opened", "done": False},
    ]

    tick = mt5.symbol_info_tick(symbol)
    executed_entry = None
    slippage = 0
    if tick:
        executed_entry = tick.ask if direction == "BUY" else tick.bid
        slippage = abs(executed_entry - planned_entry)

    from execution import execute_buy, execute_sell
    if direction == "BUY":
        result = execute_buy(symbol, lot, sl, tp, comment="EA Trade")
    else:
        result = execute_sell(symbol, lot, sl, tp, comment="EA Trade")

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        pending_trades[trade_id]["status"] = "executed"
        pending_trades[trade_id]["stages"][-1]["done"] = True
        add_log("success", f"Executed {direction} {lot} {symbol} entry={executed_entry} SL={sl} TP={tp}")

        history_entry = {
            "time": datetime.now().strftime("%H:%M"),
            "symbol": symbol,
            "direction": direction,
            "lot": lot,
            "entry": executed_entry or planned_entry,
            "sl": sl,
            "tp": tp,
            "status": "Executed",
            "ticket": result.order,
            "slippage": slippage,
            "risk_amount": trade.get("risk_amount", 0),
            "rr_ratio": trade.get("rr_ratio", 0),
        }
        trade_history.append(history_entry)

        return jsonify({
            "status": "executed",
            "ticket": result.order,
            "deal": result.deal,
            "entry": executed_entry or planned_entry,
            "planned_entry": planned_entry,
            "slippage": slippage,
            "sl": sl,
            "tp": tp,
            "lot": lot,
        })
    else:
        rc = result.retcode if result else 0
        comment = result.comment if result else "Unknown"
        pending_trades[trade_id]["status"] = "error"
        pending_trades[trade_id]["error"] = comment
        add_log("error", f"Execution failed: retcode={rc}, comment={comment}")
        return jsonify({
            "status": "error",
            "retcode": rc,
            "comment": comment,
        }), 500


@app.route("/api/cancel_trade", methods=["POST"])
def api_cancel_trade():
    data = request.get_json()
    trade_id = data.get("trade_id")
    
    if not trade_id or trade_id not in pending_trades:
        return jsonify({"error": "Invalid trade_id"}), 400
    
    trade = pending_trades[trade_id]
    if trade.get("status") in ("executed", "cancelled"):
        return jsonify({"error": "Trade already final"}), 400
    
    trade["status"] = "cancelled"
    add_log("info", f"Trade {trade_id} cancelled by user")
    return jsonify({"status": "cancelled"})


@app.route("/api/trade", methods=["POST"])
def api_trade():
    try:
        data = request.get_json()
        direction = data.get("direction", "BUY")
        lot = data.get("lot", 0.01)
        sl_price = data.get("sl_price")
        tp_price = data.get("tp_price")
        symbol = data.get("symbol") or SYMBOL

        if not ensure_connected():
            return jsonify({"message": "Not connected to MT5"}), 400

        if len(get_open_positions(symbol)) > 0:
            return jsonify({"message": "Position already open"}), 400

        if not sl_price or not tp_price:
            return jsonify({"message": "Missing SL or TP price"}), 400

        try:
            sl_price = float(sl_price)
            tp_price = float(tp_price)
        except (TypeError, ValueError):
            return jsonify({"message": "Invalid price values"}), 400

        if sl_price == tp_price:
            return jsonify({"message": "Stop Loss cannot equal Take Profit"}), 400

        from execution import execute_buy, execute_sell
        if direction == "BUY":
            result = execute_buy(symbol, lot, sl_price, tp_price, comment="UI Trade")
        else:
            result = execute_sell(symbol, lot, sl_price, tp_price, comment="UI Trade")

        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            _, ask = get_current_price(symbol)
            add_log("success", f"{direction} {lot} {symbol} entry={ask} SL={sl_price} TP={tp_price}")
            return jsonify({"message": f"{direction} trade executed successfully"})
        else:
            rc = result.retcode if result else 0
            comment = result.comment if result else "Order rejected (unknown reason)"
            add_log("error", f"{direction} trade failed: retcode={rc}, comment={comment}")
            return jsonify({
                "message": "Trade failed",
                "retcode": rc,
                "comment": comment
            }), 500
    except Exception as e:
        logger.error("Trade error: " + str(e))
        return jsonify({"message": "Error: " + str(e)}), 500


def run_ui(host="0.0.0.0", port=5000):
    add_log("info", "UI server starting...")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_ui()