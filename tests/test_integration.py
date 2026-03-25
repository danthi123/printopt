"""Integration tests for plugin system."""

import pytest
from unittest.mock import AsyncMock

from printopt.core.plugin import PluginManager
from printopt.plugins.vibration.plugin import VibrationPlugin
from printopt.plugins.flow.plugin import FlowPlugin
from printopt.plugins.thermal.plugin import ThermalPlugin


SIMPLE_GCODE = """G1 Z0.2 F3000
G1 X10 Y10 E0.5 F1500
G1 X50 Y10 E2.0
G1 X50 Y50 E4.0
G1 X10 Y50 E6.0
G1 X10 Y10 E8.0
"""


class TestPluginIntegration:
    @pytest.mark.asyncio
    async def test_all_plugins_register_and_start(self):
        mgr = PluginManager()
        mgr.register(VibrationPlugin())
        mgr.register(FlowPlugin())
        mgr.register(ThermalPlugin())
        await mgr.start_all()
        assert len(mgr.plugins) == 3
        assert all(p.enabled for p in mgr.plugins.values())
        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_print_lifecycle_all_plugins(self):
        mgr = PluginManager()
        flow = FlowPlugin()
        thermal = ThermalPlugin()
        vib = VibrationPlugin()
        mgr.register(vib)
        mgr.register(flow)
        mgr.register(thermal)
        await mgr.start_all()

        # Simulate print start
        for plugin in mgr.plugins.values():
            await plugin.on_print_start("test.gcode", SIMPLE_GCODE)

        # Flow should have parsed
        assert flow.parse_result is not None
        assert flow.parse_result.total_time > 0

        # Thermal should have grid
        assert thermal.grid is not None

        # Simulate status updates
        await mgr.broadcast_status({
            "state": "printing",
            "progress": 50.0,
            "x_position": 30.0,
            "y_position": 30.0,
            "z_position": 0.2,
            "nozzle_temp": 248.0,
            "fan_speed": 35.0,
            "filename": "test.gcode",
        })

        # Flow should track state
        assert flow._print_state == "printing"

        # Thermal should track position
        assert thermal._last_x == 30.0

        # Simulate print end
        for plugin in mgr.plugins.values():
            await plugin.on_print_end()

        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_flow_thermal_bridge(self):
        flow = FlowPlugin()
        thermal = ThermalPlugin()
        flow._thermal_plugin = thermal

        await thermal.on_print_start("test.gcode", SIMPLE_GCODE)
        await flow.on_print_start("test.gcode", SIMPLE_GCODE)

        # Heat up a spot
        thermal.grid.grid[30, 30] = 120.0  # above Tg

        assert flow._thermal_plugin is thermal
        assert flow._thermal_plugin.grid is not None

    @pytest.mark.asyncio
    async def test_kill_disables_flow(self):
        mgr = PluginManager()
        flow = FlowPlugin()
        mgr.register(flow)
        await mgr.start_all()

        flow.kill()
        assert flow._kill is True
        data = flow.get_dashboard_data()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_dashboard_data_all_plugins(self):
        mgr = PluginManager()
        mgr.register(VibrationPlugin())
        mgr.register(FlowPlugin())
        mgr.register(ThermalPlugin())
        await mgr.start_all()

        for name, plugin in mgr.plugins.items():
            data = plugin.get_dashboard_data()
            assert isinstance(data, dict)
