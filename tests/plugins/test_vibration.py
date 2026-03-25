"""Tests for vibration analysis plugin."""

import numpy as np
import pytest

from printopt.plugins.vibration.plugin import VibrationPlugin
from printopt.plugins.vibration.analysis import (
    compute_psd,
    find_resonance_peaks,
    evaluate_shapers,
    ShaperResult,
)


class TestVibrationPlugin:
    def test_plugin_name(self):
        plugin = VibrationPlugin()
        assert plugin.name == "vibration"

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        plugin = VibrationPlugin()
        await plugin.on_start()
        await plugin.on_stop()


class TestFFTAnalysis:
    def test_compute_psd_basic(self):
        """PSD of a known sine wave should peak at the sine frequency."""
        fs = 3200  # ADXL345 sample rate
        duration = 2.0
        freq = 50.0  # 50 Hz sine wave
        t = np.arange(0, duration, 1/fs)
        signal = np.sin(2 * np.pi * freq * t)

        freqs, psd = compute_psd(signal, fs)

        # Find peak frequency
        peak_idx = np.argmax(psd)
        peak_freq = freqs[peak_idx]
        assert abs(peak_freq - freq) < 2.0  # within 2 Hz

    def test_compute_psd_returns_positive_freqs(self):
        fs = 3200
        signal = np.random.randn(6400)
        freqs, psd = compute_psd(signal, fs)
        assert freqs[0] >= 0
        assert len(freqs) == len(psd)

    def test_find_resonance_peaks_single(self):
        """Should find a single clear peak."""
        fs = 3200
        duration = 2.0
        t = np.arange(0, duration, 1/fs)
        signal = np.sin(2 * np.pi * 45 * t) + 0.1 * np.random.randn(len(t))

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        assert len(peaks) >= 1
        assert any(abs(p.frequency - 45) < 3 for p in peaks)

    def test_find_resonance_peaks_multiple(self):
        """Should find two distinct peaks."""
        fs = 3200
        duration = 2.0
        t = np.arange(0, duration, 1/fs)
        signal = (np.sin(2 * np.pi * 40 * t) +
                  0.7 * np.sin(2 * np.pi * 80 * t) +
                  0.1 * np.random.randn(len(t)))

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        peak_freqs = sorted([p.frequency for p in peaks])
        assert len(peaks) >= 2
        assert any(abs(f - 40) < 5 for f in peak_freqs)
        assert any(abs(f - 80) < 5 for f in peak_freqs)

    def test_evaluate_shapers(self):
        """Should return scored shaper recommendations."""
        fs = 3200
        duration = 2.0
        t = np.arange(0, duration, 1/fs)
        signal = np.sin(2 * np.pi * 45 * t) + 0.1 * np.random.randn(len(t))

        freqs, psd = compute_psd(signal, fs)
        results = evaluate_shapers(freqs, psd)

        assert len(results) > 0
        assert all(isinstance(r, ShaperResult) for r in results)
        assert all(r.remaining_vibration >= 0 for r in results)
        # Best result should be first
        assert results[0].remaining_vibration <= results[-1].remaining_vibration
        # Should have a shaper type and frequency
        assert results[0].shaper_type in ("zv", "mzv", "ei", "2hump_ei", "3hump_ei")
        assert results[0].frequency > 0
