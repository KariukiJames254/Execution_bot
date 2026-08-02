"""In-process store for symbol information reported by the Windows MT5 EA.

The VPS no longer runs the MetaTrader5 Python package, so it cannot call
``mt5.symbol_info()`` directly.  Instead the Expert Advisor reports the static
symbol properties (and keeps the live bid/ask fresh via the market report).
This module is the single cache consumed by every VPS component that previously
called ``mt5.symbol_info()``.
"""
import threading
from datetime import datetime

from logger import setup_logger

logger = setup_logger("symbol_store")

_lock = threading.Lock()
_symbols = {}
_candles = {}


class SymbolInfo:
    """Lightweight object mimicking the MT5 symbol_info interface."""

    def __init__(self, symbol, data=None):
        d = dict(data or {})
        self.symbol = symbol or d.get("symbol")
        self.digits = int(d.get("digits", 5) or 5)
        self.point = float(d.get("point") or 0.00001)
        self.volume_min = float(d.get("volume_min", 0.01) or 0.01)
        self.volume_max = float(d.get("volume_max", 100.0) or 100.0)
        self.volume_step = float(d.get("volume_step", 0.01) or 0.01)
        self.trade_tick_value = float(d.get("trade_tick_value") or 0.0)
        self.trade_stops_level = int(d.get("trade_stops_level", 0) or 0)
        self.filling_mode = int(d.get("filling_mode", 0) or 0)
        self.visible = bool(d.get("visible", True))
        self.trade_mode = int(d.get("trade_mode", 0) or 0)
        self.trade_expert = bool(d.get("trade_expert", True))
        self.bid = float(d.get("bid") or 0.0)
        self.ask = float(d.get("ask") or 0.0)
        self.time = d.get("time")

    def __bool__(self):
        return bool(self.symbol)

    def __getitem__(self, key):
        return getattr(self, key, None)


def set_symbol_info(symbol, data):
    """Store (or refresh) the static symbol info reported by the EA."""
    key = _key(symbol)
    if not key:
        return
    with _lock:
        _symbols.setdefault(key, {"symbol": symbol})
        _symbols[key].update(data or {})
        _symbols[key]["symbol"] = symbol


def update_tick(symbol, bid, ask):
    """Refresh the live bid/ask for a symbol (called by the market report)."""
    key = _key(symbol)
    if not key:
        return
    with _lock:
        _symbols.setdefault(key, {"symbol": symbol})
        _symbols[key]["bid"] = float(bid)
        _symbols[key]["ask"] = float(ask)
        _symbols[key]["time"] = datetime.now().isoformat()


def get_symbol_info(symbol=None):
    """Return a :class:`SymbolInfo` for *symbol*, or ``None`` if not reported."""
    key = _key(symbol)
    if not key:
        return None
    with _lock:
        data = _symbols.get(key)
    if not data:
        return None
    return SymbolInfo(symbol, data)


def get_tick(symbol=None):
    """Return ``(bid, ask)`` for *symbol*, or ``(None, None)`` if unknown."""
    info = get_symbol_info(symbol)
    if info is None:
        return None, None
    return info.bid, info.ask


def has_symbol_info(symbol=None):
    key = _key(symbol)
    return bool(key and key in _symbols)


def _key(symbol):
    if symbol is None:
        from config import SYMBOL
        symbol = SYMBOL
    return symbol.upper() if symbol else ""


def _mt5():
    try:
        import MetaTrader5 as mt5
        return mt5
    except Exception:
        return None


def symbol_info(symbol=None):
    """EA-reported symbol info, falling back to the MT5 package when installed.

    On the production VPS the MetaTrader5 package is unavailable, so callers
    rely on the values reported by the EA.  When the package *is* present (e.g.
    the standalone CLI bot) the MT5 terminal is still consulted as a fallback
    for symbols the EA has not reported yet.
    """
    info = get_symbol_info(symbol)
    if info is not None:
        return info
    mt5 = _mt5()
    if mt5 is not None:
        try:
            return mt5.symbol_info(symbol)
        except Exception:
            return None
    return None


def set_candle(symbol, timeframe, candle):
    """Store the latest candle data reported by the EA."""
    key = _key(symbol)
    if not key:
        return
    with _lock:
        _candles.setdefault(key, {})
        _candles[key][timeframe] = candle


def get_candle(symbol=None, timeframe=None):
    """Return the latest candle reported by the EA, or ``None``."""
    key = _key(symbol)
    if not key:
        return None
    if timeframe is None:
        from config import TIMEFRAME
        timeframe = TIMEFRAME
    with _lock:
        return _candles.get(key, {}).get(timeframe)
