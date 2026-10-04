"""Heist detections use the existing active capture and HUD timer policy."""

from types import SimpleNamespace

import pytest

from src.app import AppState
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


def run_two_cycles(app, monkeypatch, state, *, active_fps=3.0, timer_available=True):
    timer = object() if timer_available else None
    reads, rates, results = [], [], []
    app._settings.set("capture.active_fps", active_fps, save=False, validate=False)
    app._settings.set("capture.idle_fps", 0.25, save=False)
    app._state = AppState.RUNNING
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()
    app._capture = SimpleNamespace(
        regions=SimpleNamespace(
            full_screen=0, money_display=1, mission_text=2, center_prompt=3, timer_bottom_right=4,
            mission_banner=5,
        ),
        capture_multiple_regions=lambda regions: [object(), None, None, None, timer, None],
        set_capture_rate=rates.append,
    )

    def recognize(image, **kwargs):
        assert image is timer
        reads.append(image)
        return SimpleNamespace(text="5:30")

    app._ocr = SimpleNamespace(is_available=True, recognize_preprocessed=recognize)
    app._state_detector = SimpleNamespace(
        detect=lambda *args, **kwargs: StateDetectionResult(
            state=state, confidence=1.0, mission_text="synthetic mission", reason="synthetic frame"
        )
    )
    # Native resource shutdown and business-computer capture are separate paths.
    monkeypatch.setattr(app, "_finish_stop", lambda worker: None)
    monkeypatch.setattr(app, "_process_business_computer", lambda: None)

    def stop_after_second(result):
        results.append(result)
        if len(results) == 2:
            app._stop_event.set()

    app.on_capture(stop_after_second)
    app._capture_loop()
    assert app._data.total_captures == 2
    return rates, reads, results


@pytest.mark.parametrize(
    "state",
    [GameState.MISSION_ACTIVE, GameState.SELLING, GameState.HEIST_PREP, GameState.HEIST_FINALE],
)
def test_active_states_read_timers_and_apply_configured_active_rate(app, monkeypatch, state):
    rates, reads, results = run_two_cycles(app, monkeypatch, state)
    assert rates == [3.0]
    assert len(reads) == 2
    assert [result.timer.total_seconds for result in results] == [330, 330]
    assert all(result.game_state == state for result in results)
    assert app._activity_tracker.current_activity is not None


@pytest.mark.parametrize("state", [GameState.HEIST_PREP, GameState.HEIST_FINALE])
def test_heists_use_existing_active_rate_validation_fallback(app, monkeypatch, state):
    rates, reads, results = run_two_cycles(app, monkeypatch, state, active_fps=float("nan"))
    assert rates == [2.0]
    assert len(reads) == 2
    assert results[0].timer.formatted == "5:30"


@pytest.mark.parametrize(
    "state,rate",
    [
        (GameState.IDLE, 0.25),
        (GameState.UNKNOWN, 0.25),
        (GameState.MISSION_STARTING, 0.25),
        (GameState.BUSINESS_COMPUTER, 4.0),
    ],
)
def test_non_active_states_keep_existing_rate_and_skip_timer_region(app, monkeypatch, state, rate):
    rates, reads, results = run_two_cycles(app, monkeypatch, state)
    assert rates == [rate]
    assert reads == []
    assert all(result.timer is None for result in results)


def test_heist_without_timer_capture_keeps_active_rate(app, monkeypatch):
    rates, reads, results = run_two_cycles(
        app, monkeypatch, GameState.HEIST_PREP, timer_available=False
    )
    assert rates == [3.0]
    assert reads == []
    assert all(result.timer is None for result in results)
