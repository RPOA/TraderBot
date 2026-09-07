"""HTTP sender to the trader service. Messages are forwarded unchanged."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger("watcher")


def send_webhook(url: str, message: str, timeout: int = 5) -> dict[str, Any]:
    """POST the alert message as-is (secret already inside the body)."""
    try:
        logger.info("Sending to %s: %s", url, message[:120])
        response = requests.post(
            url,
            data=message,
            headers={"Content-Type": "text/plain"},
            timeout=timeout,
        )
        if response.status_code == 200:
            return {"success": True, "status_code": 200, "response": response.text}
        return {
            "success": False,
            "status_code": response.status_code,
            "response": response.text,
            "error": f"HTTP {response.status_code}",
        }
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Timeout"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
