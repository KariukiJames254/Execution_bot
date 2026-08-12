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


class _CloseResult:
    __slots__ = ("retcode", "comment", "order", "deal", "already_closed", "verified_closed", "volume")
    def __init__(self, retcode=0, comment="", order=0, deal=0, already_closed=False, verified_closed=False, volume=0.0):
        self.retcode = retcode
        self.comment = comment
        self.order = order
        self.deal = deal
        self.already_closed = already_closed
        self.verified_closed = verified_closed
        self.volume = volume

def close_position(position_ticket):
    if mt5 is None:
        return None

    position = mt5.positions_get(ticket=position_ticket)
    if position is None or len(position) == 0:
        logger.info(f"[CLOSE_REQUEST] ALREADY_CLOSED ticket={position_ticket}")
        return _CloseResult(retcode=_TRADE_RETCODE_DONE, comment="Position already closed", already_closed=True, verified_closed=True)

    pos = position[0]
    symbol = pos.symbol
    volume = pos.volume

    logger.info(f"[CLOSE_REQUEST] ticket={position_ticket} symbol={symbol} volume={volume} type={pos.type}")

    tick = _symbol_info(symbol)
    if tick is None:
        logger.error(f"[CLOSE_ATTEMPT] NO_TICK ticket={position_ticket} symbol={symbol}")
        return None

    if pos.type == _ORDER_TYPE_BUY:
        price = tick.bid
        order_type = _ORDER_TYPE_SELL
    else:
        price = tick.ask
        order_type = _ORDER_TYPE_BUY

    logger.info(f"[CLOSE_ATTEMPT] ticket={position_ticket} symbol={symbol} type={order_type} volume={volume} price={price}")

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

    result = _send_order(request, f"CLOSE {pos.type}")

    if result is not None:
        logger.info(f"[CLOSE_RESULT] ticket={position_ticket} retcode={result.retcode} comment={result.comment} order={result.order} deal={result.deal}")
    else:
        logger.error(f"[CLOSE_RESULT] ticket={position_ticket} retcode=None order_check or order_send failed")

    positions_after = mt5.positions_get(ticket=position_ticket)
    position_exists_after = positions_after is not None and len(positions_after) > 0
    remaining_volume = positions_after[0].volume if position_exists_after else 0

    logger.info(f"[CLOSE_VERIFY] ticket={position_ticket} position_exists={position_exists_after} remaining_volume={remaining_volume}")

    if not position_exists_after:
        logger.info(f"[FINAL_CLOSE_STATE] CLOSED ticket={position_ticket} symbol={symbol}")
        if result is not None:
            result.verified_closed = True
            result.already_closed = False
            result.volume = volume
            return result
        return _CloseResult(retcode=_TRADE_RETCODE_DONE, comment="Position closed (verified)", order=0, deal=0, verified_closed=True, volume=volume)

    if result is not None and result.retcode == _TRADE_RETCODE_DONE:
        logger.info(f"[FINAL_CLOSE_STATE] CLOSED ticket={position_ticket} symbol={symbol}")
        result.verified_closed = True
        result.already_closed = False
        result.volume = volume
        return result

    logger.error(f"[FINAL_CLOSE_STATE] FAILED ticket={position_ticket} symbol={symbol} retcode={result.retcode if result else 0} comment={result.comment if result else 'Unknown'}")
    if result is not None:
        result.verified_closed = False
    if result is None:
        result = _CloseResult(retcode=0, comment="Order send failed", volume=volume)
    return result


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