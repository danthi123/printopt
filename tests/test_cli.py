"""Smoke test for CLI entry point."""

import subprocess
import sys

from printopt.core.printer import PrinterConfig


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "printopt.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "printopt" in result.stdout


def test_cli_no_args():
    result = subprocess.run(
        [sys.executable, "-m", "printopt.cli"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1


def test_printer_list_empty(tmp_path, capsys):
    """printer list with no printers should say none configured."""
    from printopt.cli import do_printer_list
    do_printer_list(config_dir=tmp_path)
    captured = capsys.readouterr()
    assert "No printers configured" in captured.out


def test_printer_list_shows_printers(tmp_path, capsys):
    """printer list should display all saved printer profiles."""
    from printopt.cli import do_printer_list

    printers_dir = tmp_path / "printers"
    printers_dir.mkdir()

    config = PrinterConfig(
        host="192.168.0.248", kinematics="corexy",
        bed_x=245, bed_y=258, bed_z=248,
    )
    config.save(printers_dir / "qidi.json")

    config2 = PrinterConfig(
        host="10.0.0.5", kinematics="corexy",
        bed_x=300, bed_y=300, bed_z=350,
    )
    config2.save(printers_dir / "voron.json")

    do_printer_list(config_dir=tmp_path)
    captured = capsys.readouterr()
    assert "qidi" in captured.out
    assert "voron" in captured.out
    assert "192.168.0.248" in captured.out
    assert "10.0.0.5" in captured.out
