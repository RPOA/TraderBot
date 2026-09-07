"""Load wallets.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import DEFAULT_COLLATERAL_PERCENTAGE, DEFAULT_LEVERAGE

logger = logging.getLogger("trader")


def load_wallets(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"wallets.json not found at {path}")
    wallets = json.loads(path.read_text())
    if not wallets:
        raise ValueError("wallets.json is empty")
    for wallet_id, config in wallets.items():
        if "private_key" not in config:
            raise ValueError(f"Wallet '{wallet_id}' missing private_key")
        config.setdefault("leverage", DEFAULT_LEVERAGE)
        config.setdefault("collateral_percentage", DEFAULT_COLLATERAL_PERCENTAGE)
        config.setdefault("account_address", "")
        config.setdefault("description", "")
        logger.info(
            "Wallet %s L:%sx C:%s%%",
            wallet_id,
            config["leverage"],
            config["collateral_percentage"],
        )
    return wallets
