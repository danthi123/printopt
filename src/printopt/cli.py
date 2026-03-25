"""CLI entry point for printopt."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

from printopt.core.materials import (
    MaterialProfile,
    get_all_profiles,
    save_custom_profile,
)
from printopt.core.moonraker import MoonrakerClient
from printopt.core.plugin import PluginManager
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


async def do_run(
    plugins: str = "all",
    port: int = 8484,
    profile: str | None = None,
    config_dir: Path | None = None,
    _client: Any = None,
) -> None:
    """Start the optimization daemon with dashboard."""
    config_dir = config_dir or get_config_dir()
    config_path = config_dir / "printer.json"
    if not config_path.exists():
        print("No printer configured. Run 'printopt connect <host>' first.")
        sys.exit(1)

    config = PrinterConfig.load(config_path)
    print(f"Loaded config for {config.host} ({config.kinematics}, {config.bed_x}x{config.bed_y})")

    # Connect to Moonraker
    if _client is None:
        client = MoonrakerClient(config.host)
        await client.connect()
        print(f"Connected to Moonraker at {config.host}")
    else:
        client = _client

    # Initialize plugin manager
    mgr = PluginManager()
    # Plugins will be registered here as they're implemented
    # For now, just start empty
    await mgr.start_all()
    print(f"Plugin manager started ({len(mgr.plugins)} plugins)")

    # Update dashboard state
    from printopt.dashboard.server import _state
    _state["printer"]["connected"] = True
    _state["printer"]["host"] = config.host

    # Start dashboard
    print(f"Dashboard at http://localhost:{port}")

    import uvicorn
    from printopt.dashboard.server import create_app
    app = create_app()

    server_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    try:
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        await mgr.stop_all()
        if _client is None:
            await client.disconnect()
        print("printopt stopped")


def do_vibration(args: argparse.Namespace) -> None:
    """Handle vibration subcommands."""
    sub = getattr(args, "vib_command", None)
    if sub is None:
        print("Usage: printopt vibration {analyze,report,apply}")
        sys.exit(1)

    if sub == "analyze":
        print("Starting vibration analysis...")
        print("(Requires a live printer connection with ADXL345 attached)")
        return

    if sub == "report":
        print("Vibration report (not yet implemented)")
        return

    if sub == "apply":
        print("Apply shaper config (not yet implemented)")
        return

    print(f"printopt vibration: unknown subcommand '{sub}'")
    sys.exit(1)


def do_profile_list(config_dir: Path | None = None) -> None:
    """List all available filament profiles."""
    config_dir = config_dir or get_config_dir()
    profiles = get_all_profiles(config_dir)
    if not profiles:
        print("No profiles available.")
        return
    for name, p in sorted(profiles.items()):
        print(
            f"  {name:<16s} density={p.density:.2f}  Cp={p.specific_heat:.1f}  "
            f"k={p.thermal_conductivity:.2f}  Tg={p.glass_transition:.0f}C"
        )


def do_profile_create(
    name: str,
    config_dir: Path | None = None,
    *,
    density: float = 1.27,
    specific_heat: float = 1.2,
    thermal_conductivity: float = 0.20,
    glass_transition: float = 78,
    cte: float = 60e-6,
) -> Path:
    """Create a custom filament profile.

    Defaults are based on PETG.
    """
    config_dir = config_dir or get_config_dir()
    profile = MaterialProfile(
        name=name,
        density=density,
        specific_heat=specific_heat,
        thermal_conductivity=thermal_conductivity,
        glass_transition=glass_transition,
        cte=cte,
    )
    path = save_custom_profile(profile, config_dir)
    print(f"Profile '{name}' saved to {path}")
    return path


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

    if args.command == "run":
        asyncio.run(do_run(
            plugins=args.plugins,
            port=args.port,
            profile=args.profile,
        ))
        return

    if args.command == "vibration":
        do_vibration(args)
        return

    if args.command == "profile":
        sub = getattr(args, "prof_command", None)
        if sub == "list":
            do_profile_list()
            return
        if sub == "create":
            do_profile_create(args.name)
            return
        print("Usage: printopt profile {list,create}")
        sys.exit(1)

    print(f"printopt: {args.command} (not yet implemented)")


if __name__ == "__main__":
    main()
