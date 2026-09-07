"""Watcher configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


WATCHER_PORT = int(os.getenv("WATCHER_PORT", "8090"))
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
ORDERS_FILE = DATA_DIR / "orders.json"

TV_USERNAME = os.getenv("TV_USERNAME", "")
TV_PASSWORD = os.getenv("TV_PASSWORD", "")
TRADINGVIEW_URL = os.getenv("TRADINGVIEW_URL", "https://www.tradingview.com/ideas/")
CHROME_HEADLESS = os.getenv("CHROME_HEADLESS", "false").lower() == "true"
CHROME_BIN = os.getenv("CHROME_BIN", "/usr/bin/chromium")
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH", "/usr/bin/chromedriver")
CHROME_PROFILE_DIR = _path("CHROME_PROFILE_DIR", _SERVICE_ROOT / "chrome-profile")
CHROME_BACKUP_DIR = CHROME_PROFILE_DIR.parent / f"{CHROME_PROFILE_DIR.name}-backup"

TRADER_DO_TRADE_URL = os.getenv("TRADER_DO_TRADE_URL", "http://trader:8080/do_trade")
TRADER_TREND_URL = os.getenv("TRADER_TREND_URL", "http://trader:8080/trend")
WEBHOOK_RETRIES = int(os.getenv("WEBHOOK_RETRIES", "3"))

ALERT_CHECK_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL", "1"))
ALERT_MAX_AGE_MINUTES = int(os.getenv("ALERT_MAX_AGE_MINUTES", "5"))
HEALTH_CHECK_ENABLED = os.getenv("HEALTH_CHECK_ENABLED", "true").lower() == "true"
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))

TRADE_ALERT_NAMES = _csv("TRADE_ALERT_NAMES", "BotTradeAlert")
TREND_ALERT_NAMES = _csv("TREND_ALERT_NAMES", "TREND")

SELECTOR_ALERTS_BUTTON = os.getenv("SELECTOR_ALERTS_BUTTON", '[data-name="alerts"]')
SELECTOR_ALERTS_CONTAINER = os.getenv("SELECTOR_ALERTS_CONTAINER", '[data-name="widgetbar-pages-with-tabs"]')
SELECTOR_LOG_TAB = os.getenv("SELECTOR_LOG_TAB", "button#log")
SELECTOR_USER_MENU = os.getenv("SELECTOR_USER_MENU", 'button[aria-label*="menu"]')

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
