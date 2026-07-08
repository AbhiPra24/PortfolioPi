"""Centralized logging configuration for the bot and web dashboard."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        "logs/portfoliopi.log", maxBytes=5*1024*1024, backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(log_format))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
