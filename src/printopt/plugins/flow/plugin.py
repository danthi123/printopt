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
        self._compensated_lines: set[int] = set()

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
        self._compensated_lines = set()
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
        if not self.parse_result or not self.parse_result.features:
            return

        # Map progress (0-100%) to approximate move index in the gcode.
        # Moonraker progress is file-position-based (bytes), which is roughly
        # proportional to line count, not time.
        total_moves = len(self.parse_result.moves)
        if total_moves == 0:
            return

        current_move_idx = int(self._current_progress / 100.0 * total_moves)

        # Find the current line number from the move index
        if current_move_idx < total_moves:
            current_line = self.parse_result.moves[current_move_idx].line_number
        else:
            current_line = self.parse_result.moves[-1].line_number

        # Lookahead: find features within the next N lines
        lookahead_lines = 500  # ~5 seconds of gcode at typical speeds

        compensations = []
        for feature in self.parse_result.features:
            if feature.line_number < current_line:
                continue
            if feature.line_number > current_line + lookahead_lines:
                break

            comps = self.compensator._compensate_feature(feature)
            compensations.extend(comps)

        # Apply thermal adjustments if available
        if self._thermal_plugin and self._thermal_plugin.grid:
            from printopt.plugins.flow.thermal_bridge import ThermalFlowBridge
            bridge = ThermalFlowBridge(
                glass_transition=self._thermal_plugin.grid.config.glass_transition
            )
            heatmap = self._thermal_plugin.grid.get_heatmap()
            resolution = self._thermal_plugin.grid.config.resolution

            lookahead_moves = self.parse_result.moves[current_move_idx:current_move_idx+20]
            for move in lookahead_moves:
                if move.is_extrusion:
                    tc = bridge.evaluate_position(heatmap, move.x, move.y, resolution)
                    if tc.speed_factor != 1.0:
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
        if self._moonraker and compensations and not self._kill:
            for comp in compensations:
                if comp.line_number in self._compensated_lines:
                    continue
                self._compensated_lines.add(comp.line_number)
                try:
                    await self._inject_with_retry(comp.value)
                    self.total_adjustments += 1
                    self._log_entry(comp)
                except Exception as e:
                    logger.warning("Failed to inject compensation after retries: %s", e)

    async def _inject_with_retry(self, gcode: str, max_retries: int = 3) -> None:
        """Inject gcode with retry on timeout."""
        for attempt in range(max_retries):
            try:
                await self._moonraker.inject(gcode)
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug("Inject retry %d for '%s': %s", attempt + 1, gcode, e)
                    await asyncio.sleep(0.5)
                else:
                    raise

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
        features_ahead = 0
        if self.parse_result and self.parse_result.moves:
            total_moves = len(self.parse_result.moves)
            current_idx = int(self._current_progress / 100.0 * total_moves)
            current_line = self.parse_result.moves[min(current_idx, total_moves - 1)].line_number
            features_ahead = sum(1 for f in self.parse_result.features if f.line_number > current_line)

        return {
            "enabled": self.enabled and not self._kill,
            "total_adjustments": self.total_adjustments,
            "active_compensations": [
                {"type": c.type, "value": c.value} for c in self.active_compensations
            ],
            "features_ahead": features_ahead,
            "state": self._print_state,
            "filename": self._current_filename,
            "progress": self._current_progress,
            "log": self._log[-10:],
        }
