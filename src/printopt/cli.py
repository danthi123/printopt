"""CLI entry point for printopt."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from printopt.core.moonraker import MoonrakerClient
from printopt.core.printer import PrinterConfig, discover_printer


def get_config_dir() -> Path:
    """Return the default config directory (~/.config/printopt)."""
    return Path.home() / ".config" / "printopt"


async def do_connect(
    host: str,
    config_dir: Optional[Path] = None,
    _client: Optional[MoonrakerClient] = None,
) -> PrinterConfig:
    """Connect to a printer, discover its config, and save locally.

    Args:
        host: Printer IP or hostname.
        config_dir: Directory to save config into; defaults to ~/.config/printopt.
        _client: Optional pre-built client (for testing).

    Returns:
        The discovered PrinterConfig.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    own_client = _client is None
    client = _client or MoonrakerClient(host)

    try:
        if own_client:
            await client.connect()

        config = await discover_printer(client)

        config_dir.mkdir(parents=True, exist_ok=True)
        config.save(config_dir / "printer.json")

        return config
    finally:
        if own_client:
            await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="printopt",
        description="PC-assisted print optimization for Klipper CoreXY printers.",
    )
    subparsers = parser.add_subparsers(dest="command")

    connect_parser = subparsers.add_parser("connect", help="Connect to a printer")
    connect_parser.add_argument("host", help="Printer IP or hostname")

    run_parser = subparsers.add_parser("run", help="Start optimization daemon")
    run_parser.add_argument("--plugins", default="all", help="Comma-separated plugin list or 'all'")
    run_parser.add_argument("--port", type=int, default=8484, help="Dashboard port")
    run_parser.add_argument("--profile", default=None, help="Filament profile name")

    vib_parser = subparsers.add_parser("vibration", help="Vibration analysis")
    vib_sub = vib_parser.add_subparsers(dest="vib_command")
    analyze = vib_sub.add_parser("analyze", help="Run vibration analysis")
    analyze.add_argument("--positions", type=int, default=1, help="Number of bed positions to test")
    vib_sub.add_parser("report", help="View analysis results")
    vib_sub.add_parser("apply", help="Apply optimized input shaper config")

    prof_parser = subparsers.add_parser("profile", help="Filament profiles")
    prof_sub = prof_parser.add_subparsers(dest="prof_command")
    prof_sub.add_parser("list", help="List saved profiles")
    create = prof_sub.add_parser("create", help="Create a new profile")
    create.add_argument("name", help="Profile name")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "connect":
        config = asyncio.run(do_connect(args.host))
        print(f"Connected to {config.host} ({config.kinematics}, "
              f"bed {config.bed_x}x{config.bed_y}x{config.bed_z})")
        return

    print(f"printopt: {args.command} (not yet implemented)")


if __name__ == "__main__":
    main()
