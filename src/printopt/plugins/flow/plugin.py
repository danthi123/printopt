"""Predictive flow compensation plugin."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from printopt.core.gcode import GcodeParser, ParseResult, FeatureType
from printopt.core.plugin import Plugin
from printopt.plugins.flow.compensator import FlowCompensator, Compensation

logger = logging.getLogger(__name__)


class FlowPlugin(Plugin):
    name = "flow"

    def __init__(self) -> None:
        super().__init__()
        self.compensator = FlowCompensator()
        self.parse_result: ParseResult | None = None
        self.active_compensations: list[Compensation] = []
        self.total_adjustments = 0
        self._kill = False
        self._moonraker = None  # Set externally for gcode injection
        self._thermal_plugin = None  # Set externally if thermal plugin is active
        self._print_start_time: float = 0
        self._current_progress: float = 0
        self._print_state: str = "standby"
        self._previous_state: str = "standby"
        self._log: list[dict] = []
        self._current_filename: str = ""

    async def on_start(self) -> None:
        logger.info("Flow compensation plugin started")

    async def on_print_start(self, filename: str, gcode: str) -> None:
        parser = GcodeParser()
        self.parse_result = parser.parse(gcode)
        self.active_compensations = []
        self.total_adjustments = 0
        self._kill = False
        self._print_start_time = time.monotonic()
        self._current_progress = 0
        self._current_filename = filename
        self._log = []
        logger.info(
            "Parsed %s: %d moves, %d features, %.1fs estimated",
            filename, len(self.parse_result.moves),
            len(self.parse_result.features), self.parse_result.total_time,
        )

    async def on_status_update(self, status: dict) -> None:
        new_state = status.get("state", self._print_state)
        progress = status.get("progress", self._current_progress)
        filename = status.get("filename", "")

        # Detect print start
        if new_state == "printing" and self._previous_state != "printing":
            if filename and not self.parse_result:
                logger.info("Print started: %s (gcode not yet loaded)", filename)
                self._print_start_time = time.monotonic()
                self._current_filename = filename

        self._previous_state = self._print_state
        self._print_state = new_state
        self._current_progress = progress

        # During active printing, compute and apply compensations
        if self._print_state == "printing" and self.parse_result and not self._kill:
            await self._apply_compensations()

    async def _apply_compensations(self) -> None:
        if not self.parse_result or self.parse_result.total_time <= 0:
            return

        # Estimate current time in the gcode based on progress
        current_time = self._current_progress / 100.0 * self.parse_result.total_time

        compensations = self.compensator.compute_compensations(
            self.parse_result.features,
            current_time=current_time,
            lookahead_seconds=5.0,
        )

        # Apply thermal adjustments if available
        if self._thermal_plugin and self._thermal_plugin.grid:
            from printopt.plugins.flow.thermal_bridge import ThermalFlowBridge
            bridge = ThermalFlowBridge(
                glass_transition=self._thermal_plugin.grid.config.glass_transition
            )
            heatmap = self._thermal_plugin.grid.get_heatmap()
            resolution = self._thermal_plugin.grid.config.resolution

            # Check thermal state at upcoming positions
            if self.parse_result:
                current_idx = int(self._current_progress / 100.0 * len(self.parse_result.moves))
                lookahead_moves = self.parse_result.moves[current_idx:current_idx+20]
                for move in lookahead_moves:
                    if move.is_extrusion:
                        tc = bridge.evaluate_position(heatmap, move.x, move.y, resolution)
                        if tc.speed_factor != 1.0:
                            # Only add thermal compensation if significant
                            compensations.append(Compensation(
                                type="M220",
                                value=f"M220 S{int(tc.speed_factor * 100)}",
                                feature_type=FeatureType.LAYER_CHANGE,
                                line_number=move.line_number,
                                estimated_time=move.cumulative_time,
                            ))
                            break  # One thermal adjustment per cycle

        self.active_compensations = compensations

        # Inject compensations via Moonraker if available
        if self._moonraker and compensations:
            for comp in compensations:
                try:
                    await self._moonraker.inject(comp.value)
                    self.total_adjustments += 1
                    self._log_entry(comp)
                except Exception as e:
                    logger.warning("Failed to inject compensation: %s", e)

    def _log_entry(self, comp: Compensation) -> None:
        entry = {
            "time": time.time(),
            "type": comp.type,
            "value": comp.value,
            "feature": comp.feature_type.name,
            "line": comp.line_number,
        }
        self._log.append(entry)
        if len(self._log) > 50:
            self._log = self._log[-50:]

    async def on_print_end(self) -> None:
        logger.info("Print ended. Total adjustments made: %d", self.total_adjustments)
        self.parse_result = None
        self.active_compensations = []

    async def on_stop(self) -> None:
        pass

    def kill(self) -> None:
        self._kill = True
        self.active_compensations = []
        logger.warning("Flow compensation killed")

    def get_dashboard_data(self) -> dict:
        return {
            "enabled": self.enabled and not self._kill,
            "total_adjustments": self.total_adjustments,
            "active_compensations": [
                {"type": c.type, "value": c.value} for c in self.active_compensations
            ],
            "features_ahead": (
                len([f for f in (self.parse_result.features if self.parse_result else [])
                     if f.estimated_time > (self._current_progress / 100.0 * (self.parse_result.total_time if self.parse_result else 1))])
            ),
            "state": self._print_state,
            "filename": self._current_filename,
            "progress": self._current_progress,
            "log": self._log[-10:],
        }
