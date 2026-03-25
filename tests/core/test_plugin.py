"""Tests for plugin base class and lifecycle."""

import pytest
from printopt.core.plugin import Plugin, PluginManager


class DummyPlugin(Plugin):
    name = "dummy"
    def __init__(self):
        super().__init__()
        self.started = False
        self.stopped = False
        self.layers = []
        self.statuses = []
    async def on_start(self):
        self.started = True
    async def on_layer(self, layer: int, z: float):
        self.layers.append(layer)
    async def on_status_update(self, status: dict):
        self.statuses.append(status)
    async def on_stop(self):
        self.stopped = True


class CrashPlugin(Plugin):
    name = "crash"
    async def on_start(self):
        raise RuntimeError("plugin crashed")


class CrashOnStatusPlugin(Plugin):
    name = "crash_status"
    async def on_start(self):
        pass
    async def on_status_update(self, status: dict):
        raise RuntimeError("status crash")


class TestPlugin:
    def test_plugin_name(self):
        p = DummyPlugin()
        assert p.name == "dummy"

    def test_plugin_enabled_by_default(self):
        p = DummyPlugin()
        assert p.enabled is True

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        p = DummyPlugin()
        await p.on_start()
        assert p.started
        await p.on_layer(1, 0.2)
        assert p.layers == [1]
        await p.on_stop()
        assert p.stopped

    @pytest.mark.asyncio
    async def test_dashboard_data_default(self):
        p = DummyPlugin()
        assert p.get_dashboard_data() == {}


class TestPluginManager:
    @pytest.mark.asyncio
    async def test_register_and_start(self):
        mgr = PluginManager()
        dummy = DummyPlugin()
        mgr.register(dummy)
        assert "dummy" in mgr.plugins
        await mgr.start_all()
        assert dummy.started

    @pytest.mark.asyncio
    async def test_crash_isolation(self):
        mgr = PluginManager()
        crash = CrashPlugin()
        dummy = DummyPlugin()
        mgr.register(crash)
        mgr.register(dummy)
        await mgr.start_all()
        assert dummy.started
        assert not crash.enabled

    @pytest.mark.asyncio
    async def test_broadcast_status(self):
        mgr = PluginManager()
        dummy = DummyPlugin()
        mgr.register(dummy)
        await mgr.start_all()
        await mgr.broadcast_status({"bed_temp": 70})
        assert dummy.statuses == [{"bed_temp": 70}]

    @pytest.mark.asyncio
    async def test_broadcast_skips_disabled(self):
        mgr = PluginManager()
        crash = CrashPlugin()
        dummy = DummyPlugin()
        mgr.register(crash)
        mgr.register(dummy)
        await mgr.start_all()
        await mgr.broadcast_status({"test": True})
        assert dummy.statuses == [{"test": True}]

    @pytest.mark.asyncio
    async def test_broadcast_isolates_runtime_crash(self):
        mgr = PluginManager()
        crasher = CrashOnStatusPlugin()
        dummy = DummyPlugin()
        mgr.register(crasher)
        mgr.register(dummy)
        await mgr.start_all()
        await mgr.broadcast_status({"x": 1})
        assert dummy.statuses == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_broadcast_layer(self):
        mgr = PluginManager()
        dummy = DummyPlugin()
        mgr.register(dummy)
        await mgr.start_all()
        await mgr.broadcast_layer(3, 0.6)
        assert dummy.layers == [3]

    @pytest.mark.asyncio
    async def test_stop_all(self):
        mgr = PluginManager()
        dummy = DummyPlugin()
        mgr.register(dummy)
        await mgr.start_all()
        await mgr.stop_all()
        assert dummy.stopped
