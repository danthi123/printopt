"""Tests for thermal simulation plugin."""

import numpy as np
import pytest

from printopt.plugins.thermal.grid import ThermalGrid, ThermalConfig
from printopt.plugins.thermal.plugin import ThermalPlugin


class TestThermalGrid:
    def test_init_ambient(self):
        grid = ThermalGrid()
        assert grid.grid.shape == (245, 245)
        assert np.allclose(grid.grid, 35.0)

    def test_custom_resolution(self):
        config = ThermalConfig(bed_x=100, bed_y=100, resolution=2.0)
        grid = ThermalGrid(config)
        assert grid.grid.shape == (50, 50)

    def test_deposit_heat(self):
        grid = ThermalGrid()
        initial_temp = grid.grid[120, 120]
        grid.deposit_heat(120.0, 120.0, flow_rate=5.0, dt=1.0)
        assert grid.grid[120, 120] > initial_temp

    def test_deposit_heat_localized(self):
        grid = ThermalGrid()
        grid.deposit_heat(50.0, 50.0, flow_rate=10.0, dt=1.0)
        # Heated cell should be warmer than neighbors
        assert grid.grid[50, 50] > grid.grid[100, 100]

    def test_step_cooling(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        # Heat a spot
        grid.grid[25, 25] = 200.0
        hot_before = grid.grid[25, 25]
        grid.step(1.0)
        # Should cool down
        assert grid.grid[25, 25] < hot_before

    def test_step_conduction_spreads_heat(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 200.0
        neighbor_before = grid.grid[25, 26]
        grid.step(1.0)
        # Neighbor should warm up
        assert grid.grid[25, 26] > neighbor_before

    def test_fan_increases_cooling(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid1 = ThermalGrid(config)
        grid2 = ThermalGrid(config)
        grid1.grid[25, 25] = 200.0
        grid2.grid[25, 25] = 200.0
        grid1.fan_speed = 0.0
        grid2.fan_speed = 1.0
        grid1.step(1.0)
        grid2.step(1.0)
        # Higher fan should cool more
        assert grid2.grid[25, 25] < grid1.grid[25, 25]

    def test_reset(self):
        grid = ThermalGrid()
        grid.grid[100, 100] = 200.0
        grid.reset()
        assert np.allclose(grid.grid, 35.0)

    def test_get_hotspots_empty(self):
        grid = ThermalGrid()
        hotspots = grid.get_hotspots()
        assert len(hotspots) == 0

    def test_get_hotspots_found(self):
        grid = ThermalGrid()
        grid.grid[50, 50] = 100.0  # above glass transition (78)
        hotspots = grid.get_hotspots()
        assert len(hotspots) >= 1
        assert any(t > 78 for _, _, t in hotspots)

    def test_thermal_gradient(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 200.0
        grad = grid.get_max_gradient()
        assert grad > 0

    def test_heatmap_returns_copy(self):
        grid = ThermalGrid()
        heatmap = grid.get_heatmap()
        heatmap[0, 0] = 999
        assert grid.grid[0, 0] != 999


class TestThermalPlugin:
    def test_plugin_name(self):
        plugin = ThermalPlugin()
        assert plugin.name == "thermal"

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        plugin = ThermalPlugin()
        await plugin.on_start()
        await plugin.on_stop()

    @pytest.mark.asyncio
    async def test_print_initializes_grid(self):
        plugin = ThermalPlugin(material="petg")
        gcode = "G1 Z0.2 F3000\nG1 X50 Y50 E1.0 F1500"
        await plugin.on_print_start("test.gcode", gcode)
        assert plugin.grid is not None
        assert plugin.grid.config.glass_transition == 78

    @pytest.mark.asyncio
    async def test_print_end_cleans_up(self):
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X10 Y10 E1 F1000")
        await plugin.on_print_end()
        assert plugin._print_active is False

    def test_dashboard_data(self):
        plugin = ThermalPlugin()
        data = plugin.get_dashboard_data()
        assert "layer" in data
        assert "material" in data

    @pytest.mark.asyncio
    async def test_status_update_tracks_position(self):
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        await plugin.on_status_update({
            "state": "printing",
            "x_position": 100.0,
            "y_position": 100.0,
            "z_position": 0.2,
            "nozzle_temp": 248.0,
            "fan_speed": 35.0,
        })
        assert plugin._last_x == 100.0
        assert plugin._last_y == 100.0
        assert plugin._nozzle_temp == 248.0

    @pytest.mark.asyncio
    async def test_downsample_heatmap(self):
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        heatmap = plugin._downsample_heatmap()
        assert heatmap is not None
        assert len(heatmap) <= 50
        assert len(heatmap[0]) <= 50

    @pytest.mark.asyncio
    async def test_dashboard_data_with_grid(self):
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        data = plugin.get_dashboard_data()
        assert "heatmap" in data
        assert "nozzle_pos" in data
        assert data["print_active"] is True
