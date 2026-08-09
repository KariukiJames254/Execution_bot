import os
import json
import sqlite3
import hmac
import secrets
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from datetime import datetime, timedelta, timezone
from config import SYMBOL, TIMEFRAME, SL_PIPS, DEFAULT_RISK_AMOUNT, RR_RATIO, BE_ENABLED, BE_RR, MAX_OPEN_POSITIONS, MAX_TOTAL_OPEN_RISK, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, FLASK_HOST, FLASK_PORT, MIN_STOP_BUFFER_PIPS, ENFORCE_MIN_STOP, DASHBOARD_USERNAME, DASHBOARD_PASSWORD, DASHBOARD_SECRET_KEY
from logger import setup_logger
from notifications import notify

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

try:
    from execution import (
        get_open_positions as _execution_get_open_positions,
        execute_buy,
        execute_sell,
        close_position,
        set_break_even,
        validate_min_stop_distance,
    )
except Exception:
    _execution_get_open_positions = None
    execute_buy = execute_sell = None
    close_position = set_break_even = None
    validate_min_stop_distance = None

try:
    from risk import calculate_lot_from_risk, calculate_sl, calculate_tp
except Exception:
    def calculate_lot_from_risk(*args, **kwargs):
        return 0.01

    def calculate_sl(*args, **kwargs):
        return 0

    def calculate_tp(*args, **kwargs):
        return 0

try:
    from market import get_current_price, get_latest_candle
except Exception:
    from datetime import datetime as _dt, timezone as _tz

    def get_current_price(symbol):
        market = ea_state.get("market", {}).get(symbol, {})
        return market.get("bid"), market.get("ask")

    def get_latest_candle(symbol):
        from symbol_store import get_candle
        candle = get_candle(symbol)
        if candle:
            time_val = candle.get("time")
            if time_val is None:
                time_val = _dt.fromtimestamp(0, tz=_tz.utc)
            else:
                try:
                    time_val = _dt.fromtimestamp(float(time_val), tz=_tz.utc)
                except (ValueError, TypeError):
                    time_val = _dt.fromtimestamp(0, tz=_tz.utc)
            return {
                "time": time_val,
                "open": float(candle.get("open", 0)),
                "high": float(candle.get("high", 0)),
                "low": float(candle.get("low", 0)),
                "close": float(candle.get("close", 0)),
                "tick_volume": candle.get("tick_volume", 0),
                "spread": candle.get("spread", 0),
                "real_volume": candle.get("real_volume", 0),
            }
        from market import get_latest_candle as _market_get_latest_candle
        return _market_get_latest_candle(symbol)

_TRADE_RETCODE_DONE = getattr(mt5, "TRADE_RETCODE_DONE", 0)
_ORDER_TYPE_BUY = getattr(mt5, "ORDER_TYPE_BUY", 0)
_ORDER_TYPE_SELL = getattr(mt5, "ORDER_TYPE_SELL", 1)

logger = setup_logger("ui")
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"))
app.config["SECRET_KEY"] = DASHBOARD_SECRET_KEY or secrets.token_urlsafe(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["TEMPLATES_AUTO_RELOAD"] = True

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
ea_state = {
    "account": {},
    "positions": {},
    "market": {},
    "symbols": {},
    "candles": {},
    "last_seen": None,
    "requested_symbol": None,
    "close_request": None,
}

TRADE_HISTORY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_history.db")
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_history.json")
EA_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ea_state")
COMMAND_FILE = os.path.join(EA_STATE_DIR, "command.json")
RESPONSE_FILE = os.path.join(EA_STATE_DIR, "response.json")


@app.before_request
def require_dashboard_login():
    """Protect the dashboard page while allowing the API endpoints used by the UI to work."""
    path = request.path
    if path in ("/login", "/logout", "/healthz"):
        return None
    if path.startswith("/api/"):
        return None
    if session.get("dashboard_authenticated"):
        return None
    return redirect(url_for("login", next=path))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        configured = bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)
        valid = configured and hmac.compare_digest(username, DASHBOARD_USERNAME) and hmac.compare_digest(password, DASHBOARD_PASSWORD)
        if valid:
            session.clear()
            session["dashboard_authenticated"] = True
            target = request.form.get("next") or "/"
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            return redirect(target)
        error = "Dashboard credentials are not configured." if not configured else "Invalid username or password."
    return render_template("login.html", error=error, next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def _ensure_ea_state_dir():
    os.makedirs(EA_STATE_DIR, exist_ok=True)


def _write_ea_command(command):
    _ensure_ea_state_dir()
    try:
        with open(COMMAND_FILE, "w", encoding="utf-8") as f:
            json.dump(command, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to write EA command file: {e}")


def _read_ea_response():
    if not os.path.exists(RESPONSE_FILE):
        return None
    try:
        with open(RESPONSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.warning(f"Failed to read EA response file: {e}")
        return None


def _clear_ea_response():
    try:
        if os.path.exists(RESPONSE_FILE):
            os.remove(RESPONSE_FILE)
    except Exception:
        pass


def _get_db():
    conn = sqlite3.connect(TRADE_HISTORY_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                lot REAL NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                status TEXT NOT NULL,
                ticket INTEGER,
                slippage REAL DEFAULT 0,
                risk_amount REAL DEFAULT 0,
                rr_ratio REAL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                close_price REAL,
                pnl REAL DEFAULT 0
            )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "closed_at" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN closed_at TEXT")
        if "close_price" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN close_price REAL")
        if "pnl" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL DEFAULT 0")
        if "result" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN result TEXT")
        if "position_ticket" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN position_ticket INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        if "closed_at" in columns:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at)")
        conn.commit()
    finally:
        conn.close()


def _migrate_json_to_sqlite():
    if not os.path.exists(TRADE_HISTORY_FILE):
        return
    try:
        with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return
        conn = _get_db()
        try:
            for trade in data:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO trades 
                    (time, symbol, direction, lot, entry, sl, tp, status, ticket, slippage, risk_amount, rr_ratio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.get("time", ""),
                        trade.get("symbol", ""),
                        trade.get("direction", ""),
                        trade.get("lot", 0),
                        trade.get("entry", 0),
                        trade.get("sl", 0),
                        trade.get("tp", 0),
                        trade.get("status", ""),
                        trade.get("ticket"),
                        trade.get("slippage", 0),
                        trade.get("risk_amount", 0),
                        trade.get("rr_ratio", 0),
                        datetime.now().isoformat(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        os.replace(TRADE_HISTORY_FILE, TRADE_HISTORY_FILE + ".bak")
    except Exception as e:
        logger.warning(f"Failed to migrate trade history: {e}")


def _load_trade_history():
    global trade_history
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT time, symbol, direction, lot, entry, sl, tp, status, ticket, position_ticket, slippage, risk_amount, rr_ratio, close_price, pnl, result FROM trades ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            trade_history = []
            for row in rows:
                d = dict(row)
                d.setdefault("close_price", None)
                d.setdefault("pnl", 0)
                d.setdefault("result", None)
                d.setdefault("position_ticket", None)
                trade_history.append(d)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to load trade history: {e}")
        trade_history = []


def _save_trade_history_entry(entry):
    try:
        conn = _get_db()
        try:
            conn.execute(
                """
                INSERT INTO trades 
                (time, symbol, direction, lot, entry, sl, tp, status, ticket, position_ticket, slippage, risk_amount, rr_ratio, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.get("time", ""),
                    entry.get("symbol", ""),
                    entry.get("direction", ""),
                    entry.get("lot", 0),
                    entry.get("entry", 0),
                    entry.get("sl", 0),
                    entry.get("tp", 0),
                    entry.get("status", ""),
                    entry.get("ticket"),
                    entry.get("position_ticket"),
                    entry.get("slippage", 0),
                    entry.get("risk_amount", 0),
                    entry.get("rr_ratio", 0),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to save trade history entry: {e}")


_init_db()
_migrate_json_to_sqlite()
_load_trade_history()

def add_log(level, message):
    ts = datetime.now().strftime("%H:%M:%S")
    last_log.append({"time": ts, "level": "log-" + str(level), "message": str(message)})
    if len(last_log) > 100:
        last_log.pop(0)


def _current_symbol():
    return request.headers.get("X-Symbol") or request.args.get("symbol") or SYMBOL


def _ea_connected():
    last_seen = ea_state.get("last_seen")
    if not last_seen:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds() < 90
    except (TypeError, ValueError):
        return False


def is_connected():
    return _ea_connected()


def ensure_connected():
    return is_connected()


def _get_open_positions_impl(symbol=None):
    if _execution_get_open_positions is not None:
        try:
            return _execution_get_open_positions(symbol)
        except Exception:
            pass
    return []


def get_open_positions(symbol=None):
    return _get_open_positions_impl(symbol)


def _get_risk_amount(data, symbol):
    risk_mode = data.get("risk_mode", "amt")
    risk_amount = float(data.get("risk_amount", DEFAULT_RISK_AMOUNT))
    if risk_mode == "pct":
        acct = ea_state.get("account")
        if acct:
            risk_amount = float(acct.get("balance", 0)) * (risk_amount / 100.0)
    return risk_amount


def _get_total_open_risk():
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT risk_amount FROM trades WHERE status='Executed'"
        ).fetchall()
        conn.close()
        return sum(float(row["risk_amount"]) for row in rows)
    except Exception:
        return 0.0


def _validate_candle_freshness(symbol, timeframe, candle_data):
    """Validate that candle data is fresh and safe to trade on.
    
    Returns (is_fresh, reason_str).
    """
    from symbol_store import get_candle_age, get_series_synced
    from datetime import datetime, timezone
    
    if not candle_data:
        return False, "No candle data"
    
    time_val = candle_data.get("time")
    if time_val is None:
        return False, "Candle timestamp missing"
    
    candle_time = None
    if isinstance(time_val, (int, float)):
        try:
            candle_time = datetime.fromtimestamp(float(time_val), tz=timezone.utc)
        except (ValueError, TypeError):
            pass
    else:
        try:
            candle_time = datetime.fromisoformat(str(time_val).replace("Z", "+00:00"))
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    
    if candle_time is None:
        return False, f"Invalid candle timestamp: {time_val}"
    
    now = datetime.now(timezone.utc)
    age_seconds = (now - candle_time).total_seconds()
    
    tf_seconds = TF_SECONDS.get(timeframe.upper(), 900)
    max_age = tf_seconds * 3
    
    if age_seconds > max_age:
        return False, f"Stale candle: age={age_seconds:.0f}s, max={max_age}s, timestamp={time_val}"
    
    series_synced = get_series_synced(symbol)
    if series_synced is not None and not series_synced:
        return False, f"Series not synchronized: series_synced={series_synced}"
    
    return True, f"Fresh candle: age={age_seconds:.0f}s, timestamp={time_val}"


def _log_candle_stage(stage, symbol, timeframe, candle_data, extra=None):
    """Detailed logging for candle data at each pipeline stage."""
    if not candle_data:
        add_log("warn", f"[CandlePipeline][{stage}] {symbol} {timeframe}: NO DATA")
        return
    time_val = candle_data.get("time")
    try:
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(float(time_val), tz=timezone.utc).isoformat() if time_val else "None"
    except (ValueError, TypeError):
        ts = f"Invalid({time_val})"
    msg = (f"[CandlePipeline][{stage}] {symbol} {timeframe}: "
           f"time={ts} open={candle_data.get('open')} high={candle_data.get('high')} "
           f"low={candle_data.get('low')} close={candle_data.get('close')}")
    if extra:
        msg += f" | {extra}"
    add_log("info", msg)


def _preflight_checks(symbol, data=None):
    """Run pre-flight validation and return a list of check dicts.

    Each check dict has: name, passed (bool), status ('passed'|'failed'|'waiting'),
    message (str).
    - 'passed': hard requirement met, won't change.
    - 'failed': hard requirement NOT met and won't resolve without user action.
    - 'waiting': transient — EA is connected but hasn't reported data yet; will resolve
      once the EA sends the next report.
    """
    data = data or {}
    checks = []

    connected = _ea_connected()
    checks.append({
        "name": "EA Connected",
        "passed": connected,
        "status": "passed" if connected else "failed",
        "message": "EA heartbeat received" if connected else "EA not responding. Check EA is running on the chart.",
    })

    account = ea_state.get("account") or {}
    account_ok = bool(account.get("balance") is not None or account.get("login") is not None)
    checks.append({
        "name": "Account Info Received",
        "passed": account_ok,
        "status": "passed" if account_ok else ("waiting" if connected else "failed"),
        "message": f"Login {account.get('login', 'N/A')}" if account_ok else "No account report from EA yet.",
    })

    sym_info = ea_state.get("symbols", {}).get(symbol)
    sym_info_ok = sym_info is not None and sym_info.get("digits", 0) > 0 and sym_info.get("point", 0) > 0
    from symbol_store import has_symbol_info
    registered = has_symbol_info(symbol) or (sym_info is not None)
    checks.append({
        "name": f"Symbol Info ({symbol})",
        "passed": sym_info_ok,
        "status": "passed" if sym_info_ok else ("waiting" if (connected and registered) else "failed"),
        "message": f"Digits {sym_info.get('digits')}, Point {sym_info.get('point')}" if sym_info and sym_info.get("digits", 0) > 0
                   else (f"EA reports {symbol} registered; waiting for symbol details..." if registered
                         else f"Symbol {symbol} not in Market Watch. Select it in MT5."),
    })

    timeframe = data.get("timeframe", TIMEFRAME)
    candle = None
    try:
        candle = get_latest_candle(symbol)
    except Exception:
        candle = None
    candle_ok = candle is not None and candle.get("high") and candle.get("low")
    checks.append({
        "name": "Candle Data Available",
        "passed": candle_ok,
        "status": "passed" if candle_ok else ("waiting" if connected and sym_info_ok else "failed"),
        "message": "Candle fetched" if candle_ok else "Waiting for EA to report candle data...",
    })

    direction = data.get("direction", "BUY")
    high = float(data.get("high", candle.get("high", 0) if candle else 0))
    low = float(data.get("low", candle.get("low", 0) if candle else 0))
    close = float(data.get("close", candle.get("close", 0) if candle else 0))
    if candle_ok:
        entry = close
        manual_sl = data.get("manual_sl")
        if manual_sl is not None and str(manual_sl).strip() != "":
            sl = round(float(manual_sl), int(sym_info.get("digits", 5)) if sym_info else 5)
        else:
            if direction == "BUY":
                sl = low
            else:
                sl = high
            sl = round(sl, int(sym_info.get("digits", 5)) if sym_info else 5)
    else:
        sl = 0
        entry = 0
    sl_valid = sl != 0 and entry != 0
    if direction == "BUY":
        sl_valid = sl_valid and entry > sl
    else:
        sl_valid = sl_valid and entry < sl
    checks.append({
        "name": "Stop Loss Valid",
        "passed": sl_valid,
        "status": "passed" if sl_valid else ("waiting" if not candle_ok else "failed"),
        "message": f"SL={sl}, Entry={entry}" if sl_valid else (f"Waiting for candle data..." if not candle_ok else f"Invalid SL/entry for {direction}."),
    })

    point = float(sym_info.get("point") or 0.00001) if sym_info else 0.00001
    risk_amount = 0.01
    lot = 0.01
    if sl_valid:
        try:
            risk_amount = _get_risk_amount(data, symbol)
            lot = calculate_lot_from_risk(entry, sl, risk_amount, symbol=symbol)
        except Exception:
            pass
    volume_max = float(sym_info.get("volume_max") or 100.0) if sym_info else 100.0
    lot_valid = sym_info is not None and sl_valid and 0 < lot <= volume_max
    if sym_info is not None and sl_valid:
        tp_dist = abs(entry - sl) * float(data.get("rr_ratio", RR_RATIO))
        if direction == "BUY":
            tp = entry + tp_dist
        else:
            tp = entry - tp_dist
        tp_valid = tp != 0
    else:
        tp = 0
        tp_valid = False
    checks.append({
        "name": "Take Profit Valid",
        "passed": tp_valid,
        "status": "passed" if tp_valid else ("waiting" if (not sym_info_ok or not sl_valid) else "failed"),
        "message": f"TP={tp:.5f}" if tp_valid else ("Waiting for candle data to calculate TP..." if not sl_valid else "Cannot calculate TP without symbol info."),
    })

    checks.append({
        "name": "Lot Size Valid",
        "passed": lot_valid,
        "status": "passed" if lot_valid else ("waiting" if (not sym_info_ok or not sl_valid) else "failed"),
        "message": f"Lot={lot:.2f}, Max={volume_max}" if (sym_info_ok and sl_valid) else "Waiting for candle data to calculate lot...",
    })

    return checks


def _preflight_all_passed(checks):
    return all(c["passed"] for c in checks)


def _preflight_can_retry(checks):
    """True if all checks are 'passed' or 'waiting' (i.e., retry might help)."""
    return all(c["status"] in ("passed", "waiting") for c in checks)


def _preflight_has_failures(checks):
    """True if any check is a hard 'failed' that won't resolve on retry."""
    return any(c["status"] == "failed" for c in checks)


@app.route("/api/preflight")
def api_preflight():
    symbol = _current_symbol()
    ea_state["requested_symbol"] = symbol
    checks = _preflight_checks(symbol)
    all_passed = _preflight_all_passed(checks)
    can_retry = _preflight_can_retry(checks)
    has_failures = _preflight_has_failures(checks)
    return jsonify({
        "symbol": symbol,
        "all_passed": all_passed,
        "can_retry": can_retry,
        "has_failures": has_failures,
        "checks": checks,
    })


def _time_to_close(candle_time_str, timeframe):
    """Return seconds until candle close. Handles both ISO datetime strings
    and Unix timestamp strings."""
    tf_seconds = TF_SECONDS.get(timeframe.upper(), 900)
    try:
        raw = float(candle_time_str)
        close_unix = int(raw) + tf_seconds
        return max(0, close_unix - int(datetime.now(timezone.utc).timestamp()))
    except (ValueError, TypeError):
        pass
    try:
        candle_time = datetime.fromisoformat(str(candle_time_str).replace("Z", "+00:00"))
        close_time = candle_time + timedelta(seconds=tf_seconds)
        if candle_time.tzinfo is not None:
            now = datetime.now(candle_time.tzinfo)
        else:
            now = datetime.now()
        remaining = (close_time - now).total_seconds()
        return max(0, int(remaining))
    except Exception:
        return 0


def _format_countdown(seconds):
    if seconds <= 0:
        return "00:00"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _compute_candle_close_unix(trade):
    """Compute candle_close_unix from the raw EA Unix timestamp, bypassing
    timezone-naive ISO string round-trips that can produce incorrect values."""
    tf = trade.get("timeframe", TIMEFRAME)
    tf_seconds = TF_SECONDS.get(tf.upper(), 900)
    symbol = trade.get("symbol", _current_symbol())
    try:
        from symbol_store import get_candle
        ea_candle = get_candle(symbol, tf)
        if ea_candle and ea_candle.get("time") is not None:
            return int(float(ea_candle["time"])) + tf_seconds
    except Exception:
        pass
    candle_time_str = trade.get("candle_time", "")
    if candle_time_str:
        try:
            ct = datetime.fromisoformat(str(candle_time_str).replace("Z", "+00:00"))
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            return int((ct + timedelta(seconds=tf_seconds)).timestamp())
        except Exception:
            pass
    return 0


@app.route("/")
def dashboard():
    connected = _ea_connected()
    account = ea_state.get("account") or None
    current = _current_symbol()
    market = ea_state["market"].get(current, {})
    bid = market.get("bid")
    ask = market.get("ask")
    broker_name = account.get("server") if account else None
    if not broker_name:
        broker_name = "MT5 Expert Advisor" if connected else "---"
    symbols_to_show = AVAILABLE_SYMBOLS

    trade_id = request.args.get("trade_id")
    pending = pending_trades.get(trade_id) if trade_id else None
    if pending and pending.get("candle_time"):
        pending["time_remaining"] = _time_to_close(pending["candle_time"], pending.get("timeframe", TIMEFRAME))
        pending["countdown"] = _format_countdown(pending["time_remaining"])

    return render_template(
        "dashboard.html",
        connected=connected,
        account_login=account.get("login") if account else None,
        account_balance=account.get("balance") if account else None,
        account_equity=account.get("equity") if account else None,
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
    connected = _ea_connected()
    account = ea_state.get("account") or None
    market = ea_state["market"].get(current, {})
    bid = market.get("bid")
    ask = market.get("ask")
    if bid is None and ask is None:
        try:
            from symbol_store import get_tick
            bid, ask = get_tick(current)
            if bid is None and ask is None:
                from market import get_current_price
                bid, ask = get_current_price(current)
        except Exception:
            pass
    
    trade_id = request.args.get("trade_id")
    pending = pending_trades.get(trade_id) if trade_id else None
    countdown = 0
    stages = []
    open_positions = 0
    if pending:
        countdown = _time_to_close(pending.get("candle_time", ""), pending.get("timeframe", TIMEFRAME))
        stages = pending.get("stages", [])
    
    open_positions = sum(1 for position in ea_state["positions"].values()
                         if position.get("symbol") == current)
    total_open_risk = _get_total_open_risk()

    pending_ids = list(pending_trades.keys())
    add_log("info", f"[STATUS_STATE] pid={os.getpid()} pending_count={len(pending_ids)} pending_ids={pending_ids} requested_trade_id={trade_id or 'none'} armed_count={len([t for t in pending_trades.values() if t.get('status') == 'armed'])} open_positions={open_positions} total_open_risk={total_open_risk:.2f}")

    return jsonify({
        "connected": connected,
        "login": account.get("login") if account else None,
        "balance": account.get("balance") if account else None,
        "equity": account.get("equity") if account else None,
        "broker": account.get("server") if account else None,
        "bid": bid,
        "ask": ask,
        "symbol": current,
        "pending_trade": pending,
        "countdown": countdown,
        "countdown_str": _format_countdown(countdown),
        "stages": stages,
        "open_positions": open_positions,
        "total_open_risk": total_open_risk,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_total_open_risk": MAX_TOTAL_OPEN_RISK,
    })


@app.route("/api/history")
def api_history():
    try:
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        page = 1
        limit = 20
    if page < 1:
        page = 1
    if limit < 1:
        limit = 20
    if limit > 100:
        limit = 100

    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        offset = (page - 1) * limit
        rows = conn.execute(
            """
            SELECT time, symbol, direction, lot, entry, sl, tp, status, ticket, position_ticket, slippage, risk_amount, rr_ratio, close_price, pnl, result
            FROM trades
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        items = [dict(row) for row in rows]
    finally:
        conn.close()

    return jsonify({
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": max(1, (total + limit - 1) // limit),
    })


def _compute_stats():
    conn = _get_db()
    try:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        month_start = now.replace(day=1).strftime("%Y-%m-%d")

        total_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status IN ('Executed','Closed')").fetchone()[0]
        trades_today = conn.execute("SELECT COUNT(*) FROM trades WHERE date(created_at)=? AND status IN ('Executed','Closed')", (today_str,)).fetchone()[0]
        trades_week = conn.execute("SELECT COUNT(*) FROM trades WHERE date(created_at)>=? AND status IN ('Executed','Closed')", (week_start,)).fetchone()[0]
        trades_month = conn.execute("SELECT COUNT(*) FROM trades WHERE date(created_at)>=? AND status IN ('Executed','Closed')", (month_start,)).fetchone()[0]

        closed = conn.execute("SELECT pnl FROM trades WHERE status='Closed'").fetchall()
        closed_pnls = [float(row["pnl"]) for row in closed]
        wins = [pnl for pnl in closed_pnls if pnl > 0]
        losses = [pnl for pnl in closed_pnls if pnl < 0]
        win_count = len(wins)
        loss_count = len(losses)

        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        loss_rate = (loss_count / total_trades * 100) if total_trades > 0 else 0
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)
        expectancy = ((win_rate / 100) * (sum(wins) / win_count) if win_count > 0 else 0) - ((loss_rate / 100) * (sum(losses) / loss_count) if loss_count > 0 else 0)

        avg_win = (sum(wins) / win_count) if win_count > 0 else 0
        avg_loss = (sum(losses) / loss_count) if loss_count > 0 else 0
        largest_win = max(wins) if wins else 0
        largest_loss = min(losses) if losses else 0

        try:
            balance_row = conn.execute("SELECT balance FROM account_info ORDER BY id DESC LIMIT 1").fetchone()
        except Exception:
            balance_row = None
        balance = float(balance_row["balance"]) if balance_row else 0

        try:
            equity_row = conn.execute("SELECT equity FROM account_info ORDER BY id DESC LIMIT 1").fetchone()
        except Exception:
            equity_row = None
        equity = float(equity_row["equity"]) if equity_row else balance

        max_equity = equity
        max_drawdown = 0.0
        daily_drawdown = 0.0

        try:
            equity_rows = conn.execute("SELECT created_at, equity FROM account_info ORDER BY created_at ASC").fetchall()
        except Exception:
            equity_rows = []
        if equity_rows:
            peak = float(equity_rows[0]["equity"])
            for row in equity_rows:
                e = float(row["equity"])
                if e > peak:
                    peak = e
                dd = (peak - e) / peak * 100 if peak > 0 else 0
                if dd > max_drawdown:
                    max_drawdown = dd

            today_peak = None
            for row in equity_rows:
                if row["created_at"].startswith(today_str):
                    e = float(row["equity"])
                    if today_peak is None or e > today_peak:
                        today_peak = e
                    elif today_peak is not None and today_peak > 0:
                        dd = (today_peak - e) / today_peak * 100
                        if dd > daily_drawdown:
                            daily_drawdown = dd

        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        closed_trades = conn.execute("SELECT pnl FROM trades WHERE status='Closed' ORDER BY created_at ASC").fetchall()
        for row in closed_trades:
            pnl = float(row["pnl"])
            if pnl > 0:
                consecutive_wins += 1
                consecutive_losses = 0
                if consecutive_wins > max_consecutive_wins:
                    max_consecutive_wins = consecutive_wins
            elif pnl < 0:
                consecutive_losses += 1
                consecutive_wins = 0
                if consecutive_losses > max_consecutive_losses:
                    max_consecutive_losses = consecutive_losses
            else:
                consecutive_wins = 0
                consecutive_losses = 0

        open_positions = len(conn.execute("SELECT id FROM positions").fetchall()) if False else 0
        try:
            from execution import get_open_positions
            open_positions = len(get_open_positions())
        except Exception:
            pass

        avg_holding = 0
        hold_rows = conn.execute("SELECT time, closed_at FROM trades WHERE status='Closed' AND closed_at IS NOT NULL").fetchall()
        if hold_rows:
            hold_times = []
            for row in hold_rows:
                try:
                    t_open = datetime.fromisoformat(row["time"])
                    t_close = datetime.fromisoformat(row["closed_at"])
                    hold_times.append((t_close - t_open).total_seconds())
                except Exception:
                    pass
            if hold_times:
                avg_holding = sum(hold_times) / len(hold_times)

        return {
            "total_trades": total_trades,
            "trades_today": trades_today,
            "trades_week": trades_week,
            "trades_month": trades_month,
            "win_rate": round(win_rate, 2),
            "loss_rate": round(loss_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "max_drawdown": round(max_drawdown, 2),
            "daily_drawdown": round(daily_drawdown, 2),
            "consecutive_wins": max_consecutive_wins,
            "consecutive_losses": max_consecutive_losses,
            "avg_holding_time": round(avg_holding, 0),
            "open_positions": open_positions,
            "balance": round(balance, 2),
            "equity": round(equity, 2),
        }
    finally:
        conn.close()


@app.route("/api/stats")
def api_stats():
    return jsonify(_compute_stats())


@app.route("/api/ea/pending", methods=["GET", "POST"])
def api_ea_pending():
    symbol = _current_symbol()
    ea_state["last_seen"] = datetime.now().isoformat()
    trade_id = request.args.get("trade_id")
    
    pending_ids = list(pending_trades.keys())
    add_log("info", f"[EA_PENDING] pid={os.getpid()} symbol={symbol} requested_trade_id={trade_id or 'none'} pending_count={len(pending_ids)} pending_ids={pending_ids}")
    
    if trade_id and trade_id in pending_trades:
        trade = pending_trades[trade_id]
        candle_close_unix = _compute_candle_close_unix(trade)
        tf = trade.get("timeframe", TIMEFRAME)
        candle_time_str = trade.get("candle_time", "")
        close_req = ea_state.pop("close_request", None)
        response_data = {
            "trade_id": trade["trade_id"],
            "status": trade["status"],
            "direction": trade["direction"],
            "symbol": trade["symbol"],
            "candle_time": candle_time_str,
            "timeframe": tf,
            "candle_close_unix": candle_close_unix,
            "entry": trade.get("entry", 0),
            "sl": trade.get("sl", 0),
            "tp": trade.get("tp", 0),
            "lot": trade.get("lot", 0),
            "be_rr": trade.get("be_rr", 0),
            "be_trigger": trade.get("be_trigger", 0),
            "manual_sl": trade.get("sl", 0),
            "risk_amount": trade.get("risk_amount", 0),
            "rr_ratio": trade.get("rr_ratio", 0),
            "close_ticket": close_req.get("ticket") if close_req else 0,
            "close_symbol": close_req.get("symbol", trade.get("symbol", "")) if close_req else "",
            "error": trade.get("error", ""),
        }
        add_log("info", f"[TradeLifecycle][EA_PENDING_RETURN] trade_id={trade_id} symbol={trade['symbol']} direction={trade['direction']} status={trade['status']} entry={trade.get('entry', 0)} sl={trade.get('sl', 0)}")
        return jsonify(response_data)

    armed = [(tid, t) for tid, t in pending_trades.items() if t.get("status") == "armed"]
    if armed:
        latest_id = armed[-1][0]
        trade = armed[-1][1]
        candle_close_unix = _compute_candle_close_unix(trade)
        tf = trade.get("timeframe", TIMEFRAME)
        candle_time_str = trade.get("candle_time", "")
        trade_symbol = trade.get("symbol", _current_symbol())
        close_req = ea_state.pop("close_request", None)
        response_data = {
            "trade_id": trade["trade_id"],
            "status": trade["status"],
            "direction": trade["direction"],
            "symbol": trade["symbol"],
            "target_symbol": trade_symbol,
            "requested_symbol": trade_symbol,
            "candle_time": candle_time_str,
            "timeframe": tf,
            "candle_close_unix": candle_close_unix,
            "entry": trade.get("entry", 0),
            "sl": trade.get("sl", 0),
            "tp": trade.get("tp", 0),
            "lot": trade.get("lot", 0),
            "be_rr": trade.get("be_rr", 0),
            "be_trigger": trade.get("be_trigger", 0),
            "manual_sl": trade.get("sl", 0),
            "risk_amount": trade.get("risk_amount", 0),
            "rr_ratio": trade.get("rr_ratio", 0),
            "close_ticket": close_req.get("ticket") if close_req else 0,
            "close_symbol": close_req.get("symbol", trade_symbol) if close_req else "",
            "error": trade.get("error", ""),
        }
        add_log("info", f"[CandlePipeline][EA_PENDING_ARMED] trade_id={latest_id} symbol={trade['symbol']} direction={trade['direction']} candle_time={candle_time_str} entry={trade.get('entry', 0)} sl={trade.get('sl', 0)} manual_sl={trade.get('sl', 0)} risk={trade.get('risk_amount', 0)} rr={trade.get('rr_ratio', 0)}")
        return jsonify(response_data)

    close_req = ea_state.pop("close_request", None)
    return jsonify({
        "status": "idle",
        "requested_symbol": ea_state.get("requested_symbol") or _current_symbol(),
        "close_ticket": close_req.get("ticket") if close_req else 0,
        "close_symbol": close_req.get("symbol", _current_symbol()) if close_req else "",
    })


@app.route("/api/ea/report_account", methods=["POST"])
def api_ea_report_account():
    data = request.get_json(silent=True) or {}
    if "balance" not in data or "equity" not in data:
        return jsonify({"error": "Account report is missing balance or equity"}), 400
    ea_state["account"] = data
    ea_state["last_seen"] = datetime.now().isoformat()
    add_log("info", "Received account report from MT5 EA")
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_position", methods=["POST"])
def api_ea_report_position():
    data = request.get_json(silent=True) or {}
    ticket = data.get("ticket")
    if ticket is None:
        return jsonify({"error": "Missing ticket"}), 400
    ea_state["positions"][str(ticket)] = {
        "ticket": ticket,
        "type": data.get("direction", ""),
        "symbol": data.get("symbol", ""),
        "volume": data.get("lot", 0),
        "price_open": data.get("entry", 0),
        "sl": data.get("sl", 0),
        "tp": data.get("tp", 0),
        "profit": data.get("profit", 0),
    }
    ea_state["last_seen"] = datetime.now().isoformat()
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_market", methods=["POST"])
def api_ea_report_market():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    try:
        bid = float(data["bid"])
        ask = float(data["ask"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Market report requires symbol, bid, and ask"}), 400
    if not symbol or bid <= 0 or ask <= 0:
        return jsonify({"error": "Invalid market report"}), 400
    ea_state["market"][symbol] = {"bid": bid, "ask": ask, "updated_at": datetime.now().isoformat()}
    ea_state["last_seen"] = datetime.now().isoformat()
    try:
        from symbol_store import update_tick
        update_tick(symbol, bid, ask)
    except Exception:
        pass
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_symbol_info", methods=["POST"])
def api_ea_report_symbol_info():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400
    ea_state["symbols"][symbol] = data
    try:
        from symbol_store import set_symbol_info
        set_symbol_info(symbol, data)
    except Exception:
        pass
    add_log("info", f"Received symbol info from EA for {symbol}")
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_candle", methods=["POST"])
def api_ea_report_candle():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    timeframe = data.get("timeframe", TIMEFRAME)
    if not symbol:
        return jsonify({"error": "Missing symbol"}), 400
    
    _log_candle_stage("EA_REPORT", symbol, timeframe, data, 
                      f"shift={data.get('shift')} series_synced={data.get('series_synced')} bars={data.get('bars_count')}")
    
    ea_state["candles"][symbol] = data
    ea_state["last_seen"] = datetime.now().isoformat()
    try:
        from symbol_store import set_candle
        candle = {
            "time": data.get("time"),
            "open": data.get("open"),
            "high": data.get("high"),
            "low": data.get("low"),
            "close": data.get("close"),
            "tick_volume": data.get("tick_volume", 0),
            "spread": data.get("spread", 0),
            "real_volume": data.get("real_volume", 0),
        }
        set_candle(symbol, timeframe, candle)
        _log_candle_stage("STORED", symbol, timeframe, candle)
    except Exception:
        pass
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_execution", methods=["POST"])
def api_ea_report_execution():
    data = request.get_json(silent=True) or {}
    trade_id = data.get("trade_id")
    status = data.get("status", "unknown")
    retcode = data.get("retcode", 0)
    comment = data.get("comment", "")
    order = data.get("order", 0)
    deal = data.get("deal", 0)
    entry = data.get("entry", 0)
    slippage = data.get("slippage", 0)
    spread = data.get("spread", 0)
    
    add_log("info", f"[TradeLifecycle][EA_REPORT_EXECUTION] trade_id={trade_id} status={status} retcode={retcode} comment={comment} order={order} deal={deal} entry={entry} slippage={slippage} spread={spread}")
    
    if trade_id in pending_trades:
        pending_trades[trade_id].update(data)
        if status in ("executed", "error", "stale_bar", "cancelled"):
            pending_trades[trade_id]["status"] = status
            add_log("info", f"[TradeLifecycle][PENDING_STATUS_CHANGED] trade_id={trade_id} new_status={status}")
    ea_state["last_seen"] = datetime.now().isoformat()
    return jsonify({"status": "ok"})


@app.route("/api/ea/report_close", methods=["POST"])
def api_ea_report_close():
    data = request.get_json(silent=True) or {}
    ticket = data.get("ticket")
    if ticket is None:
        return jsonify({"error": "Missing ticket"}), 400
    ea_state["last_seen"] = datetime.now().isoformat()
    closed_at = datetime.now().isoformat()
    close_price = data.get("price", 0.0)
    pnl = data.get("pnl", 0.0)
    add_log("info", f"Position closed by EA: ticket={ticket} pnl={pnl}")
    notify(f"🔒 <b>Position Closed</b>\nTicket: {ticket}\nPnL: {pnl}")
    try:
        conn = _get_db()
        try:
            conn.execute(
                """
                UPDATE trades 
                SET status = 'Closed', closed_at = ?, close_price = ?, pnl = ?, result = ?
                WHERE ticket = ? AND status != 'Closed'
                """,
                (closed_at, close_price, pnl, "EA Close", int(ticket)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to update trade close: {e}")
    return jsonify({"status": "ok"})


@app.route("/api/reconnect", methods=["POST"])
def api_reconnect():
    connected = _ea_connected()
    account = ea_state.get("account") or None
    return jsonify({
        "connected": connected,
        "balance": account["balance"] if account else None,
        "equity": account["equity"] if account else None,
    })


@app.route("/api/debug/state")
def api_debug_state():
    pending = []
    for tid, trade in pending_trades.items():
        pending.append({
            "trade_id": tid,
            "status": trade.get("status"),
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "entry": trade.get("entry"),
            "sl": trade.get("sl"),
            "tp": trade.get("tp"),
            "lot": trade.get("lot"),
            "candle_time": trade.get("candle_time"),
            "risk_amount": trade.get("risk_amount"),
            "rr_ratio": trade.get("rr_ratio"),
        })
    return jsonify({
        "pending_trades": pending,
        "pending_count": len(pending_trades),
        "ea_last_seen": ea_state.get("last_seen"),
        "ea_connected": _ea_connected(),
        "server_time": datetime.now().isoformat(),
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
    ea_state["requested_symbol"] = current
    candle = get_latest_candle(current)

    if not candle:
        import time as _time
        for _ in range(3):
            _time.sleep(1.0)
            candle = get_latest_candle(current)
            if candle:
                break

    if not candle:
        from symbol_store import has_symbol_info
        if not has_symbol_info(current):
            return jsonify({
                "error": "No candle data",
                "symbol": current,
                "message": f"Symbol {current} not registered with broker. Select it in Market Watch or switch to a registered symbol.",
            })
        return jsonify({"error": "No candle data", "symbol": current})

    _log_candle_stage("API_CANDLE_DATA", current, TIMEFRAME, {
        "time": candle["time"].timestamp() if hasattr(candle["time"], "timestamp") else candle["time"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
    })

    is_fresh, reason = _validate_candle_freshness(current, TIMEFRAME, {
        "time": candle["time"].timestamp() if hasattr(candle["time"], "timestamp") else candle["time"],
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
    })
    if not is_fresh:
        add_log("warn", f"[CandlePipeline] BLOCKED stale candle in api_candle_data: {reason}")
        return jsonify({
            "error": "STALE_MARKET_DATA",
            "symbol": current,
            "message": f"Candle data is stale: {reason}. Wait for synchronized market data.",
        })

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
    ea_state["requested_symbol"] = current
    market = ea_state["market"].get(current, {})
    bid = market.get("bid")
    ask = market.get("ask")
    if bid is None and ask is None:
        try:
            from symbol_store import get_tick
            bid, ask = get_tick(current)
            if bid is None and ask is None:
                import time as _time
                for _ in range(3):
                    _time.sleep(1.0)
                    try:
                        from symbol_store import get_tick as _gt
                        bid, ask = _gt(current)
                        if bid is not None:
                            break
                    except Exception:
                        pass
                if bid is None and ask is None:
                    from market import get_current_price
                    bid, ask = get_current_price(current)
        except Exception:
            pass
    return jsonify({"bid": bid, "ask": ask})


@app.route("/api/logs")
def api_logs():
    return jsonify(last_log[-50:])


@app.route("/api/positions")
def api_positions():
    current = _current_symbol()
    positions = [position for position in ea_state["positions"].values()
                 if position.get("symbol") == current]
    total = sum(float(position.get("profit", 0) or 0) for position in positions)
    return jsonify({
        "positions": positions,
        "total_profit": total,
    })


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    data = request.get_json()
    ticket = data.get("ticket")
    if not ticket:
        return jsonify({"error": "Missing ticket"}), 400
    if not ensure_connected():
        return jsonify({"error": "Not connected"}), 400
    result = close_position(int(ticket))
    if result and result.retcode == _TRADE_RETCODE_DONE:
        add_log("info", f"Closed position ticket={ticket}")

        closed_at = datetime.now().isoformat()
        close_price = 0.0
        pnl = 0.0
        try:
            positions = get_open_positions(_current_symbol())
            for pos in positions:
                if pos.ticket == int(ticket):
                    close_price = pos.price_open
                    pnl = float(pos.profit or 0)
                    break
        except Exception:
            pass

        notify(f"🔒 <b>Trade Closed</b>\nTicket: {ticket}\nPnL: {pnl}\nClosed at: {close_price}")

        try:
            conn = _get_db()
            try:
                conn.execute(
                    """
                    UPDATE trades 
                    SET status = 'Closed', closed_at = ?, close_price = ?, pnl = ?, result = ?
                    WHERE ticket = ? AND status != 'Closed'
                    """,
                    (closed_at, close_price, pnl, "Manual", int(ticket)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to update trade close: {e}")

        return jsonify({"status": "closed", "ticket": ticket, "pnl": pnl, "close_price": close_price})

    if result is None:
        ea_state["close_request"] = {
            "ticket": int(ticket),
            "symbol": data.get("symbol", _current_symbol()),
        }
        add_log("info", f"Close request queued for EA: ticket={ticket}")
        notify(f"⏳ <b>Close Request Sent</b>\nTicket: {ticket}\nThe EA will close this position on the next tick.")
        return jsonify({"status": "queued_for_ea", "ticket": ticket}), 202

    rc = result.retcode if result else 0
    comment = result.comment if result else "Unknown"
    add_log("error", f"Close failed ticket={ticket} retcode={rc} comment={comment}")
    return jsonify({"error": comment, "retcode": rc}), 500


@app.route("/api/monitor")
def api_monitor():
    # Position and account updates are supplied by the MT5 EA.
    return jsonify({"status": "ok"})

    if not is_connected():
        return jsonify({"status": "ok"})

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ticket, position_ticket, symbol, direction, sl, tp FROM trades WHERE status='Executed'"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        deal_ticket = row["ticket"]
        position_ticket = row["position_ticket"]
        symbol = row["symbol"]
        positions = get_open_positions(symbol)
        if positions is None:
            print(f"MONITOR: positions is None for {symbol}")
            continue

        if position_ticket:
            found = any(p.ticket == position_ticket for p in positions)
        else:
            found = len(positions) > 0

        if found:
            continue

        print(f"MONITOR: closed trade detected ticket={deal_ticket} position_ticket={position_ticket}")

        try:
            history = None
            if position_ticket:
                history = mt5.history_deals_get(position=position_ticket)
            if not history:
                history = mt5.history_deals_get(ticket=deal_ticket)
            if history is None or len(history) == 0:
                print(f"MONITOR: no history deals found for ticket={deal_ticket} position_ticket={position_ticket}")
                continue

            close_deal = None
            for deal in history:
                if deal.entry == mt5.DEAL_ENTRY_OUT:
                    close_deal = deal
                    break

            if close_deal is None:
                print(f"MONITOR: no DEAL_ENTRY_OUT found for ticket={deal_ticket}")
                continue

            close_price = close_deal.price
            pnl = close_deal.profit
            result = "TP" if pnl > 0 else "SL" if pnl < 0 else "BE"
            print(f"MONITOR: updating ticket={deal_ticket} close_price={close_price} pnl={pnl} result={result}")

            conn = _get_db()
            try:
                conn.execute(
                    """
                    UPDATE trades 
                    SET status = 'Closed', closed_at = ?, close_price = ?, pnl = ?, result = ?
                    WHERE ticket = ? AND status='Executed'
                    """,
                    (datetime.now().isoformat(), close_price, pnl, result, deal_ticket),
                )
                conn.commit()
                print(f"MONITOR: updated ticket={deal_ticket} successfully")
            finally:
                conn.close()

            if pnl < 0:
                notify(f"🛑 <b>Stop Loss Hit</b>\nTicket: {deal_ticket}\nLoss: {pnl:.2f}\nClose: {close_price}")
            elif pnl > 0:
                notify(f"🎯 <b>Take Profit Hit</b>\nTicket: {deal_ticket}\nProfit: +{pnl:.2f}\nClose: {close_price}")
            else:
                notify(f"➖ <b>Break Even</b>\nTicket: {deal_ticket}\nPnL: {pnl:.2f}\nClose: {close_price}")
        except Exception:
            pass

    return jsonify({"status": "ok"})


@app.route("/api/preview_trade", methods=["POST"])
def api_preview_trade():
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get("symbol") or _current_symbol()
        direction = data.get("direction", "BUY")
        close = float(data.get("close", 0))
        manual_sl = data.get("manual_sl")
        
        sym = ea_state.get("symbols", {}).get(symbol)
        digits = int(sym.get("digits", 5)) if sym else 5
        point = float(sym.get("point") or 0.00001) if sym else 0.00001
        
        entry = round(close, digits)
        sl = round(float(manual_sl), digits) if manual_sl else 0
        
        _log_candle_stage("PREVIEW_TRADE", symbol, TIMEFRAME, {
            "time": data.get("time"),
            "open": 0,
            "high": 0,
            "low": 0,
            "close": close,
        }, f"entry={entry} sl={sl} risk={_get_risk_amount(data, symbol)}")
        
        is_fresh, fresh_reason = _validate_candle_freshness(symbol, TIMEFRAME, {
            "time": data.get("time"),
            "open": 0,
            "high": 0,
            "low": 0,
            "close": close,
        })
        if not is_fresh:
            add_log("warn", f"[CandlePipeline] BLOCKED stale candle in api_preview_trade: {fresh_reason}")
            return jsonify({"error": f"STALE_MARKET_DATA: {fresh_reason}"}), 400
        
        if sl == 0 or entry == 0:
            return jsonify({"error": "Invalid stop loss or entry price"}), 400
        
        if direction == "BUY" and sl >= entry:
            return jsonify({"error": f"Invalid SL for BUY: SL must be below entry"}), 400
        if direction == "SELL" and sl <= entry:
            return jsonify({"error": f"Invalid SL for SELL: SL must be above entry"}), 400
        
        risk_amount = _get_risk_amount(data, symbol)
        rr_ratio = float(data.get("rr_ratio", RR_RATIO))
        
        lot = calculate_lot_from_risk(entry, sl, risk_amount, symbol=symbol)
        volume_max = sym.get("volume_max") if sym else None
        if volume_max is not None and lot > volume_max:
            lot = volume_max
        
        diff = abs(entry - sl)
        tp = entry + diff * rr_ratio if direction == "BUY" else entry - diff * rr_ratio
        tp = round(tp, digits)
        
        dist_points = diff / point
        dist_pips = dist_points / 10.0
        
        return jsonify({
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "risk_amount": risk_amount,
            "distance_pips": round(dist_pips, 2),
            "rr_ratio": rr_ratio,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prepare_trade", methods=["POST"])
def api_prepare_trade():
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get("symbol") or _current_symbol()
        direction = data.get("direction", "BUY")
        timeframe = data.get("timeframe", TIMEFRAME)
        high = float(data.get("high", 0))
        low = float(data.get("low", 0))
        close = float(data.get("close", 0))
        open_ = float(data.get("open", 0))
        candle_time_str = data.get("time", "")

        checks = _preflight_checks(symbol, data)
        if _preflight_has_failures(checks):
            failed = [c for c in checks if c["status"] == "failed"]
            messages = [f"{c['name']}: {c['message']}" for c in failed]
            error_msg = "Trade not armed. Pre-flight checks failed:\n\n" + "\n".join(messages)
            add_log("warn", error_msg.replace("\n", " "))
            notify(f"❌ <b>Trade Not Armed</b>\n{symbol} {direction}\n\n" + "\n".join(messages))
            return jsonify({
                "error": error_msg,
                "preflight": checks,
                "status": "not_armed",
            }), 400
        if not _preflight_all_passed(checks):
            return jsonify({
                "status": "waiting",
                "preflight": checks,
                "message": "Waiting for EA to report data. Retrying...",
            }), 409

        if len(_get_open_positions_impl()) >= MAX_OPEN_POSITIONS:
            return jsonify({"error": "Max positions reached"}), 400

        total_open_risk = _get_total_open_risk()
        new_trade_risk = _get_risk_amount(data, symbol)
        if total_open_risk + new_trade_risk > MAX_TOTAL_OPEN_RISK:
            return jsonify({"error": f"Max total open risk exceeded. Current: ${total_open_risk:.2f}, New: ${new_trade_risk:.2f}, Limit: ${MAX_TOTAL_OPEN_RISK:.2f}"}), 400

        candle_data_for_validation = {
            "time": data.get("time"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
        is_fresh, fresh_reason = _validate_candle_freshness(symbol, timeframe, candle_data_for_validation)
        if not is_fresh:
            add_log("warn", f"[CandlePipeline] BLOCKED stale candle in api_prepare_trade: {fresh_reason}")
            notify(f"❌ <b>Trade Blocked</b>\n{symbol} {direction}\n\nStale market data: {fresh_reason}")
            return jsonify({"error": f"STALE_MARKET_DATA: {fresh_reason}"}), 400

        entry = close

        sym = ea_state.get("symbols", {}).get(symbol)
        digits = 5
        point = 0.00001
        volume_max = None
        if sym:
            digits = int(sym.get("digits", 5) or 5)
            point = float(sym.get("point") or 0.00001)
            volume_max = sym.get("volume_max")

        entry = round(entry, digits)

        manual_sl = data.get("manual_sl")
        if manual_sl is not None and str(manual_sl).strip() != "":
            sl = round(float(manual_sl), digits)
        else:
            if direction == "BUY":
                sl = low
            else:
                sl = high
            sl = round(sl, digits)

        if sl == 0 or entry == 0:
            return jsonify({"error": "Invalid stop loss or entry price"}), 400

        if direction == "BUY" and sl >= entry:
            return jsonify({"error": f"Invalid SL for BUY: SL must be below entry. SL={sl}, Entry={entry}"}), 400
        if direction == "SELL" and sl <= entry:
            return jsonify({"error": f"Invalid SL for SELL: SL must be above entry. SL={sl}, Entry={entry}"}), 400

        if ENFORCE_MIN_STOP and MIN_STOP_BUFFER_PIPS > 0:
            min_sl_distance = MIN_STOP_BUFFER_PIPS * point * 10
            actual_distance = abs(entry - sl)
            if actual_distance < min_sl_distance:
                error_msg = "Trade rejected.\n\nReason:\nThe selected candle results in a stop distance of only %.1f pips.\n\nMinimum allowed: %.1f pips.\n\nPlease select a candle with a larger range." % (actual_distance / (point * 10), MIN_STOP_BUFFER_PIPS)
                add_log("warn", error_msg.replace("\n", " "))
                notify(f"❌ <b>Trade Rejected</b>\n{direction} {symbol}\n\nReason:\nStop too close to entry ({actual_distance / (point * 10):.1f} pips).\nMinimum allowed: {MIN_STOP_BUFFER_PIPS} pips.\n\nPlease select a candle with a larger range.")
                return jsonify({"error": error_msg}), 400

        risk_amount = _get_risk_amount(data, symbol)
        rr_ratio = float(data.get("rr_ratio", RR_RATIO))
        be_rr = float(data.get("be_rr", BE_RR))

        lot = calculate_lot_from_risk(entry, sl, risk_amount, symbol=symbol)
        if volume_max is not None and lot > volume_max:
            add_log("warn", f"Calculated lot {lot} exceeds broker max {volume_max}. Capping.")
            lot = volume_max

        diff = abs(entry - sl)
        if direction == "BUY":
            tp = entry + diff * rr_ratio
        else:
            tp = entry - diff * rr_ratio

        tp = round(tp, digits)

        dist_points = abs(entry - sl) / point
        dist_pips = dist_points / 10.0

        add_log("info", f"ARMING TRADE DEBUG: RR={rr_ratio} Entry={entry} SL={sl} RiskDist={diff:.5f} ({dist_pips:.1f} pips) TP={tp}")

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
            "be_trigger": round(be_trigger, digits),
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

        pending_ids = list(pending_trades.keys())
        add_log("info", f"[ARM_STATE] trade_id={trade_id} symbol={symbol} direction={direction} pending_count={len(pending_ids)} pending_ids={pending_ids} pid={os.getpid()}")
        add_log("info", f"[TradeLifecycle][ARMED] trade_id={trade_id} symbol={symbol} direction={direction} entry={entry} manual_sl={sl} tp={tp} lot={lot} risk={risk_amount} created_at={datetime.now().isoformat()}")
        add_log("info", f"Prepared {direction} {symbol}: entry={entry}, SL={sl}, TP={tp}, lot={lot}")
        notify(f"🛡 <b>Trade Armed</b>\n{direction} {symbol}\nEntry: {entry}\nSL: {sl}\nTP: {tp}\nLot: {lot}\nRisk: {risk_amount}")

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
            "be_trigger": round(be_trigger, digits),
            "status": "armed",
            "candle_time": candle_time_str,
            "candle_open": open_,
            "candle_high": high,
            "candle_low": low,
            "candle_close": close,
            "stages": stages,
            "preflight": checks,
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Prepare trade failed")
        add_log("error", f"Prepare trade failed: {e}\n{tb}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/execute_trade", methods=["GET", "POST"])
def api_execute_trade():
    try:
        return _api_execute_trade_impl()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"EXECUTE_TRADE 500: {e}\n{tb}")
        add_log("error", f"EXECUTE_TRADE 500: {e}\n{tb}")
        try:
            notify(f"❌ <b>Execution Server Error</b>\n{e}")
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


def _api_execute_trade_impl():
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
        print(f"EXECUTE_TRADE 400: Invalid trade_id raw={raw_id!r} pending={list(pending_trades.keys())}")
        return jsonify({
            "error": "Invalid trade_id",
            "received_trade_id": trade_id,
            "pending": list(pending_trades.keys()),
        }), 400

    trade = pending_trades[trade_id]
    symbol = trade["symbol"]
    direction = trade["direction"]
    risk_amount = float(trade.get("risk_amount", DEFAULT_RISK_AMOUNT))
    rr_ratio = float(trade.get("rr_ratio", RR_RATIO))
    timeframe = trade.get("timeframe", TIMEFRAME)

    if not ensure_connected():
        error_msg = "MT5/EA not connected. Check EA status on the chart."
        print(f"EXECUTE_TRADE 400: Not connected")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: {error_msg}")
        return jsonify({"status": "error", "retcode": 0, "comment": error_msg}), 200

    bid, ask = get_current_price(symbol)
    if not bid or not ask:
        error_msg = f"Cannot get market price for {symbol}. EA may not have reported data yet."
        print(f"EXECUTE_TRADE 400: No market price bid={bid} ask={ask}")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: {error_msg}")
        return jsonify({"status": "error", "retcode": 0, "comment": error_msg}), 200

    info = ea_state.get("symbols", {}).get(symbol)
    if info is None:
        error_msg = f"Symbol info for {symbol} not available. EA may not have reported yet."
        print(f"EXECUTE_TRADE 400: Symbol info unavailable")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: {error_msg}")
        return jsonify({"status": "error", "retcode": 0, "comment": error_msg}), 200
    digits = int(info.get("digits", 5) or 5)
    point = float(info.get("point") or 0.00001)

    candle_high = trade.get("candle_high")
    candle_low = trade.get("candle_low")
    if candle_high is None or candle_low is None:
        print(f"EXECUTE_TRADE 400: Missing candle data high={candle_high} low={candle_low}")
        return jsonify({"status": "error", "retcode": 0, "comment": "Missing candle data for execution"}), 200

    if direction == "BUY":
        entry = ask
        sl = round(float(candle_low), digits)
        if entry <= sl:
            print(f"EXECUTE_TRADE 400: Price moved below candle low bid={bid} ask={ask} sl={sl}")
            return jsonify({"status": "error", "retcode": 0, "comment": "Price moved below candle low. Trade setup invalid."}), 200
    else:
        entry = bid
        sl = round(float(candle_high), digits)
        if entry >= sl:
            print(f"EXECUTE_TRADE 400: Price moved above candle high bid={bid} ask={ask} sl={sl}")
            return jsonify({"status": "error", "retcode": 0, "comment": "Price moved above candle high. Trade setup invalid."}), 200

    # Use pre-calculated lot size from arm time; only recalculate as a fallback.
    lot = trade.get("lot", 0)
    if not lot or lot <= 0:
        lot = calculate_lot_from_risk(entry, sl, risk_amount, symbol=symbol)
    if lot <= 0:
        print(f"EXECUTE_TRADE 400: Invalid lot calculated lot={lot}")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: Invalid lot size calculated (lot={lot})")
        return jsonify({"status": "error", "retcode": 0, "comment": "Invalid lot size calculated"}), 200
    volume_max = info.get("volume_max")
    if volume_max is not None and lot > volume_max:
        add_log("warn", f"Calculated lot {lot} exceeds broker max {volume_max}. Capping.")
        lot = volume_max

    # Calculate TP using the original risk distance (candle close to SL)
    # so the reward-to-risk ratio stays true to what was calculated at arm time.
    risk_distance = abs(trade.get("entry", entry) - trade.get("sl", sl))
    if direction == "BUY":
        tp = round(entry + risk_distance * rr_ratio, digits)
    else:
        tp = round(entry - risk_distance * rr_ratio, digits)

    # Check minimum stop distance before sending the order.
    # If the stop is too close to the market price, cancel the trade
    # rather than silently adjusting the stop loss or risking a broker rejection.
    broker_stops_level = float(info.get("trade_stops_level", 0) or 0)
    broker_min_dist_price = broker_stops_level * point if broker_stops_level > 0 else 0
    actual_sl_distance = abs(entry - sl)
    actual_tp_distance = abs(tp - entry)

    buffer_dist_price = 0.0
    if ENFORCE_MIN_STOP and MIN_STOP_BUFFER_PIPS > 0:
        buffer_dist_price = MIN_STOP_BUFFER_PIPS * point * 10

    required_distance = max(broker_min_dist_price, buffer_dist_price)

    if required_distance > 0 and (actual_sl_distance < required_distance or actual_tp_distance < required_distance):
        actual_dist_points = actual_sl_distance / point if point > 0 else 0
        broker_min_points = broker_stops_level
        error_msg = (
            "Trade cancelled.\n\n"
            "Reason:\n"
            "Selected candle stop-loss is too close to the current market price.\n"
            f"Broker minimum stop distance: {broker_min_points:.0f} points.\n"
            f"Actual stop distance: {actual_dist_points:.1f} points."
        )
        print(f"EXECUTE_TRADE: Stop too close. Broker min={broker_min_points:.0f} pts, "
              f"Actual={actual_dist_points:.1f} pts, SL={sl}, entry={entry}, TP={tp}")
        add_log("warn", error_msg.replace("\n", " "))
        notify(f"❌ <b>Trade Cancelled</b>\n{direction} {symbol}\n\nReason:\nStop too close to market price.\n"
               f"Broker minimum: {broker_min_points:.0f} points.\n"
               f"Actual: {actual_dist_points:.1f} points.")

        trade["status"] = "cancelled"
        trade["error"] = "stop_too_close"
        trade["cancel_reason"] = error_msg

        return jsonify({
            "status": "cancelled",
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot,
            "comment": error_msg,
        }), 200

    positions = get_open_positions()
    if positions is None:
        print(f"EXECUTE_TRADE 400: Could not fetch positions")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: Could not fetch positions")
        return jsonify({"status": "error", "retcode": 0, "comment": "Could not fetch positions"}), 200
    if len(positions) >= MAX_OPEN_POSITIONS:
        print(f"EXECUTE_TRADE 400: Max positions reached {len(positions)}")
        pending_trades[trade_id]["status"] = "error"
        pending_trades[trade_id]["error"] = "Max positions reached"
        notify(f"⚠️ <b>Max Positions Reached</b>\n{trade_id}\n{len(positions)} open positions, limit {MAX_OPEN_POSITIONS}")
        return jsonify({"status": "error", "retcode": 0, "comment": "Max positions reached"}), 200

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

    market = ea_state["market"].get(symbol, {})
    tick_bid = market.get("bid")
    tick_ask = market.get("ask")
    if tick_bid is None and tick_ask is None:
        try:
            from symbol_store import get_tick
            tick_bid, tick_ask = get_tick(symbol)
        except Exception:
            pass
    if direction == "BUY":
        executed_entry = tick_ask if tick_ask is not None else entry
    else:
        executed_entry = tick_bid if tick_bid is not None else entry
    slippage = abs(executed_entry - entry)

    from execution import execute_buy, execute_sell
    if mt5 is None:
        pending_trades[trade_id]["status"] = "queued"
        pending_trades[trade_id]["queued_for_ea"] = True
        add_log("info", f"Trade queued for EA execution: {direction} {lot} {symbol}")
        notify(f"⏳ <b>Trade Queued for EA Execution</b>\n{direction} {symbol}\nLot: {lot}\nEntry: {entry}\nSL: {sl}\nTP: {tp}")
        return jsonify({
            "status": "queued",
            "symbol": symbol,
            "direction": direction,
            "lot": lot,
            "sl": sl,
            "tp": tp,
            "entry": entry,
            "timeframe": timeframe,
        }), 202

    try:
        if direction == "BUY":
            result = execute_buy(symbol, lot, sl, tp, comment="EA Trade")
        else:
            result = execute_sell(symbol, lot, sl, tp, comment="EA Trade")
    except Exception as e:
        pending_trades[trade_id]["status"] = "error"
        pending_trades[trade_id]["error"] = str(e)
        add_log("error", f"Execution exception: {e}")
        print(f"EXECUTION_FAILED trade_id={trade_id} rc=0 comment={str(e)} result=None")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: {str(e)}")
        return jsonify({"status": "error", "retcode": 0, "comment": str(e)}), 500

    if result and result.retcode == _TRADE_RETCODE_DONE:
        pending_trades[trade_id]["status"] = "executed"
        pending_trades[trade_id]["stages"][-1]["done"] = True
        pending_trades[trade_id]["ticket"] = result.order
        pending_trades[trade_id]["deal"] = result.deal
        pending_trades[trade_id]["entry"] = entry
        pending_trades[trade_id]["sl"] = sl
        pending_trades[trade_id]["tp"] = tp
        pending_trades[trade_id]["lot"] = lot
        pending_trades[trade_id]["executed_entry"] = executed_entry
        pending_trades[trade_id]["slippage"] = slippage

        positions = get_open_positions(symbol)
        if positions:
            for pos in positions:
                if pos.magic == 123456 and abs(pos.volume - lot) < 0.001:
                    pending_trades[trade_id]["position_ticket"] = pos.ticket
                    break

        add_log("success", f"Executed {direction} {lot} {symbol} entry={executed_entry} SL={sl} TP={tp}")
        notify(f"✅ <b>Trade Executed</b>\n{direction} {symbol}\nTicket: {result.order}\nEntry: {executed_entry}\nSL: {sl}\nTP: {tp}\nLot: {lot}")

        history_entry = {
            "time": datetime.now().strftime("%H:%M"),
            "symbol": symbol,
            "direction": direction,
            "lot": lot,
            "entry": executed_entry,
            "sl": sl,
            "tp": tp,
            "status": "Executed",
            "ticket": result.order,
            "position_ticket": pending_trades[trade_id].get("position_ticket"),
            "slippage": slippage,
            "risk_amount": risk_amount,
            "rr_ratio": rr_ratio,
        }
        trade_history.append(history_entry)
        _save_trade_history_entry(history_entry)

        return jsonify({
            "status": "executed",
            "ticket": result.order,
            "deal": result.deal,
            "entry": executed_entry,
            "planned_entry": entry,
            "slippage": slippage,
            "sl": sl,
            "tp": tp,
            "lot": lot,
        })
    else:
        rc = result.retcode if result else 0
        comment = result.comment if result and getattr(result, 'comment', None) else "Execution rejected by broker"
        pending_trades[trade_id]["status"] = "error"
        pending_trades[trade_id]["error"] = comment
        add_log("error", f"Execution failed: retcode={rc}, comment={comment}")
        print(f"EXECUTION_FAILED trade_id={trade_id} rc={rc} comment={comment} result={result}")
        notify(f"⚠️ <b>Execution Failed</b>\n{trade_id}\nError: {comment}")
        return jsonify({
            "status": "error",
            "retcode": rc,
            "comment": comment,
        }), 200


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
    notify(f"❌ <b>Trade Cancelled</b>\n{trade_id}")
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

        if len(get_open_positions()) >= MAX_OPEN_POSITIONS:
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

        if result is not None and result.retcode == _TRADE_RETCODE_DONE:
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


def run_ui(host=None, port=None):
    host = host or FLASK_HOST
    port = port or FLASK_PORT
    add_log("info", f"UI server starting on {host}:{port}...")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_ui()
