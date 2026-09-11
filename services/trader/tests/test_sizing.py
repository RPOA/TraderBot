from src.hyperliquid_client import spot_assets_from_state, trading_account_value, wallet_nav


def test_classic_uses_perp_only():
    perp = {"marginSummary": {"accountValue": "100"}}
    spot = {"balances": [{"coin": "USDC", "total": "999", "hold": "0"}]}
    assert trading_account_value(perp, spot, "default") == 100.0
    assert trading_account_value(perp, spot, "disabled") == 100.0


def test_unified_uses_spot_when_perp_is_zero():
    perp = {"marginSummary": {"accountValue": "0.0"}}
    spot = {"balances": [{"coin": "USDC", "total": "999.0", "hold": "0.0"}]}
    assert trading_account_value(perp, spot, "unifiedAccount") == 999.0


def test_unified_subtracts_spot_hold():
    perp = {"marginSummary": {"accountValue": "0"}}
    spot = {"balances": [{"coin": "USDC", "total": "999", "hold": "10"}]}
    assert trading_account_value(perp, spot, "unifiedAccount") == 989.0


def test_unified_prefers_larger_of_spot_or_perp():
    perp = {"marginSummary": {"accountValue": "1050"}}
    spot = {"balances": [{"coin": "USDC", "total": "999", "hold": "0"}]}
    assert trading_account_value(perp, spot, "portfolioMargin") == 1050.0


def test_spot_assets_value_at_mark():
    spot = {
        "balances": [
            {"coin": "USDC", "total": "100", "hold": "0", "token": 0},
            {"coin": "HYPE", "total": "2", "hold": "0", "token": 150, "entryNtl": "10"},
            {"coin": "PURR", "total": "0", "hold": "0", "token": 1},
        ]
    }
    assets = spot_assets_from_state(spot, {"HYPE/USDC": 20.0, "@1": 0.5})
    by_coin = {item["coin"]: item for item in assets}
    assert set(by_coin) == {"USDC", "HYPE"}
    assert by_coin["USDC"]["value"] == 100.0
    assert by_coin["USDC"]["mark_px"] == 1.0
    assert by_coin["HYPE"]["value"] == 40.0
    assert by_coin["HYPE"]["mark_px"] == 20.0


def test_spot_assets_fallback_to_entry_ntl():
    spot = {"balances": [{"coin": "PURR", "total": "10", "hold": "0", "entryNtl": "3.5"}]}
    assets = spot_assets_from_state(spot, {})
    assert assets == [
        {"coin": "PURR", "size": 10.0, "hold": 0.0, "mark_px": None, "value": 3.5}
    ]


def test_classic_wallet_nav_adds_spot_and_perp():
    assets = [
        {"coin": "USDC", "value": 50.0, "hold": 0.0},
        {"coin": "HYPE", "value": 40.0, "hold": 0.0},
    ]
    assert wallet_nav(100.0, assets, "default") == 190.0


def test_unified_wallet_nav_adds_perp_equity_without_double_counting_margin():
    assets = [
        {"coin": "USDC", "value": 997.42, "hold": 100.46},
        {"coin": "HYPE", "value": 40.0, "hold": 0.0},
    ]
    assert wallet_nav(0.0, assets, "unifiedAccount") == 936.96
    assert wallet_nav(99.12, assets, "unifiedAccount") == 1036.08


def test_portfolio_margin_wallet_nav_uses_larger_of_perp_or_spot():
    assets = [
        {"coin": "USDC", "value": 999.0, "hold": 0.0},
        {"coin": "HYPE", "value": 40.0, "hold": 0.0},
    ]
    assert wallet_nav(1050.0, assets, "portfolioMargin") == 1050.0
    assert wallet_nav(100.0, assets, "portfolioMargin") == 1039.0
