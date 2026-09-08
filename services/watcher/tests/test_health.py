import threading
from datetime import datetime

from selenium.webdriver.common.by import By

from src import config
from src.health_monitor import HealthMonitor, HealthStatus, merge_health
from src.logutil import short_exc
from src.watcher import AlertWatcher


class FakeElement:
    def __init__(self, classes=""):
        self.classes = classes

    def get_attribute(self, name):
        return self.classes if name == "class" else None

    def click(self):
        return None

    def is_displayed(self):
        return True


class FakeSwitchTo:
    def __init__(self, driver):
        self._driver = driver

    def window(self, handle):
        from selenium.common.exceptions import NoSuchWindowException

        if handle not in self._driver.window_handles:
            raise NoSuchWindowException()
        self._driver.current_window_handle = handle


class FakeDriver:
    def __init__(self):
        self.current_window_handle = "main"
        self.window_handles = ["main"]
        self.current_url = "https://www.tradingview.com/ideas/"
        self.selectors: dict[str, list[FakeElement]] = {}
        self.texts: list[str] = []
        self.switch_to = FakeSwitchTo(self)
        self.titles: list[str] = []

    def execute_script(self, script):
        if "window.open" in script:
            if "health" not in self.window_handles:
                self.window_handles.append("health")
        if "document.title" in script:
            self.titles.append(script)

    def find_elements(self, by, value):
        if by == By.XPATH:
            needle = value.split("contains(text(), '")[-1].removesuffix("')]")
            return [FakeElement()] if needle in self.texts else []
        return list(self.selectors.get(value, []))

    def find_element(self, by, value):
        from selenium.common.exceptions import NoSuchElementException

        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException()
        return found[0]


def _healthy_driver() -> FakeDriver:
    driver = FakeDriver()
    driver.selectors = {
        config.SELECTOR_USER_MENU: [FakeElement()],
        config.SELECTOR_ALERTS_BUTTON: [FakeElement()],
        config.SELECTOR_ALERTS_CONTAINER: [FakeElement()],
        config.SELECTOR_LOG_TAB: [FakeElement()],
    }
    return driver


def _monitor(driver: FakeDriver) -> HealthMonitor:
    monitor = HealthMonitor(driver, "https://www.tradingview.com/ideas/", threading.RLock(), probe_timeout=0)
    monitor.main_window_handle = "main"
    monitor.health_window_handle = "health"
    driver.window_handles = ["main", "health"]
    monitor.is_running = True
    return monitor


def test_short_exc_strips_selenium_stack():
    err = Exception("Message: no such element: element not found\n  (Session info: chrome=152)")
    assert short_exc(err) == "no such element: element not found"


def test_merge_health_keeps_tab_issues_and_adds_scrape():
    merged = merge_health(
        {"is_healthy": True, "issues": []},
        ["Alerts panel not opened on watcher tab"],
    )
    assert merged["is_healthy"] is False
    assert merged["scrape_ok"] is False
    assert merged["issues"] == ["Alerts panel not opened on watcher tab"]


def test_merge_health_healthy_when_both_ok():
    merged = merge_health({"is_healthy": True, "issues": []}, [])
    assert merged["is_healthy"] is True
    assert merged["scrape_ok"] is True
    assert merged["issues"] == []


def test_health_monitor_passes_when_controllers_exist():
    status = _monitor(_healthy_driver()).check_elements()
    assert status.is_healthy is True
    assert status.issues == []
    assert status.controllers["alerts_button"]["ok"] is True
    assert status.controllers["alerts_container"]["ok"] is True


def test_health_monitor_fails_when_alerts_button_missing():
    driver = _healthy_driver()
    driver.selectors.pop(config.SELECTOR_ALERTS_BUTTON)
    status = _monitor(driver).check_elements()
    assert status.is_healthy is False
    assert any("alerts_button" in issue for issue in status.issues)


def test_health_monitor_log_tab_optional():
    driver = _healthy_driver()
    driver.selectors.pop(config.SELECTOR_LOG_TAB)
    status = _monitor(driver).check_elements()
    assert status.is_healthy is True
    assert status.controllers["log_tab"]["ok"] is False


def test_health_monitor_detects_login_page():
    driver = _healthy_driver()
    driver.current_url = "https://www.tradingview.com/accounts/signin/"
    status = _monitor(driver).check_elements()
    assert status.is_healthy is False
    assert any("login" in issue.lower() or "session" in issue.lower() for issue in status.issues)


def test_health_monitor_start_opens_second_tab():
    driver = _healthy_driver()
    monitor = HealthMonitor(driver, "https://www.tradingview.com/ideas/", threading.RLock(), probe_timeout=0)
    assert monitor.start() is True
    assert monitor.health_window_handle == "health"
    assert driver.current_window_handle == "main"
    assert monitor.status.is_healthy is False


def test_snapshot_includes_scrape_failure():
    watcher = AlertWatcher()
    watcher.is_running = True
    watcher.logged_in = True
    watcher.scrape_issues = ["Alerts panel not opened on watcher tab"]
    status = HealthStatus()
    status.is_healthy = True
    status.issues = []
    status.last_check_time = datetime.now()
    watcher.health_monitor = type("HM", (), {"status": status})()
    health = watcher.snapshot()["health"]
    assert health["is_healthy"] is False
    assert "Alerts panel not opened on watcher tab" in health["issues"]
