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
        self._compensated_lines: set[tuple[int, str]] = set()
        self._schedule: dict[int, list[Compensation]] = {}
        self._scheduled_lines: list[int] = []
        self._schedule_idx: int = 0
        self._needs_restore: bool = False

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
        self._compensated_lines: set[tuple[int, str]] = set()
        self._needs_restore = False

        # Pre-compute all compensations indexed by line number
        self._schedule = {}
        all_comps = self.compensator.compute_compensations(
            self.parse_result.features, current_time=0, lookahead_seconds=999999
        )
        for comp in all_comps:
            self._schedule.setdefault(comp.line_number, []).append(comp)
        self._scheduled_lines = sorted(self._schedule.keys())
        self._schedule_idx = 0

        logger.info(
            "Parsed %s: %d moves, %d features, %.1fs estimated, %d compensations across %d lines",
            filename, len(self.parse_result.moves),
            len(self.parse_result.features), self.parse_result.total_time,
            len(all_comps), len(self._scheduled_lines),
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
        # Kill switch restore: send reset commands if pending
        if self._needs_restore and self._moonraker:
            self._needs_restore = False
            try:
                await self._moonraker.inject("M220 S100")
                await self._moonraker.inject("M221 S100")
                await self._moonraker.inject(
                    f"SET_PRESSURE_ADVANCE ADVANCE={self.compensator.baseline_pa:.4f}"
                )
            except Exception:
                pass

        if not self.parse_result or not self._scheduled_lines:
            return

        if self._schedule_idx >= len(self._scheduled_lines):
            return

        total_moves = len(self.parse_result.moves)
        if total_moves == 0:
            return

        current_move_idx = int(self._current_progress / 100.0 * total_moves)
        current_line = self.parse_result.moves[
            min(current_move_idx, total_moves - 1)
        ].line_number

        # Inject all scheduled compensations up to current line + lookahead
        # Lookahead scales with total moves — 1% of the file at a time
        lookahead_lines = max(1000, total_moves // 50)

        skipped = 0
        while self._schedule_idx < len(self._scheduled_lines):
            sched_line = self._scheduled_lines[self._schedule_idx]
            if sched_line > current_line + lookahead_lines:
                break
            if sched_line < current_line - 50:
                # Already passed this line, skip
                self._schedule_idx += 1
                skipped += 1
                continue

            comps = self._schedule[sched_line]
            # Skip M220/M221 if thermal plugin is actively managing them
            thermal = self._thermal_plugin
            thermal_has_speed = thermal and hasattr(thermal, '_speed_adjusted') and thermal._speed_adjusted
            thermal_has_fan = thermal and hasattr(thermal, '_fan_adjusted') and thermal._fan_adjusted

            for comp in comps:
                comp_key = (comp.line_number, comp.value)
                if comp_key in self._compensated_lines:
                    continue
                # Defer to thermal plugin when it has active adjustments
                if comp.type == "M220" and thermal_has_speed:
                    continue
                if comp.type in ("M221", "SET_FAN_SPEED") and thermal_has_fan:
                    continue
                self._compensated_lines.add(comp_key)
                if self._moonraker and not self._kill:
                    try:
                        await self._inject_with_retry(comp.value)
                        self.total_adjustments += 1
                        self._log_entry(comp)
                    except Exception as e:
                        logger.warning("Failed to inject: %s", e)

            self._schedule_idx += 1

        if skipped > 0:
            logger.info("Flow: skipped %d, idx=%d/%d, progress=%.1f%%, adjustments=%d",
                        skipped, self._schedule_idx, len(self._scheduled_lines),
                        self._current_progress, self.total_adjustments)

        # Apply thermal adjustments if available
        if self._thermal_plugin:
            await self._apply_thermal_compensations()

        # Update active compensations for dashboard
        self.active_compensations = []
        for i in range(
            self._schedule_idx,
            min(self._schedule_idx + 5, len(self._scheduled_lines)),
        ):
            line = self._scheduled_lines[i]
            self.active_compensations.extend(self._schedule[line])

        # Feedback loop: periodically verify actual speed/flow state
        if (
            self.total_adjustments > 0
            and self.total_adjustments % 20 == 0
            and self._moonraker
        ):
            try:
                result = await self._moonraker.query(
                    "printer.objects.query",
                    {"objects": {"gcode_move": ["speed_factor", "extrude_factor"]}},
                )
                status = result.get("status", {}).get("gcode_move", {})
                actual_speed = status.get("speed_factor", 1.0)
                actual_flow = status.get("extrude_factor", 1.0)
                logger.info(
                    "Feedback: speed=%.0f%%, flow=%.0f%%",
                    actual_speed * 100,
                    actual_flow * 100,
                )
            except Exception:
                pass

    async def _apply_thermal_compensations(self) -> None:
        """Apply thermal adjustments from the thermal plugin if available."""
        if not self._thermal_plugin or not self._thermal_plugin.grid:
            return
        if not self.parse_result:
            return

        total_moves = len(self.parse_result.moves)
        if total_moves == 0:
            return

        current_move_idx = int(self._current_progress / 100.0 * total_moves)

        from printopt.plugins.flow.thermal_bridge import ThermalFlowBridge

        bridge = ThermalFlowBridge(
            glass_transition=self._thermal_plugin.grid.config.glass_transition
        )
        heatmap = self._thermal_plugin.grid.get_heatmap()
        resolution = self._thermal_plugin.grid.config.resolution

        lookahead_moves = self.parse_result.moves[
            current_move_idx : current_move_idx + 20
        ]
        for move in lookahead_moves:
            if move.is_extrusion:
                tc = bridge.evaluate_position(
                    heatmap, move.x, move.y, resolution
                )
                if tc.speed_factor != 1.0:
                    comp = Compensation(
                        type="M220",
                        value=f"M220 S{int(tc.speed_factor * 100)}",
                        feature_type=FeatureType.LAYER_CHANGE,
                        line_number=move.line_number,
                        estimated_time=move.cumulative_time,
                    )
                    if self._moonraker and not self._kill:
                        comp_key = (comp.line_number, comp.value)
                        if comp_key not in self._compensated_lines:
                            self._compensated_lines.add(comp_key)
                            try:
                                await self._inject_with_retry(comp.value)
                                self.total_adjustments += 1
                                self._log_entry(comp)
                            except Exception as e:
                                logger.warning("Failed to inject thermal: %s", e)
                    break  # One thermal adjustment per cycle

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
        self._needs_restore = False

    async def on_stop(self) -> None:
        pass

    def kill(self) -> None:
        self._kill = True
        self._needs_restore = True
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
