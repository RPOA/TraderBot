"""Trader service entrypoint."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .app import create_app
from .broker import Broker
from .config import (
    DATA_DIR,
    DIRECTION_STATE_FILE,
    HYPERLIQUID_NETWORK,
    LOG_DIR,
    LOG_LEVEL,
    TICKERS_FILE,
    TRADE_DIRECTION_INITIAL,
    TRADER_PORT,
    WALLETS_FILE,
)
from .direction import DirectionState
from hyperliquid.info import Info

from .hyperliquid_client import HyperliquidWallet, api_url, collect_universe
from .logutil import setup_logger
from .tickers import TickerRegistry
from .wallets import load_wallets

logger = setup_logger("trader", LOG_DIR / "trader.log", LOG_LEVEL)


async def run() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    tickers = TickerRegistry(TICKERS_FILE)
    try:
        info = Info(api_url(HYPERLIQUID_NETWORK), skip_ws=True)
        wanted = {item.coin for item in tickers.tickers.values()}
        tickers.activate_from_universe(collect_universe(info, wanted_coins=wanted))
    except Exception:
        logger.exception("Failed to load HyperLiquid universe — tickers stay inactive until restart")

    direction = DirectionState(DIRECTION_STATE_FILE, TRADE_DIRECTION_INITIAL)

    hl_wallets: dict[str, HyperliquidWallet] = {}
    try:
        configs = load_wallets(WALLETS_FILE)
        for wallet_id, cfg in configs.items():
            key = str(cfg["private_key"])
            if "YOUR_EVM" in key or key.endswith("PRIVATE_KEY"):
                logger.warning("Wallet %s has a placeholder key — skipped", wallet_id)
                continue
            try:
                hl_wallets[wallet_id] = HyperliquidWallet(
                    wallet_id=wallet_id,
                    private_key=key,
                    leverage=float(cfg["leverage"]),
                    collateral_percentage=float(cfg["collateral_percentage"]),
                    network=HYPERLIQUID_NETWORK,
                    account_address=str(cfg.get("account_address") or ""),
                    perp_dexs=tickers.extra_dexes(),
                )
            except Exception:
                logger.exception("Failed to init wallet %s", wallet_id)
    except FileNotFoundError:
        logger.warning("No wallets.json — trader will accept health checks only")

    broker = Broker(hl_wallets, tickers)
    app = create_app(broker, direction, tickers)
    asyncio.create_task(broker.worker())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", TRADER_PORT)
    await site.start()
    logger.info("Trader listening on :%s (%s)", TRADER_PORT, HYPERLIQUID_NETWORK)
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("trader").info("Trader stopped")


if __name__ == "__main__":
    main()
