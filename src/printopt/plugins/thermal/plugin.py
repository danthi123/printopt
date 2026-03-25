"""Thermal simulation plugin."""

from __future__ import annotations

import logging
import time

import numpy as np

from printopt.core.gcode import GcodeParser, ParseResult
from printopt.core.materials import MaterialProfile, get_profile
from printopt.core.plugin import Plugin
from printopt.plugins.thermal.grid import ThermalGrid, ThermalConfig

logger = logging.getLogger(__name__)

DASHBOARD_GRID_SIZE = 50  # Downsample to 50x50 for websocket


class ThermalPlugin(Plugin):
    name = "thermal"

    def __init__(self, material: str = "petg") -> None:
        super().__init__()
        self.material_name = material
        self.grid: ThermalGrid | None = None
        self.parse_result: ParseResult | None = None
        self.current_layer = 0
        self.warnings: list[dict] = []
        self._last_x: float = 0
        self._last_y: float = 0
        self._last_z: float = 0
        self._last_update: float = 0
        self._print_active: bool = False
        self._nozzle_temp: float = 0
        self._fan_speed: float = 0

    async def on_start(self) -> None:
        logger.info("Thermal simulation plugin started")

    async def on_print_start(self, filename: str, gcode: str) -> None:
        mat = get_profile(self.material_name)
        config = ThermalConfig(
            thermal_conductivity=mat.thermal_conductivity,
            specific_heat=mat.specific_heat,
            density=mat.density,
            glass_transition=mat.glass_transition,
        )
        self.grid = ThermalGrid(config)
        parser = GcodeParser()
        self.parse_result = parser.parse(gcode)
        self.current_layer = 0
        self.warnings = []
        self._last_update = time.monotonic()
        self._print_active = True
        logger.info("Thermal simulation initialized for %s", self.material_name)

    async def on_status_update(self, status: dict) -> None:
        state = status.get("state", "")

        if state == "printing" and not self._print_active:
            self._print_active = True
            self._last_update = time.monotonic()
        elif state in ("standby", "complete", "cancelled", "error"):
            self._print_active = False

        # Update nozzle temp and fan speed
        self._nozzle_temp = status.get("nozzle_temp", self._nozzle_temp)
        self._fan_speed = status.get("fan_speed", self._fan_speed) / 100.0  # Convert % to 0-1

        new_x = status.get("x_position", self._last_x)
        new_y = status.get("y_position", self._last_y)
        new_z = status.get("z_position", self._last_z)

        if not self.grid or not self._print_active:
            self._last_x, self._last_y, self._last_z = new_x, new_y, new_z
            return

        # Update grid fan speed
        self.grid.fan_speed = max(0.0, min(1.0, self._fan_speed))
        if self._nozzle_temp > 0:
            self.grid.nozzle_temp = self._nozzle_temp

        # Detect layer change
        if new_z > self._last_z + 0.05:
            self.current_layer += 1
            await self.on_layer(self.current_layer, new_z)

        # Calculate time delta
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now

        # If position changed and we're extruding, deposit heat
        dx = new_x - self._last_x
        dy = new_y - self._last_y
        dist = (dx**2 + dy**2) ** 0.5

        if dist > 0.1 and self._nozzle_temp > 100:
            # Estimate flow rate from distance and time
            # Approximate: 0.4mm nozzle * 0.2mm layer * speed
            speed = dist / dt if dt > 0 else 0
            flow_rate = 0.4 * 0.2 * speed  # mm3/s approximation

            # Deposit heat along the move path
            steps = max(1, int(dist))
            for i in range(steps):
                frac = (i + 0.5) / steps
                px = self._last_x + dx * frac
                py = self._last_y + dy * frac
                self.grid.deposit_heat(px, py, flow_rate, dt / steps)

        # Run simulation step
        if dt > 0:
            self.grid.step(dt)

        self._last_x, self._last_y, self._last_z = new_x, new_y, new_z

    async def on_layer(self, layer: int, z: float) -> None:
        self.current_layer = layer
        if self.grid:
            max_grad = self.grid.get_max_gradient()
            hotspots = self.grid.get_hotspots()
            if max_grad > 15.0:
                self.warnings.append({
                    "layer": layer, "type": "high_gradient", "value": max_grad,
                })
                logger.warning("Layer %d: high thermal gradient %.1f C/mm", layer, max_grad)
            if len(hotspots) > 10:
                self.warnings.append({
                    "layer": layer, "type": "hotspots", "count": len(hotspots),
                })

    async def on_print_end(self) -> None:
        if self.grid:
            logger.info("Thermal simulation complete. %d warnings.", len(self.warnings))
        self._print_active = False

    async def on_stop(self) -> None:
        self.grid = None
        self.parse_result = None

    def _downsample_heatmap(self) -> list[list[float]] | None:
        """Downsample the thermal grid to DASHBOARD_GRID_SIZE for websocket transfer."""
        if not self.grid:
            return None

        grid = self.grid.grid
        ny, nx = grid.shape

        if ny <= DASHBOARD_GRID_SIZE and nx <= DASHBOARD_GRID_SIZE:
            return grid.tolist()

        # Downsample using strided sampling
        # Use ceiling division to ensure output is <= DASHBOARD_GRID_SIZE
        step_y = max(1, -(-ny // DASHBOARD_GRID_SIZE))  # ceil division
        step_x = max(1, -(-nx // DASHBOARD_GRID_SIZE))  # ceil division

        downsampled = grid[::step_y, ::step_x]
        return [[round(float(v), 1) for v in row] for row in downsampled]

    def get_dashboard_data(self) -> dict:
        data = {
            "layer": self.current_layer,
            "warnings": self.warnings[-10:],
            "material": self.material_name,
            "print_active": self._print_active,
            "nozzle_pos": {
                "x": round(self._last_x, 1),
                "y": round(self._last_y, 1),
                "z": round(self._last_z, 2),
            },
        }
        if self.grid:
            data["max_temp"] = round(float(self.grid.grid.max()), 1)
            data["max_gradient"] = round(self.grid.get_max_gradient(), 2)
            data["hotspot_count"] = len(self.grid.get_hotspots())
            data["heatmap"] = self._downsample_heatmap()
        return data
