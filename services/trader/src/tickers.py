"""Load ticker allowlist and resolve coins against HyperLiquid meta."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("trader")


@dataclass
class Ticker:
    alert: str
    coin: str
    resolved_coin: str | None = None
    sz_decimals: int = 0
    active: bool = False


class TickerRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.tickers: dict[str, Ticker] = {}
        self._load_file()

    def _load_file(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"tickers file not found: {self.path}")
        data = yaml.safe_load(self.path.read_text()) or {}
        rows = data.get("tickers") or []
        for row in rows:
            alert = str(row["alert"]).strip()
            coin = str(row["coin"]).strip()
            self.tickers[alert] = Ticker(alert=alert, coin=coin)
        logger.info("Loaded %s ticker mapping(s) from %s", len(self.tickers), self.path.name)

    @property
    def allowed_alerts(self) -> set[str]:
        return set(self.tickers.keys())

    def resolve(self, alert: str) -> Ticker:
        ticker = self.tickers.get(alert)
        if ticker is None:
            raise ValueError(f"Unsupported ticker: {alert}")
        if not ticker.active or not ticker.resolved_coin:
            raise ValueError(f"Ticker {alert} ({ticker.coin}) is not listed on this HyperLiquid network")
        return ticker

    def activate_from_universe(self, universe: list[dict[str, Any]]) -> None:
        """Match allowlist coins against HL universe names (including dex:COIN)."""
        by_name: dict[str, dict[str, Any]] = {}
        for asset in universe:
            name = str(asset.get("name", ""))
            if not name:
                continue
            by_name[name.upper()] = asset
            short = name.split(":")[-1].upper()
            by_name.setdefault(short, asset)

        for ticker in self.tickers.values():
            asset = by_name.get(ticker.coin.upper())
            if not asset:
                ticker.active = False
                ticker.resolved_coin = None
                logger.warning("Ticker %s (%s) not found on HyperLiquid — inactive", ticker.alert, ticker.coin)
                continue
            ticker.resolved_coin = asset["name"]
            ticker.sz_decimals = int(asset.get("szDecimals", 0) or 0)
            ticker.active = True
            logger.info("Ticker %s -> %s (szDecimals=%s)", ticker.alert, ticker.resolved_coin, ticker.sz_decimals)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "alert": t.alert,
                "coin": t.coin,
                "resolved_coin": t.resolved_coin,
                "active": t.active,
            }
            for t in self.tickers.values()
        ]
