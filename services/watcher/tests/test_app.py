from aiohttp.test_utils import AioHTTPTestCase

from src.app import create_app


class FakeOrderTracker:
    def recent(self, n):
        return []

    def get_stats(self):
        return {"total": 0}


class FakeWatcher:
    def __init__(self):
        self.order_tracker = FakeOrderTracker()

    def snapshot(self):
        return {
            "is_running": True,
            "logged_in": True,
            "health": {"is_healthy": True, "issues": []},
            "orders": {"total": 0},
        }

    def restart_browser(self):
        return None


class UnhealthyWatcher(FakeWatcher):
    def snapshot(self):
        return {
            "is_running": True,
            "logged_in": True,
            "health": {
                "is_healthy": False,
                "issues": ["Alerts panel not opened on watcher tab"],
                "scrape_ok": False,
            },
            "orders": {"total": 0},
        }


class WatcherApiTests(AioHTTPTestCase):
    async def get_application(self):
        return create_app(FakeWatcher())

    async def test_logs_json_includes_success(self):
        from src.logutil import memory_handler

        memory_handler.buffer.clear()
        memory_handler.buffer.append("2026-09-08 10:00:00 [INFO] forwarded")
        resp = await self.client.get("/logs")
        assert resp.status == 200
        body = await resp.json()
        assert body["success"] is True
        assert body["lines"][-1].endswith("forwarded")

    async def test_console_html(self):
        from src.logutil import memory_handler

        memory_handler.buffer.clear()
        memory_handler.buffer.append("2026-09-08 10:00:00 [WARNING] session")
        resp = await self.client.get("/console?autoscroll=true")
        assert resp.status == 200
        assert resp.content_type == "text/html"
        text = await resp.text()
        assert "Watcher Console" in text
        assert "#f9c513" in text
        assert 'id="autoscroll-btn" class="active"' in text


class WatcherHealthDegradedTests(AioHTTPTestCase):
    async def get_application(self):
        return create_app(UnhealthyWatcher())

    async def test_health_reports_degraded_when_scrape_fails(self):
        resp = await self.client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "degraded"
        assert body["health"]["is_healthy"] is False
        assert "Alerts panel not opened on watcher tab" in body["health"]["issues"]

    async def test_status_exposes_health_issues(self):
        resp = await self.client.get("/status")
        body = await resp.json()
        assert body["health"]["is_healthy"] is False
        assert body["health"]["scrape_ok"] is False
