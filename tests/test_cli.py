"""Smoke test for CLI entry point."""

import subprocess
import sys


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
