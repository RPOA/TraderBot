"""TradingView session + controller checks on a dedicated tab."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException
from selenium.webdriver.common.by import By

from . import config
from .logutil import short_exc

logger = logging.getLogger("watcher")

TAB_WATCHER = "traderbot-watcher"
TAB_HEALTH = "traderbot-health"


class HealthStatus:
    def __init__(self):
        self.is_healthy = False
        self.last_check_time: Optional[datetime] = None
        self.issues: list[str] = ["Health check not run yet"]
        self.checks_passed = 0
        self.checks_failed = 0
        self.controllers: dict[str, dict[str, Any]] = {}
        self.tabs: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_healthy": self.is_healthy,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "issues": list(self.issues),
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "controllers": dict(self.controllers),
            "tabs": dict(self.tabs),
        }


def merge_health(dom: dict[str, Any], scrape_issues: list[str]) -> dict[str, Any]:
    """Combine second-tab controller checks with watcher-tab scrape errors."""
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


def _controller_specs() -> list[tuple[str, str, bool]]:
    """Name, CSS selector, required. These are the TradingView UI controllers."""
    return [
        ("user_menu", config.SELECTOR_USER_MENU, True),
        ("alerts_button", config.SELECTOR_ALERTS_BUTTON, True),
        ("alerts_container", config.SELECTOR_ALERTS_CONTAINER, True),
        ("log_tab", config.SELECTOR_LOG_TAB, False),
    ]


def _is_tradingview(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return "tradingview.com" in host


def _same_tv_page(url: str, expected: str) -> bool:
    if not _is_tradingview(url):
        return False
    actual_path = urlparse(url).path.rstrip("/") or "/"
    expected_path = urlparse(expected).path.rstrip("/") or "/"
    return actual_path == expected_path or actual_path.startswith(expected_path + "/")


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
            self._set_tab_name(TAB_WATCHER)
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
            self._set_tab_name(TAB_HEALTH)
            try:
                self.driver.execute_script("document.title = '[HEALTH] ' + document.title;")
            except Exception:
                pass
            self.driver.switch_to.window(self.main_window_handle)
            self.is_running = True
            logger.info("Health monitor tab opened (validates page controllers)")
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
                if self.main_window_handle and self.main_window_handle in self.driver.window_handles:
                    if self.driver.current_window_handle != self.main_window_handle:
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

        self._inspect_watcher_tab(status)
        self._inspect_health_tab(status)

        if status.is_healthy:
            logger.debug("Health check passed (%s controllers)", status.checks_passed)
        else:
            logger.error("HEALTH ALERT: %s", ", ".join(status.issues))
        return status

    def _inspect_watcher_tab(self, status: HealthStatus) -> None:
        """Read the scrape tab without activating another Chrome tab first."""
        try:
            if self.driver.current_window_handle != self.main_window_handle:
                self.driver.switch_to.window(self.main_window_handle)
        except NoSuchWindowException:
            self._fail("Watcher tab no longer exists", status)
            return

        name = self._get_tab_name()
        url = self._current_url()
        visible = self._tab_visible()
        status.tabs["watcher"] = {"name": name, "url": url, "visible": visible}

        if name and name != TAB_WATCHER:
            self._fail(f"Watcher tab identity mismatch ({name})", status)
        if not visible:
            self._fail("Watcher tab is not selected", status)
        if not _is_tradingview(url) or "/accounts/" in url:
            self._fail(f"Watcher tab left TradingView ({url})", status)
            return
        if self._session_broken(status):
            return

        if not self._panel_visible():
            self._fail("Alerts panel not visible on watcher tab", status)
        log_tab = self._log_tab_state()
        status.controllers["watcher_log_tab"] = log_tab
        if log_tab["present"] and not log_tab["selected"]:
            self._fail("Log tab is not selected on watcher tab", status)

    def _inspect_health_tab(self, status: HealthStatus) -> None:
        try:
            self.driver.switch_to.window(self.health_window_handle)
        except NoSuchWindowException:
            self._fail("Health tab no longer exists", status)
            return

        name = self._get_tab_name()
        url = self._current_url()
        visible = self._tab_visible()
        status.tabs["health"] = {"name": name, "url": url, "visible": visible}

        if name != TAB_HEALTH:
            self._fail(f"Health check ran on the wrong tab ({name or 'unnamed'})", status)
        if not _same_tv_page(url, self.tradingview_url):
            self._fail(f"Health tab navigated away ({url})", status)
            return
        if self._session_broken(status):
            return

        self._ensure_alerts_panel_open()
        for spec_name, selector, required in _controller_specs():
            found = self._selector_present(selector)
            status.controllers[spec_name] = {"selector": selector, "ok": found, "required": required}
            if found:
                status.checks_passed += 1
                continue
            if required:
                status.checks_failed += 1
                status.is_healthy = False
                status.issues.append(f"Controller missing: {spec_name} ({selector})")
            else:
                status.checks_passed += 1
                status.controllers[spec_name]["optional"] = True

    def _session_broken(self, status: HealthStatus) -> bool:
        url = self._current_url()
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
        if self._panel_visible():
            return
        buttons = self.driver.find_elements(By.CSS_SELECTOR, config.SELECTOR_ALERTS_BUTTON)
        if not buttons:
            return
        try:
            buttons[0].click()
        except Exception as exc:
            logger.debug("Could not click alerts button on health tab: %s", short_exc(exc))
            return
        self._wait_selector(config.SELECTOR_ALERTS_CONTAINER)

    def _panel_visible(self) -> bool:
        try:
            elements = self.driver.find_elements(By.CSS_SELECTOR, config.SELECTOR_ALERTS_CONTAINER)
        except Exception:
            return False
        for el in elements:
            try:
                classes = el.get_attribute("class") or ""
                if "hidden" in classes:
                    continue
                if el.is_displayed():
                    return True
            except Exception:
                continue
        return False

    def _log_tab_state(self) -> dict[str, Any]:
        state = {"selector": config.SELECTOR_LOG_TAB, "present": False, "selected": False}
        try:
            tabs = self.driver.find_elements(By.CSS_SELECTOR, config.SELECTOR_LOG_TAB)
        except Exception:
            return state
        if not tabs:
            return state
        state["present"] = True
        try:
            tabindex = tabs[0].get_attribute("tabindex")
            selected = tabs[0].get_attribute("aria-selected")
            state["selected"] = tabindex == "0" or selected == "true"
        except Exception:
            state["selected"] = False
        return state

    def _selector_present(self, selector: str) -> bool:
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

    def _set_tab_name(self, name: str) -> None:
        self.driver.execute_script("window.name = arguments[0];", name)

    def _get_tab_name(self) -> str:
        try:
            return self.driver.execute_script("return window.name;") or ""
        except Exception:
            return ""

    def _tab_visible(self) -> bool:
        try:
            return self.driver.execute_script("return document.visibilityState;") == "visible"
        except Exception:
            return False

    def _current_url(self) -> str:
        try:
            return self.driver.current_url or ""
        except Exception:
            return ""

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
