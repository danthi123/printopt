"""Tests for thermal simulation plugin."""

from unittest.mock import AsyncMock

import numpy as np
import pytest

from printopt.plugins.thermal.grid import ThermalGrid, ThermalConfig, GPU_AVAILABLE
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
        initial_temp = float(grid.grid[120, 120])
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
        hot_before = float(grid.grid[25, 25])
        grid.step(1.0)
        # Should cool down
        assert grid.grid[25, 25] < hot_before

    def test_step_conduction_spreads_heat(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 200.0
        neighbor_before = float(grid.grid[25, 26])
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


class TestGPUAcceleration:
    def test_gpu_detection(self):
        """GPU_AVAILABLE should be a boolean."""
        assert isinstance(GPU_AVAILABLE, bool)

    def test_grid_works_without_gpu(self):
        """Grid should work with use_gpu=False even if GPU is available."""
        config = ThermalConfig(bed_x=50, bed_y=50, use_gpu=False)
        grid = ThermalGrid(config)
        grid.deposit_heat(25, 25, 5.0, 1.0)
        grid.step(1.0)
        assert grid.grid[25, 25] > 35.0

    @pytest.mark.skipif(not GPU_AVAILABLE, reason="No GPU available")
    def test_grid_works_with_gpu(self):
        """Grid should produce same results on GPU."""
        config = ThermalConfig(bed_x=50, bed_y=50, use_gpu=True)
        grid = ThermalGrid(config)
        grid.deposit_heat(25, 25, 5.0, 1.0)
        grid.step(1.0)
        heatmap = grid.get_heatmap()
        assert heatmap[25, 25] > 35.0
        assert isinstance(heatmap, np.ndarray)  # Should be numpy even with GPU

    @pytest.mark.skipif(not GPU_AVAILABLE, reason="No GPU available")
    def test_gpu_hotspots_returns_python_types(self):
        """Hotspots should return plain Python types even with GPU."""
        config = ThermalConfig(bed_x=50, bed_y=50, use_gpu=True)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 100.0
        hotspots = grid.get_hotspots()
        assert len(hotspots) >= 1
        for x, y, t in hotspots:
            assert isinstance(x, int)
            assert isinstance(y, int)
            assert isinstance(t, float)

    def test_cpu_fallback_when_gpu_requested_but_unavailable(self):
        """If GPU not available, use_gpu=True should silently fall back to numpy."""
        if GPU_AVAILABLE:
            pytest.skip("GPU is available, cannot test fallback")
        config = ThermalConfig(bed_x=50, bed_y=50, use_gpu=True)
        grid = ThermalGrid(config)
        assert grid.xp is np
        grid.deposit_heat(25, 25, 5.0, 1.0)
        grid.step(1.0)
        assert grid.grid[25, 25] > 35.0


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

    @pytest.mark.asyncio
    async def test_thermal_adjustments_on_high_gradient(self):
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True
        # Create a high gradient by setting a single hot cell
        plugin.grid.grid[50, 50] = 200.0
        await plugin._apply_thermal_adjustments()
        assert plugin._fan_adjusted is True
        # Verify inject was called
        plugin._moonraker.inject.assert_called()

    @pytest.mark.asyncio
    async def test_thermal_adjustments_on_many_hotspots(self):
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True
        # Create many hotspots above glass transition (78C)
        # Need to exceed hotspot_threshold (auto-calculated from bed area)
        n_hotspots = plugin.grid.hotspot_threshold + 5
        for i in range(n_hotspots):
            plugin.grid.grid[50 + i, 50 + i] = 100.0
        await plugin._apply_thermal_adjustments()
        assert plugin._speed_adjusted is True

    @pytest.mark.asyncio
    async def test_thermal_restores_on_print_end(self):
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._speed_adjusted = True
        plugin._fan_adjusted = True
        await plugin.on_print_end()
        assert plugin._speed_adjusted is False
        assert plugin._fan_adjusted is False

    @pytest.mark.asyncio
    async def test_no_adjustment_without_moonraker(self):
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True
        plugin.grid.grid[50, 50] = 200.0
        await plugin._apply_thermal_adjustments()
        # No moonraker, so no adjustments should be made
        assert plugin._fan_adjusted is False
        assert plugin._speed_adjusted is False

    def test_dashboard_data_includes_adjustment_status(self):
        plugin = ThermalPlugin()
        data = plugin.get_dashboard_data()
        assert "speed_adjusted" in data
        assert "fan_adjusted" in data
        assert data["speed_adjusted"] is False
        assert data["fan_adjusted"] is False
        assert "fan_boost" in data
        assert "speed_pct" in data


class TestLayerHistory:
    def test_advance_layer_history(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 100.0
        grid.advance_layer()
        assert len(grid._layer_history) == 1
        # After advance, no previous layers yet so no boost from history
        # But value should still be >= 100 (only 1 layer, no prior to apply)
        assert grid.grid[25, 25] >= 100.0

    def test_advance_layer_accumulates_heat(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        # First layer
        grid.grid[25, 25] = 100.0
        grid.advance_layer()
        # Second layer: same spot gets heat again
        grid.grid[25, 25] = 100.0
        val_before = float(grid.grid[25, 25])
        grid.advance_layer()
        # Now previous layer's heat should boost the effective grid
        effective = grid.get_effective_grid()
        assert effective[25, 25] > val_before

    def test_z_history_catches_tall_features(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        # Simulate 5 layers of heat at the same spot
        for _ in range(5):
            grid.grid[25, 25] = 80.0
            grid.advance_layer()
        # Accumulated effective heat should be higher than a single layer
        effective = grid.get_effective_grid()
        assert effective[25, 25] > 80.0

    def test_layer_history_max_size(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        for _ in range(10):
            grid.grid[25, 25] = 80.0
            grid.advance_layer()
        assert len(grid._layer_history) == grid._max_history

    def test_reset_clears_history(self):
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        grid.grid[25, 25] = 100.0
        grid.advance_layer()
        grid.reset()
        assert len(grid._layer_history) == 0


class TestMaterialAwareThresholds:
    def test_petg_threshold(self):
        petg = ThermalConfig(glass_transition=78)
        grid_petg = ThermalGrid(petg)
        assert grid_petg.gradient_threshold == pytest.approx(78 * 0.25, abs=1)

    def test_abs_higher_than_petg(self):
        petg = ThermalConfig(glass_transition=78)
        grid_petg = ThermalGrid(petg)

        abs_config = ThermalConfig(glass_transition=105)
        grid_abs = ThermalGrid(abs_config)
        assert grid_abs.gradient_threshold > grid_petg.gradient_threshold

    def test_pla_threshold(self):
        pla = ThermalConfig(glass_transition=60)
        grid_pla = ThermalGrid(pla)
        assert grid_pla.gradient_threshold == pytest.approx(60 * 0.25, abs=1)

    def test_minimum_threshold(self):
        # Very low Tg should still get at least 10 C/mm
        low_tg = ThermalConfig(glass_transition=20)
        grid = ThermalGrid(low_tg)
        assert grid.gradient_threshold >= 10.0

    def test_custom_threshold_overrides(self):
        config = ThermalConfig(gradient_warning_threshold=25.0, hotspot_warning_count=10)
        grid = ThermalGrid(config)
        assert grid.gradient_threshold == 25.0
        assert grid.hotspot_threshold == 10

    def test_hotspot_threshold_scales_with_bed(self):
        small = ThermalConfig(bed_x=50, bed_y=50)
        grid_small = ThermalGrid(small)

        large = ThermalConfig(bed_x=245, bed_y=245)
        grid_large = ThermalGrid(large)
        assert grid_large.hotspot_threshold > grid_small.hotspot_threshold


class TestProportionalAdjustments:
    @pytest.mark.asyncio
    async def test_proportional_fan_adjustment(self):
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True

        # Create a high gradient by setting a single hot cell
        plugin.grid.grid[50, 50] = 200.0
        await plugin._apply_thermal_adjustments()

        # Should have adjusted fan
        assert plugin._fan_adjusted is True
        assert plugin._current_fan_boost > 0

    @pytest.mark.asyncio
    async def test_fan_restore_below_hysteresis(self):
        """Fan should only restore when gradient drops below 70% of threshold."""
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True

        # First, trigger fan boost
        plugin.grid.grid[50, 50] = 200.0
        await plugin._apply_thermal_adjustments()
        assert plugin._fan_adjusted is True

        # Cool down grid well below hysteresis threshold
        plugin.grid.reset()
        # All ambient = gradient is 0, which is < 0.7 * threshold
        await plugin._apply_thermal_adjustments()
        assert plugin._fan_adjusted is False

    @pytest.mark.asyncio
    async def test_proportional_speed_adjustment(self):
        plugin = ThermalPlugin()
        plugin._moonraker = AsyncMock()
        plugin._moonraker.inject = AsyncMock()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        plugin._print_active = True

        # Create many hotspots above glass transition (78C)
        threshold = plugin.grid.hotspot_threshold
        for i in range(threshold + 5):
            plugin.grid.grid[50 + i, 50 + i] = 100.0
        await plugin._apply_thermal_adjustments()
        assert plugin._speed_adjusted is True
        assert plugin._current_speed_pct < 100

    @pytest.mark.asyncio
    async def test_dashboard_includes_proportional_data(self):
        plugin = ThermalPlugin()
        plugin._current_fan_boost = 15.0
        plugin._current_speed_pct = 90
        data = plugin.get_dashboard_data()
        assert data["fan_boost"] == 15.0
        assert data["speed_pct"] == 90


class TestEValueHeatDeposition:
    def test_e_position_based_heat(self):
        """E-based flow should deposit heat via volumetric calculation."""
        import math as _math
        config = ThermalConfig(bed_x=50, bed_y=50, resolution=1.0)
        grid = ThermalGrid(config)
        # Compute expected flow from delta_e=5mm over dt=1s
        filament_area = _math.pi * (1.75 / 2) ** 2
        volume = filament_area * 5.0
        flow_rate = volume / 1.0  # mm3/s
        initial_temp = float(grid.grid[25, 25])
        grid.deposit_heat(25.0, 25.0, flow_rate, 1.0)
        assert grid.grid[25, 25] > initial_temp

    @pytest.mark.asyncio
    async def test_e_position_in_status_update(self):
        """Plugin should use e_position from status for heat deposition."""
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        # Set initial position with e_position
        await plugin.on_status_update({
            "state": "printing",
            "x_position": 100.0,
            "y_position": 100.0,
            "z_position": 0.2,
            "nozzle_temp": 248.0,
            "fan_speed": 35.0,
            "e_position": 0.0,
        })
        assert plugin._last_e == 0.0
        # Move with extrusion
        await plugin.on_status_update({
            "state": "printing",
            "x_position": 120.0,
            "y_position": 100.0,
            "z_position": 0.2,
            "nozzle_temp": 248.0,
            "fan_speed": 35.0,
            "e_position": 5.0,
        })
        assert plugin._last_e == 5.0

    @pytest.mark.asyncio
    async def test_fallback_without_e_position(self):
        """Without e_position, _last_e stays at 0 and fallback path is used."""
        plugin = ThermalPlugin()
        await plugin.on_print_start("test.gcode", "G1 X50 Y50 E1 F1000")
        # Manually set _last_update back to ensure non-zero dt
        # (on Windows, time.monotonic() resolution may cause dt=0)
        plugin._last_update -= 0.5
        await plugin.on_status_update({
            "state": "printing",
            "x_position": 100.0,
            "y_position": 100.0,
            "z_position": 0.2,
            "nozzle_temp": 248.0,
            "fan_speed": 35.0,
        })
        # _last_e should remain 0 (no e_position in status)
        assert plugin._last_e == 0.0
        # The distance-based fallback should have been used for the initial
        # move (0,0 -> 100,100), which deposits heat. Verify grid has heat.
        max_temp = float(plugin.grid.grid.max())
        assert max_temp > 35.0  # above ambient = heat was deposited
