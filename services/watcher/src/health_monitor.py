"""TradingView session + .env selector checks on a dedicated tab."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional

from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException
from selenium.webdriver.common.by import By

from . import config
from .logutil import short_exc

logger = logging.getLogger("watcher")


def env_selectors() -> list[tuple[str, str, bool]]:
    """All TradingView controller addresses from .env. (name, css, required)."""
    return [
        ("SELECTOR_USER_MENU", config.SELECTOR_USER_MENU, True),
        ("SELECTOR_ALERTS_BUTTON", config.SELECTOR_ALERTS_BUTTON, True),
        ("SELECTOR_ALERTS_CONTAINER", config.SELECTOR_ALERTS_CONTAINER, True),
        ("SELECTOR_LOG_TAB", config.SELECTOR_LOG_TAB, False),
    ]


class HealthStatus:
    def __init__(self):
        self.is_healthy = False
        self.last_check_time: Optional[datetime] = None
        self.issues: list[str] = ["Health check not run yet"]
        self.checks_passed = 0
        self.checks_failed = 0
        self.controllers: dict[str, dict[str, Any]] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_healthy": self.is_healthy,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "issues": list(self.issues),
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "controllers": dict(self.controllers),
        }


def merge_health(dom: dict[str, Any], scrape_issues: list[str]) -> dict[str, Any]:
    """Combine selector checks with watcher-tab scrape errors."""
    health = dict(dom)
    issues = list(health.get("issues") or [])
    for issue in scrape_issues:
        if issue not in issues:
            issues.append(issue)
    health["issues"] = issues
    health["scrape_ok"] = not scrape_issues
    if scrape_issues:
        health["is_healthy"] = False
    return health


class HealthMonitor:
    def __init__(
        self,
        driver: webdriver.Chrome,
        tradingview_url: str,
        driver_lock: threading.RLock,
        probe_timeout: float = 2.0,
    ):
        self.driver = driver
        self.tradingview_url = tradingview_url
        self.driver_lock = driver_lock
        self.probe_timeout = probe_timeout
        self.main_window_handle: Optional[str] = None
        self.health_window_handle: Optional[str] = None
        self.is_running = False
        self.status = HealthStatus()

    def start(self) -> bool:
        if not self.driver_lock.acquire(timeout=10):
            self.status.issues = ["Health tab failed to open: driver busy"]
            logger.error("Health monitor start failed: driver busy")
            return False
        try:
            self.main_window_handle = self.driver.current_window_handle
            existing = set(self.driver.window_handles)
            self.driver.execute_script(f"window.open('{self.tradingview_url}', '_blank');")
            deadline = time.time() + 5
            new_handles: list[str] = []
            while time.time() < deadline:
                new_handles = [h for h in self.driver.window_handles if h not in existing]
                if new_handles:
                    break
                time.sleep(0.1)
            if not new_handles:
                self.status.issues = ["Health tab failed to open"]
                logger.error("Health monitor start failed: no new tab")
                return False
            self.health_window_handle = new_handles[0]
            self.driver.switch_to.window(self.health_window_handle)
            time.sleep(min(1.0, max(self.probe_timeout, 0.0)))
            try:
                self.driver.execute_script("document.title = '[HEALTH] ' + document.title;")
            except Exception:
                pass
            self.driver.switch_to.window(self.main_window_handle)
            self.is_running = True
            logger.info("Health monitor tab opened (validates .env selectors)")
            return True
        except Exception as exc:
            self.status.issues = [f"Health tab failed to open: {short_exc(exc)}"]
            logger.error("Health monitor start failed: %s", short_exc(exc))
            return False
        finally:
            self.driver_lock.release()

    def check_elements(self) -> HealthStatus:
        if not self.driver_lock.acquire(timeout=10):
            logger.warning("Health check skipped: driver busy")
            return self.status
        try:
            return self._check_elements_locked()
        finally:
            try:
                if self.main_window_handle:
                    self.driver.switch_to.window(self.main_window_handle)
            except Exception:
                pass
            self.driver_lock.release()

    def _check_elements_locked(self) -> HealthStatus:
        status = HealthStatus()
        status.last_check_time = datetime.now()
        status.issues = []
        status.is_healthy = True
        self.status = status

        if not self.health_window_handle or not self.main_window_handle:
            return self._fail("Health tab not initialized")

        try:
            handles = self.driver.window_handles
        except Exception as exc:
            return self._fail(f"Cannot access browser windows: {short_exc(exc)}")

        if self.health_window_handle not in handles:
            return self._fail("Health tab missing")
        if self.main_window_handle not in handles:
            return self._fail("Watcher tab missing")

        try:
            self.driver.switch_to.window(self.health_window_handle)
        except NoSuchWindowException:
            return self._fail("Health tab no longer exists")

        if self._session_broken(status):
            return status

        self._ensure_alerts_panel_open()
        for env_name, selector, required in env_selectors():
            found = self._selector_present(selector)
            status.controllers[env_name] = {
                "selector": selector,
                "ok": found,
                "required": required,
            }
            if found:
                status.checks_passed += 1
                continue
            if required:
                status.checks_failed += 1
                status.is_healthy = False
                status.issues.append(f"{env_name} not found ({selector})")
            else:
                status.checks_passed += 1
                status.controllers[env_name]["optional"] = True

        if status.is_healthy:
            logger.debug("Health check passed (%s selectors)", status.checks_passed)
        else:
            logger.error("HEALTH ALERT: %s", ", ".join(status.issues))
        return status

    def _session_broken(self, status: HealthStatus) -> bool:
        try:
            url = self.driver.current_url or ""
        except Exception as exc:
            self._fail(f"Cannot read page URL: {short_exc(exc)}", status)
            return True
        if "/accounts/signin" in url or "/accounts/login" in url:
            self._fail("Redirected to login page - session expired", status)
            return True
        if self._text_present("session disconnected") or self._text_present("Session disconnected"):
            self._fail("Session disconnected", status)
            return True
        if self._text_present("Get started"):
            self._fail("Not logged in - Get started visible", status)
            return True
        return False

    def _ensure_alerts_panel_open(self) -> None:
        if self._selector_present(config.SELECTOR_ALERTS_CONTAINER):
            return
        buttons = self.driver.find_elements(By.CSS_SELECTOR, config.SELECTOR_ALERTS_BUTTON)
        if not buttons:
            return
        try:
            buttons[0].click()
        except Exception as exc:
            logger.debug("Could not click %s: %s", "SELECTOR_ALERTS_BUTTON", short_exc(exc))
            return
        self._wait_selector(config.SELECTOR_ALERTS_CONTAINER)

    def _selector_present(self, selector: str) -> bool:
        if not selector:
            return False
        try:
            return bool(self.driver.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            return False

    def _wait_selector(self, selector: str) -> bool:
        deadline = time.time() + max(self.probe_timeout, 0.0)
        while True:
            if self._selector_present(selector):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.1)

    def _text_present(self, text: str) -> bool:
        try:
            return bool(self.driver.find_elements(By.XPATH, f"//*[contains(text(), '{text}')]"))
        except Exception:
            return False

    def _fail(self, issue: str, status: Optional[HealthStatus] = None) -> HealthStatus:
        target = status or self.status
        target.is_healthy = False
        target.checks_failed += 1
        if issue not in target.issues:
            target.issues.append(issue)
        self.status = target
        return target

    def monitor_loop(self, check_interval: int = 30) -> None:
        while self.is_running:
            try:
                self.check_elements()
            except Exception as exc:
                logger.error("Health loop error: %s", short_exc(exc))
            time.sleep(check_interval)

    def stop(self) -> None:
        self.is_running = False
        if not self.driver or not self.health_window_handle:
            self.health_window_handle = None
            return
        acquired = self.driver_lock.acquire(timeout=10)
        try:
            try:
                if self.health_window_handle in self.driver.window_handles:
                    self.driver.switch_to.window(self.health_window_handle)
                    self.driver.close()
                if self.main_window_handle and self.main_window_handle in self.driver.window_handles:
                    self.driver.switch_to.window(self.main_window_handle)
            except Exception:
                pass
        finally:
            if acquired:
                self.driver_lock.release()
        self.health_window_handle = None
