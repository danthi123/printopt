"""Async Moonraker client — HTTP for commands, WebSocket for status."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
from urllib.request import urlopen, Request
from urllib.error import URLError

import websockets
import websockets.client

logger = logging.getLogger(__name__)


class MoonrakerClient:
    """Client for Moonraker using HTTP for commands and WebSocket for status."""

    def __init__(self, host: str, port: int = 7125) -> None:
        self.host = host
        self.port = port
        self._ws: websockets.client.WebSocketClientProtocol | None = None
        self._connected = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[str, list[Callable]] = {}
        self._listen_task: asyncio.Task | None = None
        self._reconnecting = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}/websocket"

    @property
    def http_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def connect(self) -> None:
        """Establish websocket connection to Moonraker."""
        self._ws = await websockets.connect(self.url, ping_interval=None)
        self._connected = True
        self._listen_task = asyncio.create_task(self._listen())

    async def disconnect(self) -> None:
        """Close the websocket connection."""
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _reconnect(self) -> None:
        """Attempt to reconnect the websocket."""
        if self._reconnecting:
            return
        self._reconnecting = True
        logger.info("Reconnecting to Moonraker...")
        for attempt in range(5):
            try:
                if self._listen_task:
                    self._listen_task.cancel()
                self._ws = await websockets.connect(self.url, ping_interval=None)
                self._connected = True
                self._listen_task = asyncio.create_task(self._listen())
                logger.info("Reconnected to Moonraker")
                self._reconnecting = False
                return
            except Exception as e:
                logger.debug("Reconnect attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(2)
        self._reconnecting = False
        logger.error("Failed to reconnect to Moonraker after 5 attempts")

    def query_http(self, endpoint: str, method: str = "GET", data: dict | None = None) -> Any:
        """Synchronous HTTP request to Moonraker. Use for non-async contexts."""
        url = f"{self.http_url}/{endpoint}"
        if method == "GET":
            req = Request(url)
        else:
            body = json.dumps(data).encode() if data else b""
            req = Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    async def query(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request via websocket and return the result.

        Falls back to HTTP if websocket is unavailable.
        """
        # Try HTTP first for query endpoints (more reliable)
        if method == "printer.objects.query" and params:
            try:
                objects = params.get("objects", {})
                query_str = "&".join(
                    f"{k}" for k in objects.keys()
                )
                url = f"{self.http_url}/printer/objects/query?{query_str}"
                req = Request(url)
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: urlopen(req, timeout=10))
                data = json.loads(result.read())
                return data.get("result", data)
            except Exception:
                pass  # Fall through to websocket

        # Websocket path
        if not self._ws or not self._connected:
            await self._reconnect()
            if not self._connected:
                raise MoonrakerError("Not connected to Moonraker")

        self._request_id += 1
        req_id = self._request_id
        request = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            request["params"] = params

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future

        try:
            await self._ws.send(json.dumps(request))
        except Exception as e:
            self._pending.pop(req_id, None)
            self._connected = False
            raise MoonrakerError(f"Send failed: {e}")

        # For tests with mock ws (no listen task running)
        if not self._listen_task or self._listen_task.done():
            try:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                self._pending.pop(req_id, None)
                if "result" in msg:
                    return msg["result"]
                if "error" in msg:
                    raise MoonrakerError(msg["error"])
            except Exception as e:
                self._pending.pop(req_id, None)
                raise

        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise MoonrakerError(f"Query timed out: {method}")

    async def inject(self, gcode: str) -> Any:
        """Send a gcode command via HTTP POST (non-blocking, doesn't tie up websocket)."""
        try:
            url = f"{self.http_url}/printer/gcode/script"
            body = json.dumps({"script": gcode}).encode()
            req = Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: urlopen(req, timeout=300)  # 5 min timeout for long commands
            )
            data = json.loads(result.read())
            return data.get("result", "ok")
        except URLError as e:
            raise MoonrakerError(f"HTTP inject failed: {e}")

    async def subscribe(
        self, objects: dict[str, list[str] | None], callback: Callable
    ) -> Any:
        """Subscribe to printer object updates."""
        result = await self.query("printer.objects.subscribe", {"objects": objects})
        for obj_name in objects:
            self._subscriptions.setdefault(obj_name, []).append(callback)
        return result

    async def _listen(self) -> None:
        """Background task: read messages and dispatch responses/notifications."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    future = self._pending.pop(msg["id"])
                    if not future.done():
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
            logger.warning("Moonraker websocket connection closed")
            # Try to reconnect
            asyncio.create_task(self._reconnect())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._connected = False
            logger.error("Moonraker listen error: %s", e)


class MoonrakerError(Exception):
    pass
