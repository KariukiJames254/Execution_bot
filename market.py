import pandas as pd
import MetaTrader5 as mt5
from logger import setup_logger
from config import TIMEFRAME

logger = setup_logger("market")


def get_current_price(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Failed to get tick for {symbol}: {mt5.last_error()}")
        return None, None
    return tick.bid, tick.ask


def get_latest_candle(symbol, timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 1)
    if rates is None or len(rates) == 0:
        logger.error(f"No latest candle data for {symbol}")
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


def get_previous_candle(symbol, timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)
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
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        logger.error(f"Failed to fetch candles for {symbol}")
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def get_symbol_info(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol {symbol} not found")
        return None
    return info


def is_market_open(symbol):
    info = get_symbol_info(symbol)
    if info is None:
        return False
    return info.trade_mode != 0