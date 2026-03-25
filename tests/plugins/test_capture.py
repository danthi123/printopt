"""Tests for vibration data capture and CSV parsing."""

import numpy as np
import pytest

from printopt.plugins.vibration.capture import parse_accel_csv, AccelData


RAW_CSV = """#time,accel_x,accel_y,accel_z
0.000312,1.23,4.56,7.89
0.000625,2.34,5.67,8.90
0.000937,3.45,6.78,9.01
0.001250,4.56,7.89,0.12
"""

PSD_CSV = """#freq,psd_x,psd_y,psd_z,psd_xyz
1.0,0.001,0.002,0.001,0.004
2.0,0.010,0.020,0.010,0.040
3.0,0.100,0.200,0.100,0.400
"""


class TestParseAccelCSV:
    def test_parse_raw_csv_x_axis(self):
        result = parse_accel_csv(RAW_CSV, axis="x")
        assert result.axis == "x"
        assert len(result.samples) == 4
        assert result.samples[0] == pytest.approx(1.23)

    def test_parse_raw_csv_y_axis(self):
        result = parse_accel_csv(RAW_CSV, axis="y")
        assert result.axis == "y"
        assert len(result.samples) == 4
        assert result.samples[0] == pytest.approx(4.56)

    def test_parse_raw_csv_sample_rate(self):
        result = parse_accel_csv(RAW_CSV, axis="x")
        assert result.sample_rate > 0

    def test_parse_psd_csv(self):
        result = parse_accel_csv(PSD_CSV, axis="x")
        assert len(result.samples) == 3
        assert result.samples[2] == pytest.approx(0.400)

    def test_parse_empty(self):
        result = parse_accel_csv("", axis="x")
        assert len(result.samples) == 0

    def test_parse_comments_only(self):
        result = parse_accel_csv("#header\n#another comment\n", axis="x")
        assert len(result.samples) == 0
