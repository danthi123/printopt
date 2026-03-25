"""Tests for printer model auto-discovery."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from printopt.core.printer import PrinterConfig, discover_printer


MOCK_SERVER_INFO = {
    "klippy_state": "ready",
    "klippy_connected": True,
}

MOCK_PRINTER_CONFIG = {
    "config": {
        "stepper_x": {"position_max": "245", "position_min": "-5.5"},
        "stepper_y": {"position_max": "258", "position_min": "-4.5"},
        "stepper_z": {"position_max": "248"},
        "extruder": {"nozzle_diameter": "0.400", "filament_diameter": "1.750"},
        "printer": {"kinematics": "corexy", "max_velocity": "600"},
        "adxl345": {"cs_pin": "gpio13"},
        "input_shaper": {
            "shaper_type_x": "ei",
            "shaper_freq_x": "81.6",
            "shaper_type_y": "ei",
            "shaper_freq_y": "39.8",
        },
        "heater_bed": {"max_temp": "120"},
    }
}


class TestPrinterConfig:
    def test_from_moonraker_data(self):
        config = PrinterConfig.from_moonraker_data(MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG)
        assert config.kinematics == "corexy"
        assert config.bed_x == 245
        assert config.bed_y == 258
        assert config.bed_z == 248
        assert config.nozzle_diameter == 0.4
        assert config.has_accelerometer is True
        assert config.shaper_x == ("ei", 81.6)
        assert config.shaper_y == ("ei", 39.8)

    def test_no_accelerometer(self):
        cfg = {"config": {
            "stepper_x": {"position_max": "200"},
            "stepper_y": {"position_max": "200"},
            "stepper_z": {"position_max": "200"},
            "printer": {"kinematics": "corexy"},
        }}
        config = PrinterConfig.from_moonraker_data({}, cfg)
        assert config.has_accelerometer is False

    def test_save_and_load(self, tmp_path):
        config = PrinterConfig.from_moonraker_data(MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG)
        path = tmp_path / "printer.json"
        config.save(path)
        loaded = PrinterConfig.load(path)
        assert loaded.kinematics == config.kinematics
        assert loaded.bed_x == config.bed_x
        assert loaded.has_accelerometer == config.has_accelerometer
        assert loaded.shaper_x == config.shaper_x


@pytest.mark.asyncio
async def test_discover_printer():
    mock_client = AsyncMock()
    mock_client.host = "192.168.0.248"
    mock_client.query = AsyncMock(side_effect=[MOCK_SERVER_INFO, MOCK_PRINTER_CONFIG])
    config = await discover_printer(mock_client)
    assert config.kinematics == "corexy"
    assert config.bed_x == 245
    assert config.host == "192.168.0.248"
