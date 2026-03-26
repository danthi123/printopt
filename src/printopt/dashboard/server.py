"""FastAPI web dashboard for printopt."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"

_state = {
    "printer": {"connected": False, "status": {}},
    "plugins": {},
}
_ws_clients: list[WebSocket] = []
_poll_callback = None  # Set by do_run before starting the app
_poll_task = None

_kill_all = False
_reset_all = False
_pending_actions: list[dict] = []
_settings = {
    "printer_ip": "",
    "printer_port": 7125,
    "baseline_pa": 0.04,
    "corner_boost": 1.3,
    "corner_threshold": 60.0,
    "bridge_flow": 0.95,
    "bridge_fan": 70.0,
    "thin_wall_speed": 0.80,
    "small_perimeter_speed": 0.70,
    "material": "petg",
    "grid_resolution": 1.0,
}


def set_poll_callback(callback) -> None:
    """Register the status polling coroutine to run on startup."""
    global _poll_callback
    _poll_callback = callback


def create_app() -> FastAPI:
    app = FastAPI(title="printopt")

    # Load saved settings from disk
    settings_path = Path.home() / ".config" / "printopt" / "settings.json"
    if settings_path.exists():
        try:
            saved = json.loads(settings_path.read_text())
            _settings.update(saved)
        except Exception:
            pass

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.on_event("startup")
    async def start_polling():
        global _poll_task
        if _poll_callback is not None:
            import asyncio
            # _poll_callback returns a coroutine; wrap in create_task so
            # startup completes immediately while polling runs in background
            coro = _poll_callback()
            _poll_task = asyncio.create_task(coro)

    @app.on_event("shutdown")
    async def stop_polling():
        if _poll_task is not None:
            _poll_task.cancel()

    @app.get("/", response_class=HTMLResponse)
    async def index():
        template = TEMPLATE_DIR / "index.html"
        if template.exists():
            return template.read_text()
        return "<html><body><h1>printopt</h1><p>Dashboard loading...</p></body></html>"

    @app.get("/api/status")
    async def api_status():
        return _state

    @app.get("/api/settings")
    async def get_settings():
        return _settings

    @app.post("/api/settings")
    async def save_settings(request: Request):
        body = await request.json()
        _settings.update(body)
        # Save to disk
        settings_path = Path.home() / ".config" / "printopt" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(_settings, indent=2))
        # Notify via pending actions so the poll loop picks up changes
        _pending_actions.append({"action": "settings_changed", "settings": dict(_settings)})
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        global _kill_all, _reset_all
        await ws.accept()
        _ws_clients.append(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                    action = msg.get("action")
                    if action == "kill_all":
                        _kill_all = True
                    elif action == "reset":
                        _reset_all = True
                    elif action:
                        import logging
                        logging.getLogger(__name__).info("WS action received: %s", action)
                        _pending_actions.append(msg)
                except (json.JSONDecodeError, AttributeError):
                    pass
        except WebSocketDisconnect:
            _ws_clients.remove(ws)

    return app


def get_and_clear_kill() -> bool:
    global _kill_all
    if _kill_all:
        _kill_all = False
        return True
    return False


def get_and_clear_reset() -> bool:
    global _reset_all
    if _reset_all:
        _reset_all = False
        return True
    return False


def get_pending_actions() -> list[dict]:
    """Return and clear all pending actions from the dashboard."""
    if _pending_actions:
        import logging
        logging.getLogger(__name__).info("Pending actions: %s", _pending_actions)
    actions = list(_pending_actions)
    _pending_actions.clear()
    return actions


async def broadcast_state(state: dict) -> None:
    _state.update(state)
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(state)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)
