import pandas as pd
import MetaTrader5 as mt5
from logger import setup_logger
from config import TIMEFRAME

logger = setup_logger("market")

_TF_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4, "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12, "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2, "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4, "H6": mt5.TIMEFRAME_H6, "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def _resolve_timeframe(timeframe):
    if isinstance(timeframe, int):
        return timeframe
    return _TF_MAP.get(str(timeframe).upper())


def _is_ipc_error(error):
    if not error:
        return False
    code = error[0] if isinstance(error, tuple) else None
    return code == -10001 or (isinstance(code, int) and code < 0)


def _safe_call(func, *args, on_ipc_reconnect=None, **kwargs):
    try:
        result = func(*args, **kwargs)
        if result is None:
            error = mt5.last_error()
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
    info = _safe_call(mt5.symbol_info, symbol)
    if info is None:
        return False
    if not info.visible:
        logger.info(f"Selecting {symbol} in Market Watch")
        _safe_call(mt5.symbol_select, symbol, True)
    return True


def get_latest_candle(symbol, timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME

    if not _ensure_symbol_selected(symbol):
        logger.error(f"Symbol {symbol} not available")
        return None

    tf = _resolve_timeframe(timeframe)
    if tf is None:
        logger.error(f"Invalid timeframe: {timeframe}")
        return None

    def _fetch_candle():
        return mt5.copy_rates_from_pos(symbol, tf, 0, 1)

    def _fetch_range():
        import datetime as dt
        end = dt.datetime.now()
        start = end - dt.timedelta(minutes=5)
        return mt5.copy_rates_range(symbol, tf, start, end)

    rates = _safe_call(_fetch_candle, on_ipc_reconnect=_fetch_candle)
    if rates is None or len(rates) == 0:
        logger.warning(f"copy_rates_from_pos failed for {symbol}, trying copy_rates_range")
        rates = _safe_call(_fetch_range, on_ipc_reconnect=_fetch_range)

    if rates is None or len(rates) == 0:
        logger.error(f"No latest candle data for {symbol}")
        return None

    row = rates[len(rates) - 1]
    return {
        "time": pd.to_datetime(row["time"], unit="s"),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "tick_volume": row["tick_volume"],
        "spread": row["spread"],
        "real_volume": row["real_volume"],
    }


def get_current_price(symbol):
    tick = _safe_call(mt5.symbol_info_tick, symbol)
    if tick is None:
        return None, None
    return tick.bid, tick.ask


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
        "time": pd.to_datetime(row["time"], unit="s"),
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
        return pd.DataFrame()

    def _fetch_candles():
        return mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

    rates = _safe_call(_fetch_candles, on_ipc_reconnect=_fetch_candles)
    if rates is None or len(rates) == 0:
        logger.error(f"Failed to fetch candles for {symbol}")
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_symbol_info(symbol):
    info = _safe_call(mt5.symbol_info, symbol)
    if info is None:
        logger.error(f"Symbol {symbol} not found")
        return None
    return info


def is_market_open(symbol):
    info = get_symbol_info(symbol)
    if info is None:
        return False
    return info.trade_mode != 0