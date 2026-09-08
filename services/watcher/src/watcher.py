"""AlertWatcher: poll TradingView and forward alerts to the trader."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import config
from .health_monitor import TAB_WATCHER, HealthMonitor, HealthStatus, merge_health
from .logutil import short_exc
from .order_tracker import OrderTracker
from .tradingview import backup_chrome_profile, create_chrome_driver, get_active_alerts, is_logged_in, open_alerts_panel
from .webhook import send_webhook

logger = logging.getLogger("watcher")


def parse_alert_age_minutes(timestamp_text: str) -> Optional[int]:
    if not timestamp_text:
        return None
    text = timestamp_text.strip()
    try:
        stamp = text.rstrip("Z")
        if "T" in stamp:
            parsed = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - parsed).total_seconds() // 60)
    except Exception:
        pass
    match = re.match(r"(\d+)\s*m", text)
    if match:
        return int(match.group(1))
    match = re.match(r"(\d+)\s*h", text)
    if match:
        return int(match.group(1)) * 60
    return None


def extract_wallet_id(message: str) -> Optional[str]:
    if "##@" not in message:
        return None
    suffix = message.rsplit("@", 1)[-1].strip()
    return suffix or None


def parse_trade_fields(message: str) -> dict[str, Any]:
    fields = {
        "action": None,
        "ticker": None,
        "sl_price": None,
        "tp_price": None,
        "qty_percentage": None,
        "wallet_id": extract_wallet_id(message),
    }
    if "##" not in message:
        return fields
    start = message.find("##")
    end = message.find("##", start + 2)
    if end <= start:
        return fields
    parts = message[start + 2 : end].split("/")
    if len(parts) >= 2:
        fields["action"] = parts[0]
        fields["ticker"] = parts[1]
    if len(parts) >= 4:
        try:
            sl = float(parts[2])
            tp = float(parts[3])
            fields["sl_price"] = sl if sl > 0 else None
            fields["tp_price"] = tp if tp > 0 else None
        except ValueError:
            pass
    if len(parts) >= 5:
        try:
            qty = float(parts[4])
            if qty > 0:
                fields["qty_percentage"] = qty
        except ValueError:
            pass
    return fields


class AlertWatcher:
    def __init__(self):
        self.driver = None
        self.order_tracker = OrderTracker(config.ORDERS_FILE)
        self.health_monitor: Optional[HealthMonitor] = None
        self.health_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.logged_in = False
        self.processed_alerts: set[str] = set()
        self.driver_lock = threading.RLock()
        self._generation = 0
        self.scrape_issues: list[str] = []

    def start(self) -> bool:
        generation = self._generation
        try:
            self.driver = create_chrome_driver()
            self.driver.get(config.TRADINGVIEW_URL)
            time.sleep(5)
            self.driver.execute_script("window.name = arguments[0];", TAB_WATCHER)
            self.logged_in = is_logged_in(self.driver)
            if not self.logged_in:
                logger.error("TradingView login required via VNC (port 5900). reCAPTCHA blocks automated login.")
                logger.error("Account hint: %s", config.TV_USERNAME)
            else:
                logger.info("TradingView session is logged in")
            if config.HEALTH_CHECK_ENABLED and self.logged_in:
                self._start_health_monitor()
            self.is_running = True
            self.monitor_alerts(generation)
            return True
        except Exception as exc:
            logger.error("Watcher startup failed: %s", short_exc(exc))
            return False

    def monitor_alerts(self, generation: Optional[int] = None) -> None:
        if generation is None:
            generation = self._generation
        logger.info("Monitoring alerts (trade -> /do_trade, trend -> /trend)")
        loop_count = 0
        last_backup = time.time()
        filters = config.TRADE_ALERT_NAMES + config.TREND_ALERT_NAMES
        while self.is_running and self._generation == generation:
            try:
                if loop_count % 10 == 0 and self.health_monitor:
                    status = self.health_monitor.status
                    if not status.is_healthy:
                        login_issues = [
                            issue
                            for issue in status.issues
                            if "login" in issue.lower() or "session" in issue.lower()
                        ]
                        if login_issues:
                            logger.error("Session expired: %s", login_issues)
                            self.logged_in = False
                            self.cleanup()
                            break
                if not self.logged_in:
                    time.sleep(config.ALERT_CHECK_INTERVAL)
                    continue
                alerts = self._scan_alerts(filters)
                for alert in alerts:
                    alert_id = f"{alert.get('name')}_{alert.get('timestamp')}_{hash(alert['message'])}"
                    if alert_id in self.processed_alerts:
                        continue
                    self.process_alert(alert)
                    self.processed_alerts.add(alert_id)
                    if len(self.processed_alerts) > 1000:
                        self.processed_alerts = set(list(self.processed_alerts)[-500:])
                loop_count += 1
                if loop_count % 300 == 0:
                    logger.info("Heartbeat: %s loops", loop_count)
                if loop_count % 3600 == 0:
                    self.order_tracker.cleanup_old_orders(days=7)
                if time.time() - last_backup >= 2 * 3600:
                    if backup_chrome_profile():
                        last_backup = time.time()
                time.sleep(config.ALERT_CHECK_INTERVAL)
            except Exception as exc:
                logger.error("Monitor loop error: %s", short_exc(exc))
                self._set_scrape_issue(f"Monitor loop error: {short_exc(exc)}")
                time.sleep(config.ALERT_CHECK_INTERVAL * 2)

    def process_alert(self, alert: dict[str, Any]) -> str:
        name = alert.get("name", "")
        is_trend = any(token in name for token in config.TREND_ALERT_NAMES)
        is_trade = any(token in name for token in config.TRADE_ALERT_NAMES)
        if is_trend:
            return self._process_trend(alert)
        if is_trade:
            return self._process_trade(alert)
        return "skipped"

    def _too_old(self, timestamp: str, name: str, action: str) -> bool:
        age = parse_alert_age_minutes(timestamp)
        if age is None:
            return False
        if age <= config.ALERT_MAX_AGE_MINUTES:
            return False
        if not self.order_tracker.is_processed(timestamp):
            self.order_tracker.add_order(timestamp, name, action, "unknown", status="discarded_old")
        return True

    def _process_trade(self, alert: dict[str, Any]) -> str:
        timestamp = alert.get("timestamp") or ""
        name = alert.get("name", "")
        message = alert["message"]
        if not timestamp:
            logger.warning("Trade alert discarded: missing timestamp")
            return "discarded"
        if self._too_old(timestamp, name, "unknown"):
            return "old"
        if self.order_tracker.is_processed(timestamp):
            return "already_processed"
        fields = parse_trade_fields(message)
        self.order_tracker.add_order(
            timestamp,
            name,
            fields["action"] or "unknown",
            fields["ticker"] or "unknown",
            alert_type="trade",
            sl_price=fields["sl_price"],
            tp_price=fields["tp_price"],
            qty_percentage=fields["qty_percentage"],
            wallet_id=fields["wallet_id"],
            status="pending",
        )
        success = False
        last_error = None
        for attempt in range(1, config.WEBHOOK_RETRIES + 1):
            result = send_webhook(config.TRADER_DO_TRADE_URL, message)
            if result["success"]:
                success = True
                break
            last_error = result.get("error")
            time.sleep(1)
        self.order_tracker.update_order_status(timestamp, "sent" if success else "failed")
        if not success:
            logger.error("Trade webhook failed after retries: %s", last_error)
            return "failed"
        logger.info("Forwarded trade alert %s", timestamp)
        return "sent"

    def _process_trend(self, alert: dict[str, Any]) -> str:
        timestamp = alert.get("timestamp") or ""
        name = alert.get("name", "")
        message = alert["message"]
        if not timestamp:
            return "discarded"
        if self._too_old(timestamp, name, "trend"):
            return "old"
        if self.order_tracker.is_processed(timestamp):
            return "already_processed"
        direction = "unknown"
        start = message.find("**trend/")
        end = message.find("**", start + 8) if start != -1 else -1
        if start != -1 and end != -1:
            direction = message[start + 8 : end].lower()
        if direction not in ("short", "long", "all"):
            self.order_tracker.add_order(timestamp, name, "trend", direction, status="failed")
            return "failed"
        self.order_tracker.add_order(timestamp, name, "trend", direction, status="pending")
        result = send_webhook(config.TRADER_TREND_URL, message)
        self.order_tracker.update_order_status(timestamp, "sent" if result["success"] else "failed")
        return "sent" if result["success"] else "failed"

    def _scan_alerts(self, filters: list[str]) -> list[dict[str, Any]]:
        if not self.driver_lock.acquire(timeout=5):
            logger.warning("Alert scan skipped: driver busy")
            return []
        try:
            self._pin_watcher_tab()
            if not open_alerts_panel(self.driver):
                self._set_scrape_issue("Alerts panel not opened on watcher tab")
                return []
            alerts = get_active_alerts(self.driver, filters)
            self._set_scrape_issue(None)
            return alerts
        except Exception as exc:
            self._set_scrape_issue(f"Alert scan failed: {short_exc(exc)}")
            logger.error("Alert scan failed: %s", short_exc(exc))
            return []
        finally:
            self.driver_lock.release()

    def _set_scrape_issue(self, issue: Optional[str]) -> None:
        self.scrape_issues = [issue] if issue else []

    def _start_health_monitor(self) -> None:
        self.health_monitor = HealthMonitor(self.driver, config.TRADINGVIEW_URL, self.driver_lock)
        if not self.health_monitor.start():
            logger.error("Health monitor tab failed to start; /health will stay degraded")
            return
        self.health_monitor.check_elements()
        self.health_thread = threading.Thread(
            target=self.health_monitor.monitor_loop,
            args=(config.HEALTH_CHECK_INTERVAL,),
            daemon=True,
            name="health-monitor",
        )
        self.health_thread.start()

    def restart_browser(self) -> None:
        with self.driver_lock:
            self._generation += 1
            self.cleanup()
            self.processed_alerts.clear()
        threading.Thread(target=self.start, daemon=True, name="alert-watcher").start()

    def cleanup(self) -> None:
        self.is_running = False
        if self.health_monitor:
            self.health_monitor.is_running = False
        acquired = self.driver_lock.acquire(timeout=10)
        try:
            if self.health_monitor:
                self.health_monitor.stop()
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                self.driver = None
            self.logged_in = False
        finally:
            if acquired:
                self.driver_lock.release()
        logger.info("Watcher cleaned up")

    def snapshot(self) -> dict[str, Any]:
        if self.health_monitor:
            health = merge_health(self.health_monitor.status.to_dict(), self.scrape_issues)
        else:
            fallback = HealthStatus().to_dict()
            fallback["issues"] = ["Health monitor not running"]
            health = merge_health(fallback, self.scrape_issues)
        return {
            "is_running": self.is_running,
            "logged_in": self.logged_in,
            "health": health,
            "orders": self.order_tracker.get_stats(),
        }
