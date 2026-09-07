"""Lightweight TradingView session / DOM health checks."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, NoSuchWindowException
from selenium.webdriver.common.by import By

from . import config

logger = logging.getLogger("watcher")


class HealthStatus:
    def __init__(self):
        self.is_healthy = True
        self.last_check_time: Optional[datetime] = None
        self.issues: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_healthy": self.is_healthy,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "issues": self.issues,
        }


class HealthMonitor:
    def __init__(self, driver: webdriver.Chrome, tradingview_url: str):
        self.driver = driver
        self.tradingview_url = tradingview_url
        self.main_window_handle: Optional[str] = None
        self.health_window_handle: Optional[str] = None
        self.is_running = False
        self.status = HealthStatus()

    def start(self) -> bool:
        try:
            self.main_window_handle = self.driver.current_window_handle
            self.driver.execute_script(f"window.open('{self.tradingview_url}', '_blank');")
            time.sleep(2)
            for handle in self.driver.window_handles:
                if handle != self.main_window_handle:
                    self.health_window_handle = handle
                    break
            if not self.health_window_handle:
                return False
            self.driver.switch_to.window(self.health_window_handle)
            self.driver.switch_to.window(self.main_window_handle)
            self.is_running = True
            return True
        except Exception as exc:
            logger.error("Health monitor start failed: %s", exc)
            return False

    def check_elements(self) -> HealthStatus:
        self.status = HealthStatus()
        self.status.last_check_time = datetime.now()
        if not self.health_window_handle:
            self.status.is_healthy = False
            self.status.issues.append("Health tab not initialized")
            return self.status
        try:
            handles = self.driver.window_handles
            if self.health_window_handle not in handles or self.main_window_handle not in handles:
                self.status.is_healthy = False
                self.status.issues.append("Browser tab missing")
                return self.status
            self.driver.switch_to.window(self.health_window_handle)
            url = self.driver.current_url
            if "/accounts/signin" in url or "/accounts/login" in url:
                self.status.is_healthy = False
                self.status.issues.append("Redirected to login page - session expired")
            disconnected = self.driver.find_elements(
                By.XPATH,
                "//*[contains(text(), 'session disconnected') or contains(text(), 'Session disconnected')]",
            )
            if disconnected:
                self.status.is_healthy = False
                self.status.issues.append("Session disconnected")
            started = self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Get started')]")
            if started:
                self.status.is_healthy = False
                self.status.issues.append("Not logged in - Get started visible")
            try:
                self.driver.find_element(By.CSS_SELECTOR, config.SELECTOR_ALERTS_BUTTON)
            except NoSuchElementException:
                self.status.is_healthy = False
                self.status.issues.append("Alerts button not found")
        except NoSuchWindowException:
            self.status.is_healthy = False
            self.status.issues.append("Health tab no longer exists")
        except Exception as exc:
            self.status.is_healthy = False
            self.status.issues.append(str(exc))
        finally:
            try:
                if self.main_window_handle:
                    self.driver.switch_to.window(self.main_window_handle)
            except Exception:
                pass
        return self.status

    def monitor_loop(self, check_interval: int = 30) -> None:
        while self.is_running:
            try:
                status = self.check_elements()
                if not status.is_healthy:
                    logger.error("HEALTH ALERT: %s", ", ".join(status.issues))
                time.sleep(check_interval)
            except Exception as exc:
                logger.error("Health loop error: %s", exc)
                time.sleep(check_interval)

    def stop(self) -> None:
        self.is_running = False
        if self.driver and self.health_window_handle:
            try:
                self.driver.switch_to.window(self.health_window_handle)
                self.driver.close()
                if self.main_window_handle:
                    self.driver.switch_to.window(self.main_window_handle)
            except Exception:
                pass
        self.health_window_handle = None
