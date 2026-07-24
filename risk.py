from logger import setup_logger

logger = setup_logger("risk")


def calculate_stop_loss(entry_price, sl_pips, order_type):
    symbol_info = _get_symbol_info()
    if symbol_info is None:
        return entry_price

    point = symbol_info.point
    digits = symbol_info.digits

    if order_type == "BUY":
        sl = round(entry_price - sl_pips * point, digits)
    else:
        sl = round(entry_price + sl_pips * point, digits)

    logger.info(f"Stop Loss calculated: {order_type} entry={entry_price}, SL={sl}, {sl_pips}pips")
    return sl


def calculate_take_profit(entry_price, sl_pips, order_type, reward_ratio=5):
    tp_pips = sl_pips * reward_ratio
    symbol_info = _get_symbol_info()
    if symbol_info is None:
        return entry_price

    point = symbol_info.point
    digits = symbol_info.digits

    if order_type == "BUY":
        tp = round(entry_price + tp_pips * point, digits)
    else:
        tp = round(entry_price - tp_pips * point, digits)

    logger.info(
        f"Take Profit calculated: {order_type} entry={entry_price}, "
        f"TP={tp}, {tp_pips}pips ({reward_ratio}:1 ratio)"
    )
    return tp


def calculate_lot_from_risk(entry_price, sl_price, risk_amount, symbol=None):
    if symbol is None:
        from config import SYMBOL
        symbol = SYMBOL

    if sl_price == entry_price:
        logger.error("Stop Loss equals Entry price, cannot calculate lot size")
        return 0.01

    symbol_info = _get_symbol_info(symbol)
    if symbol_info is None:
        return 0.01

    point = symbol_info.point
    digits = symbol_info.digits
    pip_size = point * 10 if point < 0.01 else point

    distance_pips = abs(entry_price - sl_price) / pip_size
    if distance_pips <= 0:
        return 0.01

    tick_info = _get_tick_info(symbol)
    if tick_info is None:
        return 0.01

    pip_value = _get_pip_value(symbol, tick_info)
    if pip_value <= 0:
        return 0.01

    lot_size = risk_amount / (distance_pips * pip_value)
    lot_size = round_to_lot_step(lot_size, symbol)
    lot_size = max(lot_size, _get_min_lot(symbol))
    lot_size = min(lot_size, _get_max_lot(symbol))

    logger.info(
        f"Lot from risk: entry={entry_price}, SL={sl_price}, "
        f"distance={distance_pips}pips, pip_value={pip_value}, "
        f"risk=${risk_amount}, lot={lot_size}"
    )
    return lot_size


def calculate_lot_size(symbol, risk_percent, sl_pips, fixed_lot=None):
    if fixed_lot is not None:
        logger.info(f"Using fixed lot size: {fixed_lot}")
        return fixed_lot

    account_info = _get_account_info()
    if account_info is None:
        return 0.01

    balance = account_info.balance
    risk_amount = balance * (risk_percent / 100.0)

    tick_info = _get_tick_info(symbol)
    if tick_info is None:
        return 0.01

    pip_value = _get_pip_value(symbol, tick_info)
    if pip_value <= 0:
        return 0.01

    lot_size = risk_amount / (sl_pips * pip_value)
    lot_size = round_to_lot_step(lot_size, symbol)
    lot_size = max(lot_size, _get_min_lot(symbol))
    lot_size = min(lot_size, _get_max_lot(symbol))

    logger.info(
        f"Lot size calculated: balance={balance}, risk={risk_percent}%, "
        f"sl={sl_pips}pips, pip_value={pip_value}, lot={lot_size}"
    )
    return lot_size


def round_to_lot_step(lot_size, symbol):
    symbol_info = _get_symbol_info(symbol)
    if symbol_info is None:
        return round(lot_size, 2)
    step = symbol_info.volume_step
    if step <= 0:
        return round(lot_size, 2)
    return round(lot_size / step) * step


def _get_account_info():
    import MetaTrader5 as mt5
    info = mt5.account_info()
    if info is None:
        logger.error("Failed to get account info")
    return info


def _get_tick_info(symbol):
    import MetaTrader5 as mt5
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Failed to get tick info for {symbol}")
    return tick


def _get_pip_value(symbol, tick_info):
    import MetaTrader5 as mt5
    symbol_info = _get_symbol_info(symbol)
    if symbol_info is None:
        return 0.0
    tick_value = tick_info.value
    if tick_value <= 0:
        tick_value = 0.01 * symbol_info.point * 100000
    return tick_value / (symbol_info.point * 10000000) if symbol_info.point > 0 else tick_value


def _get_symbol_info(symbol=None):
    import MetaTrader5 as mt5
    if symbol is None:
        from config import SYMBOL
        symbol = SYMBOL
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info not found for {symbol}")
    return info


def _get_min_lot(symbol):
    info = _get_symbol_info(symbol)
    if info is None:
        return 0.01
    return info.volume_min


def _get_max_lot(symbol):
    info = _get_symbol_info(symbol)
    if info is None:
        return 100.0
    return info.volume_max