import os
from dotenv import load_dotenv

load_dotenv()

MT5_LOGIN = os.environ.get("MT5_LOGIN", "")
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER = os.environ.get("MT5_SERVER", "")
MT5_PATH = os.environ.get("MT5_PATH", "")

SYMBOL = os.environ.get("SYMBOL", "EURUSD")
TIMEFRAME = os.environ.get("TIMEFRAME", "M15")

RISK_PER_TRADE = float(os.environ.get("RISK_PER_TRADE", "1.0"))
MAX_DAILY_LOSS = float(os.environ.get("MAX_DAILY_LOSS", "5.0"))
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "1"))

SL_PIPS = int(os.environ.get("SL_PIPS", "50"))
TP_PIPS = int(os.environ.get("TP_PIPS", "100"))

DEFAULT_RISK_AMOUNT = float(os.environ.get("DEFAULT_RISK_AMOUNT", "100"))

BE_ENABLED = os.environ.get("BE_ENABLED", "True").lower() == "true"
BE_PIPS = int(os.environ.get("BE_PIPS", "5"))

FIXED_LOT = os.environ.get("FIXED_LOT", "")
if FIXED_LOT:
    FIXED_LOT = float(FIXED_LOT)
else:
    FIXED_LOT = None

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")