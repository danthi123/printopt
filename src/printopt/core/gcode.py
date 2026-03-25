"""Gcode parser with geometric analysis and feature detection."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum, auto


class FeatureType(Enum):
    CORNER = auto()
    BRIDGE = auto()
    OVERHANG = auto()
    THIN_WALL = auto()
    SMALL_PERIMETER = auto()
    LAYER_CHANGE = auto()
    SPEED_CHANGE = auto()


@dataclass
class Move:
    line_number: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    e: float = 0.0
    feedrate: float = 0.0
    is_extrusion: bool = False
    distance: float = 0.0
    direction: float = 0.0
    estimated_time: float = 0.0
    cumulative_time: float = 0.0


@dataclass
class Feature:
    type: FeatureType
    line_number: int
    estimated_time: float = 0.0
    angle: float = 0.0
    length: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    moves: list[Move] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)
    total_time: float = 0.0
    layer_count: int = 0


_GCODE_RE = re.compile(r"([GXYZEFMST])(-?\d+\.?\d*)", re.IGNORECASE)


class GcodeParser:
    def __init__(self, corner_threshold: float = 45.0) -> None:
        self.corner_threshold = corner_threshold

    def parse(self, gcode: str) -> ParseResult:
        result = ParseResult()
        state_x, state_y, state_z, state_e = 0.0, 0.0, 0.0, 0.0
        state_f = 1500.0
        cumulative_time = 0.0
        current_z = 0.0
        layer_count = 0

        for line_num, line in enumerate(gcode.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(";"):
                continue

            params = {}
            for match in _GCODE_RE.finditer(line):
                params[match.group(1).upper()] = float(match.group(2))

            g_code = params.get("G")
            if g_code not in (0, 1):
                continue

            new_x = params.get("X", state_x)
            new_y = params.get("Y", state_y)
            new_z = params.get("Z", state_z)
            new_e = params.get("E", state_e)
            new_f = params.get("F", state_f)

            dx = new_x - state_x
            dy = new_y - state_y
            dist = math.hypot(dx, dy)
            is_extrusion = new_e > state_e and dist > 0.01
            direction = math.atan2(dy, dx) if dist > 0.01 else 0.0

            speed_mm_s = new_f / 60.0
            move_time = dist / speed_mm_s if speed_mm_s > 0 and dist > 0 else 0.0
            cumulative_time += move_time

            if new_z != current_z and new_z > current_z:
                layer_count += 1
                result.features.append(Feature(
                    type=FeatureType.LAYER_CHANGE,
                    line_number=line_num,
                    estimated_time=cumulative_time,
                    metadata={"z": new_z, "layer": layer_count},
                ))
                current_z = new_z

            move = Move(
                line_number=line_num,
                x=new_x, y=new_y, z=new_z, e=new_e,
                feedrate=new_f, is_extrusion=is_extrusion,
                distance=dist, direction=direction,
                estimated_time=move_time, cumulative_time=cumulative_time,
            )
            result.moves.append(move)
            state_x, state_y, state_z, state_e, state_f = new_x, new_y, new_z, new_e, new_f

        extrusions = [m for m in result.moves if m.is_extrusion]
        for i in range(1, len(extrusions)):
            prev = extrusions[i - 1]
            curr = extrusions[i]
            angle_diff = abs(math.degrees(curr.direction - prev.direction))
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            if angle_diff >= self.corner_threshold:
                result.features.append(Feature(
                    type=FeatureType.CORNER,
                    line_number=curr.line_number,
                    estimated_time=curr.cumulative_time,
                    angle=angle_diff,
                ))

        result.total_time = cumulative_time
        result.layer_count = layer_count
        return result
