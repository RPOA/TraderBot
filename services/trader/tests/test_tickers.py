from pathlib import Path

from src.tickers import TickerRegistry


def test_activate_tsla(tmp_path: Path):
    path = tmp_path / "tickers.yaml"
    path.write_text(
        """
tickers:
  - alert: SOLUSDT.P
    coin: SOL
  - alert: TSLAUSDT.P
    coin: TSLA
"""
    )
    registry = TickerRegistry(path)
    registry.activate_from_universe(
        [
            {"name": "SOL", "szDecimals": 2},
            {"name": "xyz:TSLA", "szDecimals": 3},
        ]
    )
    sol = registry.resolve("SOLUSDT.P")
    assert sol.resolved_coin == "SOL"
    tsla = registry.resolve("TSLAUSDT.P")
    assert tsla.resolved_coin == "xyz:TSLA"


def test_inactive_when_missing(tmp_path: Path):
    path = tmp_path / "tickers.yaml"
    path.write_text(
        """
tickers:
  - alert: TSLAUSDT.P
    coin: TSLA
"""
    )
    registry = TickerRegistry(path)
    registry.activate_from_universe([{"name": "SOL", "szDecimals": 2}])
    try:
        registry.resolve("TSLAUSDT.P")
        assert False
    except ValueError as exc:
        assert "not listed" in str(exc)
