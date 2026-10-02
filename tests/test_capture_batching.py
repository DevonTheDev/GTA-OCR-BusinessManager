"""Measure capture scheduling without Windows, screenshots, or OCR services."""

from types import SimpleNamespace

import numpy as np
import pytest

from src.app import GTABusinessManager
from src.capture import screen_capture
from src.config.settings import Settings
from src.game.state_machine import GameState
from src.utils.performance import PerformanceMonitor


def test_regular_detection_cycle_rate_limits_once_for_all_hud_regions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        screen_capture,
        "ResolutionScaler",
        lambda _index: SimpleNamespace(width=200, height=100, offset=(10, 20), scale_factor=1.0),
    )
    capture = screen_capture.ScreenCapture()
    waits, grabs = [], []
    monkeypatch.setattr(capture, "_wait_for_rate_limit", lambda: waits.append(True))

    class SyntheticScreen:
        def grab(self, monitor):
            grabs.append(monitor)
            return np.zeros((monitor["height"], monitor["width"], 4), dtype=np.uint8)

    monkeypatch.setattr(capture, "_ensure_mss", lambda: SyntheticScreen())
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    app._capture = capture
    app._perf_monitor = PerformanceMonitor()
    app._ocr = SimpleNamespace(is_available=False)
    app._state_detector = SimpleNamespace(
        detect=lambda *args, **kwargs: SimpleNamespace(
            state=GameState.UNKNOWN, confidence=0.0, mission_text="", objective_text=""
        )
    )
    monkeypatch.setattr(app, "_process_state", lambda *args: None)

    result = app._do_capture_cycle()

    assert len(waits) == 1
    assert len(grabs) == 5
    regions = capture.regions
    expected = [
        regions.full_screen,
        regions.money_display,
        regions.mission_text,
        regions.center_prompt,
        regions.timer_bottom_right,
    ]
    assert grabs == [region.to_mss_monitor(200, 100, 10, 20) for region in expected]
    assert result.game_state == GameState.UNKNOWN
    assert app._data.total_captures == 1


@pytest.mark.parametrize("value", [float("nan"), "NaN"])
def test_nonfinite_capture_rate_cannot_disable_throttling(tmp_path, value):
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert app._validate_fps(value, default=0.5, name="idle_fps") == 0.5
