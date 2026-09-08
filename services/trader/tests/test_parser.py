from src.parser import parse_secret_payload, parse_trade_command, parse_trend_command


ALLOWED = {"SOLUSDT.P", "TSLAUSDT.P", "BTCUSDT.P"}


def test_basic_buy():
    parsed = parse_trade_command("##buy/SOLUSDT.P##", ALLOWED)
    assert parsed["action"] == "buy"
    assert parsed["ticker"] == "SOLUSDT.P"
    assert parsed["stop_loss_price"] is None
    assert parsed["take_profit_price"] is None
    assert parsed["wallet_id"] == "main"


def test_sl_tp_both_zero_disables():
    parsed = parse_trade_command("##buy/SOLUSDT.P/0/0##", ALLOWED)
    assert parsed["stop_loss_price"] is None
    assert parsed["take_profit_price"] is None


def test_sl_only():
    parsed = parse_trade_command("##buy/SOLUSDT.P/183/0##", ALLOWED)
    assert parsed["stop_loss_price"] == 183.0
    assert parsed["take_profit_price"] is None


def test_tp_only():
    parsed = parse_trade_command("##sell/SOLUSDT.P/0/197##", ALLOWED)
    assert parsed["stop_loss_price"] is None
    assert parsed["take_profit_price"] == 197.0


def test_sl_tp_qty_wallet():
    parsed = parse_trade_command("##buy/TSLAUSDT.P/180/200/15/OID##@bot2", ALLOWED)
    assert parsed["ticker"] == "TSLAUSDT.P"
    assert parsed["stop_loss_price"] == 180.0
    assert parsed["take_profit_price"] == 200.0
    assert parsed["qty_percentage"] == 15.0
    assert parsed["wallet_id"] == "bot2"


def test_unknown_ticker():
    try:
        parse_trade_command("##buy/DOGEUSDT.P##", ALLOWED)
        assert False, "should have raised"
    except ValueError as exc:
        assert "Unsupported ticker" in str(exc)


def test_close_all_bypasses_allowlist():
    parsed = parse_trade_command("##close/all##", ALLOWED)
    assert parsed["action"] == "close"
    assert parsed["ticker"] == "all"
    assert parsed["wallet_id"] == "main"


def test_secret_split():
    secret, command = parse_secret_payload("abc||##buy/SOLUSDT.P##")
    assert secret == "abc"
    assert command == "##buy/SOLUSDT.P##"


def test_trend_all():
    assert parse_trend_command("**trend/all**") == "all"


def test_trend_rejects_range():
    try:
        parse_trend_command("**trend/range**")
        assert False
    except ValueError:
        pass
