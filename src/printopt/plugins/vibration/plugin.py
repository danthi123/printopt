"""Vibration analysis plugin."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from printopt.core.plugin import Plugin
from printopt.plugins.vibration.analysis import (
    compute_psd,
    find_resonance_peaks,
    evaluate_shapers,
    ShaperResult,
    ResonancePeak,
)

logger = logging.getLogger(__name__)


class VibrationPlugin(Plugin):
    name = "vibration"

    def __init__(self) -> None:
        super().__init__()
        self.results: dict = {}
        self.position_results: dict[str, dict] = {}  # key: "x_120_120" -> results
        self._results_path: Path | None = None

    async def on_start(self) -> None:
        # Try to load cached results
        config_dir = Path.home() / ".config" / "printopt"
        self._results_path = config_dir / "vibration_results.json"
        if self._results_path.exists():
            try:
                self.results = json.loads(self._results_path.read_text())
                logger.info("Loaded cached vibration results")
            except Exception:
                pass

    async def on_stop(self) -> None:
        pass

    def store_results(
        self,
        axis: str,
        peaks: list[ResonancePeak],
        shapers: list[ShaperResult],
        freqs: list[float],
        psd: list[float],
        custom_a: list[float] | None = None,
        custom_t: list[float] | None = None,
    ) -> None:
        """Store analysis results for dashboard and persistence."""
        self.results[axis] = {
            "peaks": [
                {"frequency": p.frequency, "amplitude": p.amplitude, "prominence": p.prominence}
                for p in peaks
            ],
            "shapers": [
                {
                    "shaper_type": s.shaper_type,
                    "frequency": round(s.frequency, 1),
                    "remaining_vibration": round(s.remaining_vibration, 4),
                    "max_accel_loss": round(s.max_accel_loss, 4),
                }
                for s in shapers[:5]  # top 5 recommendations
            ],
            "best": {
                "shaper_type": shapers[0].shaper_type,
                "frequency": round(shapers[0].frequency, 1),
            } if shapers else None,
            "psd_freqs": [round(f, 1) for f in freqs[::4]],  # downsample for dashboard
            "psd_values": [round(float(v), 6) for v in psd[::4]],
        }
        # Store custom shaper coefficients if present
        if custom_a and custom_t:
            self.results[axis]["custom_a"] = [round(a, 6) for a in custom_a]
            self.results[axis]["custom_t"] = [round(t, 6) for t in custom_t]

        # Save to disk
        if self._results_path:
            self._results_path.parent.mkdir(parents=True, exist_ok=True)
            self._results_path.write_text(json.dumps(self.results, indent=2))
            logger.info("Vibration results saved for %s axis", axis)

    def store_position_result(
        self, axis: str, x: float, y: float,
        peaks: list[ResonancePeak], shapers: list[ShaperResult],
    ) -> None:
        """Store per-position resonance results for the resonance map."""
        key = f"{axis}_{int(x)}_{int(y)}"
        self.position_results[key] = {
            "axis": axis,
            "x": x,
            "y": y,
            "peaks": [
                {"frequency": p.frequency, "amplitude": p.amplitude}
                for p in peaks
            ],
            "best": {
                "shaper_type": shapers[0].shaper_type,
                "frequency": shapers[0].frequency,
            } if shapers else None,
        }

    def get_dashboard_data(self) -> dict:
        data = {"results": {}}
        for axis in ("x", "y"):
            if axis in self.results:
                r = self.results[axis]
                data["results"][axis] = {
                    "peaks": r.get("peaks", []),
                    "best": r.get("best"),
                    "shapers": r.get("shapers", []),
                    "psd_freqs": r.get("psd_freqs", []),
                    "psd_values": r.get("psd_values", []),
                }
        data["position_results"] = self.position_results
        return data
