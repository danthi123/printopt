"""Thermal simulation plugin."""

from __future__ import annotations

import logging
import math
import time

import numpy as np

from printopt.core.gcode import GcodeParser, ParseResult
from printopt.core.materials import MaterialProfile, get_profile
from printopt.core.plugin import Plugin
from printopt.plugins.thermal.grid import ThermalGrid, ThermalConfig, GPU_AVAILABLE

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
        self._last_e: float = 0.0
        self._last_update: float = 0
        self._print_active: bool = False
        self._nozzle_temp: float = 0
        self._fan_speed: float = 0
        self._moonraker = None  # Set externally for gcode injection
        self._speed_adjusted: bool = False
        self._fan_adjusted: bool = False
        self._baseline_fan: float = 35.0  # % from printer status
        self._current_fan_boost: float = 0
        self._current_speed_pct: int = 100
        self._last_injected_fan: int = -1
        self._last_injected_speed: int = -1
        self._toolpath_segments: list[dict] = []  # recent segments for dashboard
        self._max_dashboard_segments = 2000  # limit for websocket

    async def on_start(self) -> None:
        logger.info("Thermal simulation plugin started")

    async def on_print_start(self, filename: str, gcode: str) -> None:
        mat = get_profile(self.material_name)
        config = ThermalConfig(
            thermal_conductivity=mat.thermal_conductivity,
            specific_heat=mat.specific_heat,
            density=mat.density,
            glass_transition=mat.glass_transition,
            resolution=0.5 if GPU_AVAILABLE else 1.0,
        )
        self.grid = ThermalGrid(config)
        parser = GcodeParser()
        self.parse_result = parser.parse(gcode)
        self.current_layer = 0
        self.warnings = []
        self._toolpath_segments = []
        self._last_update = time.monotonic()

        # Pre-build per-layer extrusion segments for Fluidd-style toolpath rendering
        self._layer_segments = self._build_layer_segments()
        logger.info("Built layer index: %d layers, %d total segments",
                     len(self._layer_segments),
                     sum(len(v) for v in self._layer_segments.values()))
        self._print_active = True
        logger.info("Thermal simulation initialized for %s", self.material_name)

    async def on_status_update(self, status: dict) -> None:
        state = status.get("state", "")

        if state == "printing" and not self._print_active:
            self._print_active = True
            self._last_update = time.monotonic()
        elif state in ("standby", "complete", "cancelled", "error"):
            self._print_active = False

        # Track progress for toolpath rendering
        self._current_progress = status.get("progress", getattr(self, '_current_progress', 0))

        # Update nozzle temp and fan speed
        self._nozzle_temp = status.get("nozzle_temp", self._nozzle_temp)
        raw_fan = status.get("fan_speed")
        if raw_fan is not None:
            self._fan_speed = raw_fan / 100.0  # Only divide when we get a new value

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

        # Find current gcode layer from Z position
        z_values = getattr(self, '_layer_z_values', [])
        if z_values:
            import bisect
            idx = bisect.bisect_right([z for z, _ in z_values], new_z + 0.05) - 1
            if idx >= 0:
                gcode_layer = z_values[idx][1]
            else:
                gcode_layer = z_values[0][1] if z_values else 0
        else:
            gcode_layer = max(0, int(new_z / 0.2))

        if gcode_layer != self.current_layer:
            self.current_layer = gcode_layer
            await self.on_layer(self.current_layer, new_z)

        # Calculate time delta
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now

        # If position changed, deposit heat based on extrusion
        dx = new_x - self._last_x
        dy = new_y - self._last_y
        dist = (dx**2 + dy**2) ** 0.5

        # Query extruder position if available
        new_e = status.get("e_position", self._last_e)

        # Use E-value based heat deposition when available
        delta_e = new_e - self._last_e
        if delta_e > 0 and dt > 0:
            # E is in mm of filament. Convert to volumetric flow:
            # Volume = pi * (filament_diameter/2)^2 * delta_e
            filament_area = math.pi * (1.75 / 2) ** 2  # mm^2 for 1.75mm filament
            volume = filament_area * delta_e  # mm^3
            flow_rate = volume / dt  # mm^3/s

            # Deposit heat along the move path
            if dist > 0.1:
                steps = max(1, int(dist))
                for s in range(steps):
                    frac = (s + 0.5) / steps
                    px = self._last_x + dx * frac
                    py = self._last_y + dy * frac
                    self.grid.deposit_heat(px, py, flow_rate, dt / steps)
        elif dist > 0.1 and self._nozzle_temp > 100:
            # Fallback: estimate from distance (original method)
            speed = dist / dt if dt > 0 else 0
            flow_rate = 0.4 * 0.2 * speed  # mm3/s approximation

            # Deposit heat along the move path
            steps = max(1, int(dist))
            for s in range(steps):
                frac = (s + 0.5) / steps
                px = self._last_x + dx * frac
                py = self._last_y + dy * frac
                self.grid.deposit_heat(px, py, flow_rate, dt / steps)

        # Build toolpath from parsed gcode (much more complete than polled positions)
        # Re-render on each layer change or periodically
        if self._print_active and self.parse_result:
            self._rebuild_toolpath_from_gcode()

        self._last_e = new_e

        # Run simulation step
        if dt > 0:
            self.grid.step(dt)

        self._last_x, self._last_y, self._last_z = new_x, new_y, new_z

        # Apply thermal adjustments when actively printing
        if self._print_active:
            await self._apply_thermal_adjustments()

    async def _apply_thermal_adjustments(self) -> None:
        """Adjust fan speed and print speed based on thermal conditions.

        Uses proportional scaling based on gradient severity and hotspot count,
        with hysteresis to avoid oscillation.
        """
        if not self.grid or not self._moonraker or not self._print_active:
            return

        max_grad = self.grid.get_max_gradient()
        hotspot_count = len(self.grid.get_hotspots())
        grad_threshold = self.grid.gradient_threshold
        hotspot_threshold = self.grid.hotspot_threshold

        # Proportional fan adjustment based on gradient severity
        if max_grad > grad_threshold:
            # Scale: at threshold = +10% fan, at 3x threshold = +30% fan
            severity = min((max_grad / grad_threshold - 1.0) / 2.0, 1.0)
            fan_boost = 10 + severity * 20  # 10-30% boost
            fan_pct = min(100, self._baseline_fan + fan_boost)
            fan_value = int(fan_pct * 255 / 100)
            try:
                if fan_value != self._last_injected_fan:
                    await self._moonraker.inject(f"M106 S{fan_value}")
                    self._last_injected_fan = fan_value
                if not self._fan_adjusted:
                    logger.info("Thermal: fan +%.0f%% (gradient %.1f C/mm)", fan_boost, max_grad)
                self._fan_adjusted = True
                self._current_fan_boost = fan_boost
            except Exception as e:
                logger.warning("Thermal fan adjust failed: %s", e)
        elif max_grad < grad_threshold * 0.7 and self._fan_adjusted:
            # Restore when well below threshold (hysteresis)
            fan_value = int(self._baseline_fan * 255 / 100)
            try:
                if fan_value != self._last_injected_fan:
                    await self._moonraker.inject(f"M106 S{fan_value}")
                    self._last_injected_fan = fan_value
                self._fan_adjusted = False
                self._current_fan_boost = 0
                logger.info("Thermal: fan restored to %.0f%%", self._baseline_fan)
            except Exception:
                pass

        # Proportional speed adjustment based on hotspot count
        if hotspot_count > hotspot_threshold:
            # Scale: at threshold = 95% speed, at 5x threshold = 75% speed
            severity = min((hotspot_count / hotspot_threshold - 1.0) / 4.0, 1.0)
            speed_pct = max(90, int(100 - severity * 25))  # 90-95%
            try:
                if speed_pct != self._last_injected_speed:
                    await self._moonraker.inject(f"M220 S{speed_pct}")
                    self._last_injected_speed = speed_pct
                if not self._speed_adjusted:
                    logger.info("Thermal: speed %d%% (%d hotspots)", speed_pct, hotspot_count)
                self._speed_adjusted = True
                self._current_speed_pct = speed_pct
            except Exception as e:
                logger.warning("Thermal speed adjust failed: %s", e)
        elif hotspot_count <= max(1, hotspot_threshold // 3) and self._speed_adjusted:
            # Restore when well below threshold
            try:
                if 100 != self._last_injected_speed:
                    await self._moonraker.inject("M220 S100")
                    self._last_injected_speed = 100
                self._speed_adjusted = False
                self._current_speed_pct = 100
                logger.info("Thermal: speed restored to 100%%")
            except Exception:
                pass

    async def on_layer(self, layer: int, z: float) -> None:
        self.current_layer = layer
        if self.grid:
            self.grid.advance_layer()
            max_grad = self.grid.get_max_gradient()
            hotspots = self.grid.get_hotspots()
            if max_grad > self.grid.gradient_threshold:
                self.warnings.append({
                    "layer": layer, "type": "high_gradient", "value": max_grad,
                })
                logger.warning("Layer %d: high thermal gradient %.1f C/mm", layer, max_grad)
            if len(hotspots) > self.grid.hotspot_threshold:
                self.warnings.append({
                    "layer": layer, "type": "hotspots", "count": len(hotspots),
                })
            # Bound warnings list to prevent unbounded growth
            if len(self.warnings) > 500:
                self.warnings = self.warnings[-250:]

    async def on_print_end(self) -> None:
        if self._moonraker:
            if self._speed_adjusted:
                try:
                    await self._moonraker.inject("M220 S100")
                except Exception:
                    pass
            if self._fan_adjusted:
                try:
                    await self._moonraker.inject(f"M106 S{int(self._baseline_fan * 255 / 100)}")
                except Exception:
                    pass
        self._speed_adjusted = False
        self._fan_adjusted = False
        self._print_active = False
        if self.grid:
            logger.info("Thermal simulation complete. %d warnings.", len(self.warnings))

    async def on_stop(self) -> None:
        self.grid = None
        self.parse_result = None

    def _build_layer_segments(self) -> dict[int, list[tuple[float, float, float, float]]]:
        """Pre-build per-layer extrusion segments from parsed gcode.

        Called once at print start. Returns a dict mapping layer number
        to a list of (x1, y1, x2, y2) line segments for that layer.
        """
        if not self.parse_result:
            return {}

        from printopt.core.gcode import FeatureType

        layers: dict[int, list[tuple[float, float, float, float]]] = {}
        current_layer = 0
        prev_x, prev_y = 0.0, 0.0

        # Group segments by Z value of the extrusion move
        # This naturally handles variable layer heights and skips Z-hops
        # (Z-hop moves are travel moves, not extrusions)
        self._layer_z_values = []  # (z, layer_key) for bisect lookup

        z_to_key: dict[float, int] = {}
        key_counter = 0

        for move in self.parse_result.moves:
            if move.is_extrusion and move.distance > 0.3:
                z_rounded = round(move.z, 2)
                if z_rounded not in z_to_key:
                    key_counter += 1
                    z_to_key[z_rounded] = key_counter
                    self._layer_z_values.append((z_rounded, key_counter))
                layer_key = z_to_key[z_rounded]
                seg = (round(prev_x, 1), round(prev_y, 1),
                       round(move.x, 1), round(move.y, 1))
                layers.setdefault(layer_key, []).append(seg)

            prev_x, prev_y = move.x, move.y

        self._layer_z_values.sort()
        logger.info("Layer index: %d layers from extrusion Z values (1-%d)",
                     len(z_to_key), key_counter)
        return layers

    def _rebuild_toolpath_from_gcode(self) -> None:
        """Look up pre-built layer segments and sample grid temperatures.

        Uses the layer index built at print start — no gcode scanning needed.
        Renders current layer plus 2 previous layers with age-based fading.
        """
        if not self.grid or not hasattr(self, '_layer_segments'):
            return

        now = time.monotonic()
        last_rebuild = getattr(self, '_last_rebuild_time', 0)
        if now - last_rebuild < 2.0:
            return
        self._last_rebuild_time = now

        layer_segs = getattr(self, '_layer_segments', {})
        if not layer_segs:
            return

        # Single GPU→CPU transfer for temperature lookups
        if self.grid.xp is not np:
            grid_np = self.grid.xp.asnumpy(self.grid.grid)
        else:
            grid_np = self.grid.grid

        resolution = self.grid.config.resolution
        segments = []
        now_mono = time.monotonic()

        # Render current layer + 2 previous layers
        for layer_num in range(max(0, self.current_layer - 2), self.current_layer + 1):
            layer_moves = layer_segs.get(layer_num, [])
            age_offset = (self.current_layer - layer_num) * 10.0  # older layers appear cooler

            for (x1, y1, x2, y2) in layer_moves:
                # Sample temperature at endpoint
                ix = int(x2 / resolution)
                iy = int(y2 / resolution)
                temp = 35.0
                if 0 <= ix < self.grid.nx and 0 <= iy < self.grid.ny:
                    temp = float(grid_np[iy, ix])

                segments.append({
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "temp": round(temp, 1),
                    "layer": layer_num,
                    "age": age_offset,
                    "created": now_mono - age_offset,
                })

        # Cap to dashboard limit
        if len(segments) > self._max_dashboard_segments:
            # Keep most recent segments (current layer prioritized)
            segments = segments[-self._max_dashboard_segments:]

        self._toolpath_segments = segments

    def _downsample_heatmap(self) -> list[list[float]] | None:
        """Downsample the thermal grid to DASHBOARD_GRID_SIZE for websocket transfer."""
        if not self.grid:
            return None

        grid = self.grid.grid
        ny, nx = grid.shape

        # Transfer to numpy FIRST to avoid CuPy row iteration issues
        if self.grid.xp is not np:
            grid_np = self.grid.xp.asnumpy(grid)
        else:
            grid_np = grid

        if ny <= DASHBOARD_GRID_SIZE and nx <= DASHBOARD_GRID_SIZE:
            return [[round(float(v), 1) for v in row] for row in grid_np]

        # Downsample using strided sampling
        # Use ceiling division to ensure output is <= DASHBOARD_GRID_SIZE
        step_y = max(1, -(-ny // DASHBOARD_GRID_SIZE))  # ceil division
        step_x = max(1, -(-nx // DASHBOARD_GRID_SIZE))  # ceil division

        downsampled = grid_np[::step_y, ::step_x]
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
        data["speed_adjusted"] = self._speed_adjusted
        data["fan_adjusted"] = self._fan_adjusted
        data["fan_boost"] = self._current_fan_boost
        data["speed_pct"] = self._current_speed_pct
        if self.grid:
            grid_arr = self.grid.grid
            if self.grid.xp is np:
                max_val = float(grid_arr.max())
            else:
                max_val = float(self.grid.xp.asnumpy(grid_arr).max())
            data["max_temp"] = round(max_val, 1)
            data["max_gradient"] = round(self.grid.get_max_gradient(), 2)
            data["hotspot_count"] = len(self.grid.get_hotspots())
            # Update segment ages before sending
            now = time.monotonic()
            for seg in self._toolpath_segments:
                seg["age"] = round(now - seg.get("created", now), 1)
            # Send toolpath segments instead of downsampled grid
            data["toolpath"] = self._toolpath_segments[-1000:]
            # Send bed dimensions for scaling
            data["bed_x"] = self.grid.config.bed_x
            data["bed_y"] = self.grid.config.bed_y
            # Fallback heatmap for when toolpath is empty
            if not self._toolpath_segments:
                data["heatmap"] = self._downsample_heatmap()
        return data
