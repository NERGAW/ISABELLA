"""Logging setup for the ISABELLA core."""

import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "isabella.log"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_path: Path | None = None, debug: bool = False) -> logging.Logger:
    """Configure terminal and file handlers and return the core logger."""
    destination = log_path or DEFAULT_LOG_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    stream_handler = logging.StreamHandler(sys.__stdout__)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(destination, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    return logging.getLogger("CORE")
