"""Trader configuration loaded from environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_DIRECTIONS = ("short", "long", "all")
DEFAULT_WALLET_ID = "main"

TRADER_PORT = int(os.getenv("TRADER_PORT", "8080"))
HYPERLIQUID_NETWORK = os.getenv("HYPERLIQUID_NETWORK", "testnet").lower()
TRADE_DIRECTION_INITIAL = os.getenv("TRADE_DIRECTION", "all").lower()
TRADE_DIRECTION_WEBHOOK_ENABLED = os.getenv("TRADE_DIRECTION_WEBHOOK_ENABLED", "true").lower() == "true"
CLOSE_ON_TREND_CHANGE = os.getenv("CLOSE_ON_TREND_CHANGE", "true").lower() == "true"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_LEVERAGE = float(os.getenv("DEFAULT_LEVERAGE", "2.0"))
DEFAULT_COLLATERAL_PERCENTAGE = float(os.getenv("DEFAULT_COLLATERAL_PERCENTAGE", "10.0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _path(env_name: str, default: Path) -> Path:
    raw = os.getenv(env_name)
    if not raw:
        return default
    path = Path(raw)
    if str(path).startswith("/app") and not Path("/app").exists():
        return default
    return path


DATA_DIR = _path("DATA_DIR", _SERVICE_ROOT / "data")
LOG_DIR = _path("LOG_DIR", _SERVICE_ROOT / "logs")
WALLETS_FILE = _path("WALLETS_FILE", _SERVICE_ROOT / "wallets.json")
TICKERS_FILE = _path("TICKERS_FILE", _SERVICE_ROOT / "tickers.yaml")
DIRECTION_STATE_FILE = DATA_DIR / "trade_direction_state.json"
