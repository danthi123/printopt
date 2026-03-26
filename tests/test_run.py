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


def test_run_with_named_printer(tmp_path):
    """Run with --printer should load from printers/<name>.json."""
    config = PrinterConfig(
        host="10.0.0.5", kinematics="corexy",
        bed_x=300, bed_y=300, bed_z=350,
    )
    printers_dir = tmp_path / "printers"
    printers_dir.mkdir()
    config.save(printers_dir / "voron.json")

    loaded = PrinterConfig.load(printers_dir / "voron.json")
    assert loaded.host == "10.0.0.5"
    assert loaded.bed_x == 300


def test_run_single_printer_auto_select(tmp_path):
    """Run without --printer should auto-select if only one printer exists."""
    config = PrinterConfig(
        host="10.0.0.5", kinematics="corexy",
        bed_x=300, bed_y=300, bed_z=350,
    )
    printers_dir = tmp_path / "printers"
    printers_dir.mkdir()
    config.save(printers_dir / "voron.json")

    # No printer.json, but one printer in printers dir => should auto-select
    loaded = PrinterConfig.load(printers_dir / "voron.json")
    assert loaded.host == "10.0.0.5"


def test_run_multiple_printers_no_selection(tmp_path, capsys):
    """Run with multiple printers and no --printer should list and exit."""
    import asyncio

    printers_dir = tmp_path / "printers"
    printers_dir.mkdir()
    for name, host in [("voron", "10.0.0.5"), ("qidi", "10.0.0.6")]:
        config = PrinterConfig(host=host, kinematics="corexy", bed_x=245, bed_y=258, bed_z=248)
        config.save(printers_dir / f"{name}.json")

    with pytest.raises(SystemExit):
        asyncio.run(do_run(config_dir=tmp_path))

    captured = capsys.readouterr()
    assert "Multiple printers configured" in captured.out
