import asyncio
import os
from telegram import Bot
from logger import setup_logger

logger = setup_logger("telegram")


def _get_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _get_chat_id():
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if chat_id:
        return chat_id
    logger.warning("TELEGRAM_CHAT_ID not set in environment")
    return None


def _get_bot():
    token = _get_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured")
        return None
    return Bot(token=token)


def _async_send(bot, chat_id, text, parse_mode=None):
    async def _do_send():
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    asyncio.run(_do_send())


def send_trade_notification(direction, symbol, lot, entry, sl, tp):
    bot = _get_bot()
    if bot is None:
        return False

    chat_id = _get_chat_id()
    if chat_id is None:
        return False

    message = (
        f"Trade Signal\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Direction: {direction}\n"
        f"Symbol: {symbol}\n"
        f"Entry: {entry}\n"
        f"Lot Size: {lot}\n"
        f"Stop Loss: {sl}\n"
        f"Take Profit: {tp}\n"
        f"Risk/Reward: 1:5\n"
        f"━━━━━━━━━━━━━━━"
    )

    try:
        _async_send(bot, chat_id, message, parse_mode="Markdown")
        logger.info(f"Trade notification sent to chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False


def send_error_notification(error_message):
    bot = _get_bot()
    if bot is None:
        return False

    chat_id = _get_chat_id()
    if chat_id is None:
        return False

    message = f"Bot Error\n━━━━━━━━━━━\n{error_message}"

    try:
        _async_send(bot, chat_id, message)
        logger.info("Error notification sent to Telegram")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram error notification: {e}")
        return False


def send_status_message(status):
    bot = _get_bot()
    if bot is None:
        return False

    chat_id = _get_chat_id()
    if chat_id is None:
        return False

    message = f"Bot Status\n━━━━━━━━━━━\n{status}"

    try:
        _async_send(bot, chat_id, message)
        logger.info("Status notification sent to Telegram")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram status notification: {e}")
        return False