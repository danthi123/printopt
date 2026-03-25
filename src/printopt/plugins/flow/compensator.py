"""Flow compensation calculator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from printopt.core.gcode import Feature, FeatureType, ParseResult


class CompensationType(Enum):
    PRESSURE_ADVANCE = auto()
    FLOW_RATE = auto()
    SPEED = auto()
    FAN_SPEED = auto()


@dataclass
class Compensation:
    """A single compensation action to inject."""
    type: str  # gcode command type
    value: str  # gcode command to send
    feature_type: FeatureType
    line_number: int
    estimated_time: float


@dataclass
class CompensationProfile:
    """Tunable compensation weights per filament type."""
    # Pressure advance multiplier for corners (1.0 = no change)
    corner_pa_boost: float = 1.3  # 30% boost
    # PA boost angle threshold (degrees)
    corner_angle_threshold: float = 60.0
    # Flow rate adjustment for bridges (fraction, e.g. 0.95 = 5% reduction)
    bridge_flow: float = 0.95
    # Bridge fan speed (0-100)
    bridge_fan: float = 70.0
    # Speed factor for thin walls (fraction)
    thin_wall_speed: float = 0.80
    # Speed factor for small perimeters
    small_perimeter_speed: float = 0.70
    # Maximum PA multiplier allowed
    max_pa_multiplier: float = 2.0
    # Maximum flow adjustment (fraction from baseline)
    max_flow_deviation: float = 0.15  # +-15%
    # Maximum speed adjustment (fraction from baseline)
    max_speed_deviation: float = 0.30  # +-30%


DEFAULT_PROFILE = CompensationProfile()


class FlowCompensator:
    """Calculates flow compensations based on upcoming gcode features."""

    def __init__(
        self,
        profile: CompensationProfile | None = None,
        baseline_pa: float = 0.04,
    ) -> None:
        self.profile = profile or DEFAULT_PROFILE
        self.baseline_pa = baseline_pa

    def compute_compensations(
        self,
        features: list[Feature],
        current_time: float,
        lookahead_seconds: float = 5.0,
    ) -> list[Compensation]:
        """Compute compensations for features within the lookahead window.

        Args:
            features: All features from the parsed gcode.
            current_time: Current estimated print time in seconds.
            lookahead_seconds: How far ahead to look (seconds).

        Returns:
            List of Compensation actions to inject, sorted by time.
        """
        window_end = current_time + lookahead_seconds
        compensations = []

        for feature in features:
            if feature.estimated_time < current_time:
                continue
            if feature.estimated_time > window_end:
                break

            comps = self._compensate_feature(feature)
            compensations.extend(comps)

        return sorted(compensations, key=lambda c: c.estimated_time)

    def _compensate_feature(self, feature: Feature) -> list[Compensation]:
        """Generate compensations for a single feature."""
        comps = []
        p = self.profile

        if feature.type == FeatureType.CORNER and feature.angle >= p.corner_angle_threshold:
            # Boost pressure advance approaching sharp corners
            boost = min(
                self.baseline_pa * p.corner_pa_boost,
                self.baseline_pa * p.max_pa_multiplier,
            )
            comps.append(Compensation(
                type="SET_PRESSURE_ADVANCE",
                value=f"SET_PRESSURE_ADVANCE ADVANCE={boost:.4f}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time,
            ))
            # Restore PA after the corner (0.2s later)
            comps.append(Compensation(
                type="SET_PRESSURE_ADVANCE",
                value=f"SET_PRESSURE_ADVANCE ADVANCE={self.baseline_pa:.4f}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time + 0.2,
            ))

        elif feature.type == FeatureType.BRIDGE:
            # Reduce flow and boost fan for bridges
            flow_pct = max(
                int(p.bridge_flow * 100),
                int((1.0 - p.max_flow_deviation) * 100),
            )
            comps.append(Compensation(
                type="M221",
                value=f"M221 S{flow_pct}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time,
            ))
            comps.append(Compensation(
                type="SET_FAN_SPEED",
                value=f"M106 S{int(p.bridge_fan * 255 / 100)}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time,
            ))

        elif feature.type == FeatureType.THIN_WALL:
            speed_pct = max(
                int(p.thin_wall_speed * 100),
                int((1.0 - p.max_speed_deviation) * 100),
            )
            comps.append(Compensation(
                type="M220",
                value=f"M220 S{speed_pct}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time,
            ))

        elif feature.type == FeatureType.SMALL_PERIMETER:
            speed_pct = max(
                int(p.small_perimeter_speed * 100),
                int((1.0 - p.max_speed_deviation) * 100),
            )
            comps.append(Compensation(
                type="M220",
                value=f"M220 S{speed_pct}",
                feature_type=feature.type,
                line_number=feature.line_number,
                estimated_time=feature.estimated_time,
            ))

        return comps
