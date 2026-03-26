"""Tests for flow compensation plugin."""

import pytest

from printopt.core.gcode import Feature, FeatureType
from printopt.plugins.flow.plugin import FlowPlugin
from printopt.plugins.flow.compensator import (
    FlowCompensator,
    CompensationProfile,
    Compensation,
)


class TestFlowPlugin:
    def test_plugin_name(self):
        plugin = FlowPlugin()
        assert plugin.name == "flow"

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        plugin = FlowPlugin()
        await plugin.on_start()
        await plugin.on_stop()

    @pytest.mark.asyncio
    async def test_parse_on_print_start(self):
        plugin = FlowPlugin()
        gcode = "G1 Z0.2 F3000\nG1 X10 Y10 E0.5 F1500\nG1 X50 Y10 E2.0\nG1 X50 Y50 E4.0"
        await plugin.on_print_start("test.gcode", gcode)
        assert plugin.parse_result is not None
        assert len(plugin.parse_result.moves) > 0

    def test_kill_switch(self):
        plugin = FlowPlugin()
        plugin.kill()
        assert plugin._kill is True
        data = plugin.get_dashboard_data()
        assert data["enabled"] is False

    def test_dashboard_data(self):
        plugin = FlowPlugin()
        data = plugin.get_dashboard_data()
        assert "total_adjustments" in data
        assert "active_compensations" in data

    @pytest.mark.asyncio
    async def test_status_update_tracks_state(self):
        plugin = FlowPlugin()
        await plugin.on_status_update({"state": "printing", "progress": 25.0, "filename": "test.gcode"})
        assert plugin._print_state == "printing"
        assert plugin._current_progress == 25.0

    @pytest.mark.asyncio
    async def test_dashboard_data_during_print(self):
        plugin = FlowPlugin()
        gcode = "G1 Z0.2 F3000\nG1 X10 Y10 E0.5 F1500\nG1 X50 Y10 E2.0\nG1 X50 Y50 E4.0"
        await plugin.on_print_start("test.gcode", gcode)
        data = plugin.get_dashboard_data()
        assert data["filename"] == "test.gcode"
        assert "log" in data
        assert "state" in data

    @pytest.mark.asyncio
    async def test_line_based_compensation(self):
        plugin = FlowPlugin()
        gcode = "G1 Z0.2 F3000\nG1 X10 Y10 E0.5 F1500\nG1 X50 Y10 E2.0\nG1 X50 Y50 E4.0\nG1 X10 Y50 E6.0\nG1 X10 Y10 E8.0"
        await plugin.on_print_start("test.gcode", gcode)
        # Simulate 50% progress
        await plugin.on_status_update({"state": "printing", "progress": 50.0, "filename": "test.gcode"})
        data = plugin.get_dashboard_data()
        assert data["features_ahead"] >= 0
        assert data["state"] == "printing"

    @pytest.mark.asyncio
    async def test_no_duplicate_compensations(self):
        plugin = FlowPlugin()
        gcode = "G1 Z0.2 F3000\nG1 X10 Y10 E0.5 F1500\nG1 X50 Y10 E2.0\nG1 X50 Y50 E4.0"
        await plugin.on_print_start("test.gcode", gcode)
        plugin._current_progress = 10.0
        plugin._print_state = "printing"
        await plugin._apply_compensations()
        first_count = plugin.total_adjustments
        # Apply again at same progress — should not add duplicates
        await plugin._apply_compensations()
        assert plugin.total_adjustments == first_count

    @pytest.mark.asyncio
    async def test_inject_with_retry(self):
        """Test that injection retries on failure."""
        plugin = FlowPlugin()

        call_count = 0

        class MockMoonraker:
            async def inject(self, gcode):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise TimeoutError("timeout")

        plugin._moonraker = MockMoonraker()
        await plugin._inject_with_retry("G1 X10")
        assert call_count == 3  # failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_inject_with_retry_gives_up(self):
        """Test that injection raises after max retries."""
        plugin = FlowPlugin()

        class MockMoonraker:
            async def inject(self, gcode):
                raise TimeoutError("timeout")

        plugin._moonraker = MockMoonraker()
        with pytest.raises(TimeoutError):
            await plugin._inject_with_retry("G1 X10", max_retries=2)


class TestFlowCompensator:
    def test_corner_compensation(self):
        comp = FlowCompensator(baseline_pa=0.04)
        features = [
            Feature(
                type=FeatureType.CORNER,
                line_number=100,
                estimated_time=5.0,
                angle=90.0,
            )
        ]
        result = comp.compute_compensations(features, current_time=0.0, lookahead_seconds=10.0)
        assert len(result) == 2  # boost + restore
        assert "SET_PRESSURE_ADVANCE" in result[0].value
        assert "0.0520" in result[0].value  # 0.04 * 1.3
        assert "0.0400" in result[1].value  # restore

    def test_corner_below_threshold_ignored(self):
        comp = FlowCompensator(baseline_pa=0.04)
        features = [
            Feature(type=FeatureType.CORNER, line_number=100, estimated_time=5.0, angle=30.0)
        ]
        result = comp.compute_compensations(features, current_time=0.0)
        assert len(result) == 0

    def test_bridge_compensation(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.BRIDGE, line_number=200, estimated_time=10.0)
        ]
        result = comp.compute_compensations(features, current_time=5.0)
        assert len(result) == 2  # flow + fan
        assert any("M221" in c.value for c in result)
        assert any("M106" in c.value for c in result)

    def test_thin_wall_compensation(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.THIN_WALL, line_number=300, estimated_time=15.0)
        ]
        result = comp.compute_compensations(features, current_time=10.0)
        assert len(result) == 1
        assert "M220 S80" in result[0].value

    def test_small_perimeter_compensation(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.SMALL_PERIMETER, line_number=400, estimated_time=20.0)
        ]
        result = comp.compute_compensations(features, current_time=15.0)
        assert len(result) == 1
        assert "M220 S70" in result[0].value

    def test_lookahead_window(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.CORNER, line_number=100, estimated_time=2.0, angle=90.0),
            Feature(type=FeatureType.CORNER, line_number=200, estimated_time=20.0, angle=90.0),
        ]
        result = comp.compute_compensations(features, current_time=0.0, lookahead_seconds=5.0)
        # Only the first corner should be in the window
        assert all(c.estimated_time <= 5.2 for c in result)  # 5.0 + 0.2 for PA restore

    def test_past_features_skipped(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.CORNER, line_number=100, estimated_time=2.0, angle=90.0),
        ]
        result = comp.compute_compensations(features, current_time=5.0)
        assert len(result) == 0

    def test_pa_clamped_to_max(self):
        profile = CompensationProfile(corner_pa_boost=5.0, max_pa_multiplier=2.0)
        comp = FlowCompensator(profile=profile, baseline_pa=0.04)
        features = [
            Feature(type=FeatureType.CORNER, line_number=100, estimated_time=5.0, angle=90.0)
        ]
        result = comp.compute_compensations(features, current_time=0.0)
        # PA should be clamped to 2x baseline = 0.08
        boost_comp = result[0]
        pa_value = float(boost_comp.value.split("ADVANCE=")[1])
        assert pa_value <= 0.08 + 0.001

    def test_custom_profile(self):
        profile = CompensationProfile(bridge_flow=0.90, bridge_fan=80.0)
        comp = FlowCompensator(profile=profile)
        features = [
            Feature(type=FeatureType.BRIDGE, line_number=200, estimated_time=10.0)
        ]
        result = comp.compute_compensations(features, current_time=5.0)
        flow_comp = [c for c in result if "M221" in c.value][0]
        assert "S90" in flow_comp.value

    def test_sorted_by_time(self):
        comp = FlowCompensator()
        features = [
            Feature(type=FeatureType.BRIDGE, line_number=200, estimated_time=8.0),
            Feature(type=FeatureType.CORNER, line_number=100, estimated_time=5.0, angle=90.0),
        ]
        result = comp.compute_compensations(features, current_time=0.0, lookahead_seconds=15.0)
        times = [c.estimated_time for c in result]
        assert times == sorted(times)
