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


def create_app() -> FastAPI:
    app = FastAPI(title="printopt")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
        await ws.accept()
        _ws_clients.append(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            _ws_clients.remove(ws)

    return app


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
