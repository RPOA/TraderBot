"""HTTP API: /do_trade, /trend, and supervision endpoints."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from .broker import Broker
from .config import (
    CLOSE_ON_TREND_CHANGE,
    DEFAULT_WALLET_ID,
    HYPERLIQUID_NETWORK,
    TRADE_DIRECTION_WEBHOOK_ENABLED,
    WEBHOOK_SECRET,
)
from .direction import DirectionState
from .logutil import memory_handler, parse_console_params, render_console_html
from .parser import parse_secret_payload, parse_trade_command, parse_trend_command
from .tickers import TickerRegistry

logger = logging.getLogger("trader")


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"success": False, "error": message}, status=status)


def create_app(broker: Broker, direction: DirectionState, tickers: TickerRegistry) -> web.Application:
    app = web.Application()
    app["broker"] = broker
    app["direction"] = direction
    app["tickers"] = tickers

    app.router.add_post("/do_trade", handle_do_trade)
    app.router.add_post("/trend", handle_trend)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/logs", handle_logs)
    app.router.add_get("/console", handle_console)
    app.router.add_get("/wallet", handle_wallet)
    app.router.add_get("/direction", handle_direction)
    app.router.add_get("/tickers", handle_tickers)
    return app


def _authenticate(raw: str) -> tuple[str, str] | web.Response:
    try:
        secret, command = parse_secret_payload(raw)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    if not WEBHOOK_SECRET:
        return _json_error("Webhook secret not configured", 500)
    if secret != WEBHOOK_SECRET:
        return _json_error("Invalid webhook secret", 403)
    return secret, command


async def handle_do_trade(request: web.Request) -> web.Response:
    broker: Broker = request.app["broker"]
    direction: DirectionState = request.app["direction"]
    tickers: TickerRegistry = request.app["tickers"]

    raw = await request.text()
    logger.info("POST /do_trade from %s", request.remote)
    auth = _authenticate(raw)
    if isinstance(auth, web.Response):
        return auth
    _, command = auth

    try:
        parsed = parse_trade_command(command, allowed_tickers=tickers.allowed_alerts)
        if not (parsed["action"] == "close" and parsed["ticker"] == "all"):
            tickers.resolve(parsed["ticker"])
    except ValueError as exc:
        logger.warning("Rejected /do_trade: %s", exc)
        return _json_error(str(exc), 400)

    action = parsed["action"]
    if action == "close" and parsed["ticker"] == "all":
        wallet_id = parsed["wallet_id"] if "##@" in command else None
        if wallet_id is not None and wallet_id not in broker.wallets:
            return _json_error(
                f"Unknown wallet: {wallet_id}. Available: {list(broker.wallets)}",
                400,
            )
        results = await broker.close_all_positions(wallet_id)
        return web.json_response(
            {
                "success": not results.get("errors"),
                "message": "Closed all positions",
                "action": "close",
                "ticker": "all",
                "wallet_id": wallet_id or "all",
                "positions_closed": results.get("closed", []),
                "close_errors": results.get("errors", []),
            }
        )

    wallet_id = parsed["wallet_id"]
    if wallet_id not in broker.wallets:
        return _json_error(
            f"Unknown wallet: {wallet_id}. Available: {list(broker.wallets)}",
            400,
        )

    if not direction.allows(action):
        logger.warning("Blocked %s (direction=%s)", action, direction.value)
        return _json_error(f"{action} orders blocked (direction={direction.value})", 400)

    try:
        broker.get_wallet(wallet_id)
    except (KeyError, RuntimeError) as exc:
        return _json_error(str(exc), 503)

    await broker.enqueue(parsed)
    return web.json_response(
        {
            "success": True,
            "message": "Trade queued for execution",
            "action": action,
            "ticker": parsed["ticker"],
            "wallet_id": wallet_id,
            "stop_loss_price": parsed["stop_loss_price"],
            "take_profit_price": parsed["take_profit_price"],
        }
    )


async def handle_trend(request: web.Request) -> web.Response:
    broker: Broker = request.app["broker"]
    direction: DirectionState = request.app["direction"]

    raw = await request.text()
    logger.info("POST /trend from %s", request.remote)

    if not TRADE_DIRECTION_WEBHOOK_ENABLED:
        return web.json_response({"success": False, "message": "Trend webhook disabled"}, status=200)

    auth = _authenticate(raw)
    if isinstance(auth, web.Response):
        return auth
    _, command = auth

    try:
        new_direction = parse_trend_command(command)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    old = direction.set(new_direction)
    logger.info("Direction %s -> %s", old, new_direction)

    close_results: dict[str, Any] | None = None
    if CLOSE_ON_TREND_CHANGE and new_direction != old:
        close_results = await broker.close_on_trend_change(new_direction)
    elif CLOSE_ON_TREND_CHANGE:
        logger.info("Direction unchanged (%s), no positions closed", new_direction)

    accepting = {"short": "sell", "long": "buy", "all": "buy+sell"}[new_direction]
    payload = {
        "success": True,
        "old_direction": old,
        "new_direction": new_direction,
        "accepting": accepting,
    }
    if close_results is not None:
        payload["positions_closed"] = close_results.get("closed", [])
        payload["close_errors"] = close_results.get("errors", [])
    return web.json_response(payload)


async def handle_health(request: web.Request) -> web.Response:
    broker: Broker = request.app["broker"]
    direction: DirectionState = request.app["direction"]
    ready = all(w.ready for w in broker.wallets.values()) and bool(broker.wallets)
    return web.json_response(
        {
            "status": "healthy" if ready else "degraded",
            "service": "trader",
            "network": HYPERLIQUID_NETWORK,
            "direction": direction.value,
            "wallets": list(broker.wallets),
            "wallets_ready": ready,
        }
    )


async def handle_status(request: web.Request) -> web.Response:
    broker: Broker = request.app["broker"]
    direction: DirectionState = request.app["direction"]
    tickers: TickerRegistry = request.app["tickers"]
    return web.json_response(
        {
            "service": "trader",
            "network": HYPERLIQUID_NETWORK,
            "direction": direction.value,
            "close_on_trend_change": CLOSE_ON_TREND_CHANGE,
            "queue_size": broker.queue.qsize(),
            "wallets": {
                wallet_id: {"ready": wallet.ready, "address": wallet.address}
                for wallet_id, wallet in broker.wallets.items()
            },
            "tickers": tickers.snapshot(),
        }
    )


async def handle_logs(request: web.Request) -> web.Response:
    try:
        lines = min(int(request.query.get("lines", "50")), 500)
    except ValueError:
        lines = 50
    return web.json_response({"success": True, "lines": memory_handler.lines(lines)})


async def handle_console(request: web.Request) -> web.Response:
    n_lines, refresh, autoscroll = parse_console_params(request.query)
    body = render_console_html(
        "Trader Console",
        memory_handler.lines(n_lines),
        refresh,
        autoscroll,
    )
    return web.Response(text=body, content_type="text/html")


async def handle_wallet(request: web.Request) -> web.Response:
    broker: Broker = request.app["broker"]
    wallet_id = request.query.get("wallet", DEFAULT_WALLET_ID)
    if wallet_id == "all":
        snapshots = []
        for wid, wallet in broker.wallets.items():
            try:
                snapshots.append(wallet.wallet_snapshot())
            except Exception as exc:
                snapshots.append({"wallet_id": wid, "error": str(exc)})
        return web.json_response({"wallets": snapshots})
    try:
        wallet = broker.get_wallet(wallet_id)
        return web.json_response(wallet.wallet_snapshot())
    except Exception as exc:
        return _json_error(str(exc), 400)


async def handle_direction(request: web.Request) -> web.Response:
    direction: DirectionState = request.app["direction"]
    return web.json_response({"direction": direction.value})


async def handle_tickers(request: web.Request) -> web.Response:
    tickers: TickerRegistry = request.app["tickers"]
    return web.json_response({"tickers": tickers.snapshot()})
