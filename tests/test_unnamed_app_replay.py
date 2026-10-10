"""A consumed UNKNOWN owner cannot restart from a shared-app replay.

Only screen acquisition and OCR text are substituted by the existing CaptureHarness;
classification, ownership, trackers and temporary SQLite remain production code.
These are controlled observation regressions, not native OCR or gameplay measurements.
"""

from copy import deepcopy
from datetime import timedelta

import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import CaptureHarness, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414


PROMPT = "Use the Sightseer app to find the next package."
OUTCOMES = ("MISSION PASSED", "MISSION FAILED")
# Exercise both source orders, with every primary source owning the app text.
SOURCE_PAIRS = (
    ("top", "center"), ("top", "banner"),
    ("center", "top"), ("center", "banner"),
    ("banner", "top"), ("banner", "center"),
)


@pytest.fixture
def hud(app, monkeypatch):
    return CaptureHarness(app, monkeypatch)


def start_unnamed(app, hud, source="center"):
    observed = hud.observe(**{source: PROMPT}, money="$1,000")
    owner = app._activity_tracker.current_activity
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_type is MissionType.UNKNOWN
    assert observed.mission.mission_name == ""
    assert observed.mission.sightseer_app_reference
    assert owner is not None and owner.activity_type is ActivityType.UNKNOWN
    assert owner.name == PROMPT
    assert app._data.mission_identity_status == "unknown"
    assert app._data.mission_objectives.entries == frozenset()
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []
    return owner


def finish_unnamed(app, hud, clock, outcome="MISSION PASSED",
                   prompt_source="center", result_source="banner"):
    owner = start_unnamed(app, hud, prompt_source)
    clock.value += timedelta(seconds=20)
    observed = hud.observe(
        **{prompt_source: PROMPT, result_source: outcome}, money="$1,100",
    )
    success = outcome == "MISSION PASSED"
    expected = GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED
    assert observed.game_state is expected
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.sightseer_app_reference
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [owner]
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        PROMPT, "UNKNOWN", success, 20, 100 if success else 0,
    )
    assert [row["amount"] for row in persisted(app)["earnings"]] == [100]
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_active_cooldowns() == []
    return owner


def assert_one_consumed_owner(app, owner, before, outcome, callbacks):
    # Assert the durable effect first, after all four observations have run.
    assert len(persisted(app)["activities"]) == 1, "Replay added a second activity row"
    assert persisted(app) == before
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [owner]
    assert app._data.mission_start_time is app._data.mission_start_money is None
    assert app.session_stats.activities_completed == 1
    assert app.session_stats.missions_passed == int(outcome == "MISSION PASSED")
    assert app.session_stats.missions_failed == int(outcome == "MISSION FAILED")
    assert app.current_money == 1100
    assert app.session_earnings == app.session_stats.total_earnings == 100
    assert [row["amount"] for row in persisted(app)["earnings"]] == [100]
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert callbacks == ([owner] if outcome == "MISSION PASSED" else [])


@pytest.mark.parametrize("outcome", OUTCOMES, ids=("passed", "failed"))
@pytest.mark.parametrize(
    "prompt_source,result_source", SOURCE_PAIRS,
    ids=("top-center", "top-banner", "center-top", "center-banner", "banner-top", "banner-center"),
)
def test_four_frame_replay_does_not_complete_unnamed_owner_twice(
    app, hud, clock, outcome, prompt_source, result_source,
):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    owner = finish_unnamed(app, hud, clock, outcome, prompt_source, result_source)
    before = deepcopy(persisted(app))
    clock.value += timedelta(seconds=1)
    replay = hud.observe(**{prompt_source: PROMPT}, money="$1,100")
    clock.value += timedelta(seconds=1)
    hud.observe(**{prompt_source: PROMPT, result_source: outcome}, money="$1,100")
    assert_one_consumed_owner(app, owner, before, outcome, callbacks)
    assert replay.game_state is GameState.UNKNOWN and replay.state_confidence == 0
    assert replay.mission.raw_text == PROMPT and replay.mission.sightseer_app_reference
    assert replay.mission.identity_status == "unknown"
    assert len(hud.grabs) == 4


@pytest.mark.parametrize("outcome", OUTCOMES, ids=("passed", "failed"))
def test_direct_unparsed_app_replay_cannot_create_a_second_owner(app, hud, clock, outcome):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    owner = finish_unnamed(app, hud, clock, outcome)
    before = deepcopy(persisted(app))
    replay = CaptureResult()
    app._process_state(
        StateDetectionResult(GameState.MISSION_ACTIVE, .9, "raw direct callback",
                             objective_text=PROMPT), replay,
    )
    terminal = GameState.MISSION_COMPLETE if outcome == "MISSION PASSED" else GameState.MISSION_FAILED
    app._process_state(
        StateDetectionResult(terminal, .9, "raw direct result",
                             objective_text=PROMPT, banner_text=outcome), CaptureResult(),
    )
    assert_one_consumed_owner(app, owner, before, outcome, callbacks)
    assert replay.game_state is GameState.UNKNOWN
    assert replay.mission.sightseer_app_reference and replay.mission.identity_status == "unknown"


def test_completion_callback_app_replay_is_blocked_before_reentry(app, hud, clock):
    owner = start_unnamed(app, hud)
    callbacks = []
    replays = []

    def replay_app(activity):
        callbacks.append(activity)
        # Record rather than assert inside a callback whose errors are isolated.
        replays.append(hud.observe(center=PROMPT, money="$1,100"))

    app.on_mission_complete(replay_app)
    clock.value += timedelta(seconds=20)
    hud.observe(center=PROMPT, banner="MISSION PASSED", money="$1,100")
    assert len(replays) == 1 and callbacks == [owner]
    assert app._activity_tracker.current_activity is None
    assert replays[0].game_state is GameState.UNKNOWN
    before = deepcopy(persisted(app))
    hud.observe(center=PROMPT, banner="MISSION PASSED", money="$1,100")
    assert_one_consumed_owner(app, owner, before, "MISSION PASSED", callbacks)


@pytest.mark.parametrize("outcome", OUTCOMES, ids=("passed", "failed"))
@pytest.mark.parametrize("title,kind", [
    ("Headhunter", ActivityType.VIP_WORK), ("Cayo Perico", ActivityType.CAYO_PERICO),
])
def test_preservation_new_explicit_identity_rearms_after_unnamed_completion(
    app, hud, clock, outcome, title, kind,
):
    old = finish_unnamed(app, hud, clock, outcome)
    clock.value += timedelta(seconds=5)
    observed = hud.observe(top=title, center=PROMPT, money="$1,100")
    owner = app._activity_tracker.current_activity
    assert owner is not None and owner is not old
    assert (owner.name, owner.activity_type) == (title, kind)
    assert observed.mission.identity_status in ("known_name", "type_only")
    assert app._data.mission_start_time == clock.value
    assert app._data.mission_start_money == 1100
    clock.value += timedelta(seconds=10)
    hud.observe(center=PROMPT, banner=f"MISSION PASSED\n{title}", money="$1,100")
    assert len(persisted(app)["activities"]) == 2
    assert app._activity_tracker.completed_activities == [old, owner]
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize("same_crop", [False, True], ids=("separate-crop", "same-crop"))
def test_preservation_new_complete_objective_rearms_unnamed_activity(app, hud, clock, same_crop):
    old = finish_unnamed(app, hud, clock)
    inputs = {"center": "Collect a new package.\n" + PROMPT} if same_crop else {
        "top": "Collect a new package.", "center": PROMPT,
    }
    clock.value += timedelta(seconds=5)
    observed = hud.observe(**inputs, money="$1,100")
    owner = app._activity_tracker.current_activity
    assert owner is not None and owner is not old
    assert owner.activity_type is ActivityType.UNKNOWN
    assert observed.mission.identity_status == "unknown" and observed.mission.sightseer_app_reference
    assert app._data.mission_objectives.entries == {"collect a new package"}
    assert app._data.mission_start_time == clock.value
    assert app._data.mission_start_money == 1100
    clock.value += timedelta(seconds=10)
    hud.observe(**inputs, banner="MISSION PASSED", money="$1,100")
    before = deepcopy(persisted(app))
    hud.observe(**inputs, banner="MISSION PASSED", money="$1,100")
    assert persisted(app) == before and len(before["activities"]) == 2
    assert app._activity_tracker.completed_activities == [old, owner]


def test_preservation_non_app_unresolved_start_keeps_existing_policy(app, hud, clock):
    old = finish_unnamed(app, hud, clock)
    observed = hud.observe(center="Find a cache", money="$1,100")
    current = app._activity_tracker.current_activity
    assert current is not None and current is not old
    assert current.activity_type is ActivityType.UNKNOWN
    assert not observed.mission.sightseer_app_reference
    assert len(persisted(app)["activities"]) == 1


@pytest.mark.parametrize("outcome", OUTCOMES, ids=("passed", "failed"))
def test_preservation_initial_generic_result_does_not_own_a_later_activity(app, hud, outcome):
    hud.observe(center=PROMPT, banner=outcome, money="$1,100")
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []
    observed = hud.observe(center=PROMPT, money="$1,100")
    unresolved = app._activity_tracker.current_activity
    assert unresolved is not None and unresolved.activity_type is ActivityType.UNKNOWN
    assert observed.mission.identity_status == "unknown"
    hud.observe(top="Headhunter", center=PROMPT, money="$1,100")
    current = app._activity_tracker.current_activity
    assert current is unresolved and current.name == "Headhunter"
    assert current.activity_type is ActivityType.VIP_WORK
    assert app.session_stats.activities_completed == 0


def test_guarded_replay_keeps_real_balance_delta_without_duplicate_activity(app, hud, clock):
    owner = finish_unnamed(app, hud, clock)
    activities_before = deepcopy(persisted(app)["activities"])
    replay = hud.observe(center=PROMPT, money="$1,200")
    hud.observe(center=PROMPT, banner="MISSION PASSED", money="$1,200")
    assert persisted(app)["activities"] == activities_before
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [owner]
    assert replay.game_state is GameState.UNKNOWN and replay.money_change == 100
    assert [row["amount"] for row in persisted(app)["earnings"]] == [100, 100]
    assert app.current_money == 1200
    assert app.session_earnings == app.session_stats.total_earnings == 200
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_active_cooldowns() == []


@pytest.mark.parametrize("owned_outcome", OUTCOMES, ids=("owned-passed", "owned-failed"))
@pytest.mark.parametrize("result_outcome", OUTCOMES, ids=("later-passed", "later-failed"))
@pytest.mark.parametrize("title,status", [
    ("Headhunter", "known_name"), ("Cayo Perico", "type_only"),
])
def test_later_explicit_unowned_result_retains_its_replay_fence(
    app, hud, clock, owned_outcome, result_outcome, title, status,
):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    owner = finish_unnamed(app, hud, clock, owned_outcome)
    before = deepcopy(persisted(app))
    result = hud.observe(banner=f"{result_outcome}\n{title}", money="$1,100")
    assert result.mission.identity_status == status
    assert app._activity_tracker.current_activity is None
    replay = hud.observe(banner=title, money="$1,100")
    hud.observe(banner=f"{result_outcome}\n{title}", money="$1,100")
    assert_one_consumed_owner(app, owner, before, owned_outcome, callbacks)
    assert replay.game_state is GameState.UNKNOWN and replay.state_confidence == 0


@pytest.mark.parametrize("objective", [
    "Collect the package", "Go to the airport", "Destroy the target",
])
def test_later_explicit_result_retains_accumulated_unknown_objectives(
    app, hud, clock, objective,
):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    owner = start_unnamed(app, hud)
    hud.observe(top="Collect the package", center=PROMPT, money="$1,000")
    clock.value += timedelta(seconds=20)
    hud.observe(center=PROMPT, banner="MISSION PASSED", money="$1,100")
    before = deepcopy(persisted(app))
    # A repeated unresolved result and a later supported explicit result add
    # evidence without discarding the consumed owner's last active objective.
    hud.observe(top="Go to the airport", banner="MISSION PASSED", money="$1,100")
    hud.observe(top="Destroy the target", banner="MISSION PASSED\nHeadhunter", money="$1,100")
    replay = hud.observe(top=objective, banner="Headhunter", money="$1,100")
    hud.observe(banner="MISSION PASSED\nHeadhunter", money="$1,100")
    assert_one_consumed_owner(app, owner, before, "MISSION PASSED", callbacks)
    assert replay.game_state is GameState.UNKNOWN and replay.state_confidence == 0
