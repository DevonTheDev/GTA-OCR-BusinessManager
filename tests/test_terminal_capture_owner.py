"""Terminal frame ownership across registered state and money callbacks.

The existing synthetic capture/OCR-text harness drives real detection, state,
tracking and temporary SQLite. These cases establish callback robustness, not
native OCR/gameplay accuracy or the cause of an observed balance increase.
"""

from datetime import timedelta

import pytest

from src.app import CaptureResult
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_bottom_objective_capture import persisted
from tests.test_mission_result_accounting import clock as clock
from tests.test_terminal_mission_identity_ownership import mission_stats
from tests.test_vip_status_capture import VipCaptureHarness, owner_snapshot


OUTCOMES = [("MISSION PASSED", GameState.MISSION_COMPLETE, True),
            ("MISSION FAILED", GameState.MISSION_FAILED, False)]


@pytest.fixture
def hud(app, monkeypatch):
    return VipCaptureHarness(app, monkeypatch)


def process(app, state, text, objective=""):
    app._process_state(StateDetectionResult(
        state, .9, "explicit callback observation", mission_text=text,
        objective_text=objective,
    ), CaptureResult())


def register(app, window, state, callback):
    if window == "state":
        app.on_state_change(lambda _old, new: callback() if new is state else None)
    else:
        app.on_money_change(lambda *_args: callback())


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
@pytest.mark.parametrize("window", ["state", "money"])
def test_ownerless_result_cannot_consume_callback_created_owner(app, hud, window, outcome, state, success):
    hud.observe(money="$1,000")
    created, transitions, changes, completed = [], [], [], []
    app.on_state_change(lambda old, new: transitions.append((old, new)))
    app.on_money_change(lambda reading, delta: changes.append((reading.display_value, delta)))
    app.on_mission_complete(completed.append)

    def create():
        if not created:
            process(app, GameState.MISSION_ACTIVE, "Headhunter", "Find the target")
            created.append(owner_snapshot(app))

    register(app, window, state, create)
    observed = hud.observe(center=outcome, money="$1,250")
    assert len(created) == 1
    assert owner_snapshot(app) == created[0]
    assert observed.game_state is GameState.UNKNOWN
    assert observed.objective_text == outcome and observed.mission.outcome == ("complete" if success else "failed")
    assert app._state_machine.state is state and transitions[-1][1] is state
    assert changes == [(1250, 250)] and observed.money_change == 250
    assert observed.money.display_value == app.current_money == 1250
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert completed == persisted(app)["activities"] == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert app._data.terminal_mission_episode is None
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, "Mission" if window == "money" and success else ""),
    ]


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
@pytest.mark.parametrize("window", ["state", "money"])
@pytest.mark.parametrize("next_name", ["Headhunter", "Sightseer", "Go to the location"])
def test_result_cannot_consume_callback_replacement(app, hud, clock, window, next_name, outcome, state, success):
    hud.observe(top="Headhunter", money="$1,000")
    original = app._activity_tracker.current_activity
    created, completed = [], []
    app.on_mission_complete(completed.append)
    clock.value += timedelta(seconds=30)

    def replace_owner():
        if created:
            return
        process(app, state, outcome)
        process(app, GameState.MISSION_ACTIVE, next_name, "Find the target")
        created.append(owner_snapshot(app))

    register(app, window, state, replace_owner)
    observed = hud.observe(center=outcome, money="$1,250")
    assert len(created) == 1 and created[0][0] is not original
    assert owner_snapshot(app) == created[0]
    assert observed.game_state is GameState.UNKNOWN
    assert app._state_machine.state is state
    assert observed.money_change == 250 and app.current_money == 1250
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert app._activity_tracker.completed_activities == [original]
    assert completed == ([original] if success else [])
    assert mission_stats(app) == (1, int(success), int(not success), 0)
    row, = persisted(app)["activities"]
    assert (row["name"], row["success"], row["duration_seconds"], row["earnings"]) == (
        "Headhunter", success, 30, 250 if window == "money" and success else 0,
    )
    assert app._data.terminal_mission_episode is None
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, "Headhunter" if window == "money" and success else ""),
    ]


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
@pytest.mark.parametrize("window", ["state", "money"])
def test_result_cannot_use_callback_refined_identity(app, hud, window, outcome, state, success):
    hud.observe(top="Go to the location", money="$1,000")
    owner = app._activity_tracker.current_activity
    refined = []

    def refine():
        if not refined:
            process(app, GameState.MISSION_ACTIVE, "Sightseer")
            refined.append(owner_snapshot(app))

    register(app, window, state, refine)
    observed = hud.observe(center=outcome, money="$1,250")
    assert len(refined) == 1 and refined[0][0] is owner
    assert owner.name == "Sightseer" and owner_snapshot(app) == refined[0]
    assert observed.game_state is GameState.UNKNOWN
    assert persisted(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert app._data.terminal_mission_episode is None
    assert app.current_money == 1250 and observed.money_change == 250
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, "Go to the location" if window == "money" and success else ""),
    ]


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
@pytest.mark.parametrize("first,title,name,kind", [
    ("Headhunter", "", "Headhunter", ActivityType.VIP_WORK),
    ("Go to the location", "Sightseer", "Sightseer", ActivityType.VIP_WORK),
    ("Casino Heist", "The Big Con\nFinale", "The Big Con", ActivityType.HEIST_FINALE),
])
def test_unchanged_owner_allows_terminal_name_and_family_refinement(
        app, hud, clock, outcome, state, success, first, title, name, kind):
    hud.observe(top=first, money="$1,000")
    owner = app._activity_tracker.current_activity
    started = owner.started_at
    transitions, changes, completed = [], [], []
    app.on_state_change(lambda old, new: transitions.append((old, new)))
    app.on_money_change(lambda reading, delta: changes.append(delta))
    app.on_mission_complete(completed.append)
    clock.value += timedelta(seconds=45)
    observed = hud.observe(center=outcome, banner=title, money="$1,250")
    assert observed.game_state is state and app._state_machine.state is state
    assert transitions[-1][1] is state and changes == [250]
    assert owner.started_at == started and owner.name == name and owner.activity_type is kind
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [owner]
    assert completed == ([owner] if success else [])
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        name, kind.name, success, 45, 250 if success else 0,
    )
    assert mission_stats(app) == (1, int(success), int(not success), 0)
    assert app._data.terminal_mission_episode.identity.name == name
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, first if success else ""),
    ]


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
def test_ownerless_ordinary_result_keeps_existing_earning_fallback(app, hud, outcome, state, success):
    hud.observe(money="$1,000")
    first = hud.observe(center=outcome, money="$1,250")
    repeated = hud.observe(center=outcome, money="$1,300")
    assert first.game_state is repeated.game_state is state
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    assert app.session_earnings == app.session_stats.total_earnings == 300
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, "Mission" if success else ""), (50, "Mission" if success else ""),
    ]


@pytest.mark.parametrize("outcome,state,success", OUTCOMES)
def test_rejected_capture_preserves_subsequent_valid_completion(app, hud, clock, outcome, state, success):
    hud.observe(money="$1,000")
    created = []

    def create():
        if not created:
            process(app, GameState.MISSION_ACTIVE, "Headhunter", "Find the target")
            created.append(app._activity_tracker.current_activity)

    register(app, "state", state, create)
    rejected = hud.observe(center=outcome, money="$1,250")
    assert rejected.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is created[0]
    clock.value += timedelta(seconds=20)
    accepted = hud.observe(center=outcome, banner="Headhunter", money="$1,300")
    assert accepted.game_state is state
    row, = persisted(app)["activities"]
    assert (row["name"], row["earnings"], row["duration_seconds"], row["success"]) == (
        "Headhunter", 300 if success else 0, 20, success,
    )
    assert app._activity_tracker.completed_activities == created
    assert mission_stats(app) == (1, int(success), int(not success), 0)
    cooldown = app.cooldown_tracker.get_cooldown("headhunter")
    assert (cooldown is not None) is success
    hud.observe(center=outcome, banner="Headhunter", money="$1,300")
    assert persisted(app)["activities"] == [row]
    assert app.cooldown_tracker.get_cooldown("headhunter") == cooldown
    assert mission_stats(app) == (1, int(success), int(not success), 0)
