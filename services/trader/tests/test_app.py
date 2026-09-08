from unittest.mock import MagicMock

from aiohttp.test_utils import AioHTTPTestCase

from src.app import create_app
from src.direction import DirectionState
from src.tickers import TickerRegistry


class FakeWallet:
    ready = True
    address = "0xabc"


class FakeBroker:
    def __init__(self):
        self.wallets = {"main": FakeWallet()}
        self.queued = []
        self.queue = MagicMock()
        self.queue.qsize.return_value = 0

    def get_wallet(self, wallet_id):
        return self.wallets[wallet_id]

    async def enqueue(self, trade):
        self.queued.append(trade)

    async def close_all_positions(self, wallet_id=None):
        self.closed_all_for = wallet_id
        return {"closed": [{"wallet_id": "main", "coin": "ETH", "side": "long"}], "errors": []}


def _tickers(tmp_path):
    path = tmp_path / "tickers.yaml"
    path.write_text(
        """
tickers:
  - alert: SOLUSDT.P
    coin: SOL
"""
    )
    registry = TickerRegistry(path)
    registry.activate_from_universe([{"name": "SOL", "szDecimals": 2}])
    return registry


class TradeApiTests(AioHTTPTestCase):
    async def get_application(self):
        import tempfile
        from pathlib import Path

        self.tmp = Path(tempfile.mkdtemp())
        self.broker = FakeBroker()
        self.direction = DirectionState(self.tmp / "dir.json", initial="short")
        self.registry = _tickers(self.tmp)
        return create_app(self.broker, self.direction, self.registry)

    async def test_short_blocks_buy(self):
        import src.app as appmod

        appmod.WEBHOOK_SECRET = "secret"
        resp = await self.client.post("/do_trade", data="secret||##buy/SOLUSDT.P##")
        assert resp.status == 400
        body = await resp.json()
        assert "blocked" in body["error"]
        assert self.broker.queued == []

    async def test_short_allows_sell(self):
        import src.app as appmod

        appmod.WEBHOOK_SECRET = "secret"
        resp = await self.client.post("/do_trade", data="secret||##sell/SOLUSDT.P/0/0##")
        assert resp.status == 200
        body = await resp.json()
        assert body["success"] is True
        assert self.broker.queued[0]["action"] == "sell"
        assert self.broker.queued[0]["stop_loss_price"] is None

    async def test_close_always_allowed(self):
        import src.app as appmod

        appmod.WEBHOOK_SECRET = "secret"
        resp = await self.client.post("/do_trade", data="secret||##close/SOLUSDT.P##")
        assert resp.status == 200
        assert self.broker.queued[0]["action"] == "close"

    async def test_close_all_runs_immediately(self):
        import src.app as appmod

        appmod.WEBHOOK_SECRET = "secret"
        resp = await self.client.post("/do_trade", data="secret||##close/all##")
        assert resp.status == 200
        body = await resp.json()
        assert body["success"] is True
        assert body["ticker"] == "all"
        assert body["wallet_id"] == "all"
        assert self.broker.closed_all_for is None
        assert self.broker.queued == []

    async def test_trend_to_all(self):
        import src.app as appmod

        appmod.WEBHOOK_SECRET = "secret"
        appmod.TRADE_DIRECTION_WEBHOOK_ENABLED = True
        appmod.CLOSE_ON_TREND_CHANGE = False
        resp = await self.client.post("/trend", data="secret||**trend/all**")
        assert resp.status == 200
        body = await resp.json()
        assert body["new_direction"] == "all"

    async def test_logs_json_includes_success(self):
        from src.logutil import memory_handler

        memory_handler.buffer.clear()
        memory_handler.buffer.append("2026-09-08 10:00:00 [INFO] queued")
        resp = await self.client.get("/logs")
        assert resp.status == 200
        body = await resp.json()
        assert body["success"] is True
        assert body["lines"][-1].endswith("queued")

    async def test_console_html(self):
        from src.logutil import memory_handler

        memory_handler.buffer.clear()
        memory_handler.buffer.append("2026-09-08 10:00:00 [ERROR] boom")
        memory_handler.buffer.append("<script>alert(1)</script>")
        resp = await self.client.get("/console?lines=50&refresh=5")
        assert resp.status == 200
        assert resp.content_type == "text/html"
        text = await resp.text()
        assert "Trader Console" in text
        assert "#f85149" in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
        assert "<script>alert(1)</script>" not in text
