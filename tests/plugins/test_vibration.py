"""Tests for vibration analysis plugin."""

import numpy as np
import pytest

from printopt.plugins.vibration.plugin import VibrationPlugin
from printopt.plugins.vibration.analysis import (
    compute_psd,
    compute_psd_multitaper,
    find_resonance_peaks,
    evaluate_shapers,
    analyze_raw_data,
    design_custom_shaper,
    ShaperResult,
    ResonancePeak,
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


class TestVibrationPluginResults:
    @pytest.mark.asyncio
    async def test_store_and_retrieve_results(self, tmp_path):
        plugin = VibrationPlugin()
        plugin._results_path = tmp_path / "results.json"

        peaks = [ResonancePeak(frequency=45.0, amplitude=0.5, prominence=0.3)]
        shapers = [ShaperResult(shaper_type="ei", frequency=45.0, remaining_vibration=0.01, max_accel_loss=0.1)]

        plugin.store_results("x", peaks, shapers, [1.0, 2.0], [0.1, 0.2])

        assert "x" in plugin.results
        assert plugin.results["x"]["best"]["shaper_type"] == "ei"
        assert (tmp_path / "results.json").exists()

    def test_dashboard_data_with_results(self):
        plugin = VibrationPlugin()
        plugin.results = {
            "x": {
                "peaks": [{"frequency": 45.0, "amplitude": 0.5, "prominence": 0.3}],
                "best": {"shaper_type": "ei", "frequency": 45.0},
                "shapers": [],
                "psd_freqs": [1.0, 2.0],
                "psd_values": [0.1, 0.2],
            }
        }
        data = plugin.get_dashboard_data()
        assert "x" in data["results"]
        assert data["results"]["x"]["best"]["shaper_type"] == "ei"


class TestEnhancedAnalysis:
    def test_compute_psd_high_resolution(self):
        """High-res PSD should have more frequency bins than default."""
        fs = 3200
        duration = 5.0
        t = np.arange(0, duration, 1/fs)
        signal = np.sin(2 * np.pi * 50 * t) + 0.1 * np.random.randn(len(t))

        freqs, psd = compute_psd(signal, fs)
        # With 2-second windows at 3200Hz, should have ~400 freq bins in 1-200Hz range
        assert len(freqs) > 200

    def test_compute_psd_multitaper(self):
        """Multi-taper should produce smoother PSD."""
        fs = 3200
        t = np.arange(0, 3.0, 1/fs)
        signal = np.sin(2 * np.pi * 60 * t) + 0.2 * np.random.randn(len(t))

        freqs, psd = compute_psd_multitaper(signal, fs)
        assert len(freqs) > 0
        peak_idx = np.argmax(psd)
        assert abs(freqs[peak_idx] - 60) < 3

    def test_fine_frequency_sweep(self):
        """0.1Hz steps should give more precise frequency than 1Hz."""
        fs = 3200
        t = np.arange(0, 3.0, 1/fs)
        signal = np.sin(2 * np.pi * 45.3 * t) + 0.1 * np.random.randn(len(t))

        freqs, psd = compute_psd(signal, fs)

        # Fine sweep (0.1Hz)
        fine_results = evaluate_shapers(freqs, psd, freq_step=0.1)
        # Coarse sweep (1Hz)
        coarse_results = evaluate_shapers(freqs, psd, freq_step=1.0)

        # Fine should have better (lower) remaining vibration
        assert fine_results[0].remaining_vibration <= coarse_results[0].remaining_vibration + 0.01

    def test_analyze_raw_data(self):
        """Full pipeline from raw CSV should produce results."""
        # Generate fake raw CSV
        fs = 3200
        t = np.arange(0, 2.0, 1/fs)
        accel_x = np.sin(2 * np.pi * 50 * t) + 0.1 * np.random.randn(len(t))
        accel_y = 0.5 * np.sin(2 * np.pi * 80 * t) + 0.1 * np.random.randn(len(t))
        accel_z = 0.1 * np.random.randn(len(t))

        lines = ["#time,accel_x,accel_y,accel_z"]
        for i in range(len(t)):
            lines.append(f"{t[i]:.6f},{accel_x[i]:.4f},{accel_y[i]:.4f},{accel_z[i]:.4f}")
        raw_csv = "\n".join(lines)

        freqs, psd, peaks, shapers = analyze_raw_data(raw_csv, axis="x", fs=fs)
        assert len(freqs) > 100
        assert len(peaks) >= 1
        assert any(abs(p.frequency - 50) < 5 for p in peaks)
        assert len(shapers) > 0
        assert shapers[0].frequency > 0


class TestCustomShaper:
    def test_design_custom_shaper_single_peak(self):
        """Custom shaper for a single peak should produce 2 pulses (ZV-like)."""
        fs = 3200
        t = np.arange(0, 3.0, 1 / fs)
        signal = np.sin(2 * np.pi * 50 * t) + 0.1 * np.random.randn(len(t))

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        assert len(peaks) >= 1

        A, T, remaining = design_custom_shaper(freqs, psd, peaks)
        assert len(A) == 2  # single peak -> ZV = 2 pulses
        assert len(T) == 2
        assert T[0] == 0.0  # first pulse at t=0
        assert abs(sum(A) - 1.0) < 1e-9  # normalized
        assert 0.0 < remaining < 1.0

    def test_design_custom_shaper_two_peaks(self):
        """Custom shaper for two peaks should produce 4 pulses (2 ZV convolved)."""
        fs = 3200
        t = np.arange(0, 3.0, 1 / fs)
        signal = (
            np.sin(2 * np.pi * 40 * t)
            + 0.8 * np.sin(2 * np.pi * 85 * t)
            + 0.1 * np.random.randn(len(t))
        )

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        assert len(peaks) >= 2

        A, T, remaining = design_custom_shaper(freqs, psd, peaks)
        assert len(A) == 4  # two peaks -> 2x2 = 4 pulses
        assert len(T) == 4
        assert T[0] == 0.0
        assert abs(sum(A) - 1.0) < 1e-9
        assert 0.0 < remaining < 1.0

    def test_design_custom_shaper_three_peaks(self):
        """Custom shaper for three peaks should produce 8 pulses (2^3)."""
        fs = 3200
        t = np.arange(0, 3.0, 1 / fs)
        signal = (
            np.sin(2 * np.pi * 35 * t)
            + 0.7 * np.sin(2 * np.pi * 70 * t)
            + 0.5 * np.sin(2 * np.pi * 120 * t)
            + 0.1 * np.random.randn(len(t))
        )

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        assert len(peaks) >= 3

        A, T, remaining = design_custom_shaper(freqs, psd, peaks)
        assert len(A) == 8  # three peaks -> 2^3 = 8 pulses
        assert len(T) == 8
        assert len(A) <= 12  # within Klipper limit
        assert T[0] == 0.0
        assert abs(sum(A) - 1.0) < 1e-9
        # Pulses should be sorted by time
        for i in range(len(T) - 1):
            assert T[i] <= T[i + 1]

    def test_design_custom_shaper_no_peaks(self):
        """No peaks should return empty shaper."""
        freqs = np.linspace(1, 200, 1000)
        psd = np.ones_like(freqs)
        A, T, remaining = design_custom_shaper(freqs, psd, [])
        assert A == []
        assert T == []
        assert remaining == 1.0

    def test_design_custom_shaper_beats_preset(self):
        """Custom shaper should outperform presets on multi-peak signals."""
        fs = 3200
        t = np.arange(0, 3.0, 1 / fs)
        # Two strong resonances far apart -- hard for single-frequency shapers
        signal = (
            np.sin(2 * np.pi * 40 * t)
            + np.sin(2 * np.pi * 90 * t)
            + 0.1 * np.random.randn(len(t))
        )

        freqs, psd = compute_psd(signal, fs)
        peaks = find_resonance_peaks(freqs, psd)
        presets = evaluate_shapers(freqs, psd, freq_step=1.0)

        custom_A, custom_T, custom_remaining = design_custom_shaper(freqs, psd, peaks)

        # Custom should have lower remaining vibration than worst preset
        assert custom_remaining < presets[-1].remaining_vibration

    def test_design_custom_shaper_max_pulses_respected(self):
        """Pulse count should never exceed max_pulses."""
        peaks = [
            ResonancePeak(frequency=30.0, amplitude=1.0, prominence=0.5),
            ResonancePeak(frequency=60.0, amplitude=0.8, prominence=0.4),
            ResonancePeak(frequency=90.0, amplitude=0.6, prominence=0.3),
        ]
        freqs = np.linspace(1, 200, 1000)
        psd = np.ones_like(freqs) * 0.01

        # With max_pulses=4, the 8-pulse result from 3 peaks must be merged
        A, T, remaining = design_custom_shaper(freqs, psd, peaks, max_pulses=4)
        assert len(A) <= 4
        assert len(T) <= 4
