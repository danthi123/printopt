"""FastAPI web dashboard for printopt."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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


def set_poll_callback(callback) -> None:
    """Register the status polling coroutine to run on startup."""
    global _poll_callback
    _poll_callback = callback


def create_app() -> FastAPI:
    app = FastAPI(title="printopt")

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
