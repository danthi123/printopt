"""Thermal simulation plugin."""

from __future__ import annotations

import logging

from printopt.core.gcode import GcodeParser, ParseResult, Move
from printopt.core.materials import MaterialProfile, get_profile
from printopt.core.plugin import Plugin
from printopt.plugins.thermal.grid import ThermalGrid, ThermalConfig

logger = logging.getLogger(__name__)


class ThermalPlugin(Plugin):
    name = "thermal"

    def __init__(self, material: str = "petg") -> None:
        super().__init__()
        self.material_name = material
        self.grid: ThermalGrid | None = None
        self.parse_result: ParseResult | None = None
        self.current_layer = 0
        self.warnings: list[dict] = []

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
        logger.info(f"Thermal simulation initialized for {self.material_name}")

    async def on_layer(self, layer: int, z: float) -> None:
        self.current_layer = layer
        if self.grid:
            max_grad = self.grid.get_max_gradient()
            hotspots = self.grid.get_hotspots()
            if max_grad > 15.0:
                self.warnings.append({
                    "layer": layer,
                    "type": "high_gradient",
                    "value": max_grad,
                })
                logger.warning(f"Layer {layer}: high thermal gradient {max_grad:.1f} C/mm")
            if len(hotspots) > 10:
                self.warnings.append({
                    "layer": layer,
                    "type": "hotspots",
                    "count": len(hotspots),
                })

    async def on_print_end(self) -> None:
        if self.grid:
            logger.info(f"Thermal simulation complete. {len(self.warnings)} warnings.")
        self.grid = None
        self.parse_result = None

    async def on_stop(self) -> None:
        pass

    def get_dashboard_data(self) -> dict:
        data = {
            "layer": self.current_layer,
            "warnings": self.warnings[-10:],  # last 10 warnings
            "material": self.material_name,
        }
        if self.grid:
            data["max_temp"] = float(self.grid.grid.max())
            data["max_gradient"] = self.grid.get_max_gradient()
            data["hotspot_count"] = len(self.grid.get_hotspots())
        return data
