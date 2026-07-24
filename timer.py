import time
from datetime import datetime, timedelta, timezone
from logger import setup_logger
from config import TIMEFRAME

logger = setup_logger("timer")

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}


def detect_timeframe(timeframe=None):
    if timeframe is None:
        timeframe = TIMEFRAME
    if timeframe not in TIMEFRAME_SECONDS:
        logger.error(f"Unknown timeframe: {timeframe}")
        return None, None
    seconds = TIMEFRAME_SECONDS[timeframe]
    logger.info(f"Timeframe detected: {timeframe} ({seconds}s)")
    return timeframe, seconds


def get_seconds_remaining(timeframe=None):
    tf, tf_seconds = detect_timeframe(timeframe)
    if tf is None:
        return None
    now = datetime.now(timezone.utc)
    candle_end = _get_candle_boundary(now, tf, tf_seconds)
    remaining = (candle_end - now).total_seconds()
    return max(remaining, 0)


def _get_candle_boundary(now, timeframe, tf_seconds):
    if timeframe.startswith("M"):
        minutes = int(timeframe[1:])
        candle_start = now.replace(second=0, microsecond=0)
        candle_start = candle_start.replace(minute=candle_start.minute - (candle_start.minute % minutes))
    elif timeframe == "H1":
        candle_start = now.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "H4":
        candle_start = now.replace(minute=0, second=0, microsecond=0)
        candle_start = candle_start.replace(hour=candle_start.hour - (candle_start.hour % 4))
    elif timeframe == "D1":
        candle_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == "W1":
        candle_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        candle_start = candle_start.replace(day=now.day - ((now.weekday()) % 7))
    elif timeframe == "MN1":
        candle_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        candle_start = now.replace(second=0, microsecond=0)
    return candle_start + timedelta(seconds=tf_seconds)


def wait_for_candle_close(symbol, timeframe=None, callback=None):
    tf, tf_seconds = detect_timeframe(timeframe)
    if tf is None:
        return

    logger.info(f"Waiting for {symbol} {tf} candle to close...")

    while True:
        now = datetime.now(timezone.utc)
        candle_end = _get_candle_boundary(now, tf, tf_seconds)
        seconds_remaining = (candle_end - now).total_seconds()

        if seconds_remaining <= 0:
            logger.info(f"Candle closed for {symbol} {tf}")
            if callback is not None:
                logger.info("Notifying execution module via callback")
                callback()
            return

        logger.info(f"Waiting {seconds_remaining:.0f}s for {symbol} {tf} candle close")
        time.sleep(min(seconds_remaining, 1.0))