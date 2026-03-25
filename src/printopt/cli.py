"""CLI entry point for printopt."""

import argparse
import sys


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

    print(f"printopt: {args.command} (not yet implemented)")


if __name__ == "__main__":
    main()
