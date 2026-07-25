import MetaTrader5 as mt5
from logger import setup_logger

logger = setup_logger("execution")


def validate_min_stop_distance(symbol, order_type, entry, sl, tp):
    info = _symbol_info(symbol)
    if info is None:
        return True
    point = info.point
    min_stop = info.trade_stops_level * point

    if order_type == mt5.ORDER_TYPE_BUY:
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
    if order_type == mt5.ORDER_TYPE_BUY:
        if sl >= price or tp <= price:
            logger.error("Invalid stops for BUY: SL must be below entry, TP must be above entry")
            return None
    else:
        if sl <= price or tp >= price:
            logger.error("Invalid stops for SELL: SL must be above entry, TP must be below entry")
            return None
    return True


def execute_buy(symbol, lot, sl, tp, comment=""):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return None

    info = _symbol_info(symbol)
    if info is None:
        logger.error("Failed to get symbol info for " + symbol)
        return None
    sl = round(sl, info.digits)
    tp = round(tp, info.digits)

    if not execute_sl_tp(mt5.ORDER_TYPE_BUY, tick.ask, sl, tp):
        return None
    if not validate_min_stop_distance(symbol, mt5.ORDER_TYPE_BUY, tick.ask, sl, tp):
        return None
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY,
        "price": tick.ask,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 123456,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, "BUY")


def execute_sell(symbol, lot, sl, tp, comment=""):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return None

    info = _symbol_info(symbol)
    if info is None:
        logger.error("Failed to get symbol info for " + symbol)
        return None
    sl = round(sl, info.digits)
    tp = round(tp, info.digits)

    if not execute_sl_tp(mt5.ORDER_TYPE_SELL, tick.bid, sl, tp):
        return None
    if not validate_min_stop_distance(symbol, mt5.ORDER_TYPE_SELL, tick.bid, sl, tp):
        return None
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_SELL,
        "price": tick.bid,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 123456,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, "SELL")


def close_position(position_ticket):
    position = mt5.positions_get(ticket=position_ticket)
    if position is None or len(position) == 0:
        logger.error(f"Position {position_ticket} not found")
        return None

    pos = position[0]
    symbol = pos.symbol
    volume = pos.volume

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Failed to get tick for {symbol}")
        return None

    if pos.type == mt5.ORDER_TYPE_BUY:
        price = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
    else:
        price = tick.ask
        order_type = mt5.ORDER_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "position": position_ticket,
        "price": price,
        "deviation": 10,
        "magic": 123456,
        "comment": "Close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    return _send_order(request, f"CLOSE {pos.type}")


def set_break_even(position_ticket, symbol):
    position = mt5.positions_get(ticket=position_ticket)
    if position is None or len(position) == 0:
        logger.error("Position " + str(position_ticket) + " not found for break-even")
        return False

    pos = position[0]
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error("Failed to get tick for " + symbol)
        return False

    point = _get_point(symbol)
    digits = _get_digits(symbol)

    if pos.type == mt5.ORDER_TYPE_BUY:
        new_sl = round(pos.price_open + 1 * point, digits)
        if not execute_sl_tp(mt5.ORDER_TYPE_BUY, tick.bid, new_sl, pos.tp):
            return False
    else:
        new_sl = round(pos.price_open - 1 * point, digits)
        if not execute_sl_tp(mt5.ORDER_TYPE_SELL, tick.ask, new_sl, pos.tp):
            return False

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position_ticket,
        "sl": new_sl,
        "tp": pos.tp,
        "symbol": symbol,
        "deviation": 10,
        "magic": 123456,
        "comment": "Break-even",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _get_filling(symbol),
    }
    result = _send_order(request, "BREAK-EVEN")
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


def get_open_positions(symbol=None):
    filters = {}
    if symbol:
        filters["symbol"] = symbol
    positions = mt5.positions_get(**filters)
    if positions is None:
        return []
    return positions


def _send_order(request, label):
    check = mt5.order_check(request)
    if check is None:
        logger.error(f"{label} order_check returned None: {mt5.last_error()}")
        return None

    if check.retcode != 0:
        logger.error(f"{label} order_check failed: retcode={check.retcode}, comment={check.comment}")
        return check

    result = mt5.order_send(request)
    if result is None:
        logger.error(f"{label} order_send returned None: {mt5.last_error()}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            f"{label} order failed: retcode={result.retcode}, "
            f"comment={result.comment}, request={request}"
        )
        return result

    logger.info(f"{label} order executed: ticket={result.order}, deal={result.deal}")
    return result


def _symbol_info(symbol):
    return mt5.symbol_info(symbol)


def _get_point(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.00001
    return info.point


def _get_digits(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return 5
    return info.digits


def _get_filling(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_FOK
    mode = info.filling_mode
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    if mode & 4:
        return mt5.ORDER_FILLING_RETURN
    return mt5.ORDER_FILLING_FOK