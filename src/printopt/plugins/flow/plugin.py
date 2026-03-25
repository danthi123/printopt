"""Predictive flow compensation plugin."""

from __future__ import annotations

import logging
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

    async def on_start(self) -> None:
        logger.info("Flow compensation plugin started")

    async def on_print_start(self, filename: str, gcode: str) -> None:
        parser = GcodeParser()
        self.parse_result = parser.parse(gcode)
        self.active_compensations = []
        self.total_adjustments = 0
        self._kill = False
        logger.info(
            f"Parsed {filename}: {len(self.parse_result.moves)} moves, "
            f"{len(self.parse_result.features)} features, "
            f"{self.parse_result.total_time:.1f}s estimated"
        )

    async def on_print_end(self) -> None:
        logger.info(f"Print ended. Total adjustments made: {self.total_adjustments}")
        self.parse_result = None
        self.active_compensations = []

    async def on_stop(self) -> None:
        pass

    def kill(self) -> None:
        """Emergency stop all compensation."""
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
                len(self.parse_result.features) if self.parse_result else 0
            ),
        }
