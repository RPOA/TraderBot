from src.hyperliquid_client import (
    _round_px,
    fill_avg_px,
    prepare_triggers,
    trigger_already_active,
    trigger_limit_px,
)


def test_short_tp_above_entry_would_close_immediately():
    assert trigger_already_active(False, "tp", 4200, 2486) is True
    assert trigger_already_active(False, "sl", 3500, 2486) is False


def test_short_sl_below_entry_would_close_immediately():
    assert trigger_already_active(False, "sl", 1500, 2484) is True
    assert trigger_already_active(False, "tp", 4900, 2484) is True


def test_long_valid_and_invalid():
    entry = 102.65
    assert trigger_already_active(True, "sl", 90, entry) is False
    assert trigger_already_active(True, "tp", 120, entry) is False
    assert trigger_already_active(True, "sl", 110, entry) is True
    assert trigger_already_active(True, "tp", 90, entry) is True


def test_fill_avg_px():
    raw = {
        "status": "ok",
        "response": {"type": "order", "data": {"statuses": [{"filled": {"totalSz": "0.97", "avgPx": "102.6514"}}]}},
    }
    assert fill_avg_px(raw) == 102.6514
    assert fill_avg_px({"status": "ok"}) is None


def test_trigger_limit_px_is_more_aggressive():
    assert trigger_limit_px(True, 100, 2) > 100
    assert trigger_limit_px(False, 100, 2) < 100


def test_round_px_five_sig_figs_and_decimals():
    # SOL szDecimals=2 → max 4 decimal places, 5 sig figs.
    # 90.9972 (SL limit after 8% slippage) and 1093.51 (6 sig figs) were rejected.
    assert _round_px(90.9972, 2) == 90.997
    assert _round_px(1093.51, 2) == 1093.5
    assert _round_px(98.91, 2) == 98.91
    assert _round_px(1234.56, 2) == 1234.6


def test_round_px_integer_above_100k():
    assert _round_px(123456.7, 5) == 123457.0


def test_sol_sl_limit_after_slippage_is_valid():
    assert trigger_limit_px(False, 98.91, 2) == 90.997


def test_short_swaps_low_high_alert_prices():
    sl, tp, notes, err = prepare_triggers(False, 2485.8, 1500.0, 4900.0)
    assert err is None
    assert sl == 4900.0
    assert tp == 1500.0
    assert notes


def test_short_keeps_correct_sl_above_tp_below():
    sl, tp, notes, err = prepare_triggers(False, 2485.8, 4900.0, 1500.0)
    assert err is None
    assert sl == 4900.0
    assert tp == 1500.0
    assert notes == []


def test_long_keeps_low_sl_high_tp():
    sl, tp, notes, err = prepare_triggers(True, 102.65, 90.0, 120.0)
    assert err is None
    assert sl == 90.0
    assert tp == 120.0
    assert notes == []


def test_both_prices_above_entry_are_rejected():
    sl, tp, _notes, err = prepare_triggers(False, 2485.8, 3500.0, 4200.0)
    assert sl is None and tp is None
    assert err is not None
    assert "outside range" in err


def test_single_wrong_side_sl_is_rejected():
    sl, _tp, _notes, err = prepare_triggers(False, 2485.8, 1500.0, None)
    assert err is not None
    assert sl is None
