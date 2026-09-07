"""Parse Watcher / TradingView alert payloads."""

from __future__ import annotations

from typing import Any

DEFAULT_WALLET_ID = "main"


def extract_wallet_id(command: str, default: str = DEFAULT_WALLET_ID) -> tuple[str, str]:
    """Split `##...##@wallet_id` into (command, wallet_id)."""
    if "##@" in command:
        head, wallet_id = command.rsplit("@", 1)
        wallet_id = wallet_id.strip().lower()
        if wallet_id:
            return head, wallet_id
    return command, default


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _is_positive_float(value: str) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _optional_price(value: str) -> float | None:
    """0 or missing means disabled. Positive values are kept."""
    if not _is_float(value):
        return None
    price = float(value)
    return price if price > 0 else None


def parse_trade_command(payload: str, allowed_tickers: set[str] | None = None) -> dict[str, Any]:
    """Parse `##action/ticker[/sl/tp[/qty][/order_id]]##[@wallet_id]`.

    SL and TP are both disabled when omitted or both 0.
    A single 0 leaves the other price active.
    """
    payload = payload.strip()
    payload, wallet_id = extract_wallet_id(payload)

    if not (payload.startswith("##") and payload.endswith("##")):
        raise ValueError(f"Invalid format (missing ## markers): {payload}")

    body = payload[2:-2]
    parts = body.split("/")
    if len(parts) < 2:
        raise ValueError(f"Invalid format (need at least action/ticker): {body}")

    action = parts[0].lower()
    ticker = parts[1]
    if action not in ("buy", "sell", "close"):
        raise ValueError(f"Invalid action: {action}")
    if allowed_tickers is not None and ticker not in allowed_tickers:
        raise ValueError(f"Unsupported ticker: {ticker}")

    result: dict[str, Any] = {
        "action": action,
        "ticker": ticker,
        "stop_loss_price": None,
        "take_profit_price": None,
        "qty_percentage": None,
        "wallet_id": wallet_id,
    }

    if len(parts) == 2:
        return result

    if len(parts) == 3:
        return result

    if len(parts) >= 4:
        if not _is_float(parts[2]) or not _is_float(parts[3]):
            raise ValueError(f"Invalid SL/TP values: {parts[2]}/{parts[3]}")
        result["stop_loss_price"] = _optional_price(parts[2])
        result["take_profit_price"] = _optional_price(parts[3])

    if len(parts) >= 5 and _is_positive_float(parts[4]):
        result["qty_percentage"] = float(parts[4])

    return result


def parse_secret_payload(raw: str) -> tuple[str, str]:
    """Split `SECRET||command[||timestamp]` into (secret, command)."""
    raw = raw.strip()
    if "||" not in raw:
        raise ValueError("Invalid format. Use: SECRET||##action/ticker##")
    secret, rest = raw.split("||", 1)
    # Watcher normally strips {{timenow}}; tolerate a leftover timestamp.
    if rest.startswith("##") and rest.count("||") >= 1:
        maybe_cmd, maybe_ts = rest.rsplit("||", 1)
        if maybe_cmd.endswith("##") or "##@" in maybe_cmd:
            rest = maybe_cmd
    elif rest.startswith("**trend/") and rest.count("||") >= 1:
        rest = rest.split("||", 1)[0]
    return secret, rest


def parse_trend_command(command: str) -> str:
    command = command.strip()
    if command.startswith("**trend/") and command.endswith("**"):
        direction = command[8:-2].lower()
    elif command.startswith("trend/"):
        direction = command[6:].lower()
    else:
        raise ValueError("Invalid format. Use: SECRET||**trend/short**")
    if direction not in ("short", "long", "all"):
        raise ValueError(f"Invalid direction '{direction}'. Use: short, long, or all")
    return direction
