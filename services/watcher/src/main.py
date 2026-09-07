"""Watcher service entrypoint: Selenium loop + supervision API."""

from __future__ import annotations

import asyncio
import logging
import threading

from aiohttp import web

from . import config
from .app import create_app
from .logutil import setup_logger
from .watcher import AlertWatcher

logger = setup_logger("watcher", config.LOG_DIR / "watcher.log", config.LOG_LEVEL)


async def run() -> None:
    watcher = AlertWatcher()
    thread = threading.Thread(target=watcher.start, daemon=True, name="alert-watcher")
    thread.start()

    app = create_app(watcher)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.WATCHER_PORT)
    await site.start()
    logger.info("Watcher API listening on :%s", config.WATCHER_PORT)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        watcher.cleanup()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("watcher").info("Watcher stopped")


if __name__ == "__main__":
    main()
