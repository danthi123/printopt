"""Plugin base class and lifecycle management."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Plugin:
    name: str = "unnamed"

    def __init__(self) -> None:
        self.enabled = True
        self._error: Exception | None = None

    async def on_start(self) -> None:
        pass
    async def on_print_start(self, filename: str, gcode: str) -> None:
        pass
    async def on_layer(self, layer: int, z: float) -> None:
        pass
    async def on_status_update(self, status: dict) -> None:
        pass
    async def on_print_end(self) -> None:
        pass
    async def on_stop(self) -> None:
        pass
    def get_dashboard_data(self) -> dict:
        return {}


class PluginManager:
    def __init__(self) -> None:
        self.plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        self.plugins[plugin.name] = plugin

    async def start_all(self) -> None:
        for name, plugin in self.plugins.items():
            try:
                await plugin.on_start()
                logger.info("Plugin '%s' started", name)
            except Exception as e:
                logger.error("Plugin '%s' failed to start: %s", name, e)
                plugin.enabled = False
                plugin._error = e

    async def stop_all(self) -> None:
        for name, plugin in self.plugins.items():
            try:
                await plugin.on_stop()
            except Exception as e:
                logger.error("Plugin '%s' failed to stop: %s", name, e)

    async def broadcast_status(self, status: dict) -> None:
        for plugin in self.plugins.values():
            if plugin.enabled:
                try:
                    await plugin.on_status_update(status)
                except Exception as e:
                    logger.error("Plugin '%s' error on status: %s", plugin.name, e)

    async def broadcast_layer(self, layer: int, z: float) -> None:
        for plugin in self.plugins.values():
            if plugin.enabled:
                try:
                    await plugin.on_layer(layer, z)
                except Exception as e:
                    logger.error("Plugin '%s' error on layer: %s", plugin.name, e)
