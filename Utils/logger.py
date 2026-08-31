"""Lambda-friendly logger factory."""

from __future__ import annotations

import logging
import sys

from config.settings import get_settings


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger that writes to stdout (CloudWatch).

    Args:
        name (str): Logger name, typically ``__name__`` of the caller module.

    Returns:
        logging.Logger: Logger with the process log level applied. Handlers
            are attached only once to avoid duplicate lines on warm starts.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    settings = get_settings()
    logger.setLevel(settings.log_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
