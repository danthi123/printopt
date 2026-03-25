"""Tests for thermal-flow bridge integration."""

import numpy as np
import pytest

from printopt.plugins.flow.thermal_bridge import ThermalFlowBridge, ThermalCompensation


@pytest.fixture
def bridge():
    return ThermalFlowBridge(glass_transition=78.0)


@pytest.fixture
def hot_grid():
    """Grid with a hot zone in the center."""
    grid = np.full((100, 100), 35.0)
    grid[45:55, 45:55] = 100.0  # hot zone above Tg
    return grid


@pytest.fixture
def gradient_grid():
    """Grid with a sharp thermal gradient."""
    grid = np.full((100, 100), 35.0)
    grid[:, 50:] = 80.0  # sharp edge
    return grid


@pytest.fixture
def cold_grid():
    """Grid at ambient temperature."""
    return np.full((100, 100), 35.0)


class TestThermalFlowBridge:
    def test_hot_zone_slows_down(self, bridge, hot_grid):
        result = bridge.evaluate_position(hot_grid, 50.0, 50.0)
        assert result.speed_factor < 1.0
        assert result.fan_factor > 1.0
        assert "hot zone" in result.reason

    def test_cold_zone_speeds_up(self, bridge, cold_grid):
        result = bridge.evaluate_position(cold_grid, 50.0, 50.0)
        assert result.speed_factor >= 1.0
        assert "cold zone" in result.reason

    def test_gradient_zone_adjusts(self, bridge, gradient_grid):
        # Right at the gradient edge
        result = bridge.evaluate_position(gradient_grid, 50.0, 50.0)
        # Should detect the gradient
        assert result.reason != "normal" or result.speed_factor != 1.0

    def test_out_of_bounds(self, bridge, cold_grid):
        result = bridge.evaluate_position(cold_grid, 999.0, 999.0)
        assert "out of bounds" in result.reason
        assert result.speed_factor == 1.0

    def test_normal_zone(self, bridge):
        grid = np.full((100, 100), 50.0)  # between ambient and Tg
        result = bridge.evaluate_position(grid, 50.0, 50.0)
        assert result.speed_factor == 1.0
        assert result.reason == "normal"

    def test_hotspot_slowdown_bounded(self, bridge):
        grid = np.full((100, 100), 200.0)  # very hot
        result = bridge.evaluate_position(grid, 50.0, 50.0)
        assert result.speed_factor >= 0.7  # never slower than 30% reduction
        assert result.fan_factor <= 1.5  # never more than 50% boost

    def test_custom_thresholds(self):
        bridge = ThermalFlowBridge(
            glass_transition=100.0,
            hotspot_slowdown=0.5,
            hotspot_fan_boost=2.0,
        )
        grid = np.full((100, 100), 120.0)
        result = bridge.evaluate_position(grid, 50.0, 50.0)
        assert result.speed_factor < 1.0
        assert "hot zone" in result.reason

    def test_gradient_calculation(self, bridge):
        grid = np.full((100, 100), 35.0)
        # Create a known gradient: 10 degrees over 1mm
        grid[50, 49] = 30.0
        grid[50, 51] = 50.0
        result = bridge.evaluate_position(grid, 50.0, 50.0, resolution=1.0)
        # Gradient should be significant
        assert result.reason != "cold zone"  # gradient should override cold
