"""Persisted trade direction: short / long / all."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import TRADE_DIRECTION_INITIAL, VALID_DIRECTIONS

logger = logging.getLogger("trader")


class DirectionState:
    def __init__(self, state_file: Path, initial: str = TRADE_DIRECTION_INITIAL):
        self.state_file = state_file
        if initial not in VALID_DIRECTIONS:
            logger.warning("Invalid TRADE_DIRECTION '%s', defaulting to all", initial)
            initial = "all"
        self._direction = self._load(initial)

    @property
    def value(self) -> str:
        return self._direction

    def allows(self, action: str) -> bool:
        if action == "close":
            return True
        if self._direction == "all":
            return True
        if self._direction == "short":
            return action == "sell"
        if self._direction == "long":
            return action == "buy"
        return False

    def set(self, direction: str) -> str:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Invalid direction '{direction}'")
        old = self._direction
        self._direction = direction
        self._save()
        return old

    def _load(self, fallback: str) -> str:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text())
                direction = str(data.get("direction", "")).lower()
                if direction in VALID_DIRECTIONS:
                    logger.info("Loaded trade direction: %s", direction)
                    return direction
                logger.warning("Invalid direction in state file: %s", direction)
        except Exception as exc:
            logger.warning("Could not load direction state: %s", exc)
        self._direction = fallback
        self._save()
        return fallback

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(
                {
                    "direction": self._direction,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
        )
