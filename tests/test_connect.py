"""Tests for CLI connect command."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from printopt.core.printer import PrinterConfig


MOCK_SERVER_INFO = {"klippy_state": "ready"}
MOCK_PRINTER_CONFIG = {
    "config": {
        "stepper_x": {"position_max": "245"},
        "stepper_y": {"position_max": "258"},
        "stepper_z": {"position_max": "248"},
        "extruder": {"nozzle_diameter": "0.400"},
        "printer": {"kinematics": "corexy", "max_velocity": "600"},
        "adxl345": {"cs_pin": "gpio13"},
        "input_shaper": {
            "shaper_type_x": "ei", "shaper_freq_x": "81.6",
            "shaper_type_y": "ei", "shaper_freq_y": "39.8",
        },
    }
}


def test_connect_saves_config(tmp_path):
    """Connect command should discover printer and save config."""
    from printopt.cli import do_connect

    mock_client = AsyncMock()
    mock_client.host = "192.168.0.248"
    mock_client.query = AsyncMock(side_effect=[MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG])

    config = asyncio.run(do_connect("192.168.0.248", config_dir=tmp_path, _client=mock_client))

    assert config.kinematics == "corexy"
    assert config.bed_x == 245
    assert config.host == "192.168.0.248"

    # Verify saved to disk (both default and printers subdir)
    saved = PrinterConfig.load(tmp_path / "printer.json")
    assert saved.host == "192.168.0.248"
    assert saved.bed_x == 245

    # Verify saved to printers subdir with default name
    saved_named = PrinterConfig.load(tmp_path / "printers" / "192-168-0-248.json")
    assert saved_named.host == "192.168.0.248"


def test_connect_with_name(tmp_path):
    """Connect with --name should save to printers/<name>.json."""
    from printopt.cli import do_connect

    mock_client = AsyncMock()
    mock_client.host = "192.168.0.248"
    mock_client.query = AsyncMock(side_effect=[MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG])

    config = asyncio.run(do_connect("192.168.0.248", name="myprinter", config_dir=tmp_path, _client=mock_client))

    assert config.host == "192.168.0.248"
    assert (tmp_path / "printers" / "myprinter.json").exists()
    saved = PrinterConfig.load(tmp_path / "printers" / "myprinter.json")
    assert saved.host == "192.168.0.248"


def test_connect_creates_config_dir(tmp_path):
    """Connect should create the config directory if it doesn't exist."""
    from printopt.cli import do_connect

    config_dir = tmp_path / "subdir" / "printopt"
    mock_client = AsyncMock()
    mock_client.host = "192.168.0.248"
    mock_client.query = AsyncMock(side_effect=[MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG])

    config = asyncio.run(do_connect("192.168.0.248", config_dir=config_dir, _client=mock_client))
    assert config_dir.exists()
    assert (config_dir / "printer.json").exists()
    assert (config_dir / "printers").exists()
