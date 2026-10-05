"""Explicit recovery through the real parser, tracker and temporary SQLite.

Synthetic OCR boundaries only; no gameplay, native capture or accuracy claim.
"""

import copy
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest

from src.app import AppState, CaptureResult
from src.detection.mission_episode import MissionIdentity, ObjectiveEvidence, TerminalMissionEpisode
from src.detection.parsers.mission_parser import MissionType
from src.detection.parsers.money_parser import MoneyReading
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState
from src.tracking.goals import GoalType
from tests.test_app_accounting import app as app
from tests.test_mission_banner_capture import banner_frame
from tests.test_mission_result_accounting import clock as clock
from tests.test_terminal_mission_identity_ownership import capture_frame, mission_stats, stored


def recovery_status(app):
    assert hasattr(app, "get_detection_recovery_status"), "Expose paused detection recovery readiness"
    return app.get_detection_recovery_status()


def paused_snapshot(app):
    app._state = AppState.RUNNING
    app.pause()
    status = recovery_status(app)
    assert status.snapshot is not None, status.message
    return status.snapshot


def discard(app):
    snapshot = paused_snapshot(app)
    success, message = app.discard_detected_activity(snapshot)
    assert success, message
    return snapshot


def database_contents(app):
    # export_session_data includes a computed live duration, not a stored value.
    with sqlite3.connect(app._repository._db_path) as connection:
        return tuple(connection.iterdump())


def test_missed_result_can_be_discarded_before_new_activity_with_fresh_baseline(app, clock):
    completions = []
    app.on_mission_complete(completions.append)
    capture_frame(app, banner="Headhunter", balance=1000)
    old = app._activity_tracker.current_activity
    app._state = AppState.RUNNING
    app.pause()
    clock.value += timedelta(seconds=120)
    app.resume()
    different = capture_frame(app, banner="Sightseer", mission="Collect the packages", balance=1250)
    assert different.mission.mission_name == "Sightseer"
    assert different.activity_name == old.name == "Headhunter"
    app._last_capture_result = different
    before = database_contents(app)

    discard(app)

    assert app.state is AppState.PAUSED
    assert app._activity_tracker.current_activity is None
    assert app.last_capture is None
    assert database_contents(app) == before
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert completions == []
    assert app.current_money == 1250
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert recovery_status(app).waiting_for_balance
    app.resume()
    clock.value += timedelta(seconds=30)
    fresh = capture_frame(app, banner="Sightseer", mission="Collect the packages", balance=1500)
    assert fresh.activity_name == "Sightseer"
    assert app._data.mission_start_time == clock.value
    assert app._data.mission_start_money == 1500
    assert not recovery_status(app).waiting_for_balance
    clock.value += timedelta(seconds=60)
    capture_frame(app, banner="MISSION PASSED\nSightseer", balance=1750)
    row, = stored(app)["activities"]
    assert (row["name"], row["earnings"], row["duration_seconds"]) == ("Sightseer", 250, 60)
    assert mission_stats(app) == (1, 1, 0, 0)
    assert [activity.name for activity in completions] == ["Sightseer"]
    assert app.cooldown_tracker.get_cooldown("headhunter") is None
    assert app.cooldown_tracker.get_cooldown("sightseer") is not None
    assert app.session_earnings == app.session_stats.total_earnings == 750
    assert [row["amount"] for row in stored(app)["earnings"]] == [250, 250, 250]
    capture_frame(app, banner="MISSION PASSED\nSightseer", balance=1750)
    assert stored(app)["activities"] == [row]
    assert mission_stats(app) == (1, 1, 0, 0)


def test_discard_preserves_saved_history_money_goals_business_and_observed_state(app, clock):
    capture_frame(app, banner="Hostile Takeover", balance=700)
    clock.value += timedelta(seconds=20)
    capture_frame(app, banner="MISSION PASSED\nHostile Takeover", balance=1000)
    capture_frame(app, banner="Headhunter", mission="Eliminate the targets", balance=1000)
    app.set_session_goal(GoalType.EARNINGS, 1000)
    app.refresh_session_goal()
    app._data.business_states["bunker"] = {"stock": 30, "supply": 40}
    app._data.terminal_mission_episode = TerminalMissionEpisode(MissionIdentity(name="old"))
    before_data, before_stats = app.data, copy.deepcopy(app.session_stats)
    before_rows, history = database_contents(app), app.recent_activities
    goal = copy.deepcopy(app.goal_tracker.current_goal)
    cooldown_file = app.cooldown_tracker._data_path
    cooldown_bytes = cooldown_file.read_bytes()
    state = app.game_state
    transitions = tuple(app._state_machine.context.transitions)
    events = []
    app.on_state_change(lambda *args: events.append(args))
    app.on_mission_complete(lambda *args: events.append(args))
    app.on_money_change(lambda *args: events.append(args))
    app.on_capture(lambda *args: events.append(args))

    discard(app)

    assert events == []
    assert app.session_stats == before_stats
    assert app.goal_tracker.current_goal == goal
    assert database_contents(app) == before_rows
    assert app.recent_activities == history
    assert cooldown_file.read_bytes() == cooldown_bytes
    assert app.game_state == state
    assert tuple(app._state_machine.context.transitions) == transitions
    for name in ("current_money", "session_start_money", "session_earnings", "last_money_change",
                 "last_money_change_time", "business_states", "db_start_money", "db_session_id", "character_id"):
        assert getattr(app.data, name) == getattr(before_data, name)
    assert app._data.current_mission is app._data.mission_start_time is app._data.mission_start_money is None
    assert app._data.mission_identity_status == "unknown"
    assert app._data.mission_identity_type is app._data.mission_heist_phase is MissionType.UNKNOWN
    assert app._data.mission_objectives == ObjectiveEvidence()
    assert app._data.terminal_mission_episode is None
    assert app._state_detector._context.last_mission_text == ""


@pytest.mark.parametrize("balance", [0, 50, 2_300_000_000, 100_000])
def test_recovery_rejects_existing_invalid_money_candidates(app, balance):
    capture_frame(app, banner="Headhunter", balance=1000)
    discard(app)
    app.resume()
    result = capture_frame(app, banner="Sightseer", balance=balance)
    assert result.money is None  # Includes raw $0: existing MoneyParser rejects values below $100.
    assert app.current_money == 1000
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is app._data.mission_start_money is None
    assert recovery_status(app).waiting_for_balance
    assert stored(app)["activities"] == []


def test_recovery_waits_through_missing_money_idle_balance_and_result_only(app):
    capture_frame(app, banner="Headhunter", balance=1000)
    discard(app)
    app.resume()
    missing, _ = banner_frame(app, banner="Sightseer")
    assert missing.money is None
    assert app._activity_tracker.current_activity is None
    capture_frame(app, balance=1250)  # A readable balance alone is not an activity start.
    missing, _ = banner_frame(app, banner="Sightseer")
    assert app._activity_tracker.current_activity is None
    terminal = capture_frame(app, banner="MISSION PASSED\nSightseer", balance=1500)
    assert terminal.game_state == GameState.MISSION_COMPLETE
    assert app._activity_tracker.current_activity is None
    assert stored(app)["activities"] == []
    assert recovery_status(app).waiting_for_balance
    # A distinct accepted objective rearms the existing terminal fence.
    capture_frame(app, banner="Sightseer", mission="Collect the packages", balance=1500)
    assert app._activity_tracker.current_activity.name == "Sightseer"
    assert app._data.mission_start_money == 1500


@pytest.mark.parametrize("balance", [0, 1250])
def test_recovery_gate_uses_the_accepted_capture_balance_including_zero(app, balance):
    capture_frame(app, banner="Headhunter", balance=1000)
    discard(app)
    # Direct accepted-reading contract: real OCR currently rejects raw $0.
    observed = CaptureResult(money=MoneyReading(total=balance))
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, 1.0, "accepted",
                                           mission_text="Sightseer"), observed)
    assert app._data.mission_start_money == balance
    assert app.current_money == 1000  # No fallback to this unrelated retained balance.
    assert not recovery_status(app).waiting_for_balance


@pytest.mark.parametrize("confidence", [0.0, 0.6])
def test_money_without_confident_activity_keeps_recovery_wait(app, confidence):
    capture_frame(app, banner="Headhunter", balance=1000)
    discard(app)
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, confidence, "weak",
                                           mission_text="Sightseer"),
                       CaptureResult(money=MoneyReading(total=1250)))
    assert app._activity_tracker.current_activity is None
    assert recovery_status(app).waiting_for_balance


def test_ordinary_start_without_money_keeps_existing_behavior(app):
    banner_frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"
    assert app._data.mission_start_money is None


@pytest.mark.parametrize("state", [AppState.RUNNING, AppState.STARTING, AppState.STOPPING, AppState.STOPPED])
def test_recovery_is_unavailable_outside_paused(app, state):
    capture_frame(app, banner="Headhunter")
    snapshot = paused_snapshot(app)
    original = app._activity_tracker.current_activity
    app._state = state
    assert recovery_status(app).snapshot is None
    success, message = app.discard_detected_activity(snapshot)
    assert not success and message
    assert app._activity_tracker.current_activity is original


def test_stop_event_and_missing_mission_start_reject_recovery(app):
    capture_frame(app, banner="Headhunter")
    snapshot = paused_snapshot(app)
    app._stop_event.set()
    assert recovery_status(app).snapshot is None
    assert not app.discard_detected_activity(snapshot)[0]
    app._stop_event.clear()
    app._data.mission_start_time = None
    assert recovery_status(app).snapshot is None
    assert not app.discard_detected_activity(snapshot)[0]


@pytest.mark.parametrize("change", ["resume_pause", "completion", "replacement", "refinement",
                                   "new_run", "start_time", "money_baseline", "identity_axis"])
def test_frozen_confirmation_cannot_discard_changed_ownership(app, change):
    capture_frame(app, mission="Go to the location" if change == "refinement" else "Headhunter")
    snapshot = paused_snapshot(app)
    original = app._activity_tracker.current_activity
    rows_before = stored(app)["activities"]
    if change == "resume_pause":
        captures = app._data.total_captures
        app.resume()
        app.pause()
        assert app._data.total_captures == captures
    elif change == "completion":
        capture_frame(app, banner="MISSION PASSED\nHeadhunter")
    elif change == "replacement":
        newer = app._activity_tracker.start_activity(original.activity_type, original.name)
        newer.started_at = original.started_at  # Same visible fields, distinct owner.
    elif change == "refinement":
        capture_frame(app, banner="Headhunter")
        assert app._activity_tracker.current_activity is original
        assert original.name != snapshot.name
    elif change == "new_run":
        app._data = replace(app._data)  # Same values, distinct run identity.
    elif change == "start_time":
        original.started_at += timedelta(seconds=1)
    elif change == "money_baseline":
        app._data.mission_start_money += 1
    else:
        app._data.mission_heist_phase = MissionType.HEIST_PREP
    target = app._activity_tracker.current_activity
    rows_at_confirmation = stored(app)["activities"]
    success, message = app.discard_detected_activity(snapshot)
    assert not success and "changed" in message.lower()
    assert app._activity_tracker.current_activity is target
    assert stored(app)["activities"] == rows_at_confirmation
    assert not recovery_status(app).waiting_for_balance
    if change != "completion":
        assert rows_at_confirmation == rows_before


def test_snapshots_are_frozen_and_repeat_acceptance_cannot_consume_next_activity(app):
    capture_frame(app, banner="Headhunter")
    snapshot = paused_snapshot(app)
    with pytest.raises(FrozenInstanceError):
        snapshot.name = "Replacement"
    assert app.discard_detected_activity(snapshot)[0]
    assert not app.discard_detected_activity(snapshot)[0]
    capture_frame(app, banner="Sightseer", balance=1250)
    newer = app._activity_tracker.current_activity
    assert newer.name == "Sightseer"
    assert not app.discard_detected_activity(snapshot)[0]
    assert app._activity_tracker.current_activity is newer


def test_normal_two_observed_results_keep_correct_ownership(app, clock):
    capture_frame(app, banner="Headhunter", balance=1000)
    clock.value += timedelta(seconds=120)
    capture_frame(app, banner="MISSION PASSED\nHeadhunter", balance=1250)
    capture_frame(app, banner="Sightseer", mission="Collect the packages", balance=1250)
    clock.value += timedelta(seconds=60)
    capture_frame(app, banner="MISSION PASSED\nSightseer", balance=1500)
    assert [(row["name"], row["earnings"], row["duration_seconds"]) for row in stored(app)["activities"]] == [
        ("Headhunter", 250, 120), ("Sightseer", 250, 60),
    ]
