from src.hyperliquid_client import collect_universe, extra_perp_dexes, market_slippage


class FakeInfo:
    def __init__(self):
        self.meta_calls: list[str] = []

    def meta(self, dex=""):
        self.meta_calls.append(dex)
        if dex == "":
            return {"universe": [{"name": "SOL", "szDecimals": 2}]}
        if dex == "xyz":
            return {"universe": [{"name": "xyz:TSLA", "szDecimals": 3}]}
        if dex == "scam":
            return {"universe": [{"name": "scam:FAKE", "szDecimals": 0}]}
        return {"universe": []}

    def all_mids(self):
        return {"SOL": "100"}

    def perp_dexs(self):
        return [None, {"name": "scam"}, {"name": "xyz"}]


def test_extra_perp_dexes_from_hip3_names():
    assert extra_perp_dexes(["SOL", "xyz:TSLA", "xyz:NVDA", "ETH"]) == ["xyz"]
    assert extra_perp_dexes(["BTC"]) == []


def test_hip3_uses_wider_market_slippage():
    assert market_slippage("ETH") == 0.02
    assert market_slippage("xyz:TSLA") == 0.10


def test_collect_universe_finds_hip3_tsla_via_preferred_dex():
    info = FakeInfo()
    assets = collect_universe(info, wanted_coins={"SOL", "TSLA"})
    names = {item["name"] for item in assets}
    assert "SOL" in names
    assert "xyz:TSLA" in names
    assert info.meta_calls[0] == ""
    assert "xyz" in info.meta_calls
