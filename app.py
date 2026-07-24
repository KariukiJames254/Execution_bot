import time
from datetime import datetime, timedelta, timezone
from config import (
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    MT5_PATH,
    SYMBOL,
    TIMEFRAME,
    SL_PIPS,
    RISK_PER_TRADE,
    MAX_OPEN_POSITIONS,
    FIXED_LOT,
)
from broker import initialize, login, shutdown, is_connected, get_account_details
from market import get_previous_candle, get_current_price, is_market_open
from timer import wait_for_candle_close
from risk import calculate_lot_size, calculate_stop_loss, calculate_take_profit
from execution import execute_buy, execute_sell, get_open_positions, close_position
from telegram_bot import send_trade_notification
from logger import setup_logger

logger = setup_logger("app")


def check_daily_loss():
    account = get_account_details()
    if account is None:
        return False
    daily_pnl = _get_daily_pnl()
    max_loss = account["balance"] * (MAX_DAILY_LOSS / 100.0)
    if daily_pnl < -max_loss:
        logger.warning(
            f"Daily loss limit reached: pnl={daily_pnl:.2f}, "
            f"limit={-max_loss:.2f}"
        )
        return True
    return False


def _get_daily_pnl():
    import MetaTrader5 as mt5
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    trades = mt5.history_deals_get(date_from=today)
    if trades is None:
        return 0.0
    return sum(t.profit for t in trades)


def has_open_position(symbol=None):
    positions = get_open_positions(symbol)
    return len(positions) > 0


def get_user_direction():
    while True:
        direction = input("Enter direction (BUY/SELL) or 'q' to quit: ").strip().upper()
        if direction in ("BUY", "SELL"):
            return direction
        if direction == "Q":
            return None
        logger.warning("Invalid input. Please enter BUY or SELL.")


def execute_trade(direction):
    if not is_connected():
        logger.error("MT5 not connected")
        return

    if not is_market_open(SYMBOL):
        logger.warning(f"Market is closed for {SYMBOL}")
        return

    if check_daily_loss():
        logger.warning("Daily loss limit reached, skipping trade")
        return

    if has_open_position(SYMBOL):
        logger.info(f"Already have an open position on {SYMBOL}")
        return

    if len(get_open_positions()) >= MAX_OPEN_POSITIONS:
        logger.info(f"Max open positions ({MAX_OPEN_POSITIONS}) reached")
        return

    candle = get_previous_candle(SYMBOL, TIMEFRAME)
    if candle is None:
        logger.error("Could not read previous closed candle")
        return

    entry_price = candle["close"]

    lot = calculate_lot_size(SYMBOL, RISK_PER_TRADE, SL_PIPS, FIXED_LOT)
    sl = calculate_stop_loss(entry_price, SL_PIPS, direction)
    tp = calculate_take_profit(entry_price, SL_PIPS, direction)

    if direction == "BUY":
        logger.info(f"BUY: price={entry_price}, lot={lot}, SL={sl}, TP={tp}")
        result = execute_buy(SYMBOL, lot, sl, tp, comment="Manual Signal")
    else:
        logger.info(f"SELL: price={entry_price}, lot={lot}, SL={sl}, TP={tp}")
        result = execute_sell(SYMBOL, lot, sl, tp, comment="Manual Signal")

    if result is not None and result.retcode == 0:
        logger.info("Trade executed successfully, entering monitor phase")
        send_trade_notification(direction, SYMBOL, lot, entry_price, sl, tp)
        monitor_trade(direction)


def monitor_trade(direction):
    logger.info("Monitoring trade...")
    try:
        while True:
            positions = get_open_positions(SYMBOL)
            if len(positions) == 0:
                logger.info("Trade closed (position no longer open)")
                return

            pos = positions[0]
            bid, ask = get_current_price(SYMBOL)
            if bid is None or ask is None:
                logger.warning("Could not fetch current price, retrying...")
                time.sleep(5)
                continue

            if direction == "BUY":
                current_price = bid
            else:
                current_price = ask

            pnl = (current_price - pos.price_open) * pos.volume if direction == "BUY" else (pos.price_open - current_price) * pos.volume
            logger.info(
                f"Position active: type={direction}, "
                f"price={current_price:.5f}, "
                f"pnl={pnl:.2f}"
            )
            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Manual close requested by user")
        positions = get_open_positions(SYMBOL)
        if len(positions) > 0:
            close_position(positions[0].ticket)
            logger.info("Position closed manually")


def main():
    logger.info("Execution Bot starting...")

    if not initialize():
        logger.error("Failed to initialize MT5")
        return

    if not login(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH):
        logger.error("Failed to login to MT5")
        shutdown()
        return

    account = get_account_details()
    logger.info(f"Connected: {account['login']} on {account['server']}")
    logger.info(f"Trading {SYMBOL} on {TIMEFRAME} timeframe")
    logger.info(f"Risk: {RISK_PER_TRADE}% SL={SL_PIPS}pips, R:R 1:5")

    try:
        while True:
            direction = get_user_direction()
            if direction is None:
                logger.info("Exiting...")
                break

            wait_for_candle_close(SYMBOL, TIMEFRAME, callback=None)
            execute_trade(direction)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
    finally:
        shutdown()


if __name__ == "__main__":
    main()