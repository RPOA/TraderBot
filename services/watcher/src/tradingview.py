"""Selenium helpers for TradingView alert log scraping."""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import config
from .logutil import short_exc

logger = logging.getLogger("watcher")

# TradingView sometimes renders `||` as `| |` in the alert widget.
_DELIM_RE = re.compile(r"\s*\|\s*\|\s*")
_TRADE_PAYLOAD_RE = re.compile(
    r"(\S+\|\|##.+?##(?:@\S+)?)\|\|(\d{4}-\d{2}-\d{2}T[^\s]+)",
)
_TREND_PAYLOAD_RE = re.compile(
    r"(\*\*trend/\w+\*\*)\|\|(\d{4}-\d{2}-\d{2}T[^\s]+)",
)
_skipped_names: set[str] = set()


def _cleanup_chrome_locks(profile_dir: Path, debug_port: str) -> None:
    for lock_file in profile_dir.glob("Singleton*"):
        try:
            lock_file.unlink()
        except Exception:
            pass
    default = profile_dir / "Default"
    if default.exists():
        for lock_file in default.glob("Singleton*"):
            try:
                lock_file.unlink()
            except Exception:
                pass
    try:
        result = subprocess.run(
            ["fuser", f"{debug_port}/tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        for pid in result.stdout.strip().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    except FileNotFoundError:
        pass


def create_chrome_driver() -> webdriver.Chrome:
    options = Options()
    if Path(config.CHROME_BIN).exists():
        options.binary_location = config.CHROME_BIN
    if config.CHROME_HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    profile_dir = config.CHROME_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_chrome_locks(profile_dir, "9222")
    options.add_argument(f"--user-data-dir={profile_dir}")

    driver_path = config.CHROME_DRIVER_PATH
    service = Service(executable_path=driver_path) if Path(driver_path).exists() else Service()
    driver = webdriver.Chrome(service=service, options=options)
    logger.info("Chrome driver ready (profile=%s)", profile_dir)
    return driver


def is_logged_in(driver: webdriver.Chrome) -> bool:
    try:
        url = driver.current_url
        if "/accounts/signin" in url or "/accounts/login" in url:
            return False
        started = driver.find_elements(By.XPATH, "//*[contains(text(), 'Get started')]")
        if started:
            return False
        driver.find_element(By.CSS_SELECTOR, config.SELECTOR_USER_MENU)
        return True
    except NoSuchElementException:
        return False


def open_alerts_panel(driver: webdriver.Chrome) -> bool:
    try:
        try:
            panel = driver.find_element(By.CSS_SELECTOR, config.SELECTOR_ALERTS_CONTAINER)
            hidden = "hidden" in (panel.get_attribute("class") or "")
            if not hidden:
                _click_log_tab(driver)
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        wait = WebDriverWait(driver, 3)
        button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, config.SELECTOR_ALERTS_BUTTON)))
        button.click()
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, config.SELECTOR_ALERTS_CONTAINER))
        )
        _click_log_tab(driver)
        return True
    except Exception as exc:
        logger.error("Failed to open alerts panel: %s", short_exc(exc))
        return False


def _log_tab_selectors() -> list[str]:
    selectors = [config.SELECTOR_LOG_TAB, '[data-name="log"]', "button#log"]
    seen: set[str] = set()
    unique: list[str] = []
    for selector in selectors:
        if selector and selector not in seen:
            seen.add(selector)
            unique.append(selector)
    return unique


def _click_log_tab(driver: webdriver.Chrome) -> None:
    for selector in _log_tab_selectors():
        try:
            log_tab = driver.find_element(By.CSS_SELECTOR, selector)
            if log_tab.get_attribute("tabindex") != "0":
                log_tab.click()
            return
        except (NoSuchElementException, StaleElementReferenceException):
            continue


def normalize_alert_delimiters(text: str) -> str:
    return _DELIM_RE.sub("||", text)


def extract_webhook_payload(text: str) -> Optional[tuple[str, str]]:
    """Parse SECRET||##...##@wallet||{{timenow}}, including `| |` from the TV widget."""
    if not text:
        return None
    normalized = normalize_alert_delimiters(text)
    for line in normalized.split("\n"):
        line = line.strip()
        if not line:
            continue
        parsed = _payload_from_line(line)
        if parsed:
            return parsed
    compact = " ".join(normalized.split())
    match = _TRADE_PAYLOAD_RE.search(compact) or _TREND_PAYLOAD_RE.search(compact)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


def _payload_from_line(line: str) -> Optional[tuple[str, str]]:
    if "##" in line and "||" in line:
        parts = [part.strip() for part in line.split("||")]
        if len(parts) >= 3:
            return "||".join(parts[:-1]), parts[-1]
    if "**trend/" in line and "||" in line:
        parts = [part.strip() for part in line.split("||")]
        if len(parts) >= 2:
            return "||".join(parts[:-1]), parts[-1]
    return None


def _ancestor_text_with_payload(elem) -> str:
    node = elem
    last_text = ""
    for _ in range(8):
        try:
            text = (node.text or "").strip()
        except Exception:
            break
        last_text = text or last_text
        if extract_webhook_payload(text):
            return text
        try:
            node = node.find_element(By.XPATH, "..")
        except Exception:
            break
    return last_text


def get_active_alerts(driver: webdriver.Chrome, alert_name_filter: list[str]) -> list[dict[str, Any]]:
    """Scrape the alert log. Requires {{timenow}} in the TradingView message."""
    if alert_name_filter:
        conditions = " or ".join([f'contains(text(), "{name}")' for name in alert_name_filter])
        xpath = f"//*[{conditions}]"
    else:
        xpath = '//*[contains(@class, "alerts")]'

    alerts: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        elements = driver.find_elements(By.XPATH, xpath)
    except Exception as exc:
        logger.error("Failed to query alerts: %s", short_exc(exc))
        return []

    for elem in elements:
        try:
            alert_name = elem.text.strip()
            if not alert_name:
                continue
            if alert_name_filter and not any(name in alert_name for name in alert_name_filter):
                continue
            parsed = extract_webhook_payload(_ancestor_text_with_payload(elem))
            if not parsed:
                if alert_name not in _skipped_names:
                    _skipped_names.add(alert_name)
                    logger.warning(
                        "Alert %r visible but payload SECRET||##...##||timestamp not found",
                        alert_name.split("\n", 1)[0][:80],
                    )
                continue
            webhook_message, timestamp_text = parsed
            if webhook_message in seen:
                continue
            seen.add(webhook_message)
            alerts.append(
                {
                    "name": alert_name.split("\n", 1)[0].strip(),
                    "message": webhook_message,
                    "timestamp": timestamp_text,
                }
            )
        except Exception:
            continue
    return alerts


def backup_chrome_profile() -> bool:
    try:
        profile_dir = config.CHROME_PROFILE_DIR
        cookies = profile_dir / "Default" / "Cookies"
        if not cookies.exists():
            return False
        backup_base = config.CHROME_BACKUP_DIR
        backup_base.mkdir(parents=True, exist_ok=True)
        backups = sorted(backup_base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[2:]:
            shutil.rmtree(old, ignore_errors=True)
        dest = backup_base / datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copytree(profile_dir, dest, ignore=shutil.ignore_patterns("Singleton*"))
        logger.info("Chrome profile backed up to %s", dest.name)
        return True
    except Exception as exc:
        logger.warning("Profile backup failed: %s", exc)
        return False
