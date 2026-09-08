"""Trade execution: queue, close-first, SL/TP, trend close."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .config import CLOSE_ON_TREND_CHANGE, DEFAULT_WALLET_ID
from .hyperliquid_client import HyperliquidWallet
from .tickers import TickerRegistry

logger = logging.getLogger("trader")


class Broker:
    def __init__(self, wallets: dict[str, HyperliquidWallet], tickers: TickerRegistry):
        self.wallets = wallets
        self.tickers = tickers
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def get_wallet(self, wallet_id: str | None) -> HyperliquidWallet:
        wallet_id = wallet_id or DEFAULT_WALLET_ID
        if wallet_id not in self.wallets:
            raise KeyError(wallet_id)
        wallet = self.wallets[wallet_id]
        if not wallet.ready:
            raise RuntimeError(f"Wallet {wallet_id} is not connected")
        return wallet

    async def enqueue(self, trade: dict[str, Any]) -> None:
        await self.queue.put(trade)
        size = self.queue.qsize()
        logger.info("Trade queued (%s in queue): %s %s", size, trade["action"], trade["ticker"])

    async def worker(self) -> None:
        logger.info("Trade queue worker started")
        while True:
            trade = await self.queue.get()
            try:
                await self.execute(trade)
            except Exception:
                logger.exception("Trade queue worker error")
            finally:
                self.queue.task_done()

    async def execute(self, trade: dict[str, Any]) -> dict[str, Any]:
        action = trade["action"]
        ticker = trade["ticker"]
        wallet_id = trade.get("wallet_id") or DEFAULT_WALLET_ID
        wallet = self.get_wallet(wallet_id)
        resolved = self.tickers.resolve(ticker)
        coin = resolved.resolved_coin
        assert coin is not None

        started = time.monotonic()
        if action == "close":
            result = await asyncio.to_thread(self._close, wallet, coin)
            result["execution_time"] = time.monotonic() - started
            self._log_result(action, ticker, wallet_id, result)
            return result

        existing = await asyncio.to_thread(wallet.position_size, coin)
        if abs(existing) > 1e-12:
            side = "long" if existing > 0 else "short"
            logger.info(
                "Close-first: %s has %s %s (size=%s) before %s",
                wallet_id,
                side,
                coin,
                existing,
                action,
            )
            close_result = await asyncio.to_thread(self._close, wallet, coin)
            if not close_result.get("success"):
                return {
                    "success": False,
                    "error": f"Failed to close existing position: {close_result.get('error')}",
                }
            flat = await wallet.wait_flat(coin)
            if not flat:
                return {"success": False, "error": f"Position on {coin} not flat after close"}
            logger.info("Position on %s is flat — opening %s", coin, action)

        result = await asyncio.to_thread(
            self._open,
            wallet,
            coin,
            resolved.sz_decimals,
            action,
            trade.get("stop_loss_price"),
            trade.get("take_profit_price"),
            trade.get("qty_percentage"),
        )
        result["execution_time"] = time.monotonic() - started
        self._log_result(action, ticker, wallet_id, result)
        return result

    def _close(self, wallet: HyperliquidWallet, coin: str) -> dict[str, Any]:
        size = wallet.position_size(coin)
        if abs(size) < 1e-12:
            return {"success": True, "message": "already flat"}
        try:
            raw = wallet.market_close(coin)
            return {"success": True, "raw": raw, "closed_size": size}
        except Exception as exc:
            logger.exception("market_close failed")
            return {"success": False, "error": str(exc)}

    def _open(
        self,
        wallet: HyperliquidWallet,
        coin: str,
        sz_decimals: int,
        action: str,
        stop_loss: float | None,
        take_profit: float | None,
        qty_percentage: float | None,
    ) -> dict[str, Any]:
        is_buy = action == "buy"
        try:
            wallet.set_leverage(coin)
            size = wallet.calculate_size(coin, sz_decimals, qty_percentage)
            raw = wallet.market_open(coin, is_buy, size)
            sl_raw = None
            tp_raw = None
            if stop_loss is not None or take_profit is not None:
                # Triggers close the position, so side is opposite the entry.
                close_is_buy = not is_buy
                if stop_loss is not None:
                    sl_raw = wallet.place_trigger(coin, close_is_buy, size, stop_loss, "sl")
                if take_profit is not None:
                    tp_raw = wallet.place_trigger(coin, close_is_buy, size, take_profit, "tp")
            return {
                "success": True,
                "coin": coin,
                "size": size,
                "raw": raw,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "stop_loss_raw": sl_raw,
                "take_profit_raw": tp_raw,
            }
        except Exception as exc:
            logger.exception("open failed")
            return {"success": False, "error": str(exc)}

    async def close_all_positions(self, wallet_id: str | None = None) -> dict[str, Any]:
        if wallet_id:
            if wallet_id not in self.wallets:
                return {
                    "closed": [],
                    "errors": [{"wallet_id": wallet_id, "error": f"Unknown wallet: {wallet_id}"}],
                    "skipped": False,
                }
            targets = {wallet_id: self.wallets[wallet_id]}
        else:
            targets = self.wallets
        return await self._close_positions(targets, lambda _side: (True, "close all"))

    async def close_on_trend_change(self, new_direction: str) -> dict[str, Any]:
        if not CLOSE_ON_TREND_CHANGE:
            return {"closed": [], "errors": [], "skipped": True}

        def decide(side: str) -> tuple[bool, str]:
            if new_direction == "all":
                return True, "flip to all — close first (HyperLiquid may reject pyramiding)"
            if new_direction == "long" and side == "short":
                return True, "SHORT conflicts with LONG"
            if new_direction == "short" and side == "long":
                return True, "LONG conflicts with SHORT"
            return False, ""

        return await self._close_positions(self.wallets, decide)

    async def _close_positions(self, targets: dict[str, HyperliquidWallet], decide) -> dict[str, Any]:
        closed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for wallet_id, wallet in targets.items():
            try:
                snapshot = await asyncio.to_thread(wallet.wallet_snapshot)
            except Exception as exc:
                errors.append({"wallet_id": wallet_id, "error": str(exc)})
                continue

            for pos in snapshot["positions"]:
                coin = pos["coin"]
                side = pos["side"]
                should_close, reason = decide(side)
                if not should_close:
                    logger.info("Keeping %s/%s %s", wallet_id, coin, side)
                    continue

                logger.info("Close %s/%s: %s", wallet_id, coin, reason)
                result = await asyncio.to_thread(self._close, wallet, coin)
                if result.get("success"):
                    await wallet.wait_flat(coin)
                    closed.append({"wallet_id": wallet_id, "coin": coin, "side": side})
                else:
                    errors.append({"wallet_id": wallet_id, "coin": coin, "error": result.get("error")})

        return {"closed": closed, "errors": errors, "skipped": False}

    def _log_result(self, action: str, ticker: str, wallet_id: str, result: dict[str, Any]) -> None:
        if result.get("success"):
            logger.info("OK %s %s wallet=%s %s", action, ticker, wallet_id, result)
        else:
            logger.error("FAIL %s %s wallet=%s: %s", action, ticker, wallet_id, result.get("error"))
