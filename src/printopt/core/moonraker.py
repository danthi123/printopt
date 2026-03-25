"""Async Moonraker websocket client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import websockets
import websockets.client

logger = logging.getLogger(__name__)


class MoonrakerClient:
    """WebSocket client for Moonraker JSON-RPC API."""

    def __init__(self, host: str, port: int = 7125) -> None:
        self.host = host
        self.port = port
        self._ws: websockets.client.WebSocketClientProtocol | None = None
        self._connected = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[str, list[Callable]] = {}
        self._listen_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}/websocket"

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.url)
        self._connected = True
        self._listen_task = asyncio.create_task(self._listen())

    async def disconnect(self) -> None:
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()

    async def query(self, method: str, params: dict | None = None) -> Any:
        self._request_id += 1
        req_id = self._request_id
        request: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            request["params"] = params

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._ws.send(json.dumps(request))

        if not self._listen_task or self._listen_task.done():
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if "result" in msg:
                return msg["result"]
            if "error" in msg:
                raise MoonrakerError(msg["error"])

        return await asyncio.wait_for(future, timeout=10.0)

    async def inject(self, gcode: str) -> Any:
        return await self.query("printer.gcode.script", {"script": gcode})

    async def subscribe(
        self, objects: dict[str, list[str] | None], callback: Callable
    ) -> Any:
        result = await self.query("printer.objects.subscribe", {"objects": objects})
        for obj_name in objects:
            self._subscriptions.setdefault(obj_name, []).append(callback)
        return result

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    future = self._pending.pop(msg["id"])
                    if "result" in msg:
                        future.set_result(msg["result"])
                    elif "error" in msg:
                        future.set_exception(MoonrakerError(msg["error"]))
                elif "method" in msg and msg["method"] == "notify_status_update":
                    params = msg.get("params", [{}])
                    status = params[0] if params else {}
                    for obj_name, callbacks in self._subscriptions.items():
                        if obj_name in status:
                            for cb in callbacks:
                                cb(obj_name, status[obj_name])
        except websockets.exceptions.ConnectionClosed:
            self._connected = False


class MoonrakerError(Exception):
    pass
