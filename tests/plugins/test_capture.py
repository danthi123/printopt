"""Tests for vibration data capture and CSV parsing."""

from unittest.mock import patch, MagicMock
import subprocess

import numpy as np
import pytest

from printopt.plugins.vibration.capture import parse_accel_csv, fetch_resonance_csv, AccelData


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

PSD_CSV_NO_HASH = """freq,psd_x,psd_y,psd_z,psd_xyz
0.0,2.614e+03,1.578e+02,1.169e+02,2.889e+03
1.6,2.920e+03,6.408e+02,4.480e+02,4.009e+03
3.2,3.100e+03,7.000e+02,5.000e+02,4.300e+03
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

    def test_parse_psd_csv_no_hash_header(self):
        """CSV with 'freq,...' header (no # prefix) should parse correctly."""
        result = parse_accel_csv(PSD_CSV_NO_HASH, axis="x")
        assert len(result.samples) == 3
        assert result.samples[0] == pytest.approx(2889.0, rel=1e-2)

    def test_parse_psd_csv_no_hash_uses_psd_xyz(self):
        """Should use psd_xyz column when header has no # prefix."""
        result = parse_accel_csv(PSD_CSV_NO_HASH, axis="y")
        # psd_xyz is used regardless of axis arg for PSD data
        assert result.samples[1] == pytest.approx(4009.0, rel=1e-2)


class TestFetchResonanceCSV:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        """SSH commands succeed and return CSV content."""
        mock_client = MagicMock()
        mock_client.host = "192.168.0.248"

        ls_result = MagicMock(
            stdout="/tmp/resonances_x_20260325_120000.csv\n",
            returncode=0,
        )
        cat_result = MagicMock(
            stdout=PSD_CSV_NO_HASH,
            returncode=0,
            stderr="",
        )

        with patch("printopt.plugins.vibration.capture.subprocess.run", side_effect=[ls_result, cat_result]):
            text = await fetch_resonance_csv(mock_client, "x")

        assert "freq" in text
        assert "2.614e+03" in text

    @pytest.mark.asyncio
    async def test_fetch_no_file(self):
        """Returns empty string when no CSV exists."""
        mock_client = MagicMock()
        mock_client.host = "192.168.0.248"

        ls_result = MagicMock(stdout="", returncode=0)

        with patch("printopt.plugins.vibration.capture.subprocess.run", return_value=ls_result):
            text = await fetch_resonance_csv(mock_client, "x")

        assert text == ""

    @pytest.mark.asyncio
    async def test_fetch_ssh_timeout(self):
        """Returns empty string on SSH timeout."""
        mock_client = MagicMock()
        mock_client.host = "192.168.0.248"

        with patch(
            "printopt.plugins.vibration.capture.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10),
        ):
            text = await fetch_resonance_csv(mock_client, "x")

        assert text == ""
