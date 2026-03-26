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

        # Bridge detection: single pass with layer tracking
        layer_changes = [f for f in result.features if f.type == FeatureType.LAYER_CHANGE]
        if len(layer_changes) > 1:
            layer_cells: dict[int, set[tuple[int, int]]] = {}
            lc_idx = 0
            cur_layer = 0

            # First pass: build cell map per layer
            for move in result.moves:
                while lc_idx < len(layer_changes) and layer_changes[lc_idx].line_number <= move.line_number:
                    cur_layer = layer_changes[lc_idx].metadata.get("layer", cur_layer)
                    lc_idx += 1
                if move.is_extrusion:
                    layer_cells.setdefault(cur_layer, set()).add((int(move.x / 2), int(move.y / 2)))

            # Second pass: find unsupported moves
            lc_idx = 0
            cur_layer = 0
            MAX_BRIDGES_PER_LAYER = 10
            bridges_this_layer = 0
            prev_layer_for_bridges = 0

            for move in result.moves:
                while lc_idx < len(layer_changes) and layer_changes[lc_idx].line_number <= move.line_number:
                    cur_layer = layer_changes[lc_idx].metadata.get("layer", cur_layer)
                    lc_idx += 1
                    if cur_layer != prev_layer_for_bridges:
                        bridges_this_layer = 0
                        prev_layer_for_bridges = cur_layer

                if (move.is_extrusion and cur_layer > 1 and move.distance > 5.0
                        and bridges_this_layer < MAX_BRIDGES_PER_LAYER):
                    cell = (int(move.x / 2), int(move.y / 2))
                    prev_cells = layer_cells.get(cur_layer - 1, set())
                    if cell not in prev_cells:
                        result.features.append(Feature(
                            type=FeatureType.BRIDGE,
                            line_number=move.line_number,
                            estimated_time=move.cumulative_time,
                            length=move.distance,
                        ))
                        bridges_this_layer += 1

        # Detect small perimeters: sequences of extrusion moves that form a
        # closed or near-closed loop with total length < threshold
        SMALL_PERIMETER_THRESHOLD = 15.0  # mm total perimeter length

        perimeter_start: int | None = None
        perimeter_length = 0.0
        perimeter_moves: list[Move] = []

        for move in result.moves:
            if move.is_extrusion:
                if perimeter_start is None:
                    perimeter_start = move.line_number
                perimeter_length += move.distance
                perimeter_moves.append(move)
            else:
                if perimeter_start is not None and perimeter_length > 0:
                    if (
                        perimeter_length < SMALL_PERIMETER_THRESHOLD
                        and len(perimeter_moves) >= 3
                    ):
                        result.features.append(
                            Feature(
                                type=FeatureType.SMALL_PERIMETER,
                                line_number=perimeter_start,
                                estimated_time=perimeter_moves[0].cumulative_time,
                                length=perimeter_length,
                            )
                        )
                    perimeter_start = None
                    perimeter_length = 0.0
                    perimeter_moves = []

        # Handle trailing perimeter at end of gcode
        if perimeter_start is not None and perimeter_length > 0:
            if (
                perimeter_length < SMALL_PERIMETER_THRESHOLD
                and len(perimeter_moves) >= 3
            ):
                result.features.append(
                    Feature(
                        type=FeatureType.SMALL_PERIMETER,
                        line_number=perimeter_start,
                        estimated_time=perimeter_moves[0].cumulative_time,
                        length=perimeter_length,
                    )
                )

        result.total_time = cumulative_time
        result.layer_count = layer_count
        return result
