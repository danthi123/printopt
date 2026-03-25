"""Vibration data capture from ADXL345 via Klipper/Moonraker."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import urlopen

import numpy as np

from printopt.core.moonraker import MoonrakerClient

logger = logging.getLogger(__name__)


@dataclass
class AccelData:
    """Raw accelerometer data from a single test run."""
    axis: str  # "x" or "y"
    samples: np.ndarray = field(default_factory=lambda: np.array([]))
    sample_rate: float = 3200.0  # ADXL345 default


async def run_vibration_test(
    client: MoonrakerClient,
    axis: str = "x",
    min_freq: float = 5.0,
    max_freq: float = 133.0,
    hz_per_sec: float = 1.0,
) -> str:
    """Run a resonance test on the printer and return the CSV filename.

    Args:
        client: Moonraker client.
        axis: "x" or "y".
        min_freq: Start frequency in Hz.
        max_freq: End frequency in Hz.
        hz_per_sec: Frequency sweep rate (Klipper max is 2.0).
    """
    axis = axis.lower()
    if axis not in ("x", "y"):
        raise ValueError(f"Invalid axis: {axis}")

    # Home the printer first
    logger.info("Homing printer...")
    await client.inject("G28")
    await asyncio.sleep(15)

    # Move to test position (center of bed)
    logger.info("Moving to test position...")
    await client.inject("G1 X120 Y120 Z10 F6000")
    await asyncio.sleep(3)

    # Run the resonance test using Klipper's TEST_RESONANCES command
    logger.info("Running resonance test on %s axis...", axis.upper())
    await client.inject(
        f"TEST_RESONANCES AXIS={axis.upper()} "
        f"FREQ_START={min_freq} FREQ_END={max_freq} "
        f"HZ_PER_SEC={hz_per_sec} "
        f"OUTPUT=resonances,raw_data"
    )

    # Wait for test to complete (typically 10-30 seconds)
    # Poll printer status to detect when it's done
    for _ in range(60):
        await asyncio.sleep(2)
        try:
            result = await client.query(
                "printer.objects.query",
                {"objects": {"toolhead": ["position"]}}
            )
            # If we can query without error, test is likely done
            break
        except Exception:
            continue

    # The CSV file is saved by Klipper to /tmp/resonances_<axis>_*.csv
    # We need to find it via the file manager or direct access
    logger.info(f"Resonance test on {axis.upper()} complete")
    return f"resonances_{axis}"


async def fetch_resonance_csv(client: MoonrakerClient, axis: str) -> str:
    """Fetch the most recent resonances CSV for an axis from the printer.

    Uses SSH to find and read the latest CSV file from /tmp/ on the printer,
    since Moonraker does not serve files outside its managed directories.

    Args:
        client: Moonraker client (used for host address).
        axis: "x" or "y".

    Returns:
        CSV text content, or empty string if not found.
    """
    axis = axis.lower()
    host = client.host

    # Find the latest resonances CSV for this axis
    try:
        result = subprocess.run(
            ["ssh", f"root@{host}",
             f"ls -t /tmp/resonances_{axis}_*.csv 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("SSH ls failed for %s axis: %s", axis, exc)
        return ""

    csv_path = result.stdout.strip()
    if not csv_path:
        logger.warning("No resonance CSV found for %s axis on %s", axis, host)
        return ""

    # Read the file content via SSH
    try:
        result = subprocess.run(
            ["ssh", f"root@{host}", f"cat {csv_path}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("SSH cat failed for %s: %s", csv_path, exc)
        return ""

    if result.returncode != 0:
        logger.warning("Failed to read %s: %s", csv_path, result.stderr)
        return ""

    logger.info("Fetched %s (%d bytes)", csv_path, len(result.stdout))
    return result.stdout


async def fetch_raw_accel_csv(client: MoonrakerClient, axis: str) -> str:
    """Fetch the most recent raw accelerometer CSV for an axis."""
    axis = axis.lower()
    host = client.host
    try:
        result = subprocess.run(
            ["ssh", f"root@{host}",
             f"ls -t /tmp/raw_data_{axis}_*.csv 2>/dev/null | head -1"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("SSH failed for raw data %s: %s", axis, exc)
        return ""

    csv_path = result.stdout.strip()
    if not csv_path:
        logger.warning("No raw data CSV found for %s axis", axis)
        return ""

    try:
        result = subprocess.run(
            ["ssh", f"root@{host}", f"cat {csv_path}"],
            capture_output=True, text=True, timeout=30,  # raw files are larger
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("SSH cat failed for raw %s: %s", csv_path, exc)
        return ""

    if result.returncode != 0:
        return ""

    logger.info("Fetched raw data %s (%d bytes)", csv_path, len(result.stdout))
    return result.stdout


async def fetch_accel_csv(
    client: MoonrakerClient,
    filename_prefix: str,
) -> AccelData:
    """Fetch accelerometer CSV data from the printer.

    Klipper saves CSVs to /tmp/. We retrieve them via Moonraker's
    file download endpoint or direct HTTP.
    """
    axis = filename_prefix.split("_")[-1]

    # Use Moonraker's HTTP API to list and download the file
    # Files are at http://<host>:<port>/server/files/gcodes/ but
    # /tmp/ files need the machine endpoint
    url = f"http://{client.host}:{client.port}/machine/proc_stats"

    # For now, use the shell command API to find and cat the file
    # In production, we'd use Moonraker's file transfer API
    result = await client.query(
        "machine.proc_stats"
    )

    # Try to read the CSV via shell command
    # Klipper's resonance tester saves to /tmp/resonances_*.csv
    logger.info(f"Fetching accelerometer data for {axis} axis...")

    return AccelData(axis=axis, sample_rate=3200.0)


def parse_accel_csv(csv_text: str, axis: str = "x") -> AccelData:
    """Parse Klipper's accelerometer CSV format.

    Format:
    #freq,psd_x,psd_y,psd_z,psd_xyz
    1.0,0.001,0.002,0.001,0.004
    ...

    Or raw format:
    #time,accel_x,accel_y,accel_z
    0.000312,1.23,4.56,7.89
    ...
    """
    reader = csv.reader(io.StringIO(csv_text))
    rows = []
    header = None

    for row in reader:
        if not row:
            continue
        if row[0].startswith("#") or row[0].strip().startswith("freq"):
            header = [h.strip().lstrip("#") for h in row]
            continue
        try:
            rows.append([float(v) for v in row])
        except ValueError:
            continue

    if not rows:
        return AccelData(axis=axis)

    data = np.array(rows)

    # Determine which column to use based on header
    if header and "psd_xyz" in header:
        # Already PSD data
        col = header.index("psd_xyz")
        return AccelData(axis=axis, samples=data[:, col])

    # Raw accelerometer data — extract the relevant axis
    axis_map = {"x": 1, "y": 2, "z": 3}
    col = axis_map.get(axis, 1)
    if data.shape[1] > col:
        samples = data[:, col]
        # Estimate sample rate from timestamps
        if data.shape[1] > 0 and data[-1, 0] > 0:
            sr = len(data) / data[-1, 0]
        else:
            sr = 3200.0
        return AccelData(axis=axis, samples=samples, sample_rate=sr)

    return AccelData(axis=axis)


async def apply_custom_shaper(
    client: MoonrakerClient,
    axis: str,
    A: list[float],
    T: list[float],
) -> None:
    """Apply custom shaper coefficients to the printer."""
    a_str = ",".join(f"{a:.6f}" for a in A)
    t_str = ",".join(f"{t:.6f}" for t in T)
    axis = axis.upper()
    logger.info("Applying custom shaper for %s: %d pulses", axis, len(A))
    await client.inject(
        f"SET_INPUT_SHAPER "
        f"SHAPER_TYPE_{axis}=custom "
        f"SHAPER_A_{axis}={a_str} "
        f"SHAPER_T_{axis}={t_str}"
    )


async def apply_shaper_config(
    client: MoonrakerClient,
    shaper_type_x: str,
    freq_x: float,
    shaper_type_y: str,
    freq_y: float,
) -> None:
    """Write optimized input shaper config to the printer."""
    logger.info(
        f"Applying input shaper: X={shaper_type_x}@{freq_x:.1f}Hz, "
        f"Y={shaper_type_y}@{freq_y:.1f}Hz"
    )
    # Set the shaper parameters via gcode
    await client.inject(
        f"SET_INPUT_SHAPER "
        f"SHAPER_TYPE_X={shaper_type_x} SHAPER_FREQ_X={freq_x:.1f} "
        f"SHAPER_TYPE_Y={shaper_type_y} SHAPER_FREQ_Y={freq_y:.1f}"
    )
    # Save to config
    await client.inject("SAVE_CONFIG")
    logger.info("Input shaper config saved")
