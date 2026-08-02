from logger import setup_logger

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

logger = setup_logger("broker")


def initialize():
    if mt5 is None:
        logger.error("MetaTrader5 package is not installed on this VPS")
        return False
    if not mt5.initialize():
        logger.error(f"MT5 initialization failed: {mt5.last_error()}")
        return False
    logger.info("MT5 initialized successfully")
    return True


def verify_terminal():
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        logger.error("MT5 terminal is not running")
        return False
    logger.info(f"MT5 terminal running: {terminal_info.name} v{terminal_info.build}")
    return True


def login(login, password, server, path=None):
    account = mt5.account_info()
    if account is not None:
        logger.info(f"Already logged in as {account.login}")
        return True

    authorized = mt5.login(
        login=login,
        password=password,
        server=server,
        path=path,
    )
    if not authorized:
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        return False
    logger.info(f"MT5 logged in as {login}")
    return True


def is_connected():
    if mt5 is None:
        return False
    return mt5.terminal_info() is not None and mt5.account_info() is not None


def get_account_details():
    account = mt5.account_info()
    if account is None:
        logger.error("No account info available")
        return None

    details = {
        "login": account.login,
        "server": account.server,
        "balance": account.balance,
        "equity": account.equity,
        "profit": account.profit,
        "currency": account.currency,
        "leverage": account.leverage,
        "trade_mode": account.trade_mode,
    }
    logger.info(f"Account details: {details}")
    return details


def shutdown():
    mt5.shutdown()
    logger.info("MT5 shutdown complete")


def ensure_connected():
    if is_connected():
        return True
    logger.warning("MT5 connection lost, attempting reconnect...")
    shutdown()
    if not initialize():
        return False
    from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
    return login(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH)