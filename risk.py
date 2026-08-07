from logger import setup_logger
from symbol_store import symbol_info, get_tick

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

logger = setup_logger("risk")


class _Tick:
    """Minimal stand-in for an MT5 tick object."""

    __slots__ = ("bid", "ask", "time")

    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask
        self.time = None

    def __bool__(self):
        return self.bid is not None and self.ask is not None


def _symbol_info(symbol=None):
    if symbol is None:
        from config import SYMBOL
        symbol = SYMBOL
    info = symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info not found for {symbol}")
    return info


def _tick_info(symbol=None):
    if symbol is None:
        from config import SYMBOL
        symbol = SYMBOL
    bid, ask = get_tick(symbol)
    if bid is None or ask is None:
        logger.error(f"Failed to get tick for {symbol}")
        return None
    return _Tick(bid, ask)


def _account_info():
    info = mt5.account_info()
    if info is None:
        logger.error("Failed to get account info")
    return info


def calculate_sl(entry_price, sl_distance_pips, order_type):
    info = _symbol_info()
    if info is None:
        return entry_price
    point = info.point
    digits = info.digits
    if order_type == "BUY":
        return round(entry_price - sl_distance_pips * point, digits)
    return round(entry_price + sl_distance_pips * point, digits)


def calculate_tp(entry_price, sl_distance_pips, order_type, reward_ratio=5):
    tp_pips = sl_distance_pips * reward_ratio
    info = _symbol_info()
    if info is None:
        return entry_price
    point = info.point
    digits = info.digits
    if order_type == "BUY":
        return round(entry_price + tp_pips * point, digits)
    return round(entry_price - tp_pips * point, digits)


def calculate_pips_between(entry, sl):
    info = _symbol_info()
    if info is None:
        return 0.0
    return abs(entry - sl) / info.point


def get_pip_value(symbol=None):
    info = _symbol_info(symbol)
    if info is None:
        return 0.0
    return info.trade_tick_value / info.point


def get_loss_per_lot(entry, sl):
    info = _symbol_info()
    if info is None:
        return 0.0
    sl_distance_points = abs(entry - sl) / info.point
    return info.trade_tick_value * sl_distance_points


def calculate_lot_from_risk(entry_price, sl_price, risk_amount, symbol=None):
    if entry_price == sl_price:
        logger.error("Entry equals Stop Loss, cannot calculate lot size")
        return 0.01

    info = _symbol_info(symbol)
    if info is None:
        return 0.01

    tick = _tick_info(symbol)
    if tick is None:
        return 0.01

    tick_value = info.trade_tick_value
    if tick_value <= 0:
        logger.warning("trade_tick_value is " + str(tick_value) + ", using fallback for " + str(symbol or 'default'))
        point = info.point
        tick_value = 0.01 * point * 100000

    sl_distance_points = abs(entry_price - sl_price) / info.point
    loss_per_lot = tick_value * sl_distance_points

    if loss_per_lot <= 0:
        return 0.01

    lot_size = risk_amount / loss_per_lot
    lot_size = _round_to_lot_step(lot_size, symbol)
    lot_size = max(lot_size, info.volume_min)
    lot_size = min(lot_size, info.volume_max)
    lot_size = round(lot_size, 2)

    logger.info(
        f"Lot from risk: {symbol} entry={entry_price} SL={sl_price} "
        f"dist={sl_distance_points:.0f}pts tick_val=${tick_value:.4f}/pt "
        f"loss/lot=${loss_per_lot:.4f} risk=${risk_amount} -> lot={lot_size}"
    )
    return lot_size


def calculate_lot_percentage(symbol, risk_percent, sl_distance_pips, fixed_lot=None):
    if fixed_lot is not None:
        return fixed_lot

    account = _account_info()
    if account is None:
        return 0.01

    risk_amount = account.balance * (risk_percent / 100.0)
    info = _symbol_info(symbol)
    if info is None:
        return 0.01

    point = info.point
    tick = _tick_info(symbol)
    if tick is None:
        return 0.01

    tick_value = info.trade_tick_value
    if tick_value <= 0:
        tick_value = 0.01 * point * 100000

    sl_distance_points = sl_distance_pips * (0.0001 / point) if point < 0.01 else sl_distance_pips
    loss_per_lot = tick_value * sl_distance_points

    if loss_per_lot <= 0:
        return 0.01

    lot_size = risk_amount / loss_per_lot
    lot_size = _round_to_lot_step(lot_size, symbol)
    lot_size = max(lot_size, info.volume_min)
    lot_size = min(lot_size, info.volume_max)
    lot_size = round(lot_size, 2)

    logger.info(
        f"Lot from % risk: {symbol} balance={account.balance} "
        f"risk={risk_percent}% amount=${risk_amount:.2f} "
        f"sl={sl_distance_pips}pips loss/lot=${loss_per_lot:.4f} -> lot={lot_size}"
    )
    return lot_size


def _round_to_lot_step(lot_size, symbol):
    info = _symbol_info(symbol)
    if info is None:
        return round(lot_size, 2)
    step = info.volume_step
    if step <= 0 or step == 1:
        return round(lot_size, 2)
    return round(lot_size / step) * step


def get_risk_summary(entry_price, sl_price, risk_amount, symbol=None):
    info = _symbol_info(symbol)
    if info is None:
        return None

    sl_distance = abs(entry_price - sl_price)
    sl_distance_pips = sl_distance / info.point

    tick_value = info.trade_tick_value
    loss_per_lot = tick_value * sl_distance_pips

    lot_size = calculate_lot_from_risk(entry_price, sl_price, risk_amount, symbol)
    actual_risk = loss_per_lot * lot_size

    return {
        "symbol": symbol,
        "entry": entry_price,
        "sl": sl_price,
        "sl_distance_pips": round(sl_distance_pips, 1),
        "trade_tick_value": round(tick_value, 6),
        "loss_per_lot": round(loss_per_lot, 4),
        "risk_amount": risk_amount,
        "lot_size": lot_size,
        "actual_risk": round(actual_risk, 2),
        "pip_value": round(get_pip_value(symbol), 6),
    }