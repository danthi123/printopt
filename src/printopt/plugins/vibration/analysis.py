"""FFT-based vibration analysis engine.

Provides PSD estimation, resonance peak detection, and input shaper
evaluation using the same transfer functions as Klipper.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import welch, find_peaks


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


# ---------------------------------------------------------------------------
# PSD estimation
# ---------------------------------------------------------------------------

def compute_psd(
    signal: np.ndarray,
    fs: float,
    nperseg: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Welch's method PSD estimation.

    Parameters
    ----------
    signal : array
        1-D time-domain accelerometer samples.
    fs : float
        Sample rate in Hz.
    nperseg : int, optional
        Segment length for Welch's method.  Defaults to
        ``min(len(signal), int(fs * 2))`` for high frequency resolution.

    Returns
    -------
    freqs : ndarray
        Positive frequency bins (Hz).
    psd : ndarray
        Power spectral density estimate.
    """
    if nperseg is None:
        nperseg = min(len(signal), int(fs * 2))
    freqs, psd = welch(signal, fs=fs, nperseg=nperseg)
    return freqs, psd


# ---------------------------------------------------------------------------
# Peak detection
# ---------------------------------------------------------------------------

def find_resonance_peaks(
    freqs: np.ndarray,
    psd: np.ndarray,
    min_freq: float = 10.0,
    max_freq: float = 200.0,
    prominence_ratio: float = 0.1,
) -> list[ResonancePeak]:
    """Detect resonance peaks in a PSD.

    Parameters
    ----------
    freqs, psd : arrays
        Output of :func:`compute_psd`.
    min_freq, max_freq : float
        Frequency band of interest (Hz).
    prominence_ratio : float
        Minimum prominence as a fraction of the max PSD value.

    Returns
    -------
    List of :class:`ResonancePeak` sorted by amplitude descending.
    """
    # Restrict to the frequency band of interest
    mask = (freqs >= min_freq) & (freqs <= max_freq)
    band_psd = psd[mask]
    band_freqs = freqs[mask]

    if len(band_psd) == 0:
        return []

    min_prominence = prominence_ratio * np.max(band_psd)
    indices, properties = find_peaks(band_psd, prominence=min_prominence)

    peaks: list[ResonancePeak] = []
    for idx, prom in zip(indices, properties["prominences"]):
        peaks.append(
            ResonancePeak(
                frequency=float(band_freqs[idx]),
                amplitude=float(band_psd[idx]),
                prominence=float(prom),
            )
        )

    peaks.sort(key=lambda p: p.amplitude, reverse=True)
    return peaks


# ---------------------------------------------------------------------------
# Input shaper evaluation
# ---------------------------------------------------------------------------

_SHAPER_TYPES = ("zv", "mzv", "ei", "2hump_ei", "3hump_ei")


def _shaper_response(
    shaper_type: str, freq: float, test_freqs: np.ndarray
) -> np.ndarray:
    """Compute the amplitude response of an input shaper at given frequencies."""
    if shaper_type == "zv":
        K = 1 / (1 + np.exp(-np.pi / np.sqrt(1 - 0.05**2)))
        A = np.array([K, 1 - K])
        T = np.array([0, 0.5 / freq])
    elif shaper_type == "mzv":
        b = np.exp(-0.05 * np.pi / np.sqrt(1 - 0.05**2))
        A = np.array([1, 2 * b, b**2]) / (1 + 2 * b + b**2)
        T = np.array([0, 0.375 / freq, 0.75 / freq])
    elif shaper_type == "ei":
        A = np.array([0.25, 0.50, 0.25])
        T = np.array([0, 0.5 / freq, 1.0 / freq])
    elif shaper_type == "2hump_ei":
        A = np.array([0.0625, 0.25, 0.375, 0.25, 0.0625])
        T = np.array([0, 0.5 / freq, 1.0 / freq, 1.5 / freq, 2.0 / freq])
    elif shaper_type == "3hump_ei":
        A = np.array([1 / 64, 6 / 64, 15 / 64, 20 / 64, 15 / 64, 6 / 64, 1 / 64])
        T = np.array([i * 0.5 / freq for i in range(7)])
    else:
        raise ValueError(f"Unknown shaper type: {shaper_type}")

    response = np.zeros_like(test_freqs, dtype=float)
    for i, f in enumerate(test_freqs):
        if f == 0:
            response[i] = 1.0
            continue
        w = 2 * np.pi * f
        real = sum(a * np.cos(w * t) for a, t in zip(A, T))
        imag = sum(a * np.sin(w * t) for a, t in zip(A, T))
        response[i] = np.sqrt(real**2 + imag**2)
    return response


def evaluate_shapers(
    freqs: np.ndarray,
    psd: np.ndarray,
    shaper_types: tuple[str, ...] | None = None,
) -> list[ShaperResult]:
    """Evaluate all input shaper types across a frequency sweep.

    For each shaper type and candidate frequency (10-200 Hz, 1 Hz steps),
    the shaper's frequency response is applied to *psd* and the remaining
    vibration energy and maximum acceleration loss are computed.

    Returns a list of :class:`ShaperResult` sorted by remaining vibration
    (ascending — best first).
    """
    if shaper_types is None:
        shaper_types = _SHAPER_TYPES

    # Total vibration energy (for normalising)
    total_energy = np.trapz(psd, freqs)
    if total_energy == 0:
        total_energy = 1.0

    results: list[ShaperResult] = []

    for shaper_type in shaper_types:
        best: ShaperResult | None = None
        for candidate_freq in range(10, 201):
            response = _shaper_response(shaper_type, float(candidate_freq), freqs)
            filtered_psd = psd * response**2
            remaining = float(np.trapz(filtered_psd, freqs) / total_energy)

            # Max acceleration loss: the shaper's response at low frequencies
            # (below the shaper frequency) determines how much usable
            # acceleration is lost.  We take the worst-case attenuation.
            low_mask = freqs <= float(candidate_freq) * 0.5
            if np.any(low_mask):
                max_accel_loss = 1.0 - float(np.min(response[low_mask]))
            else:
                max_accel_loss = 0.0

            if best is None or remaining < best.remaining_vibration:
                best = ShaperResult(
                    shaper_type=shaper_type,
                    frequency=float(candidate_freq),
                    remaining_vibration=remaining,
                    max_accel_loss=max_accel_loss,
                )
        if best is not None:
            results.append(best)

    results.sort(key=lambda r: r.remaining_vibration)
    return results
