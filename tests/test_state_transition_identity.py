"""Repeated detections do not restart state timers or publish false transitions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.game import state_machine as module
from src.game.state_machine import GameState, GameStateMachine
from src.detection.state_detector import StateDetectionResult
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime(2026, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    return Clock


@pytest.mark.parametrize("state", list(GameState))
def test_unchanged_state_retains_context_elapsed_time_and_history(clock, state):
    machine = GameStateMachine()
    notified = []
    machine.add_listener(notified.append)
    if state != GameState.UNKNOWN:
        assert machine.transition_to(state, trigger="first detection")
    machine.set_mission_name("Captured mission")
    machine.set_business_type("Bunker")
    machine.set_money_at_start(100)
    context = machine.context
    history = list(context.transitions)
    clock.value += timedelta(seconds=90)

    assert machine.transition_to(state, trigger="same detected state") is False
    assert machine.context is context
    assert context.time_in_state == 90
    assert context.mission_name == "Captured mission"
    assert context.business_type == "Bunker" and context.money_at_start == 100
    assert list(context.transitions) == notified == history


def test_real_change_after_repeated_detection_resets_timer_and_notifies(clock):
    machine = GameStateMachine()
    notified = []
    machine.add_listener(notified.append)
    assert machine.transition_to(GameState.IDLE, trigger="idle")
    clock.value += timedelta(seconds=30)
    assert machine.transition_to(GameState.IDLE, trigger="still idle") is False
    assert machine.context.time_in_state == 30
    clock.value += timedelta(seconds=15)
    assert machine.transition_to(GameState.LOADING, trigger="loading")
    assert machine.context.time_in_state == 0
    assert [(item.from_state, item.to_state, item.trigger) for item in notified] == [
        (GameState.UNKNOWN, GameState.IDLE, "idle"),
        (GameState.IDLE, GameState.LOADING, "loading"),
    ]
    clock.value += timedelta(seconds=5)
    assert machine.context.time_in_state == 5
    assert list(machine.context.transitions) == notified


def test_listener_repeating_current_state_does_not_publish_another_event(clock):
    machine = GameStateMachine()
    observed = []
    repeat_results = []

    def repeat(transition):
        observed.append(transition)
        if len(observed) == 1:
            repeat_results.append(
                machine.transition_to(transition.to_state, trigger="listener repeat")
            )

    machine.add_listener(repeat)
    assert machine.transition_to(GameState.MISSION_ACTIVE)
    assert repeat_results == [False]
    assert len(observed) == 1
    assert list(machine.context.transitions) == observed


def test_actual_capture_cycles_keep_one_transition_and_original_active_mission(app, clock):
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()
    app._capture = SimpleNamespace(
        regions=SimpleNamespace(
            full_screen=0, money_display=1, mission_text=2, center_prompt=3, timer_bottom_right=4
        ),
        capture_multiple_regions=lambda regions: [object(), None, None, None, None],
    )
    app._ocr = SimpleNamespace(is_available=False)
    app._state_detector = SimpleNamespace(
        detect=lambda *args, **kwargs: StateDetectionResult(
            state=GameState.MISSION_ACTIVE,
            confidence=1.0,
            mission_text="Headhunter",
            reason="synthetic frame",
        )
    )
    changes = []
    app.on_state_change(lambda old, new: changes.append((old, new)))
    app._state_machine.add_listener(app._on_game_state_transition)

    first = app._do_capture_cycle()
    original_activity = app._activity_tracker.current_activity
    for _ in range(3):
        clock.value += timedelta(seconds=30)
        assert app._do_capture_cycle().game_state == first.game_state

    assert app._state_machine.context.time_in_state == 90
    assert changes == [(GameState.UNKNOWN, GameState.MISSION_ACTIVE)]
    assert len(app._state_machine.get_recent_transitions()) == 1
    assert app._activity_tracker.current_activity is original_activity
    assert app._data.total_captures == 4
