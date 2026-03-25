"""Bridge between thermal simulation and flow compensation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from printopt.plugins.flow.compensator import Compensation, CompensationType
from printopt.core.gcode import FeatureType


@dataclass
class ThermalCompensation:
    """Thermal-aware adjustment recommendation."""
    speed_factor: float = 1.0  # multiplier (0.7 = 30% slower)
    fan_factor: float = 1.0  # multiplier (1.5 = 50% more fan)
    reason: str = ""


class ThermalFlowBridge:
    """Reads thermal grid state and produces compensation recommendations.

    The flow plugin calls this with upcoming move positions to get
    thermal-aware speed and fan adjustments.
    """

    def __init__(
        self,
        glass_transition: float = 78.0,
        gradient_threshold: float = 10.0,  # C/mm
        hotspot_slowdown: float = 0.75,  # 25% slower in hot zones
        hotspot_fan_boost: float = 1.5,  # 50% more fan in hot zones
        cold_speedup: float = 1.1,  # 10% faster in cold zones
    ) -> None:
        self.glass_transition = glass_transition
        self.gradient_threshold = gradient_threshold
        self.hotspot_slowdown = hotspot_slowdown
        self.hotspot_fan_boost = hotspot_fan_boost
        self.cold_speedup = cold_speedup

    def evaluate_position(
        self,
        grid: np.ndarray,
        x: float,
        y: float,
        resolution: float = 1.0,
        ambient_temp: float = 35.0,
    ) -> ThermalCompensation:
        """Evaluate thermal state at a position and recommend compensation.

        Args:
            grid: 2D temperature array from ThermalGrid.get_heatmap()
            x: X position in mm
            y: Y position in mm
            resolution: Grid resolution in mm/cell
            ambient_temp: Ambient/chamber temperature

        Returns:
            ThermalCompensation with speed and fan adjustment factors.
        """
        ix = int(x / resolution)
        iy = int(y / resolution)
        ny, nx = grid.shape

        if not (0 <= ix < nx and 0 <= iy < ny):
            return ThermalCompensation(reason="out of bounds")

        temp = grid[iy, ix]

        # Check local thermal gradient
        gradient = self._local_gradient(grid, ix, iy, resolution)

        # Hot zone: temperature above glass transition
        if temp > self.glass_transition:
            excess = (temp - self.glass_transition) / (self.glass_transition - ambient_temp + 1)
            # Scale slowdown by how far above Tg we are
            speed = max(self.hotspot_slowdown, 1.0 - 0.3 * min(excess, 1.0))
            fan = min(self.hotspot_fan_boost, 1.0 + 0.5 * min(excess, 1.0))
            return ThermalCompensation(
                speed_factor=speed,
                fan_factor=fan,
                reason=f"hot zone: {temp:.1f}C (Tg={self.glass_transition}C)",
            )

        # High gradient: warping risk
        if gradient > self.gradient_threshold:
            return ThermalCompensation(
                speed_factor=0.85,
                fan_factor=0.8,  # reduce fan to equalize
                reason=f"high gradient: {gradient:.1f} C/mm",
            )

        # Cold zone: well below Tg, can go faster
        if temp < ambient_temp + 5:
            return ThermalCompensation(
                speed_factor=self.cold_speedup,
                fan_factor=1.0,
                reason=f"cold zone: {temp:.1f}C",
            )

        # Normal zone
        return ThermalCompensation(reason="normal")

    def _local_gradient(
        self, grid: np.ndarray, ix: int, iy: int, resolution: float
    ) -> float:
        """Compute thermal gradient magnitude at a cell."""
        ny, nx = grid.shape
        gx = 0.0
        gy = 0.0
        if 0 < ix < nx - 1:
            gx = (grid[iy, ix + 1] - grid[iy, ix - 1]) / (2 * resolution)
        if 0 < iy < ny - 1:
            gy = (grid[iy + 1, ix] - grid[iy - 1, ix]) / (2 * resolution)
        return float(np.sqrt(gx**2 + gy**2))
