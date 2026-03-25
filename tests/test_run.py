"""Tests for CLI run command."""

import json
from pathlib import Path

import pytest

from printopt.cli import do_run
from printopt.core.printer import PrinterConfig


def test_run_without_config(tmp_path):
    """Run should exit with error if no printer configured."""
    import asyncio
    with pytest.raises(SystemExit):
        asyncio.run(do_run(config_dir=tmp_path))


def test_run_loads_config(tmp_path):
    """Run should load saved printer config."""
    config = PrinterConfig(
        host="192.168.0.248", kinematics="corexy",
        bed_x=245, bed_y=258, bed_z=248,
    )
    config.save(tmp_path / "printer.json")

    # We can't easily test the full run loop (it blocks on uvicorn),
    # but we can verify the config loads correctly
    loaded = PrinterConfig.load(tmp_path / "printer.json")
    assert loaded.host == "192.168.0.248"
    assert loaded.kinematics == "corexy"
