from logger import setup_logger
from notifications import notify

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

from symbol_store import symbol_info, get_tick

_ORDER_TYPE_BUY = getattr(mt5, "ORDER_TYPE_BUY", 0)
_ORDER_TYPE_SELL = getattr(mt5, "ORDER_TYPE_SELL", 1)
_TRADE_ACTION_DEAL = getattr(mt5, "TRADE_ACTION_DEAL", 0)
_TRADE_ACTION_SLTP = getattr(mt5, "TRADE_ACTION_SLTP", 3)
_ORDER_TIME_GTC = getattr(mt5, "ORDER_TIME_GTC", 1)
_TRADE_RETCODE_DONE = getattr(mt5, "TRADE_RETCODE_DONE", 0)
_ORDER_FILLING_FOK = getattr(mt5, "ORDER_FILLING_FOK", 2)
_ORDER_FILLING_IOC = getattr(mt5, "ORDER_FILLING_IOC", 1)
_ORDER_FILLING_RETURN = getattr(mt5, "ORDER_FILLING_RETURN", 0)

logger = setup_logger("execution")


def _ensure_mt5():
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package is not installed on this VPS; trades must be executed by the EA")


def validate_min_stop_distance(symbol, order_type, entry, sl, tp):
    info = _symbol_info(symbol)
    if info is None:
        return True
    point = info.point
    min_stop = info.trade_stops_level * point

    if order_type == _ORDER_TYPE_BUY:
        sl_dist = entry - sl
        tp_dist = tp - entry
    else:
        sl_dist = sl - entry
        tp_dist = entry - tp

    if sl_dist < min_stop:
        logger.error(
            f"SL too close to entry for {order_type}: SL={sl}, entry={entry}, "
            f"distance={sl_dist:.6f}, min={min_stop:.6f}"
        )
        return False
    if tp_dist < min_stop:
        logger.error(
            f"TP too close to entry for {order_type}: TP={tp}, entry={entry}, "
            f"distance={tp_dist:.6f}, min={min_stop:.6f}"
        )
        return False
    return True


def execute_sl_tp(order_type, price, sl, tp):
    if order_type == _ORDER_TYPE_BUY:
        if sl >= price or tp <= price:
            logger.error("Invalid stops for BUY: SL must be below entry, TP must be above entry")
            return None
    else:
        if sl <= price or tp >= price:
            logger.error("Invalid stops for SELL: SL must be above entry, TP must be below entry")
            return None
    return True


def execute_buy(symbol, lot, sl, tp, comment=""):
    tick = _symbol_info(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return None

    info = _symbol_info(symbol)
    if info is None:
        logger.error("Failed to get symbol info for " + symbol)
        return None
    sl = round(sl, info.digits)
    tp = round(tp, info.digits)

    if not execute_sl_tp(_ORDER_TYPE_BUY, tick.ask, sl, tp):
        return None
    if not validate_min_stop_distance(symbol, _ORDER_TYPE_BUY, tick.ask, sl, tp):
        return None
    request = {
        "action": _TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": _ORDER_TYPE_BUY,
        "price": tick.ask,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 123456,
        "comment": comment,
        "type_time": _ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, "BUY")


def execute_sell(symbol, lot, sl, tp, comment=""):
    tick = _symbol_info(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return None

    info = _symbol_info(symbol)
    if info is None:
        logger.error("Failed to get symbol info for " + symbol)
        return None
    sl = round(sl, info.digits)
    tp = round(tp, info.digits)

    if not execute_sl_tp(_ORDER_TYPE_SELL, tick.bid, sl, tp):
        return None
    if not validate_min_stop_distance(symbol, _ORDER_TYPE_SELL, tick.bid, sl, tp):
        return None
    request = {
        "action": _TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": _ORDER_TYPE_SELL,
        "price": tick.bid,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 123456,
        "comment": comment,
        "type_time": _ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, "SELL")


def close_position(position_ticket):
    if mt5 is None:
        return None
    position = mt5.positions_get(ticket=position_ticket)
    if position is None or len(position) == 0:
        logger.error(f"Position {position_ticket} not found")
        return None

    pos = position[0]
    symbol = pos.symbol
    volume = pos.volume

    tick = _symbol_info(symbol)
    if tick is None:
        logger.error(f"Failed to get tick for {symbol}")
        return None

    if pos.type == _ORDER_TYPE_BUY:
        price = tick.bid
        order_type = _ORDER_TYPE_SELL
    else:
        price = tick.ask
        order_type = _ORDER_TYPE_BUY

    request = {
        "action": _TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "position": position_ticket,
        "price": price,
        "deviation": 10,
        "magic": 123456,
        "comment": "Close",
        "type_time": _ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, f"CLOSE {pos.type}")


def set_break_even(position_ticket, symbol):
    if mt5 is None:
        return False
    position = mt5.positions_get(ticket=position_ticket)
    if position is None or len(position) == 0:
        logger.error("Position " + str(position_ticket) + " not found for break-even")
        return False

    pos = position[0]
    tick = _symbol_info(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return False

    point = _get_point(symbol)
    digits = _get_digits(symbol)

    if pos.type == _ORDER_TYPE_BUY:
        new_sl = round(pos.price_open, digits)
        if not execute_sl_tp(_ORDER_TYPE_BUY, tick.bid, new_sl, pos.tp):
            return False
    else:
        new_sl = round(pos.price_open, digits)
        if not execute_sl_tp(_ORDER_TYPE_SELL, tick.ask, new_sl, pos.tp):
            return False

    request = {
        "action": _TRADE_ACTION_SLTP,
        "position": position_ticket,
        "sl": new_sl,
        "tp": pos.tp,
        "symbol": symbol,
        "deviation": 10,
        "magic": 123456,
        "comment": "Break-even",
        "type_time": _ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    result = _send_order(request, "BREAK-EVEN")
    if result is not None and result.retcode == _TRADE_RETCODE_DONE:
        notify(f"🔵 <b>Break-Even Activated</b>\nTicket: {position_ticket}\nSL moved to: {new_sl}")
    return result is not None and result.retcode == _TRADE_RETCODE_DONE


def get_open_positions(symbol=None):
    if mt5 is None:
        return []
    filters = {}
    if symbol:
        filters["symbol"] = symbol
    positions = mt5.positions_get(**filters)
    if positions is None:
        return []
    return positions


def _send_order(request, label):
    _ensure_mt5()
    check = mt5.order_check(request)
    if check is None:
        err = mt5.last_error()
        logger.error(f"{label} order_check returned None: {err}")
        raise RuntimeError(f"order_check failed: {err}")

    if check.retcode != 0:
        logger.error(f"{label} order_check failed: retcode={check.retcode}, comment={check.comment}")
        raise RuntimeError(f"order_check failed: retcode={check.retcode}, comment={check.comment}")

    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        logger.error(f"{label} order_send returned None: {err}")
        raise RuntimeError(f"order_send failed: {err}")

    if result.retcode != _TRADE_RETCODE_DONE:
        logger.error(
            f"{label} order failed: retcode={result.retcode}, "
            f"comment={result.comment}, request={request}"
        )
        return result

    logger.info(f"{label} order executed: ticket={result.order}, deal={result.deal}")
    return result


def _symbol_info(symbol):
    return symbol_info(symbol)


def _get_point(symbol):
    info = symbol_info(symbol)
    if info is None:
        return 0.00001
    return info.point


def _get_digits(symbol):
    info = symbol_info(symbol)
    if info is None:
        return 5
    return info.digits


def _get_filling(symbol):
    info = symbol_info(symbol)
    if info is None:
        return _ORDER_FILLING_FOK
    mode = info.filling_mode
    if mode & 1:
        return _ORDER_FILLING_FOK
    if mode & 2:
        return _ORDER_FILLING_IOC
    if mode & 4:
        return _ORDER_FILLING_RETURN
    return _ORDER_FILLING_FOK