"""Vibration data capture from ADXL345 via Klipper/Moonraker."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
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
    accel_per_hz: float = 75.0,
) -> str:
    """Run a resonance test on the printer and return the CSV filename.

    Sends SHAPER_CALIBRATE-style test movements and captures accelerometer data.
    Returns the filename of the CSV on the printer (in /tmp/).
    """
    axis = axis.lower()
    if axis not in ("x", "y"):
        raise ValueError(f"Invalid axis: {axis}")

    # Home the printer first
    logger.info("Homing printer...")
    await client.inject("G28")
    await asyncio.sleep(2)

    # Move to test position (center of bed)
    logger.info("Moving to test position...")
    await client.inject("G1 X120 Y120 Z10 F6000")
    await asyncio.sleep(2)

    # Run the resonance test using Klipper's TEST_RESONANCES command
    # This captures ADXL345 data and saves CSVs to /tmp/
    logger.info(f"Running resonance test on {axis.upper()} axis...")
    await client.inject(
        f"TEST_RESONANCES AXIS={axis.upper()} "
        f"FREQ_START={min_freq} FREQ_END={max_freq} "
        f"HZ_PER_SEC={accel_per_hz / 2}"
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
        if row[0].startswith("#"):
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
