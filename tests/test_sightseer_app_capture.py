"""Shared phone-app text must not select the Sightseer VIP mission.

OCR strings are injected at the capture boundary. Detection, activity ownership,
accounting and temporary SQLite stay real; these are not gameplay screenshots.
"""

from datetime import timedelta
from copy import deepcopy

import pytest

from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult
from src.app import CaptureResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_result_accounting import clock as clock
from tests.test_bottom_objective_capture import CaptureHarness, persisted as stored


APP_PROMPTS = (
    "Use the Sightseer app to access the security cameras.",
    "Use the Sightseer app to locate the first drop-off.",
    "Use the Sightseer app to locate the final drop-off.",
    "Use the Sightseer app to find the next package.",
    "Sightseer\napp",
)


@pytest.fixture
def hud(app, monkeypatch):
    return CaptureHarness(app, monkeypatch)


@pytest.mark.parametrize("source", ["top", "center", "banner"])
@pytest.mark.parametrize("prompt", APP_PROMPTS)
def test_shared_app_prompt_cannot_select_sightseer_or_record_accounting(app, hud, source, prompt):
    observed = hud.observe(**{source: prompt})

    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_name == ""
    assert observed.mission.mission_type is MissionType.UNKNOWN
    assert observed.mission.raw_text == prompt
    # Existing generic find/drop objective cues may start an unresolved activity.
    # This fix removes the false catalog name, not that separate admission rule.
    current = app._activity_tracker.current_activity
    if prompt in APP_PROMPTS[1:4]:
        assert current is not None
        assert (current.name, current.activity_type) == (prompt, ActivityType.UNKNOWN)
        assert app._data.mission_identity_status == "unknown"
    else:
        assert current is None
        assert app._data.mission_start_time is None
    assert app._data.business_states == {}
    assert stored(app)["activities"] == stored(app)["earnings"] == []
    assert app.session_stats.activities_completed == 0
    assert app.cooldown_tracker.get_active_cooldowns() == []


@pytest.mark.parametrize("prompt", APP_PROMPTS)
def test_cayo_family_survives_app_prompt_and_owns_its_result(app, hud, clock, prompt):
    observed = hud.observe(top="Cayo Perico", center=prompt)
    current = app._activity_tracker.current_activity

    assert observed.mission.identity_status == "type_only"
    assert observed.mission.mission_type is MissionType.CAYO_PERICO
    assert observed.mission.mission_name == ""
    assert current is not None
    assert (current.name, current.activity_type) == ("Cayo Perico", ActivityType.CAYO_PERICO)
    started = current.started_at
    clock.value += timedelta(seconds=45)
    completed = hud.observe(center=prompt, banner="MISSION PASSED\nCayo Perico")

    assert completed.game_state is GameState.MISSION_COMPLETE
    assert current.started_at == started
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [current]
    (row,) = stored(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        "Cayo Perico", "CAYO_PERICO", True, 45, 0,
    )
    assert app.cooldown_tracker.get_cooldown("sightseer") is None
    before = stored(app)
    hud.observe(center=prompt, banner="MISSION PASSED\nCayo Perico")
    assert stored(app) == before
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize("prompt", APP_PROMPTS)
def test_vip_category_is_not_refined_to_sightseer_from_shared_app(app, hud, clock, prompt):
    hud.observe(top="VIP WORK")
    current = app._activity_tracker.current_activity
    started = current.started_at
    observed = hud.observe(top="VIP WORK", center=prompt)

    assert observed.mission.identity_status == "type_only"
    assert observed.mission.mission_name == ""
    assert app._activity_tracker.current_activity is current
    assert current.name == "VIP WORK"
    assert current.started_at == started
    assert app._data.mission_identity_status == "type_only"
    clock.value += timedelta(seconds=30)
    hud.observe(center=prompt, banner="MISSION PASSED")
    (row,) = stored(app)["activities"]
    assert (row["name"], row["type"], row["success"]) == ("VIP WORK", "VIP_WORK", True)
    assert app.cooldown_tracker.get_cooldown("sightseer") is None


@pytest.mark.parametrize("title", ["Sightseer", "Headhunter"])
@pytest.mark.parametrize("prompt", APP_PROMPTS)
def test_independent_real_title_and_result_keep_their_owner(app, hud, clock, title, prompt):
    observed = hud.observe(top=title, center=prompt)
    current = app._activity_tracker.current_activity

    assert observed.mission.identity_status == "known_name"
    assert observed.mission.mission_name == title
    assert current.name == title
    clock.value += timedelta(seconds=20)
    hud.observe(center=prompt, banner=f"MISSION PASSED\n{title}")
    (row,) = stored(app)["activities"]
    assert (row["name"], row["success"], row["duration_seconds"]) == (title, True, 20)
    assert app.cooldown_tracker.get_cooldown(title.lower()) is not None
    before = stored(app)
    episode = app._data.terminal_mission_episode
    hud.observe(center=prompt)
    assert app._activity_tracker.current_activity is None
    assert app._data.terminal_mission_episode is episode
    hud.observe(center=prompt, banner=f"MISSION PASSED\n{title}")
    assert stored(app) == before
    assert app.session_stats.activities_completed == 1


def test_real_conflicting_titles_remain_ambiguous_with_app_reference(app, hud):
    observed = hud.observe(top="Cayo Perico", center=APP_PROMPTS[0], banner="Sightseer")

    assert observed.mission.identity_status == "ambiguous"
    assert observed.mission.candidates == ("CAYO_PERICO", "Sightseer")
    assert app._activity_tracker.current_activity is None
    assert stored(app)["activities"] == stored(app)["earnings"] == []
    assert app.cooldown_tracker.get_active_cooldowns() == []


REPLAY_PROMPT = APP_PROMPTS[3]


def finish_with_app(app, hud, clock, title="Sightseer", outcome="MISSION PASSED", objective=""):
    hud.observe(top=title, center=REPLAY_PROMPT, money="$1,000")
    clock.value += timedelta(seconds=20)
    hud.observe(top=objective, center=REPLAY_PROMPT,
                banner=f"{outcome}\n{title}", money="$1,100")
    assert len(stored(app)["activities"]) == 1
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize("title", ["Sightseer", "Headhunter", "Cayo Perico"])
@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
def test_app_only_frame_cannot_rearm_a_completed_result(app, hud, clock, title, outcome):
    finish_with_app(app, hud, clock, title, outcome)
    before = stored(app)
    cooldowns = deepcopy(app.cooldown_tracker.get_active_cooldowns())
    episode = app._data.terminal_mission_episode
    clock.value += timedelta(seconds=1)
    observed = hud.observe(center=REPLAY_PROMPT, money="$1,100")
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert observed.mission.raw_text == REPLAY_PROMPT
    assert observed.mission.sightseer_app_reference
    assert observed.mission.identity_status == "unknown"
    assert app._activity_tracker.current_activity is None
    assert app._data.terminal_mission_episode is episode
    clock.value += timedelta(seconds=1)
    hud.observe(center=REPLAY_PROMPT, banner=f"{outcome}\n{title}", money="$1,100")
    assert stored(app) == before
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_active_cooldowns() == cooldowns


@pytest.mark.parametrize("objective", ["Collect a new package", "Go to the airport"])
def test_new_complete_objective_can_start_unresolved_activity_after_app_replay(app, hud, clock, objective):
    finish_with_app(app, hud, clock)
    observed = hud.observe(top=objective, center=REPLAY_PROMPT)
    current = app._activity_tracker.current_activity
    assert current is not None
    assert current.activity_type is ActivityType.UNKNOWN
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.sightseer_app_reference
    assert app._data.terminal_mission_episode is None
    assert app._data.mission_objectives.entries == {objective.casefold()}
    assert len(stored(app)["activities"]) == 1


@pytest.mark.parametrize("objective", ["Collect the package", "Collect the", "Collect the package at the airport"])
def test_repeated_or_partial_objective_with_app_does_not_rearm(app, hud, clock, objective):
    finish_with_app(app, hud, clock, objective="Collect the package")
    hud.observe(top=objective, center=REPLAY_PROMPT)
    assert app._activity_tracker.current_activity is None
    assert len(stored(app)["activities"]) == 1


def test_overflowed_objective_history_does_not_allow_app_to_rearm(app, hud, clock):
    finish_with_app(app, hud, clock, objective="\n".join(f"Collect package {i}" for i in range(129)))
    assert app._data.terminal_mission_episode.objectives.overflow
    hud.observe(top="Go to the airport", center=REPLAY_PROMPT)
    assert app._activity_tracker.current_activity is None
    assert len(stored(app)["activities"]) == 1


@pytest.mark.parametrize("title", ["Headhunter", "Cayo Perico", "Sell Mission"])
def test_independent_new_identity_still_starts_with_app_reference(app, hud, clock, title):
    finish_with_app(app, hud, clock)
    observed = hud.observe(top=title, center=REPLAY_PROMPT)
    assert observed.mission.sightseer_app_reference
    assert observed.mission.identity_status in ("known_name", "type_only")
    assert app._activity_tracker.current_activity is not None
    assert app._data.terminal_mission_episode is None


@pytest.mark.parametrize("title", ["Sightseer", "Headhunter", "Cayo Perico"])
def test_app_observation_during_active_mission_preserves_owner_and_start(app, hud, clock, title):
    hud.observe(top=title)
    current = app._activity_tracker.current_activity
    started = current.started_at
    identity = app._data.mission_identity_status
    clock.value += timedelta(seconds=5)
    hud.observe(center=REPLAY_PROMPT)
    assert app._activity_tracker.current_activity is current
    assert (current.name, current.started_at) == (title, started)
    assert app._data.mission_identity_status == identity
    assert stored(app)["activities"] == []


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
def test_direct_process_callback_uses_same_shared_app_fence(app, hud, clock, outcome):
    finish_with_app(app, hud, clock, outcome=outcome)
    before = stored(app)
    reading = app._mission_parser.parse(REPLAY_PROMPT)
    assert reading.identity_status == "unknown" and reading.sightseer_app_reference
    state = StateDetectionResult(GameState.MISSION_ACTIVE, .9, "direct crop callback",
                                 objective_text=REPLAY_PROMPT, mission=reading)
    app._process_state(state, CaptureResult())
    assert app._activity_tracker.current_activity is None
    hud.observe(center=REPLAY_PROMPT, banner=f"{outcome}\nSightseer", money="$1,100")
    assert stored(app) == before
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize("neutral,expected_source", [(False, "Mission"), (True, "")])
def test_guarded_app_observation_preserves_money_accounting(app, hud, clock, neutral, expected_source):
    finish_with_app(app, hud, clock)
    if neutral:
        app._state_machine.transition_to(GameState.IDLE, trigger="synthetic neutral observation")
    before = stored(app)
    observed = hud.observe(center=REPLAY_PROMPT, money="$1,200")
    assert observed.game_state is GameState.UNKNOWN
    assert observed.money_change == 100
    assert app.current_money == 1200
    assert observed.objective_text == observed.mission.raw_text == REPLAY_PROMPT
    assert app._activity_tracker.current_activity is None
    after = stored(app)
    assert after["activities"] == before["activities"]
    assert after["earnings"][:-1] == before["earnings"]
    # Rejected observations do not transition the game-state machine. Its
    # existing completed-state fallback remains generic "Mission" until idle.
    assert after["earnings"][-1]["source"] == expected_source
