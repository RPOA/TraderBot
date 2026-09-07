"""Logging helpers shared with the watcher API."""

from __future__ import annotations

import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path


class MemoryLogHandler(logging.Handler):
    def __init__(self, maxlen: int = 500):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def lines(self, n: int = 50) -> list[str]:
        return list(self.buffer)[-n:]


memory_handler = MemoryLogHandler()


def setup_logger(name: str, log_file: Path, level: str = "INFO") -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    memory_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console)
    logger.addHandler(memory_handler)
    logger.propagate = False
    return logger
