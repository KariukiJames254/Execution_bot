import os
import sys
from loguru import logger

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

_initialized = False


def setup_logger(name="execution_bot", level="INFO"):
    global _initialized
    os.makedirs(LOG_DIR, exist_ok=True)

    log_file = os.path.join(LOG_DIR, "trading.log")

    if _initialized:
        return logger

    logger.configure(
        handlers=[
            {"sink": log_file, "level": level, "format": "{time:YYYY-MM-DD HH:mm:ss} [{level}] {name}: {message}"},
            {"sink": sys.stderr, "level": level, "format": "{time:HH:mm:ss} [{level}] {name}: {message}"},
        ]
    )
    _initialized = True
    return logger