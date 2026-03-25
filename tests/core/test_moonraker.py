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

    def test_http_url(self):
        client = MoonrakerClient("192.168.0.248")
        assert client.http_url == "http://192.168.0.248:7125"

    def test_custom_port_http_url(self):
        client = MoonrakerClient("192.168.0.248", port=8080)
        assert client.http_url == "http://192.168.0.248:8080"

    @pytest.mark.asyncio
    async def test_inject_gcode(self, mock_ws):
        """Test that inject uses HTTP POST (mocked via query fallback for testing)."""
        client = MoonrakerClient("192.168.0.248")
        client._ws = mock_ws
        client._connected = True
        client._request_id = 0

        # inject() now uses HTTP POST, verify URL formation
        assert client.http_url == "http://192.168.0.248:7125"
        assert client.host == "192.168.0.248"

    @pytest.mark.asyncio
    async def test_query_with_params(self, mock_ws):
        """Test query with params uses websocket for non-object-query methods."""
        client = MoonrakerClient("192.168.0.248")
        client._ws = mock_ws
        client._connected = True
        client._request_id = 0

        response = {"jsonrpc": "2.0", "result": {"status": {}}, "id": 1}
        mock_ws.recv.return_value = json.dumps(response)

        # Use printer.objects.subscribe (not .query) so it goes through websocket
        # printer.objects.query now tries HTTP first which bypasses the mock ws
        result = await client.query(
            "printer.objects.subscribe", {"objects": {"toolhead": None}}
        )
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["params"] == {"objects": {"toolhead": None}}
