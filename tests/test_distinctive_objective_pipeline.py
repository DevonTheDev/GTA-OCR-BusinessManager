"""Distinctive command through production capture, detector, app and SQLite.

OCR text is injected at the backend boundary on neutral frames. These tests
are policy/ownership controls, not screenshots or native Windows validation.
"""

from datetime import timedelta

import pytest

from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import BATCH, BOTTOM, assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_result_header_workflow import (
    QUALIFIED_RESULT, REGIONS, ResultHeaderHarness, assert_no_accounting, owner_snapshot,
)

COMMAND = "Go to El Rubio's compound."
NORMALIZED = "go to el rubio's compound"


@pytest.fixture
def hud(app, monkeypatch):
    return ResultHeaderHarness(app, monkeypatch)


def assert_no_fabricated_effects(app):
    assert persisted(app)["earnings"] == []
    assert app.current_money is None
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert app._data.business_states == {}


def test_distinctive_bottom_starts_family_from_one_unmodified_capture(app, hud):
    result = hud.observe(bottom=COMMAND)
    assert result.game_state is GameState.MISSION_ACTIVE
    assert result.state_confidence == .8
    assert result.bottom_objective_text == COMMAND
    assert result.bottom_objective_command == COMMAND.rstrip(".")
    assert (result.mission_text, result.objective_text, result.banner_text) == ("", "", "")
    assert result.mission.mission_type is MissionType.CAYO_PERICO
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_name == ""
    assert result.mission.heist_phase is MissionType.UNKNOWN
    assert result.mission.outcome is result.mission.outcome_scope is None
    assert app._activity_tracker.current_activity.activity_type is ActivityType.CAYO_PERICO
    assert app._data.mission_objectives.entries == {NORMALIZED}
    assert hud.batches == [BATCH] and len(hud.grabs) == len(hud.waits) == 1
    assert hud.ocr_calls[3] == (BOTTOM, {"invert": True, "scale": 2.0, "threshold": False})
    assert_no_accounting(app)
    assert_no_fabricated_effects(app)


def test_distinctive_start_rearms_second_owner_and_escape_keeps_identity_and_time(app, hud, clock):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    hud.observe(bottom="Escape Cayo Perico")
    first = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=45)
    hud.observe(header=QUALIFIED_RESULT)
    assert callbacks == [first]
    clock.value += timedelta(seconds=5)
    assert hud.observe(bottom=COMMAND).game_state is GameState.MISSION_ACTIVE
    second = owner_snapshot(app)
    assert second[0] is not first
    assert second[1] == COMMAND.rstrip(".")
    clock.value += timedelta(seconds=25)
    assert hud.observe(bottom="Escape Cayo Perico").game_state is GameState.MISSION_ACTIVE
    assert owner_snapshot(app)[:-1] == second[:-1]
    assert app._activity_tracker.current_activity is second[0]
    assert app._data.mission_objectives.entries == {"escape cayo perico"}
    clock.value += timedelta(seconds=10)
    hud.observe(header=QUALIFIED_RESULT)
    assert callbacks == [first, second[0]]
    assert callbacks[1] is second[0]
    assert second[0].started_at == second[3]
    before = persisted(app)
    for _ in range(2):
        hud.observe(header=QUALIFIED_RESULT)
    assert persisted(app) == before and callbacks == [first, second[0]]
    assert app._activity_tracker.completed_activities == callbacks
    assert app._activity_tracker.current_activity is None
    assert app._data.terminal_mission_episode.identity.phase is MissionType.UNKNOWN
    assert [(row["type"], row["duration_seconds"], row["success"], row["earnings"])
            for row in before["activities"]] == [
        ("CAYO_PERICO", 45, True, 0), ("CAYO_PERICO", 35, True, 0),
    ]
    assert app.session_stats.activities_completed == 2
    assert len(hud.grabs) == len(hud.waits) == 7 and hud.batches == [BATCH] * 7
    assert_no_fabricated_effects(app)


REJECTED = (
    "Go to the location", "Go to the compound", "Retrieve the package.",
    "El Rubio's compound", "Go to El Rubio", "Go to El Rublo's compound",
    "If you go to El Rubio's compound, collect the reward",
    "Go to El Rubio's compound and wait", "Go to El Rubio's compound 2",
    "Go to El Rubio's compound $100", "Go to El Rubio's compound\nMISSION PASSED",
    "Go to El Rubio's compound\nCONTINUE", "Go to El Rubio's compound\nAgency",
)


@pytest.mark.parametrize("bottom", REJECTED)
def test_malformed_generic_or_contaminated_bottom_has_no_authority(app, hud, bottom):
    result = hud.observe(bottom=bottom)
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.bottom_objective_text == bottom
    assert result.bottom_objective_command == ""
    assert_not_started(app)
    assert_no_fabricated_effects(app)


@pytest.mark.parametrize("top,bottom", [("Go to El Rubio's", "compound"),
                                       ("Go to", "El Rubio's compound")])
def test_command_fragments_in_separate_crops_cannot_supply_cayo_family(app, hud, top, bottom):
    result = hud.observe(top=top, bottom=bottom)
    assert result.mission.mission_type is MissionType.UNKNOWN
    assert result.bottom_objective_command == ""
    assert app._data.mission_identity_type is MissionType.UNKNOWN
    assert_no_fabricated_effects(app)


@pytest.mark.parametrize("source", ["top", "center", "banner"])
def test_casino_conflict_keeps_identity_ambiguous(app, hud, source):
    result = hud.observe(bottom=COMMAND, **{source: "Casino Heist"})
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.mission.identity_status == "ambiguous"
    assert_not_started(app)


@pytest.mark.parametrize("source", ["header", "banner"])
def test_unqualified_heist_result_cannot_borrow_distinctive_family(app, hud, source):
    result = hud.observe(bottom=COMMAND, **{source: "HEIST PASSED"})
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.result_header_evidence == ""
    assert_not_started(app)


@pytest.mark.parametrize("outcome,state", [("MISSION PASSED", GameState.MISSION_COMPLETE),
                                          ("MISSION FAILED", GameState.MISSION_FAILED)])
def test_primary_result_retains_priority_over_distinctive_bottom(app, hud, outcome, state):
    result = hud.observe(top=outcome, bottom=COMMAND)
    assert result.game_state is state
    assert result.bottom_objective_command == ""
    assert_not_started(app)


def test_primary_business_retains_fields_and_existing_grab_count(app, hud):
    result = hud.observe(top="Bunker", bottom=COMMAND, header=QUALIFIED_RESULT,
                         business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"))
    assert result.game_state is GameState.BUSINESS_COMPUTER
    business = app._data.business_states["bunker"]
    assert (business["stock"], business["supply"], business["value"]) == (40, 60, 7000)
    assert hud.region_calls == [(region, False) for region in REGIONS.get_business_regions().values()]
    assert len(hud.grabs) == 4
    assert_not_started(app)
    assert_no_accounting(app)


def test_distinctive_command_refines_unresolved_owner_without_restarting(app, hud, clock):
    hud.observe(top="Go to the location")
    owner = app._activity_tracker.current_activity
    before = owner.started_at, app._data.mission_start_time, app._data.mission_start_money
    assert owner.activity_type is ActivityType.UNKNOWN
    clock.value += timedelta(seconds=30)
    result = hud.observe(bottom=COMMAND)
    assert app._activity_tracker.current_activity is owner
    assert (owner.started_at, app._data.mission_start_time, app._data.mission_start_money) == before
    assert owner.activity_type is ActivityType.CAYO_PERICO
    assert result.mission.mission_name == "" and result.mission.heist_phase is MissionType.UNKNOWN
    assert_no_accounting(app)


def test_reentrant_result_then_new_start_keeps_callback_owner_and_new_baseline(app, hud, clock):
    callbacks, replacements = [], []

    def complete(owner):
        callbacks.append(owner)
        if len(callbacks) == 1:
            hud.observe(header=QUALIFIED_RESULT)  # Reentrant old result is already consumed.
            clock.value += timedelta(seconds=5)
            hud.observe(bottom=COMMAND)
            replacements.append(owner_snapshot(app))

    app.on_mission_complete(complete)
    hud.observe(bottom="Escape Cayo Perico")
    first = app._activity_tracker.current_activity
    hud.observe(header=QUALIFIED_RESULT)
    second, = replacements
    assert callbacks == [first] and second[0] is not first
    assert app._activity_tracker.current_activity is second[0]
    assert owner_snapshot(app) == second
    # A different family's stale result has no claim on the newly started owner.
    rejected = hud.observe(header="HEIST PASSED\nCasino Heist")
    assert rejected.game_state is GameState.UNKNOWN
    assert owner_snapshot(app) == second and callbacks == [first]
    hud.observe(header=QUALIFIED_RESULT)
    assert callbacks == [first, second[0]]
    assert len(persisted(app)["activities"]) == 2
    assert_no_fabricated_effects(app)
