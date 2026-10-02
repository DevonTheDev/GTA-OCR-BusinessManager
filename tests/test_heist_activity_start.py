"""Detected heist states reach the existing activity/SQLite result lifecycle."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.app import CaptureResult
from src.detection.parsers.money_parser import MoneyReading
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_mission_result_accounting import (
    app as app,
    clock as clock,
    activities,
    detect,
)


HEISTS = [
    ("heist prep: scope out island", GameState.HEIST_PREP, ActivityType.HEIST_PREP),
    ("heist finale", GameState.HEIST_FINALE, ActivityType.HEIST_FINALE),
]


def classify(text):
    ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda *args, **kwargs: SimpleNamespace(text=text),
    )
    detector = StateDetector(template_matcher=object(), ocr_engine=ocr)
    return detector._ocr_state_check(object(), None)


@pytest.mark.parametrize("text,state,kind", HEISTS)
@pytest.mark.parametrize("success", [True, False])
def test_real_text_classifier_starts_heist_and_persists_its_result(
    app, clock, text, state, kind, success
):
    reading = classify(text)
    assert reading.state == state
    app._process_money_change(MoneyReading(total=1000))
    app._process_state(reading, CaptureResult())
    tracked = app._activity_tracker.current_activity
    assert tracked is not None
    assert tracked.activity_type == kind
    assert tracked.name == text
    assert app._data.mission_start_money == 1000
    assert app._data.mission_start_time == clock.value
    clock.value += timedelta(seconds=120)
    app._process_money_change(MoneyReading(total=1500))
    detect(app, GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED)
    (row,) = activities(app)
    assert row["type"] == kind.name and row["name"] == text
    assert row["earnings"] == (500 if success else 0)
    assert row["success"] is success and row["duration_seconds"] == 120
    assert app.session_stats.activities_completed == 1
    assert app.session_stats.missions_passed == int(success)
    assert app.session_stats.missions_failed == int(not success)
    assert app.session_stats.sells_completed == 0
    assert app._data.mission_start_time is None


@pytest.mark.parametrize("text,state,kind", HEISTS)
def test_repeated_or_generic_states_do_not_replace_active_heist(app, clock, text, state, kind):
    reading = classify(text)
    app._data.current_money = 1000
    app._process_state(reading, CaptureResult())
    tracked = app._activity_tracker.current_activity
    assert tracked is not None
    started = app._data.mission_start_time
    for later in (state, GameState.MISSION_ACTIVE, GameState.HEIST_PREP, GameState.HEIST_FINALE):
        clock.value += timedelta(seconds=10)
        app._data.current_money = 1250
        detect(app, later, "Later noisy title")
        assert app._activity_tracker.current_activity is tracked
        assert app._data.mission_start_time == started
        assert app._data.mission_start_money == 1000
        assert tracked.activity_type == kind and tracked.name == text
    detect(app, GameState.MISSION_COMPLETE)
    assert len(activities(app)) == 1
    assert activities(app)[0]["duration_seconds"] == 40


@pytest.mark.parametrize(
    "state", [GameState.IDLE, GameState.LOADING, GameState.MISSION_STARTING, GameState.UNKNOWN]
)
def test_non_active_states_still_do_not_start_an_activity(app, state):
    detect(app, state, "heist prep")
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert activities(app) == []
