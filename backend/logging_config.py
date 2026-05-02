"""Unified logging configuration for the PR Review Agent."""
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging():
    """Configure root logger with console + rotating file handlers."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Clear any existing handlers from basicConfig calls
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating file handler (10 MB max, 5 backups)
    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root
