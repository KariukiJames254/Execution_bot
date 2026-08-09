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
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))

SL_PIPS = int(os.environ.get("SL_PIPS", "50"))
TP_PIPS = int(os.environ.get("TP_PIPS", "100"))
RR_RATIO = float(os.environ.get("RR_RATIO", "5"))

DEFAULT_RISK_AMOUNT = float(os.environ.get("DEFAULT_RISK_AMOUNT", "100"))

BE_ENABLED = os.environ.get("BE_ENABLED", "True").lower() == "true"
BE_RR = float(os.environ.get("BE_RR", "1.0"))

FIXED_LOT = os.environ.get("FIXED_LOT", "")
if FIXED_LOT:
    FIXED_LOT = float(FIXED_LOT)
else:
    FIXED_LOT = None

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.environ.get("FLASK_PORT", "5000"))

# Dashboard access is disabled until both credentials are set in the VPS .env.
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DASHBOARD_SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "")

MIN_STOP_BUFFER_PIPS = float(os.environ.get("MIN_STOP_BUFFER_PIPS", "1"))
ENFORCE_MIN_STOP = os.environ.get("ENFORCE_MIN_STOP", "False").lower() == "true"
