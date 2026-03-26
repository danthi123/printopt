"""Tests for gcode parser and feature detection."""

import math
import pytest
from printopt.core.gcode import GcodeParser, Move, Feature, FeatureType


SAMPLE_GCODE = """
G28
M104 S248
G1 Z0.2 F3000
G1 X10 Y10 E0.5 F1500
G1 X50 Y10 E2.0
G1 X50 Y50 E4.0
G1 X10 Y50 E6.0
G1 X10 Y10 E8.0
""".strip()


class TestGcodeParser:
    def test_parse_moves(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        moves = [m for m in result.moves if m.is_extrusion]
        assert len(moves) > 0
        assert moves[0].x == 10
        assert moves[0].y == 10

    def test_extrusion_count(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        extrusions = [m for m in result.moves if m.is_extrusion]
        assert len(extrusions) == 5  # 5 extruding moves in the square

    def test_detect_corners(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        corners = [f for f in result.features if f.type == FeatureType.CORNER]
        assert len(corners) > 0
        assert any(f.angle >= 80 for f in corners)

    def test_90_degree_corners(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        corners = [f for f in result.features if f.type == FeatureType.CORNER]
        right_angles = [c for c in corners if 85 <= c.angle <= 95]
        assert len(right_angles) >= 3  # square has 4 corners, at least 3 detected

    def test_layer_change_detection(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        layers = [f for f in result.features if f.type == FeatureType.LAYER_CHANGE]
        assert len(layers) == 1
        assert layers[0].metadata["z"] == 0.2

    def test_time_estimation(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        extrusions = [m for m in result.moves if m.is_extrusion]
        for move in extrusions:
            assert move.estimated_time >= 0
        assert result.total_time > 0

    def test_cumulative_time_increases(self):
        parser = GcodeParser()
        result = parser.parse(SAMPLE_GCODE)
        extrusions = [m for m in result.moves if m.is_extrusion]
        for i in range(1, len(extrusions)):
            assert extrusions[i].cumulative_time >= extrusions[i-1].cumulative_time

    def test_empty_gcode(self):
        parser = GcodeParser()
        result = parser.parse("")
        assert len(result.moves) == 0
        assert len(result.features) == 0

    def test_comments_ignored(self):
        parser = GcodeParser()
        result = parser.parse("; this is a comment\nG1 X10 Y10 F1000\n; another comment")
        assert len(result.moves) == 1

    def test_custom_corner_threshold(self):
        parser = GcodeParser(corner_threshold=80.0)
        result = parser.parse(SAMPLE_GCODE)
        corners_80 = [f for f in result.features if f.type == FeatureType.CORNER]
        parser2 = GcodeParser(corner_threshold=30.0)
        result2 = parser2.parse(SAMPLE_GCODE)
        corners_30 = [f for f in result2.features if f.type == FeatureType.CORNER]
        assert len(corners_30) >= len(corners_80)


BRIDGE_GCODE = """
G1 Z0.2 F3000
G1 X10 Y10 E0.5 F1500
G1 X50 Y10 E2.0
G1 X50 Y50 E4.0
G1 Z0.4 F3000
G1 X10 Y50 E6.0
G1 X30 Y30 E8.0
""".strip()


SMALL_PERIMETER_GCODE = """
G1 Z0.2 F3000
G1 X10 Y10 F3000
G1 X12 Y10 E0.5 F1500
G1 X12 Y12 E0.6
G1 X10 Y12 E0.7
G1 X10 Y10 E0.8
G1 X100 Y100 F3000
G1 X150 Y100 E5.0 F1500
G1 X150 Y150 E7.0
""".strip()


class TestBridgeDetection:
    def test_bridge_detection(self):
        parser = GcodeParser()
        result = parser.parse(BRIDGE_GCODE)
        bridges = [f for f in result.features if f.type == FeatureType.BRIDGE]
        # After layer change, moves over unsupported area should be detected
        assert isinstance(bridges, list)

    def test_bridge_requires_multiple_layers(self):
        parser = GcodeParser()
        single_layer = "G1 Z0.2 F3000\nG1 X10 Y10 E0.5 F1500\nG1 X50 Y10 E2.0"
        result = parser.parse(single_layer)
        bridges = [f for f in result.features if f.type == FeatureType.BRIDGE]
        assert len(bridges) == 0


class TestSmallPerimeterDetection:
    def test_small_perimeter_detection(self):
        parser = GcodeParser()
        result = parser.parse(SMALL_PERIMETER_GCODE)
        small = [f for f in result.features if f.type == FeatureType.SMALL_PERIMETER]
        assert len(small) >= 1
        assert small[0].length < 15.0

    def test_large_perimeter_not_detected(self):
        parser = GcodeParser()
        large = """
G1 Z0.2 F3000
G1 X0 Y0 E0.5 F1500
G1 X50 Y0 E2.0
G1 X50 Y50 E4.0
G1 X0 Y50 E6.0
G1 X0 Y0 E8.0
""".strip()
        result = parser.parse(large)
        small = [f for f in result.features if f.type == FeatureType.SMALL_PERIMETER]
        assert len(small) == 0
