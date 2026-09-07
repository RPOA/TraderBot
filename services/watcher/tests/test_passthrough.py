from src.watcher import extract_wallet_id, parse_trade_fields
from src.webhook import send_webhook


def test_parse_trade_fields_zero_sl_tp():
    fields = parse_trade_fields("SECRET||##buy/SOLUSDT.P/0/0##@bot2")
    assert fields["action"] == "buy"
    assert fields["ticker"] == "SOLUSDT.P"
    assert fields["sl_price"] is None
    assert fields["tp_price"] is None
    assert fields["wallet_id"] == "bot2"


def test_extract_wallet():
    assert extract_wallet_id("SECRET||##sell/TSLAUSDT.P##@main") == "main"
    assert extract_wallet_id("SECRET||##sell/TSLAUSDT.P##") is None


def test_send_webhook_does_not_rewrite_message(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    def fake_post(url, data, headers, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("src.webhook.requests.post", fake_post)
    raw = "SECRET||##buy/SOLUSDT.P/0/0##@main"
    result = send_webhook("http://trader:8080/do_trade", raw)
    assert result["success"] is True
    assert captured["data"] == raw
    assert captured["url"] == "http://trader:8080/do_trade"
