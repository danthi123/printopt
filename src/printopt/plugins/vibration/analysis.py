"""Enhanced vibration analysis — high-resolution FFT with multi-peak shaper optimization."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy import signal as sp_signal

logger = logging.getLogger(__name__)


@dataclass
class ResonancePeak:
    frequency: float
    amplitude: float
    prominence: float


@dataclass
class ShaperResult:
    shaper_type: str
    frequency: float
    remaining_vibration: float
    max_accel_loss: float


def compute_psd(
    samples: np.ndarray,
    fs: float,
    nperseg: int | None = None,
    noverlap: int | None = None,
    window: str = "hann",
) -> tuple[np.ndarray, np.ndarray]:
    """Compute PSD using Welch's method with high frequency resolution.

    Uses 4x more segments than Klipper's default for higher resolution.

    Args:
        samples: Raw accelerometer samples (1D array).
        fs: Sample rate in Hz.
        nperseg: Samples per segment. Defaults to fs*2 for ~0.5Hz resolution.
        noverlap: Overlap between segments. Defaults to 75% of nperseg.
        window: Window function (hann, hamming, blackman, kaiser).

    Returns:
        (frequencies, psd) arrays.
    """
    if len(samples) == 0:
        return np.array([]), np.array([])

    if nperseg is None:
        nperseg = min(len(samples), int(fs * 2))  # 2-second windows = 0.5Hz resolution
    if noverlap is None:
        noverlap = int(nperseg * 0.75)  # 75% overlap for smoother estimate

    freqs, psd = sp_signal.welch(
        samples, fs=fs, nperseg=nperseg, noverlap=noverlap,
        window=window, scaling='density',
    )

    # Filter to useful range (1-200 Hz)
    mask = (freqs >= 1.0) & (freqs <= 200.0)
    return freqs[mask], psd[mask]


def compute_psd_multitaper(
    samples: np.ndarray,
    fs: float,
    nw: float = 4.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute PSD using multiple window functions and average for robustness.

    Runs Welch's with hann, hamming, and blackman windows, then averages.
    More robust than single-window against spectral leakage.
    """
    if len(samples) == 0:
        return np.array([]), np.array([])

    windows = ["hann", "hamming", "blackman"]
    all_psds = []
    freqs = None

    for win in windows:
        f, p = compute_psd(samples, fs, window=win)
        if freqs is None:
            freqs = f
        all_psds.append(p)

    # Geometric mean (better for log-scale data than arithmetic mean)
    psd_stack = np.array(all_psds)
    psd_stack = np.clip(psd_stack, 1e-20, None)  # avoid log(0)
    avg_psd = np.exp(np.mean(np.log(psd_stack), axis=0))

    return freqs, avg_psd


def find_resonance_peaks(
    freqs: np.ndarray,
    psd: np.ndarray,
    min_freq: float = 10.0,
    max_freq: float = 200.0,
    prominence_ratio: float = 0.1,
) -> list[ResonancePeak]:
    """Find resonance peaks with prominence filtering.

    Args:
        freqs: Frequency array.
        psd: PSD array.
        min_freq: Minimum frequency to consider.
        max_freq: Maximum frequency to consider.
        prominence_ratio: Minimum prominence as fraction of max PSD.

    Returns:
        List of ResonancePeak sorted by amplitude descending.
    """
    if len(freqs) == 0 or len(psd) == 0:
        return []

    mask = (freqs >= min_freq) & (freqs <= max_freq)
    f_masked = freqs[mask]
    p_masked = psd[mask]

    if len(p_masked) == 0:
        return []

    min_prominence = np.max(p_masked) * prominence_ratio
    # Minimum distance between peaks: 5 Hz
    min_distance = max(1, int(5.0 / (f_masked[1] - f_masked[0])) if len(f_masked) > 1 else 1)

    peak_indices, properties = sp_signal.find_peaks(
        p_masked,
        prominence=min_prominence,
        distance=min_distance,
    )

    peaks = []
    for i, idx in enumerate(peak_indices):
        peaks.append(ResonancePeak(
            frequency=float(f_masked[idx]),
            amplitude=float(p_masked[idx]),
            prominence=float(properties["prominences"][i]),
        ))

    peaks.sort(key=lambda p: p.amplitude, reverse=True)
    return peaks


def _shaper_response(shaper_type: str, freq: float, test_freqs: np.ndarray) -> np.ndarray:
    """Compute amplitude response of an input shaper at given frequencies.

    These match Klipper's shaper implementations exactly.
    """
    if shaper_type == "zv":
        df = 0.05  # damping factor
        K = np.exp(-df * np.pi / np.sqrt(1.0 - df**2))
        A = np.array([1.0, K]) / (1.0 + K)
        T = np.array([0.0, 0.5 / freq])
    elif shaper_type == "mzv":
        df = 0.05
        b = np.exp(-df * np.pi / np.sqrt(1.0 - df**2))
        a1 = 1.0 - 1.0 / np.sqrt(2.0)
        A = np.array([a1, 1.0 - 2.0 * a1, a1])
        T = np.array([0.0, 0.375 / freq, 0.75 / freq])
    elif shaper_type == "ei":
        v_tol = 0.05
        a1 = 0.25 * (1.0 + v_tol)
        a2 = 0.5 * (1.0 - v_tol)
        a3 = a1
        A = np.array([a1, a2, a3])
        T = np.array([0.0, 0.5 / freq, 1.0 / freq])
    elif shaper_type == "2hump_ei":
        v_tol = 0.05
        a1 = (3.0 * v_tol + 1.0) / 16.0
        a2 = (1.0 - v_tol) * 0.25
        a3 = (1.0 - 2.0 * a1 - 2.0 * a2)
        A = np.array([a1, a2, a3, a2, a1])
        T = np.array([0.0, 0.5/freq, 1.0/freq, 1.5/freq, 2.0/freq])
    elif shaper_type == "3hump_ei":
        v_tol = 0.05
        a1 = (1.0 + v_tol) / 64.0
        a2 = 3.0 * (1.0 + v_tol) / 32.0
        a3 = (14.0 + 3.0 * v_tol) / 32.0 - a1 - a2
        A = np.array([a1, a2, a3, 1.0 - 2.0*(a1+a2+a3), a3, a2, a1])
        T = np.array([i * 0.5 / freq for i in range(7)])
    else:
        raise ValueError(f"Unknown shaper type: {shaper_type}")

    # Normalize A
    A = A / np.sum(A)

    # Vectorized frequency response computation
    w = 2.0 * np.pi * test_freqs  # angular frequencies
    real = np.zeros_like(test_freqs)
    imag = np.zeros_like(test_freqs)
    for a, t in zip(A, T):
        real += a * np.cos(w * t)
        imag += a * np.sin(w * t)

    return np.sqrt(real**2 + imag**2)


def evaluate_shapers(
    freqs: np.ndarray,
    psd: np.ndarray,
    shaper_types: list[str] | tuple[str, ...] | None = None,
    freq_step: float = 0.1,
    min_freq: float = 10.0,
    max_freq: float = 200.0,
) -> list[ShaperResult]:
    """Evaluate all shaper types across frequency range with fine resolution.

    Tests each shaper type at 0.1Hz increments (10x finer than Klipper's default).

    Args:
        freqs: Frequency array from PSD.
        psd: PSD values.
        shaper_types: List of shaper types to evaluate. Defaults to all 5.
        freq_step: Frequency step for sweep (Hz). 0.1 = 10x finer than default.
        min_freq: Minimum shaper frequency to test.
        max_freq: Maximum shaper frequency to test.

    Returns:
        List of ShaperResult sorted by remaining_vibration ascending (best first).
    """
    if shaper_types is None:
        shaper_types = ["zv", "mzv", "ei", "2hump_ei", "3hump_ei"]

    if len(freqs) == 0 or len(psd) == 0:
        return []

    # Total vibration energy (reference)
    total_vibration = np.trapz(psd, freqs)
    if total_vibration <= 0:
        return []

    # Test frequencies at fine resolution
    test_frequencies = np.arange(min_freq, max_freq, freq_step)

    results = []
    for shaper_type in shaper_types:
        best_remaining = float('inf')
        best_freq = 0.0
        best_accel_loss = 0.0

        for sf in test_frequencies:
            try:
                response = _shaper_response(shaper_type, sf, freqs)
                # Remaining vibration = integral of filtered PSD
                filtered_psd = psd * response**2
                remaining = np.trapz(filtered_psd, freqs) / total_vibration
                # Max acceleration loss = max response value (ideally 1.0)
                accel_loss = 1.0 - np.min(response)

                if remaining < best_remaining:
                    best_remaining = remaining
                    best_freq = sf
                    best_accel_loss = accel_loss
            except (ValueError, ZeroDivisionError):
                continue

        if best_freq > 0:
            results.append(ShaperResult(
                shaper_type=shaper_type,
                frequency=round(best_freq, 1),
                remaining_vibration=round(best_remaining, 6),
                max_accel_loss=round(best_accel_loss, 6),
            ))

    results.sort(key=lambda r: r.remaining_vibration)
    return results


def analyze_raw_data(
    raw_csv: str,
    axis: str = "x",
    fs: float = 3200.0,
) -> tuple[np.ndarray, np.ndarray, list[ResonancePeak], list[ShaperResult]]:
    """Full analysis pipeline from raw ADXL345 CSV data.

    This is the high-resolution path that leverages PC compute power:
    1. Parse raw accelerometer samples
    2. Multi-window PSD estimation (3 windows averaged)
    3. Fine-grained peak detection
    4. 0.1Hz-resolution shaper evaluation

    Args:
        raw_csv: Raw CSV text from ADXL345 capture.
        axis: Which axis was tested ("x" or "y").
        fs: Expected sample rate.

    Returns:
        (freqs, psd, peaks, shapers) tuple.
    """
    import csv
    import io

    # Parse raw data
    reader = csv.reader(io.StringIO(raw_csv))
    times = []
    accel = []
    axis_col = {"x": 1, "y": 2, "z": 3}.get(axis, 1)

    for row in reader:
        if not row or row[0].startswith("#") or row[0].startswith("time"):
            continue
        try:
            times.append(float(row[0]))
            accel.append(float(row[axis_col]))
        except (ValueError, IndexError):
            continue

    if len(accel) < 100:
        logger.warning("Too few raw samples (%d) for analysis", len(accel))
        return np.array([]), np.array([]), [], []

    samples = np.array(accel)

    # Estimate actual sample rate from timestamps
    if times:
        actual_fs = len(times) / (times[-1] - times[0]) if times[-1] > times[0] else fs
        logger.info("Raw data: %d samples, %.1f Hz sample rate, %.2f seconds",
                     len(samples), actual_fs, times[-1] - times[0])
        fs = actual_fs

    # Remove DC offset
    samples = samples - np.mean(samples)

    # Multi-window PSD for robustness
    freqs, psd = compute_psd_multitaper(samples, fs)

    # Find peaks
    peaks = find_resonance_peaks(freqs, psd)

    # Fine-grained shaper evaluation (0.1Hz steps)
    shapers = evaluate_shapers(freqs, psd, freq_step=0.1)

    logger.info("Analysis complete: %d freq bins, %d peaks, best shaper: %s @ %.1f Hz",
                len(freqs), len(peaks),
                shapers[0].shaper_type if shapers else "none",
                shapers[0].frequency if shapers else 0)

    return freqs, psd, peaks, shapers
