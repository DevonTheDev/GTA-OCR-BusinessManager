"""Scoped heist results through real app ownership, trackers and disposable SQLite."""

from datetime import timedelta

import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_heist_success_results import OBSERVED_CAYO_RESULT
from tests.test_mission_result_accounting import clock as clock
from tests.test_terminal_mission_identity_ownership import capture_frame, mission_stats, stored


@pytest.mark.parametrize("first,result,name,kind,phase", [
    ("Cayo Perico", OBSERVED_CAYO_RESULT, "Cayo Perico", ActivityType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico Finale", OBSERVED_CAYO_RESULT, "Cayo Perico Finale", ActivityType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("Cayo Perico", "heist passed\nCayo Perico\nFinale", "Cayo Perico", ActivityType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("Casino Heist", "heist passed\nThe Big Con", "The Big Con", ActivityType.CASINO_HEIST, MissionType.UNKNOWN),
    ("Casino Heist", "heist passed\nThe Big Con\nFinale", "The Big Con", ActivityType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("Go to the location", "heist passed\nThe Big Con", "The Big Con", ActivityType.CASINO_HEIST, MissionType.UNKNOWN),
    ("Cayo Perico", "HEIST\nPASSED\nThe Cayo Perico Heist", "Cayo Perico", ActivityType.CAYO_PERICO, MissionType.UNKNOWN),
])
def test_qualified_completion_refines_only_explicit_axes_and_is_consumed_once(app, clock, first, result, name, kind, phase):
    completed = []
    app.on_mission_complete(completed.append)
    capture_frame(app, banner=first)
    current = app._activity_tracker.current_activity
    started = current.started_at
    clock.value += timedelta(seconds=45)
    observed = capture_frame(app, banner=result, balance=1250)
    assert observed.game_state == GameState.MISSION_COMPLETE
    assert observed.mission.outcome_scope == "heist"
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == completed == [current]
    assert current.started_at == started
    assert app._data.terminal_mission_episode.identity.phase == phase
    row, = stored(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        name, kind.name, True, 45, 250,
    )
    assert mission_stats(app) == (1, 1, 0, 0)
    capture_frame(app, banner=result, balance=1250)
    assert stored(app)["activities"] == [row]
    assert completed == [current] and mission_stats(app) == (1, 1, 0, 0)
    assert app.session_earnings == 250


REJECTED = [
    ("Headhunter", OBSERVED_CAYO_RESULT),
    ("Headhunter", "heist passed\nHeist Finale"),
    ("Deliver the goods", OBSERVED_CAYO_RESULT),
    ("Casino Heist", OBSERVED_CAYO_RESULT),
    ("The Big Con", "heist passed\nSilent & Sneaky"),
    ("Cayo Perico Prep", OBSERVED_CAYO_RESULT),
    ("The Big Con\nPrep", "heist passed\nThe Big Con"),
    ("Heist prep", OBSERVED_CAYO_RESULT),
    ("Cayo Perico", "heist passed"),
    ("Cayo Perico", "HEIST\nPASSED"),
    ("Cayo Perico", "heist passed\nCayo Perico Prep"),
    ("Cayo Perico", OBSERVED_CAYO_RESULT + "\nMISSION FAILED"),
    ("Cayo Perico", OBSERVED_CAYO_RESULT + "\nCasino Heist"),
    ("Cayo Perico", OBSERVED_CAYO_RESULT + "\nHeadhunter"),
]


@pytest.mark.parametrize("first,result", REJECTED)
def test_rejected_result_preserves_owner_timing_money_and_later_completion(app, clock, first, result):
    completed = []
    app.on_mission_complete(completed.append)
    capture_frame(app, banner=first)
    current = app._activity_tracker.current_activity
    baseline = (current.name, current.activity_type, current.started_at, app._data.mission_start_time,
                app._data.mission_start_money, app._data.mission_identity_type, app._data.mission_heist_phase)
    transitions = tuple(app._state_machine.context.transitions)
    clock.value += timedelta(seconds=20)
    rejected = capture_frame(app, banner=result, balance=1100)
    assert rejected.game_state == GameState.UNKNOWN and rejected.state_confidence == 0
    assert rejected.banner_text == rejected.mission.raw_text == result
    assert app._activity_tracker.current_activity is current
    assert (current.name, current.activity_type, current.started_at, app._data.mission_start_time,
            app._data.mission_start_money, app._data.mission_identity_type, app._data.mission_heist_phase) == baseline
    assert tuple(app._state_machine.context.transitions) == transitions
    assert app._data.terminal_mission_episode is None
    assert completed == stored(app)["activities"] == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert rejected.money_change == 100 and app.current_money == 1100
    # Rejected text does not relabel the money as a completed mission. Existing
    # active selling/prep states retain their ordinary coarse earning source.
    source = {ActivityType.SELL_MISSION: "Sell Mission", ActivityType.HEIST_PREP: "Heist"}.get(
        current.activity_type, "",
    )
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [(100, source)]
    clock.value += timedelta(seconds=10)
    accepted = capture_frame(app, banner="MISSION PASSED\n" + first, balance=1200)
    assert accepted.game_state == GameState.MISSION_COMPLETE
    assert completed == [current]
    assert mission_stats(app) == (1, 1, 0, int(current.activity_type == ActivityType.SELL_MISSION))
    row, = stored(app)["activities"]
    assert row["duration_seconds"] == 30 and row["earnings"] == 200


@pytest.mark.parametrize("first,result", REJECTED)
def test_direct_terminal_calls_cannot_bypass_scope_ownership(app, first, result):
    capture_frame(app, banner=first)
    current = app._activity_tracker.current_activity
    capture = CaptureResult()
    app._process_state(StateDetectionResult(GameState.MISSION_COMPLETE, 1.0, "direct", banner_text=result), capture)
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0
    assert app._activity_tracker.current_activity is current
    assert stored(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)


@pytest.mark.parametrize("source", ["mission", "center", "banner"])
def test_result_only_screen_and_title_dropout_never_create_fresh_activity(app, source):
    first = capture_frame(app, **{source: OBSERVED_CAYO_RESULT})
    held = capture_frame(app, banner="The Cayo Perico Heist")
    repeated = capture_frame(app, banner=OBSERVED_CAYO_RESULT)
    assert first.game_state == repeated.game_state == GameState.MISSION_COMPLETE
    assert held.game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert stored(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    assert first.activity_name == held.activity_name == repeated.activity_name == ""


def test_scope_qualified_by_an_independent_identity_crop(app):
    capture_frame(app, banner="Cayo Perico")
    result = capture_frame(app, center="heist passed", banner="The Cayo Perico Heist")
    assert result.game_state == GameState.MISSION_COMPLETE
    assert result.mission.outcome_scope == "heist"
    assert len(stored(app)["activities"]) == 1


def test_result_payout_text_never_infers_money(app):
    capture_frame(app, banner="Cayo Perico")
    result = capture_frame(app, banner=OBSERVED_CAYO_RESULT + "\nYour Final Take $1,407,990")
    assert result.game_state == GameState.MISSION_COMPLETE
    assert stored(app)["activities"][0]["earnings"] == 0
    assert stored(app)["earnings"] == [] and app.session_earnings == 0


def test_result_commands_cannot_rearm_the_same_consumed_heist(app):
    result = "HEIST PASSED\nEscape the island"
    first = capture_frame(app, mission="Cayo Perico", banner=result)
    assert first.game_state == GameState.MISSION_COMPLETE
    held = capture_frame(app, mission="Cayo Perico", banner="Escape the island")
    assert held.game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    fresh = capture_frame(app, mission="Cayo Perico", banner="Go to the compound")
    assert fresh.game_state == GameState.MISSION_ACTIVE
    assert app._activity_tracker.current_activity is not None


@pytest.mark.parametrize("text", ["heist passed", "heist passed\nCayo Perico Prep"])
def test_direct_active_callbacks_cannot_turn_unqualified_result_into_a_start(app, text):
    app._process_state(StateDetectionResult(
        GameState.MISSION_ACTIVE, 1.0, "direct active callback", banner_text=text,
    ), CaptureResult())
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert stored(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)


@pytest.mark.parametrize("prose", [
    "If the heist passed earlier, retry the mission.",
    "The guide says the heist passed yesterday.",
])
def test_explanatory_heist_words_do_not_block_ordinary_failure_accounting(app, prose):
    capture_frame(app, banner="Cayo Perico")
    current = app._activity_tracker.current_activity
    result = capture_frame(app, banner="MISSION FAILED\nCayo Perico\n" + prose)
    assert result.game_state == GameState.MISSION_FAILED
    assert result.mission.outcome == "failed" and result.mission.outcome_scope is None
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [current]
    assert mission_stats(app) == (1, 0, 1, 0)
    row, = stored(app)["activities"]
    assert row["name"] == "Cayo Perico" and row["success"] is False and row["earnings"] == 0
