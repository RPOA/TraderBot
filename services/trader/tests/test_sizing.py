from src.hyperliquid_client import trading_account_value


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
