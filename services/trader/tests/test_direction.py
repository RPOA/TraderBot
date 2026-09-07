from pathlib import Path

from src.direction import DirectionState


def test_allows(tmp_path: Path):
    state = DirectionState(tmp_path / "dir.json", initial="short")
    assert state.allows("sell")
    assert state.allows("close")
    assert not state.allows("buy")

    state.set("long")
    assert state.allows("buy")
    assert not state.allows("sell")

    state.set("all")
    assert state.allows("buy")
    assert state.allows("sell")


def test_persists(tmp_path: Path):
    path = tmp_path / "dir.json"
    first = DirectionState(path, initial="all")
    first.set("short")
    second = DirectionState(path, initial="all")
    assert second.value == "short"
