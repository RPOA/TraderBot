"""Supervision API for the watcher."""

from __future__ import annotations

from aiohttp import web

from .logutil import memory_handler
from .watcher import AlertWatcher


def create_app(watcher: AlertWatcher) -> web.Application:
    app = web.Application()
    app["watcher"] = watcher
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/logs", handle_logs)
    app.router.add_get("/orders", handle_orders)
    app.router.add_get("/alerts", handle_alerts)
    app.router.add_post("/control/restart_browser", handle_restart)
    return app


async def handle_health(request: web.Request) -> web.Response:
    watcher: AlertWatcher = request.app["watcher"]
    snap = watcher.snapshot()
    healthy = snap["is_running"] and snap["logged_in"] and snap["health"].get("is_healthy", False)
    return web.json_response(
        {
            "status": "healthy" if healthy else "degraded",
            "service": "watcher",
            "is_running": snap["is_running"],
            "logged_in": snap["logged_in"],
            "health": snap["health"],
        }
    )


async def handle_status(request: web.Request) -> web.Response:
    watcher: AlertWatcher = request.app["watcher"]
    return web.json_response(watcher.snapshot())


async def handle_logs(request: web.Request) -> web.Response:
    try:
        lines = min(int(request.query.get("lines", "50")), 500)
    except ValueError:
        lines = 50
    return web.json_response({"lines": memory_handler.lines(lines)})


async def handle_orders(request: web.Request) -> web.Response:
    watcher: AlertWatcher = request.app["watcher"]
    return web.json_response({"orders": watcher.order_tracker.recent(100)})


async def handle_alerts(request: web.Request) -> web.Response:
    watcher: AlertWatcher = request.app["watcher"]
    return web.json_response({"orders": watcher.order_tracker.recent(50), "stats": watcher.order_tracker.get_stats()})


async def handle_restart(request: web.Request) -> web.Response:
    watcher: AlertWatcher = request.app["watcher"]
    watcher.restart_browser()
    return web.json_response({"success": True, "message": "Browser restart requested"})
