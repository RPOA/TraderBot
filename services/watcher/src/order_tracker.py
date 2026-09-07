"""Persistent alert dedup."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("watcher")


class OrderTracker:
    def __init__(self, data_file: Path):
        self.data_file = data_file
        self.orders: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.data_file.exists():
            try:
                self.orders = json.loads(self.data_file.read_text())
            except Exception as exc:
                logger.error("Failed to load orders: %s", exc)
                self.orders = {}
        else:
            self.orders = {}

    def _save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data_file.write_text(json.dumps(self.orders, indent=2))

    def is_processed(self, alert_timestamp: str) -> bool:
        return alert_timestamp in self.orders and self.orders[alert_timestamp].get("processed", False)

    def add_order(
        self,
        alert_timestamp: str,
        alert_name: str,
        action: str,
        ticker: str,
        alert_type: str = "trade",
        order_id: Optional[str] = None,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        qty_percentage: Optional[float] = None,
        wallet_id: Optional[str] = None,
        status: str = "pending",
    ) -> None:
        self.orders[alert_timestamp] = {
            "alert_timestamp": alert_timestamp,
            "order_id": order_id,
            "alert_name": alert_name,
            "alert_type": alert_type,
            "action": action,
            "ticker": ticker,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "qty_percentage": qty_percentage,
            "wallet_id": wallet_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "processed": True,
        }
        self._save()

    def update_order_status(self, alert_timestamp: str, status: str) -> None:
        if alert_timestamp in self.orders:
            self.orders[alert_timestamp]["status"] = status
            self._save()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        items = list(self.orders.values())
        items.sort(key=lambda row: row.get("timestamp", ""), reverse=True)
        return items[:limit]

    def cleanup_old_orders(self, days: int = 7) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        kept = {}
        for key, order in self.orders.items():
            try:
                ts = datetime.fromisoformat(order["timestamp"].replace("Z", "+00:00"))
            except Exception:
                kept[key] = order
                continue
            if ts > cutoff:
                kept[key] = order
        if len(kept) != len(self.orders):
            self.orders = kept
            self._save()

    def get_stats(self) -> dict[str, Any]:
        actions: dict[str, int] = {}
        for order in self.orders.values():
            action = order.get("action", "unknown")
            actions[action] = actions.get(action, 0) + 1
        return {"total_orders": len(self.orders), "by_action": actions}
