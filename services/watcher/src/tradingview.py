"""Selenium helpers for TradingView alert log scraping."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import config

logger = logging.getLogger("watcher")


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
                try:
                    log_tab = driver.find_element(By.CSS_SELECTOR, config.SELECTOR_LOG_TAB)
                    if log_tab.get_attribute("tabindex") != "0":
                        log_tab.click()
                except NoSuchElementException:
                    pass
                return True
        except NoSuchElementException:
            pass

        wait = WebDriverWait(driver, 3)
        button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, config.SELECTOR_ALERTS_BUTTON)))
        button.click()
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, config.SELECTOR_ALERTS_CONTAINER))
        )
        try:
            log_tab = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, config.SELECTOR_LOG_TAB))
            )
            if log_tab.get_attribute("tabindex") != "0":
                log_tab.click()
        except TimeoutException:
            logger.debug("Log tab not present yet (no alerts)")
        return True
    except Exception as exc:
        logger.error("Failed to open alerts panel: %s", exc)
        return False


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
        logger.error("Failed to query alerts: %s", exc)
        return []

    for elem in elements:
        try:
            alert_name = elem.text.strip()
            if not alert_name:
                continue
            if alert_name_filter and not any(name in alert_name for name in alert_name_filter):
                continue
            parent = elem.find_element(By.XPATH, "..")
            webhook_message = None
            for line in parent.text.strip().split("\n"):
                line = line.strip()
                if "##" in line and "||" in line:
                    webhook_message = line
                    break
                if "**trend/" in line:
                    webhook_message = line
                    break
            if not webhook_message:
                continue
            parts = webhook_message.split("||")
            if len(parts) < 3 and not (webhook_message.startswith("**trend/") and len(parts) >= 2):
                logger.debug("Skipping alert without {{timenow}} timestamp")
                continue
            timestamp_text = parts[-1].strip()
            webhook_message = "||".join(parts[:-1])
            if webhook_message in seen:
                continue
            seen.add(webhook_message)
            alerts.append(
                {
                    "name": alert_name,
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
