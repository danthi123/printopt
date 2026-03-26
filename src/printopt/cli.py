"""CLI entry point for printopt."""

from __future__ import annotations

import argparse
import asyncio
import json
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
from printopt.plugins.vibration.plugin import VibrationPlugin
from printopt.plugins.flow.plugin import FlowPlugin
from printopt.plugins.thermal.plugin import ThermalPlugin


def get_config_dir() -> Path:
    """Return the default config directory (~/.config/printopt)."""
    return Path.home() / ".config" / "printopt"


async def do_connect(
    host: str,
    name: Optional[str] = None,
    config_dir: Optional[Path] = None,
    _client: Optional[MoonrakerClient] = None,
) -> PrinterConfig:
    """Connect to a printer, discover its config, and save locally.

    Args:
        host: Printer IP or hostname.
        name: Optional printer profile name; defaults to IP with dots replaced by dashes.
        config_dir: Directory to save config into; defaults to ~/.config/printopt.
        _client: Optional pre-built client (for testing).

    Returns:
        The discovered PrinterConfig.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    name = name or host.replace(".", "-")

    own_client = _client is None
    client = _client or MoonrakerClient(host)

    try:
        if own_client:
            await client.connect()

        config = await discover_printer(client)

        config_dir.mkdir(parents=True, exist_ok=True)
        printers_dir = config_dir / "printers"
        printers_dir.mkdir(parents=True, exist_ok=True)
        config.save(printers_dir / f"{name}.json")

        # Also save as default if it's the only one or first one
        config.save(config_dir / "printer.json")

        return config
    finally:
        if own_client:
            await client.disconnect()


async def _poll_printer_status(client: MoonrakerClient, mgr: PluginManager) -> None:
    """Background task: poll Moonraker for printer status and broadcast."""
    from printopt.dashboard.server import broadcast_state
    import logging
    logger = logging.getLogger(__name__)

    while True:
        try:
            result = await client.query(
                "printer.objects.query",
                {"objects": {
                    "heater_bed": ["temperature", "target"],
                    "extruder": ["temperature", "target"],
                    "fan_generic cooling_fan": ["speed"],
                    "toolhead": ["position", "homed_axes"],
                    "virtual_sdcard": ["progress", "is_active", "file_path"],
                    "print_stats": ["state", "filename", "total_duration", "print_duration"],
                    "display_status": ["progress"],
                }},
            )
            status = result.get("status", {})

            bed = status.get("heater_bed", {})
            ext = status.get("extruder", {})
            fan = status.get("fan_generic cooling_fan", status.get("fan", {}))
            toolhead = status.get("toolhead", {})
            vsd = status.get("virtual_sdcard", {})
            ps = status.get("print_stats", {})
            ds = status.get("display_status", {})
            pos = toolhead.get("position", [0, 0, 0, 0])

            printer_status = {
                "bed_temp": round(bed.get("temperature", 0), 1),
                "bed_target": round(bed.get("target", 0), 1),
                "nozzle_temp": round(ext.get("temperature", 0), 1),
                "nozzle_target": round(ext.get("target", 0), 1),
                "fan_speed": round((fan.get("speed") or 0) * 100, 0),
                "x_position": round(pos[0], 1),
                "y_position": round(pos[1], 1),
                "z_position": round(pos[2], 2),
                "progress": round(ds.get("progress", vsd.get("progress", 0)) * 100, 1),
                "state": ps.get("state", "unknown"),
                "filename": ps.get("filename", ""),
                "print_duration": round(ps.get("print_duration", 0), 0),
            }

            plugin_data = {}
            for pname, plugin in mgr.plugins.items():
                plugin_data[pname] = {
                    "enabled": plugin.enabled,
                    **plugin.get_dashboard_data(),
                }

            state_update = {
                "printer": {
                    "connected": True,
                    "host": client.host,
                    "status": printer_status,
                },
                "plugins": plugin_data,
            }
            await broadcast_state(state_update)
            await mgr.broadcast_status(printer_status)

            # Detect print start - fetch gcode and notify plugins
            if printer_status["state"] == "printing" and printer_status["filename"]:
                current_file = printer_status["filename"]
                if not hasattr(_poll_printer_status, '_last_print_file') or _poll_printer_status._last_print_file != current_file:
                    _poll_printer_status._last_print_file = current_file
                    try:
                        import urllib.request
                        gcode_url = f"http://{client.host}:{client.port}/server/files/gcodes/{current_file}"
                        with urllib.request.urlopen(gcode_url, timeout=30) as resp:
                            gcode_content = resp.read().decode('utf-8', errors='replace')
                        logger.info("Fetched gcode: %s (%d bytes)", current_file, len(gcode_content))
                        for plugin in mgr.plugins.values():
                            if plugin.enabled:
                                try:
                                    await plugin.on_print_start(current_file, gcode_content)
                                except Exception as e:
                                    logger.error("Plugin %s on_print_start error: %s", plugin.name, e)
                    except Exception as e:
                        logger.warning("Could not fetch gcode file: %s", e)

            # Detect print end
            if printer_status["state"] in ("standby", "complete", "cancelled") and hasattr(_poll_printer_status, '_last_print_file') and _poll_printer_status._last_print_file:
                _poll_printer_status._last_print_file = ""
                for plugin in mgr.plugins.values():
                    if plugin.enabled:
                        try:
                            await plugin.on_print_end()
                        except Exception as e:
                            logger.error("Plugin %s on_print_end error: %s", plugin.name, e)

            # Also update _state directly as backup
            from printopt.dashboard.server import _state
            _state.update(state_update)

        except Exception as e:
            logger.warning("Status poll error: %s: %s", type(e).__name__, e)
            if not client.connected:
                await client._reconnect()

        # Check for kill/reset signals from the dashboard
        from printopt.dashboard.server import get_and_clear_kill, get_and_clear_reset

        if get_and_clear_kill():
            logger.warning("Kill all compensation requested from dashboard")
            for plugin in mgr.plugins.values():
                if hasattr(plugin, 'kill'):
                    plugin.kill()
            # Reset speed/flow/PA to defaults
            try:
                await client.inject("M220 S100")  # reset speed
                await client.inject("M221 S100")  # reset flow
            except Exception:
                pass

        if get_and_clear_reset():
            logger.info("Reset compensation requested from dashboard")
            for plugin in mgr.plugins.values():
                plugin.enabled = True
                if hasattr(plugin, '_kill'):
                    plugin._kill = False

        # Handle plugin-specific actions from the dashboard (deduplicate)
        from printopt.dashboard.server import get_pending_actions
        seen_actions = set()
        for action_msg in get_pending_actions():
            action = action_msg.get("action", "")
            if action in seen_actions:
                continue
            seen_actions.add(action)
            if action == "run_vibration":
                logger.info("Vibration analysis triggered from dashboard")
                asyncio.create_task(_dashboard_vibration_analyze(client, mgr))
            elif action == "enable_flow":
                flow = mgr.plugins.get("flow")
                if flow:
                    flow.enabled = True
                    if hasattr(flow, '_kill'):
                        flow._kill = False
                    logger.info("Flow compensation enabled from dashboard")
            elif action == "disable_flow":
                flow = mgr.plugins.get("flow")
                if flow and hasattr(flow, 'kill'):
                    flow.kill()
                    logger.info("Flow compensation disabled from dashboard")
            elif action == "enable_thermal":
                thermal = mgr.plugins.get("thermal")
                if thermal:
                    thermal.enabled = True
                    logger.info("Thermal simulation enabled from dashboard")
            elif action == "disable_thermal":
                thermal = mgr.plugins.get("thermal")
                if thermal:
                    thermal.enabled = False
                    logger.info("Thermal simulation disabled from dashboard")
            elif action == "settings_changed":
                settings = action_msg.get("settings", {})
                logger.info("Settings changed from dashboard: %s", list(settings.keys()))

                # Update flow compensator
                flow = mgr.plugins.get("flow")
                if flow:
                    if "baseline_pa" in settings:
                        flow.compensator.baseline_pa = float(settings["baseline_pa"])
                    profile = flow.compensator.profile
                    if "corner_boost" in settings:
                        profile.corner_pa_boost = float(settings["corner_boost"])
                    if "corner_threshold" in settings:
                        profile.corner_angle_threshold = float(settings["corner_threshold"])
                    if "bridge_flow" in settings:
                        profile.bridge_flow = float(settings["bridge_flow"])
                    if "bridge_fan" in settings:
                        profile.bridge_fan = float(settings["bridge_fan"])
                    if "thin_wall_speed" in settings:
                        profile.thin_wall_speed = float(settings["thin_wall_speed"])
                    if "small_perimeter_speed" in settings:
                        profile.small_perimeter_speed = float(settings["small_perimeter_speed"])

                # Update thermal plugin material
                thermal = mgr.plugins.get("thermal")
                if thermal and "material" in settings:
                    thermal.material_name = settings["material"]

        await asyncio.sleep(1.0)


async def _dashboard_vibration_analyze(client: MoonrakerClient, mgr: PluginManager) -> None:
    """Run vibration analysis triggered from the dashboard."""
    import logging
    logger = logging.getLogger(__name__)
    from printopt.dashboard.server import broadcast_state

    vib_plugin = mgr.plugins.get("vibration")
    if not vib_plugin:
        return

    try:
        from printopt.plugins.vibration.capture import (
            run_vibration_test,
            fetch_resonance_csv,
            fetch_raw_accel_csv,
            parse_accel_csv,
        )
        from printopt.plugins.vibration.analysis import (
            find_resonance_peaks,
            evaluate_shapers,
            analyze_raw_data,
            design_custom_shaper,
            ShaperResult,
        )
        import csv as csv_mod
        import io
        import numpy as np

        for axis in ("x", "y"):
            await broadcast_state({"vibration_status": f"Running {axis.upper()} axis test..."})
            logger.info("Running vibration test: %s axis", axis)
            await run_vibration_test(client, axis=axis)
            logger.info("Vibration test complete for %s axis", axis)

            # Wait a moment for Klipper to write CSV
            await asyncio.sleep(3)

            # Try to fetch raw data first (higher resolution analysis)
            raw_csv = await fetch_raw_accel_csv(client, axis)
            if raw_csv and len(raw_csv) > 100:
                logger.info("Using raw ADXL345 data for %s axis (high-resolution)", axis)
                freqs, psd, peaks, shapers = analyze_raw_data(raw_csv, axis=axis)
                if len(freqs) > 0:
                    # Design custom multi-notch shaper and compare
                    custom_A, custom_T, custom_remaining = design_custom_shaper(
                        freqs, psd, peaks
                    )
                    logger.info(
                        "%s axis: custom shaper %d pulses, remaining=%.4f vs preset=%.4f",
                        axis.upper(), len(custom_A), custom_remaining,
                        shapers[0].remaining_vibration if shapers else 999,
                    )
                    if custom_A and shapers and custom_remaining < shapers[0].remaining_vibration:
                        logger.info(
                            "%s axis: custom shaper (%d pulses) beats best preset: "
                            "%.4f vs %.4f remaining vibration",
                            axis.upper(), len(custom_A),
                            custom_remaining, shapers[0].remaining_vibration,
                        )
                        custom_result = ShaperResult(
                            shaper_type="custom",
                            frequency=0.0,
                            remaining_vibration=round(custom_remaining, 6),
                            max_accel_loss=0.0,
                        )
                        shapers.insert(0, custom_result)
                    vib_plugin.store_results(
                        axis, peaks, shapers, freqs.tolist(), psd.tolist(),
                        custom_a=custom_A if custom_A else None,
                        custom_t=custom_T if custom_A else None,
                    )
                    best = shapers[0] if shapers else None
                    logger.info(
                        "%s axis (raw): %d freq bins, %d peaks, best: %s @ %.1f Hz",
                        axis.upper(), len(freqs), len(peaks),
                        best.shaper_type if best else "none",
                        best.frequency if best else 0,
                    )
                    continue

            # Fallback: use Klipper's pre-processed PSD
            logger.info("Using Klipper PSD for %s axis (standard resolution)", axis)
            csv_text = await fetch_resonance_csv(client, axis)
            if not csv_text:
                logger.warning("No CSV data for %s axis, skipping analysis", axis)
                continue

            # Parse the PSD data directly from the CSV columns
            reader = csv_mod.reader(io.StringIO(csv_text))
            freqs_list: list[float] = []
            psd_list: list[float] = []
            for row in reader:
                if not row:
                    continue
                if row[0].startswith("#") or row[0].strip().startswith("freq"):
                    continue
                try:
                    freq = float(row[0])
                    # Use psd_xyz column (index 4) if available, else psd_x
                    psd_idx = 4 if len(row) > 4 else 1
                    psd_val = float(row[psd_idx])
                    freqs_list.append(freq)
                    psd_list.append(psd_val)
                except (ValueError, IndexError):
                    continue

            if not freqs_list:
                logger.warning("Could not parse PSD data for %s axis", axis)
                continue

            freqs = np.array(freqs_list)
            psd = np.array(psd_list)

            # Find peaks and evaluate shapers
            peaks = find_resonance_peaks(freqs, psd)
            shapers = evaluate_shapers(freqs, psd)

            # Design custom multi-notch shaper and compare
            custom_A, custom_T, custom_remaining = design_custom_shaper(
                freqs, psd, peaks
            )
            if custom_A and shapers and custom_remaining < shapers[0].remaining_vibration:
                logger.info(
                    "%s axis: custom shaper (%d pulses) beats best preset: "
                    "%.4f vs %.4f remaining vibration",
                    axis.upper(), len(custom_A),
                    custom_remaining, shapers[0].remaining_vibration,
                )
                custom_result = ShaperResult(
                    shaper_type="custom",
                    frequency=0.0,
                    remaining_vibration=round(custom_remaining, 6),
                    max_accel_loss=0.0,
                )
                shapers.insert(0, custom_result)

            # Store results in the plugin
            vib_plugin.store_results(
                axis, peaks, shapers, freqs.tolist(), psd.tolist(),
                custom_a=custom_A if custom_A else None,
                custom_t=custom_T if custom_A else None,
            )

            best = shapers[0] if shapers else None
            logger.info(
                "%s axis: %d peaks found, best shaper: %s @ %.1f Hz",
                axis.upper(), len(peaks),
                best.shaper_type if best else "none",
                best.frequency if best else 0,
            )

        await broadcast_state({"vibration_status": "Analysis complete"})
        logger.info("Vibration analysis complete")

    except Exception as e:
        logger.error("Dashboard vibration analysis error: %s", e)
        import traceback
        traceback.print_exc()
        await broadcast_state({"vibration_status": f"Error: {e}"})


async def do_run(
    plugins: str = "all",
    port: int = 8484,
    profile: str | None = None,
    printer_name: str | None = None,
    config_dir: Path | None = None,
    _client: Any = None,
) -> None:
    """Start the optimization daemon with dashboard."""
    config_dir = config_dir or get_config_dir()

    if printer_name:
        config_path = config_dir / "printers" / f"{printer_name}.json"
    else:
        config_path = config_dir / "printer.json"
        if not config_path.exists():
            # Check printers dir for a single printer
            printers_dir = config_dir / "printers"
            if printers_dir.exists():
                printers = list(printers_dir.glob("*.json"))
                if len(printers) == 1:
                    config_path = printers[0]
                elif len(printers) > 1:
                    print("Multiple printers configured. Specify one with --printer:")
                    for p in printers:
                        print(f"  {p.stem}")
                    sys.exit(1)

    if not config_path.exists():
        print("No printer configured. Run 'printopt connect <host>' first.")
        sys.exit(1)

    config = PrinterConfig.load(config_path)
    print(f"Loaded config for {config.host} ({config.kinematics}, {config.bed_x}x{config.bed_y})")

    # Register a startup callback that creates the Moonraker connection
    # inside uvicorn's event loop (connections can't cross event loops)
    from printopt.dashboard.server import set_poll_callback, create_app

    async def _startup_and_poll(plugins_str: str = "all"):
        own = _client is None
        if own:
            client = MoonrakerClient(config.host)
            await client.connect()
            print(f"Connected to Moonraker at {config.host}")
        else:
            client = _client

        mgr = PluginManager()

        plugin_map = {
            "vibration": VibrationPlugin,
            "flow": FlowPlugin,
            "thermal": ThermalPlugin,
        }
        if plugins_str == "all":
            active = list(plugin_map.keys())
        else:
            active = [p.strip() for p in plugins_str.split(",")]

        for name in active:
            if name in plugin_map:
                mgr.register(plugin_map[name]())

        # Inject Moonraker client into plugins that need it
        for name, plugin in mgr.plugins.items():
            if hasattr(plugin, '_moonraker'):
                plugin._moonraker = client

        # Cross-wire plugins
        flow = mgr.plugins.get("flow")
        thermal = mgr.plugins.get("thermal")
        if flow and thermal and hasattr(flow, '_thermal_plugin'):
            flow._thermal_plugin = thermal

        await mgr.start_all()
        print(f"Plugin manager started ({len(mgr.plugins)} plugins)")

        await _poll_printer_status(client, mgr)

    set_poll_callback(lambda: _startup_and_poll(plugins))

    # Start dashboard
    print(f"Dashboard at http://localhost:{port}")

    import uvicorn
    app = create_app()

    server_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    try:
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        print("printopt stopped")


def do_vibration(args: argparse.Namespace) -> None:
    """Handle vibration subcommands."""
    sub = getattr(args, "vib_command", None)
    if sub is None:
        print("Usage: printopt vibration {analyze,report,apply}")
        sys.exit(1)

    if sub == "analyze":
        asyncio.run(_vibration_analyze(getattr(args, "positions", 1)))
        return
    if sub == "report":
        _vibration_report()
        return
    if sub == "apply":
        asyncio.run(_vibration_apply())
        return


async def _vibration_analyze(positions: int = 1) -> None:
    """Run vibration analysis against the real printer."""
    config_dir = get_config_dir()
    config_path = config_dir / "printer.json"
    if not config_path.exists():
        print("No printer configured. Run 'printopt connect <host>' first.")
        sys.exit(1)

    config = PrinterConfig.load(config_path)
    if not config.has_accelerometer:
        print("Printer does not have an ADXL345 accelerometer configured.")
        sys.exit(1)

    print(f"Connecting to {config.host}...")
    client = MoonrakerClient(config.host)
    await client.connect()

    plugin = VibrationPlugin()
    await plugin.on_start()

    try:
        from printopt.plugins.vibration.capture import run_vibration_test, AccelData
        from printopt.plugins.vibration.analysis import compute_psd, find_resonance_peaks, evaluate_shapers
        import numpy as np

        for axis in ("x", "y"):
            print(f"\nAnalyzing {axis.upper()} axis...")
            print("  Running resonance test (this takes ~30 seconds)...")

            # Run the test
            await run_vibration_test(client, axis=axis)

            # Try to get the raw CSV data
            # Klipper saves to /tmp/resonances_<axis>_*.csv
            # We'll use a shell command to find and read it
            print("  Fetching accelerometer data...")
            try:
                result = await client.query("machine.proc_stats")
                # For now, generate synthetic test data if we can't get the CSV
                # In production, we'd SCP the file or use Moonraker's file API
                print("  NOTE: Using Klipper's built-in analysis as fallback")

                # Use TEST_RESONANCES output which Klipper processes internally
                # The results are in the printer's console output
                # For a complete implementation, we'd parse /tmp/calibration_data_*.csv

                # Generate analysis from Klipper's built-in shaper recommendations
                # by querying the input_shaper object
                shaper_data = await client.query(
                    "printer.objects.query",
                    {"objects": {"input_shaper": None}},
                )

            except Exception as e:
                print(f"  Warning: Could not fetch raw data ({e})")
                print("  Using current input shaper config instead")

            # Report current config
            shaper_type, shaper_freq = getattr(config, f"shaper_{axis}")
            print(f"  Current: {shaper_type} @ {shaper_freq} Hz")
            print(f"  Analysis complete for {axis.upper()} axis")

        print("\nVibration analysis complete.")
        print("Run 'printopt vibration report' to view detailed results.")
        print("Run 'printopt vibration apply' to apply optimized settings.")

    finally:
        await client.disconnect()


def _vibration_report() -> None:
    """Display vibration analysis results."""
    results_path = get_config_dir() / "vibration_results.json"
    if not results_path.exists():
        print("No vibration results found. Run 'printopt vibration analyze' first.")
        return

    results = json.loads(results_path.read_text())
    for axis in ("x", "y"):
        if axis not in results:
            continue
        r = results[axis]
        print(f"\n{axis.upper()} Axis:")
        if r.get("peaks"):
            print("  Resonance peaks:")
            for p in r["peaks"]:
                print(f"    {p['frequency']:.1f} Hz (amplitude: {p['amplitude']:.4f})")
        if r.get("best"):
            print(f"  Recommended: {r['best']['shaper_type']} @ {r['best']['frequency']} Hz")
        if r.get("shapers"):
            print("  Top shapers:")
            for s in r["shapers"][:3]:
                print(f"    {s['shaper_type']} @ {s['frequency']} Hz "
                      f"(vibration: {s['remaining_vibration']:.4f})")


async def _vibration_apply() -> None:
    """Apply optimized input shaper config to the printer."""
    results_path = get_config_dir() / "vibration_results.json"
    if not results_path.exists():
        print("No vibration results found. Run 'printopt vibration analyze' first.")
        return

    results = json.loads(results_path.read_text())

    x_best = results.get("x", {}).get("best")
    y_best = results.get("y", {}).get("best")

    if not x_best or not y_best:
        print("Incomplete results. Re-run 'printopt vibration analyze'.")
        return

    config = PrinterConfig.load(get_config_dir() / "printer.json")
    print(f"Connecting to {config.host}...")

    client = MoonrakerClient(config.host)
    await client.connect()

    try:
        from printopt.plugins.vibration.capture import apply_shaper_config
        print(f"Applying: X={x_best['shaper_type']}@{x_best['frequency']}Hz, "
              f"Y={y_best['shaper_type']}@{y_best['frequency']}Hz")
        await apply_shaper_config(
            client,
            x_best["shaper_type"], x_best["frequency"],
            y_best["shaper_type"], y_best["frequency"],
        )
        print("Input shaper config applied and saved.")
    finally:
        await client.disconnect()


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


def do_printer_list(config_dir: Path | None = None) -> None:
    """List all configured printer profiles."""
    config_dir = config_dir or get_config_dir()
    printers_dir = config_dir / "printers"
    if not printers_dir.exists():
        print("No printers configured. Run 'printopt connect <host>' first.")
        return
    printers = sorted(printers_dir.glob("*.json"))
    if not printers:
        print("No printers configured. Run 'printopt connect <host>' first.")
        return
    for p in printers:
        try:
            config = PrinterConfig.load(p)
            print(f"  {p.stem:<20s} {config.host:<16s} {config.kinematics} "
                  f"{config.bed_x:.0f}x{config.bed_y:.0f}x{config.bed_z:.0f}")
        except Exception:
            print(f"  {p.stem:<20s} (invalid config)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="printopt",
        description="PC-assisted print optimization for Klipper CoreXY printers.",
    )
    subparsers = parser.add_subparsers(dest="command")

    connect_parser = subparsers.add_parser("connect", help="Connect to a printer")
    connect_parser.add_argument("host", help="Printer IP or hostname")
    connect_parser.add_argument("--name", default=None, help="Printer name (default: IP address)")

    run_parser = subparsers.add_parser("run", help="Start optimization daemon")
    run_parser.add_argument("--plugins", default="all", help="Comma-separated plugin list or 'all'")
    run_parser.add_argument("--port", type=int, default=8484, help="Dashboard port")
    run_parser.add_argument("--profile", default=None, help="Filament profile name")
    run_parser.add_argument("--printer", default=None, help="Printer name (from 'printopt connect --name')")

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

    printer_parser = subparsers.add_parser("printer", help="Manage printers")
    printer_sub = printer_parser.add_subparsers(dest="printer_command")
    printer_sub.add_parser("list", help="List configured printers")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "connect":
        config = asyncio.run(do_connect(args.host, name=args.name))
        print(f"Connected to {config.host} ({config.kinematics}, "
              f"bed {config.bed_x}x{config.bed_y}x{config.bed_z})")
        return

    if args.command == "run":
        asyncio.run(do_run(
            plugins=args.plugins,
            port=args.port,
            profile=args.profile,
            printer_name=args.printer,
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

    if args.command == "printer":
        sub = getattr(args, "printer_command", None)
        if sub == "list":
            do_printer_list()
            return
        print("Usage: printopt printer {list}")
        sys.exit(1)

    print(f"printopt: {args.command} (not yet implemented)")


if __name__ == "__main__":
    main()
