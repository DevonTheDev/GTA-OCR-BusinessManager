"""Terminal ownership through real capture, state, accounting and temporary SQLite.

Capture and OCR text are synthetic; detection, tracking and persistence stay real.
These tests do not claim native Windows OCR or gameplay accuracy.
"""

from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult
from src.detection.state_detector import StateDetector, StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app
from tests.test_mission_result_accounting import clock as clock


def capture_frame(app, mission="", center="", banner="", balance=1000):
    """Provide one screenshot's independent OCR crops, including real money OCR."""
    images = [np.full((120, 200, 3), 100, dtype=np.uint8),
              object(), object(), object(), None, object()]
    words = {id(images[index]): text for index, text in (
        (1, f"${balance:,}"), (2, mission), (3, center), (5, banner),
    )}
    app._capture = SimpleNamespace(
        regions=SimpleNamespace(full_screen=0, money_display=1, mission_text=2,
                                center_prompt=3, timer_bottom_right=4, mission_banner=5),
        capture_multiple_regions=lambda regions: [images[index] for index in regions],
    )
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=words[id(image)]),
    )
    if app._state_machine is None:
        app._state_machine = GameStateMachine()
        app._state_machine.add_listener(app._on_game_state_transition)
    app._perf_monitor = app._perf_monitor or PerformanceMonitor()
    if app._state_detector is None:
        app._state_detector = StateDetector(
            template_matcher=SimpleNamespace(match_any=lambda *args: None),
            ocr_engine=app._ocr,
        )
    app._state_detector._ocr = app._ocr
    return app._do_capture_cycle()


def stored(app):
    return app._repository.export_session_data(app._data.db_session_id)


def mission_stats(app):
    stats = app.session_stats
    return (stats.activities_completed, stats.missions_passed, stats.missions_failed,
            stats.sells_completed)


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
def test_previous_named_result_cannot_consume_next_mission_or_label_its_money(app, clock, outcome):
    completed, transitions, money_changes = [], [], []
    app.on_mission_complete(completed.append)
    app.on_state_change(lambda previous, current: transitions.append((previous, current)))
    app.on_money_change(lambda reading, change: money_changes.append(change))
    capture_frame(app, mission="Headhunter")
    clock.value += timedelta(seconds=120)
    capture_frame(app, banner="MISSION PASSED\nHeadhunter", balance=1250)
    first = app._activity_tracker.completed_activities[0]
    original_cooldown = app.cooldown_tracker.get_cooldown("headhunter")

    capture_frame(app, mission="Sightseer", balance=1250)
    current = app._activity_tracker.current_activity
    baseline = (current.started_at, app._data.mission_start_time, app._data.mission_start_money)
    original_transitions = list(transitions)
    clock.value += timedelta(seconds=10)
    observed = capture_frame(app, center=outcome, banner="Headhunter", balance=1500)

    assert app._activity_tracker.current_activity is current
    assert (current.started_at, app._data.mission_start_time, app._data.mission_start_money) == baseline
    assert observed.game_state == GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert "conflict" in observed.state_reason.lower()
    assert observed.mission_text == ""
    assert observed.objective_text == outcome
    assert observed.banner_text == "Headhunter"
    assert observed.mission.mission_name == "Headhunter"
    assert observed.mission.raw_text == outcome + "\nHeadhunter"
    assert observed.activity_name == "Sightseer"
    assert observed.activity_type == ActivityType.VIP_WORK
    assert observed.activity_identity_status == "known_name"
    assert app._state_machine.state == GameState.MISSION_ACTIVE
    assert transitions == original_transitions
    assert completed == [first]
    assert app._activity_tracker.completed_activities == [first]
    assert mission_stats(app) == (1, 1, 0, 0)
    assert [row["name"] for row in stored(app)["activities"]] == ["Headhunter"]
    assert app.cooldown_tracker.get_cooldown("headhunter") == original_cooldown
    assert app.cooldown_tracker.get_cooldown("sightseer") is None
    # The balance observation is real even though the result belongs elsewhere.
    assert observed.money.display_value == app.current_money == 1500
    assert observed.money_change == 250
    assert money_changes == [250, 250]
    assert app.session_earnings == app.session_stats.total_earnings == 500
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [
        (250, "Headhunter"), (250, ""),
    ]

    # Rejection consumes no result, identity, timing or completion opportunity.
    clock.value += timedelta(seconds=20)
    correct = capture_frame(app, banner=outcome + "\nSightseer", balance=1750)
    success = outcome == "MISSION PASSED"
    expected_state = GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED
    assert correct.game_state == app._state_machine.state == expected_state
    assert correct.state_reason
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [first, current]
    assert mission_stats(app) == (2, 1 + int(success), int(not success), 0)
    assert completed == ([first, current] if success else [first])
    last = stored(app)["activities"][-1]
    assert (last["name"], last["type"], last["success"], last["duration_seconds"], last["earnings"]) == (
        "Sightseer", "VIP_WORK", success, 30, 500 if success else 0,
    )
    assert (app.cooldown_tracker.get_cooldown("sightseer") is not None) is success
    capture_frame(app, banner=outcome + "\nSightseer", balance=1750)
    assert len(stored(app)["activities"]) == 2
    assert mission_stats(app)[0] == 2


CONFLICTS = [
    ("Headhunter", "Sightseer"),                         # confirmed name
    ("Security contract", "Headhunter"),                # family only vs name
    ("Security contract", "VIP work"),                  # family only vs family
    ("Casino Heist", "Doomsday Prep"),                  # heist family
    ("Cayo Perico Prep", "Cayo Perico Finale"),          # phase, same family
    ("The Big Con\nPrep", "The Big Con\nFinale"),       # phase, same name
    ("Headhunter", "Heist prep"),                       # non-heist vs phase
    ("Heist prep", "Headhunter"),                       # phase vs non-heist
]


@pytest.mark.parametrize("first,other", CONFLICTS)
@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
def test_explicit_terminal_conflict_preserves_current_activity(app, clock, first, other, outcome):
    capture_frame(app, mission=first)
    current = app._activity_tracker.current_activity
    original = (current.name, current.activity_type, app._data.mission_identity_type,
                app._data.mission_heist_phase, app._data.mission_start_time)
    active_state = app._state_machine.state
    transitions = tuple(app._state_machine.context.transitions)
    clock.value += timedelta(seconds=90)

    observed = capture_frame(app, banner=outcome + "\n" + other, balance=1100)

    assert app._activity_tracker.current_activity is current
    assert (current.name, current.activity_type, app._data.mission_identity_type,
            app._data.mission_heist_phase, app._data.mission_start_time) == original
    assert observed.game_state == GameState.UNKNOWN and observed.state_confidence == 0.0
    assert "conflict" in observed.state_reason.lower()
    assert app._state_machine.state == active_state
    assert tuple(app._state_machine.context.transitions) == transitions
    assert stored(app)["activities"] == []
    assert app._activity_tracker.completed_activities == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert observed.money_change == 100 and app.current_money == 1100
    assert stored(app)["earnings"][0]["source"] not in (current.name, "Mission")


@pytest.mark.parametrize("first,other", CONFLICTS)
@pytest.mark.parametrize("state,outcome", [
    (GameState.MISSION_COMPLETE, "MISSION PASSED"),
    (GameState.MISSION_FAILED, "MISSION FAILED"),
])
def test_direct_terminal_call_cannot_bypass_identity_ownership(app, first, other, state, outcome):
    capture_frame(app, mission=first)
    current = app._activity_tracker.current_activity
    capture = CaptureResult()
    app._process_state(StateDetectionResult(
        state, 1.0, "direct result", banner_text=outcome + "\n" + other,
    ), capture)
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0.0
    assert "conflict" in capture.state_reason.lower()
    assert capture.banner_text == capture.mission.raw_text == outcome + "\n" + other
    assert app._activity_tracker.current_activity is current
    assert stored(app)["activities"] == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []


@pytest.mark.parametrize("first,title,name,kind", [
    ("Headhunter", "", "Headhunter", ActivityType.VIP_WORK),
    ("Headhunter", "Headhunter", "Headhunter", ActivityType.VIP_WORK),
    ("Security contract", "Recover Valuables", "Recover Valuables", ActivityType.SECURITY_CONTRACT),
    ("Go to the location", "Headhunter", "Headhunter", ActivityType.VIP_WORK),
    ("Casino Heist", "The Big Con\nFinale", "The Big Con", ActivityType.HEIST_FINALE),
    ("Cayo Perico Prep", "Cayo Perico Prep", "Cayo Perico Prep", ActivityType.HEIST_PREP),
    ("Go to the location", "Headhunter\nSightseer", "Go to the location", ActivityType.UNKNOWN),
    # Ambiguous candidates still do not establish a conflicting known axis.
    ("Headhunter", "Headhunter\nSightseer", "Headhunter", ActivityType.VIP_WORK),
])
@pytest.mark.parametrize("outcome,success", [("MISSION PASSED", True), ("MISSION FAILED", False)])
def test_generic_compatible_and_unresolved_results_keep_existing_completion_behavior(
    app, clock, first, title, name, kind, outcome, success,
):
    capture_frame(app, mission=first)
    current = app._activity_tracker.current_activity
    original_start = current.started_at
    clock.value += timedelta(seconds=45)
    observed = capture_frame(app, banner=outcome + ("\n" + title if title else ""), balance=1300)
    expected_state = GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED
    assert observed.game_state == expected_state
    assert observed.state_confidence > 0.6
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [current]
    assert current.started_at == original_start
    row, = stored(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        name, kind.name, success, 45, 300 if success else 0,
    )
    assert mission_stats(app) == (1, int(success), int(not success), 0)


def test_direct_reading_preserves_independent_crop_boundaries(app):
    # These separate observations cannot manufacture the known wrapped title.
    result = StateDetectionResult(GameState.MISSION_ACTIVE, 1.0, "independent crops",
                                  mission_text="Executive", objective_text="Search")
    app._process_state(result, CaptureResult())
    assert app._data.mission_identity_status == "unknown"
    assert app._activity_tracker.current_activity.activity_type == ActivityType.UNKNOWN
