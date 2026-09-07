"""HyperLiquid SDK wrapper: positions, sizing, market orders, SL/TP."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger("trader")


def api_url(network: str) -> str:
    if network == "mainnet":
        return constants.MAINNET_API_URL
    return constants.TESTNET_API_URL


def _round_size(size: float, sz_decimals: int) -> float:
    if sz_decimals <= 0:
        return math.floor(size)
    factor = 10 ** sz_decimals
    return math.floor(size * factor) / factor


def collect_universe(info: Info, wanted_coins: set[str] | None = None) -> list[dict[str, Any]]:
    """Collect the default perp universe plus any HIP-3 coins we explicitly want.

    Do not walk every builder dex — testnet can expose hundreds and hang startup.
    """
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_meta(meta: dict[str, Any]) -> None:
        for item in meta.get("universe") or []:
            name = item.get("name")
            if name and name not in seen:
                seen.add(name)
                assets.append(item)

    add_meta(info.meta())

    wanted = {coin.upper() for coin in (wanted_coins or set())}
    found = {str(item.get("name", "")).upper() for item in assets}
    found.update(name.split(":")[-1].upper() for name in found)
    missing = wanted - found
    if not missing:
        return assets

    try:
        mids = info.all_mids() or {}
    except Exception as exc:
        logger.debug("all_mids() unavailable: %s", exc)
        return assets

    extra_dexes: set[str] = set()
    for name in mids:
        short = name.split(":")[-1].upper()
        if short in missing and ":" in name:
            extra_dexes.add(name.split(":", 1)[0])
            assets.append({"name": name, "szDecimals": 0})
            seen.add(name)
            missing.discard(short)
        elif short in missing:
            assets.append({"name": name, "szDecimals": 0})
            seen.add(name)
            missing.discard(short)

    for dex in extra_dexes:
        try:
            add_meta(info.meta(dex=dex))
        except Exception as exc:
            logger.debug("Could not load meta for dex %s: %s", dex, exc)

    return assets


class HyperliquidWallet:
    def __init__(
        self,
        wallet_id: str,
        private_key: str,
        leverage: float,
        collateral_percentage: float,
        network: str,
        account_address: str = "",
    ):
        self.wallet_id = wallet_id
        self.leverage = leverage
        self.collateral_percentage = collateral_percentage
        self.ready = False
        key = private_key.strip()
        if not key.startswith("0x"):
            key = "0x" + key
        self.account = Account.from_key(key)
        self.address = account_address.strip() or self.account.address
        base_url = api_url(network)
        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(self.account, base_url, account_address=self.address)
        self.ready = True
        logger.info("Wallet %s ready (%s) L:%sx C:%s%%", wallet_id, self.address[:10], leverage, collateral_percentage)

    def user_state(self) -> dict[str, Any]:
        return self.info.user_state(self.address)

    def mid_price(self, coin: str) -> float:
        mids = self.info.all_mids()
        if coin not in mids:
            raise ValueError(f"No mid price for {coin}")
        return float(mids[coin])

    def position_size(self, coin: str) -> float:
        state = self.user_state()
        for item in state.get("assetPositions") or []:
            pos = item.get("position") or {}
            if pos.get("coin") == coin:
                return float(pos.get("szi") or 0)
        return 0.0

    def account_value(self) -> float:
        state = self.user_state()
        summary = state.get("marginSummary") or {}
        return float(summary.get("accountValue") or 0)

    def wallet_snapshot(self) -> dict[str, Any]:
        state = self.user_state()
        positions = []
        for item in state.get("assetPositions") or []:
            pos = item.get("position") or {}
            szi = float(pos.get("szi") or 0)
            if szi == 0:
                continue
            positions.append(
                {
                    "coin": pos.get("coin"),
                    "size": szi,
                    "side": "long" if szi > 0 else "short",
                    "entry_px": pos.get("entryPx"),
                    "unrealized_pnl": pos.get("unrealizedPnl"),
                }
            )
        return {
            "wallet_id": self.wallet_id,
            "address": self.address,
            "account_value": float((state.get("marginSummary") or {}).get("accountValue") or 0),
            "withdrawable": float(state.get("withdrawable") or 0),
            "positions": positions,
        }

    def calculate_size(self, coin: str, sz_decimals: int, qty_percentage: float | None) -> float:
        pct = qty_percentage if qty_percentage is not None else self.collateral_percentage
        mid = self.mid_price(coin)
        notional = self.account_value() * (pct / 100.0) * self.leverage
        if mid <= 0 or notional <= 0:
            raise ValueError("Cannot size position: mid or account value is zero")
        size = _round_size(notional / mid, sz_decimals)
        if size <= 0:
            raise ValueError("Calculated size is zero — increase collateral or leverage")
        return size

    def set_leverage(self, coin: str) -> None:
        try:
            self.exchange.update_leverage(int(self.leverage), coin, is_cross=True)
        except Exception as exc:
            logger.warning("Could not update leverage for %s/%s: %s", self.wallet_id, coin, exc)

    def market_open(self, coin: str, is_buy: bool, size: float) -> Any:
        return self.exchange.market_open(coin, is_buy, size, None, 0.02)

    def market_close(self, coin: str, size: float | None = None) -> Any:
        return self.exchange.market_close(coin, sz=size, slippage=0.02)

    def place_trigger(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        trigger_px: float,
        tpsl: str,
    ) -> Any:
        return self.exchange.order(
            coin,
            is_buy,
            size,
            trigger_px,
            {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": tpsl}},
            reduce_only=True,
        )

    async def wait_flat(self, coin: str, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abs(self.position_size(coin)) < 1e-12:
                return True
            await asyncio.sleep(0.4)
        return abs(self.position_size(coin)) < 1e-12
