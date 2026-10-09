import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging() -> logging.Logger:
    """Configure the "server" logger once: console plus a rotating file.

    Level comes from PARKING_LOG_LEVEL (default INFO). The file lives at
    Server/logs/server.log, or PARKING_LOG_FILE if set. Safe to call twice.
    """
    logger = logging.getLogger("server")
    if logger.handlers:
        return logger

    level = os.environ.get("PARKING_LOG_LEVEL", "INFO").upper()
    logger.setLevel(level if level in logging.getLevelNamesMapping() else "INFO")
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_file = Path(os.environ.get("PARKING_LOG_FILE", LOG_DIR / "server.log"))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("File logging disabled (%s): %s", log_file, exc)

    return logger
