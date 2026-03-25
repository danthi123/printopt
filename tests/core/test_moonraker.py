"""Tests for Moonraker websocket client."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from printopt.core.moonraker import MoonrakerClient, MoonrakerError


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.recv = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestMoonrakerClient:
    def test_init(self):
        client = MoonrakerClient("192.168.0.248")
        assert client.host == "192.168.0.248"
        assert client.port == 7125
        assert not client.connected

    def test_url(self):
        client = MoonrakerClient("192.168.0.248")
        assert client.url == "ws://192.168.0.248:7125/websocket"

    @pytest.mark.asyncio
    async def test_query_server_info(self, mock_ws):
        client = MoonrakerClient("192.168.0.248")
        client._ws = mock_ws
        client._connected = True
        client._request_id = 0

        response = {"jsonrpc": "2.0", "result": {"klippy_state": "ready"}, "id": 1}
        mock_ws.recv.return_value = json.dumps(response)

        result = await client.query("server.info")
        assert result["klippy_state"] == "ready"
        mock_ws.send.assert_called_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["method"] == "server.info"
        assert sent["jsonrpc"] == "2.0"
        assert sent["id"] == 1

    @pytest.mark.asyncio
    async def test_inject_gcode(self, mock_ws):
        client = MoonrakerClient("192.168.0.248")
        client._ws = mock_ws
        client._connected = True
        client._request_id = 0

        response = {"jsonrpc": "2.0", "result": "ok", "id": 1}
        mock_ws.recv.return_value = json.dumps(response)

        result = await client.inject("G28")
        assert result == "ok"
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["method"] == "printer.gcode.script"
        assert sent["params"]["script"] == "G28"

    @pytest.mark.asyncio
    async def test_query_with_params(self, mock_ws):
        client = MoonrakerClient("192.168.0.248")
        client._ws = mock_ws
        client._connected = True
        client._request_id = 0

        response = {"jsonrpc": "2.0", "result": {"status": {}}, "id": 1}
        mock_ws.recv.return_value = json.dumps(response)

        result = await client.query("printer.objects.query", {"objects": {"toolhead": None}})
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["params"] == {"objects": {"toolhead": None}}
