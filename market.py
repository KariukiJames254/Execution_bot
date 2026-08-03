import time
from datetime import datetime, timezone

from logger import setup_logger
from config import TIMEFRAME
from symbol_store import symbol_info, get_tick

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

logger = setup_logger("market")

_TF_MAP = {
    "M1": "TIMEFRAME_M1", "M2": "TIMEFRAME_M2", "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4", "M5": "TIMEFRAME_M5", "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10", "M12": "TIMEFRAME_M12", "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4", "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def _resolve_timeframe(timeframe):
    if isinstance(timeframe, int):
        return timeframe
    if mt5 is not None:
        return getattr(mt5, _TF_MAP.get(str(timeframe).upper(), ""), None)
    return None


def _is_ipc_error(error):
    if not error:
        return False
    code = error[0] if isinstance(error, tuple) else None
    return code == -10001 or (isinstance(code, int) and code < 0)


def _safe_call(func, *args, on_ipc_reconnect=None, **kwargs):
    try:
        result = func(*args, **kwargs)
        if result is None:
            error = mt5.last_error() if mt5 is not None else None
            if _is_ipc_error(error):
                logger.warning(f"IPC error detected: {error}")
                from broker import ensure_connected
                if ensure_connected() and on_ipc_reconnect:
                    try:
                        return on_ipc_reconnect()
                    except Exception as retry_err:
                        logger.error(f"Retry after reconnect failed: {retry_err}")
                        return None
        return result
    except Exception as e:
        logger.error(f"MT5 call failed: {e}")
        try:
            from broker import ensure_connected
            if ensure_connected() and on_ipc_reconnect:
                return on_ipc_reconnect()
        except Exception as retry_err:
            logger.error(f"Retry after exception failed: {retry_err}")
        return None


def _ensure_symbol_selected(symbol):
    if mt5 is None:
        return symbol_info(symbol) is not None
    info = symbol_info(symbol)
    if info is None:
        info = _safe_call(mt5.symbol_info, symbol)
    if info is None:
        return False
    if not getattr(info, "visible", True):
        logger.info(f"Selecting {symbol} in Market Watch")
        _safe_call(mt5.symbol_select, symbol, True)
        import time as _time
        for _ in range(5):
            _time.sleep(0.3)
            info = _safe_call(mt5.symbol_info, symbol)
            if getattr(info, "visible", True):
                return True
        return False
    return True


def _normalize_ea_candle(candle):
    """Convert EA-reported candle dict to the format get_latest_candle returns."""
    if candle is None:
        return None
    time_val = candle.get("time")
    if time_val is not None:
        try:
             time_val = datetime.fromtimestamp(float(time_val), tz=timezone.utc)
        except (ValueError, TypeError):
            try:
                time_val = datetime.fromtimestamp(time_val, tz=timezone.utc)
            except Exception:
                time_val = datetime.fromtimestamp(0, tz=timezone.utc)
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


def get_latest_candle(symbol, timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME

    if not _ensure_symbol_selected(symbol):
        logger.error(f"Symbol {symbol} not available")
        from symbol_store import get_candle
        ea_candle = get_candle(symbol, timeframe)
        if ea_candle:
            return _normalize_ea_candle(ea_candle)
        return None

    if mt5 is None:
        from symbol_store import get_candle
        ea_candle = get_candle(symbol, timeframe)
        if ea_candle:
            return _normalize_ea_candle(ea_candle)
        logger.error(f"No MT5 connection and no EA candle data for {symbol}")
        return None

    tf = _resolve_timeframe(timeframe)

    def _fetch_candle():
        return mt5.copy_rates_from_pos(symbol, tf, 0, 1)

    def _fetch_range():
        import datetime as dt
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(minutes=5)
        return mt5.copy_rates_range(symbol, tf, start, end)

    rates = _safe_call(_fetch_candle, on_ipc_reconnect=_fetch_candle)
    if rates is None or len(rates) == 0:
        logger.warning(f"copy_rates_from_pos failed for {symbol}, trying copy_rates_range")
        rates = _safe_call(_fetch_range, on_ipc_reconnect=_fetch_range)

    if rates is None or len(rates) == 0:
        # After SymbolSelect the symbol's history may not be loaded yet;
        # retry a few times with a short delay before giving up.
        import time as _time
        for _ in range(3):
            _time.sleep(0.5)
            rates = _safe_call(_fetch_candle, on_ipc_reconnect=_fetch_candle)
            if rates is not None and len(rates) > 0:
                break

    if rates is None or len(rates) == 0:
        logger.error(f"No latest candle data for {symbol}")
        return None

    row = rates[len(rates) - 1]
    return {
        "time":         datetime.fromtimestamp(row["time"], tz=timezone.utc),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "tick_volume": row["tick_volume"],
        "spread": row["spread"],
        "real_volume": row["real_volume"],
    }


def get_current_price(symbol):
    bid, ask = get_tick(symbol)
    if bid is None and ask is None and mt5 is not None:
        tick = _safe_call(mt5.symbol_info_tick, symbol)
        if tick is not None:
            return tick.bid, tick.ask
    return bid, ask


def get_previous_candle(symbol, timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME

    if not _ensure_symbol_selected(symbol):
        logger.error(f"Symbol {symbol} not available")
        return None

    def _fetch_prev():
        return mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)

    rates = _safe_call(_fetch_prev, on_ipc_reconnect=_fetch_prev)
    if rates is None or len(rates) == 0:
        logger.error(f"No previous candle data for {symbol}")
        return None
    row = rates[0]
    return {
        "time":         datetime.fromtimestamp(row["time"], tz=timezone.utc),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "tick_volume": row["tick_volume"],
        "spread": row["spread"],
        "real_volume": row["real_volume"],
    }


def get_timeframe():
    return TIMEFRAME


def get_candles(symbol, timeframe=None, count=100):
    if timeframe is None:
        timeframe = TIMEFRAME

    if not _ensure_symbol_selected(symbol):
        logger.error(f"Symbol {symbol} not available")
        return []

    tf = _resolve_timeframe(timeframe)

    def _fetch_candles():
        return mt5.copy_rates_from_pos(symbol, tf, 0, count)

    rates = _safe_call(_fetch_candles, on_ipc_reconnect=_fetch_candles)
    if rates is None or len(rates) == 0:
        logger.error(f"Failed to fetch candles for {symbol}")
        return []
    candles = []
    for row in rates:
        candles.append({
        "time": datetime.fromtimestamp(row["time"], tz=timezone.utc),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "tick_volume": row["tick_volume"],
            "spread": row["spread"],
            "real_volume": row["real_volume"],
        })
    return candles


def get_symbol_info(symbol):
    info = symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol {symbol} not found")
        return None
    return info


def is_market_open(symbol):
    info = get_symbol_info(symbol)
    if info is None:
        return False
    return info.trade_mode != 0