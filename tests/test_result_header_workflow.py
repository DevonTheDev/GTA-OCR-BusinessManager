"""Result-only header through production capture, trackers and disposable SQLite.

This module substitutes OCR text at the backend boundary. Generated pixel/OCR
coverage lives separately in test_result_header_ocr_images.py; neither module
ships or silently replaces the local source-matched public-guide screenshots.
"""

from datetime import timedelta

import numpy as np
import pytest

from src.capture.regions import Region, ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import CaptureHarness, assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_terminal_mission_identity_ownership import mission_stats

REGIONS = ScreenRegions()
HEADER = Region(.20, .12, .60, .20)
QUALIFIED_RESULT = "HEIST PASSED\nThe Cayo Perico Heist"


class ResultHeaderHarness(CaptureHarness):
    """Keep production ScreenCapture and detector; optionally use actual OCR."""

    def observe(self, header="", bottom="", top="", center="", banner="", money="",
                business=None, frame=None):
        self.texts = {
            HEADER: header, REGIONS.bottom_objective: bottom, REGIONS.mission_text: top,
            REGIONS.center_prompt: center, REGIONS.mission_banner: banner,
            REGIONS.money_display: money,
        }
        if business is not None:
            self.texts.update(zip(REGIONS.get_business_regions().values(), business))
        if frame is not None:
            self.frame = np.dstack((frame, np.full(frame.shape[:2], 255, dtype=np.uint8)))
        return self.app._do_capture_cycle()


@pytest.fixture
def hud(app, monkeypatch):
    return ResultHeaderHarness(app, monkeypatch)


def owner_snapshot(app):
    owner = app._activity_tracker.current_activity
    assert owner is not None
    return (owner, owner.name, owner.activity_type, owner.started_at,
            app._data.mission_start_time, app._data.mission_start_money,
            app._data.mission_identity_status, app._data.mission_identity_type,
            app._data.mission_heist_phase, frozenset(app._data.mission_objectives.entries))


def assert_no_accounting(app):
    data = persisted(app)
    assert data["activities"] == data["earnings"] == []
    assert app._activity_tracker.completed_activities == []
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.current_money is None
    assert app.session_earnings == app.session_stats.total_earnings == 0


@pytest.mark.parametrize("first,kind,phase", [
    ({"bottom": "Escape Cayo Perico"}, ActivityType.CAYO_PERICO, MissionType.UNKNOWN),
    ({"banner": "Cayo Perico Finale"}, ActivityType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ({"top": "Go to the location"}, ActivityType.CAYO_PERICO, MissionType.UNKNOWN),
], ids=("bottom-family", "explicit-finale", "compatible-unresolved"))
def test_qualified_header_completes_exact_owner_once_without_inventing_phase_or_payout(
        app, hud, clock, first, kind, phase):
    completed = []
    app.on_mission_complete(completed.append)
    hud.observe(**first)
    owner = app._activity_tracker.current_activity
    assert owner is not None
    started = owner.started_at
    clock.value += timedelta(seconds=45)
    result = hud.observe(header=QUALIFIED_RESULT + "\nYour Final Take $1,407,990")
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.result_header_text == result.result_header_evidence
    assert result.result_header_text.startswith(QUALIFIED_RESULT)
    assert result.mission.outcome_scope == "heist" and result.mission.outcome == "complete"
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == completed == [owner]
    assert completed[0] is owner and owner.started_at == started
    assert owner.activity_type is kind
    assert app._data.terminal_mission_episode.identity.phase is phase
    row, = persisted(app)["activities"]
    assert (row["type"], row["success"], row["duration_seconds"], row["earnings"]) == (
        kind.name, True, 45, 0,
    )
    before = persisted(app)
    repeat = hud.observe(header=QUALIFIED_RESULT)
    assert repeat.game_state is GameState.MISSION_COMPLETE
    assert persisted(app) == before
    assert completed == [owner] and mission_stats(app) == (1, 1, 0, 0)
    assert persisted(app)["earnings"] == [] and app.current_money is None
    assert app.session_earnings == 0
    # Replayed objective/title does not create another episode after completion.
    old = hud.observe(**(first if "bottom" in first else {"banner": "Cayo Perico"}))
    assert old.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert completed == [owner] and persisted(app) == before


def test_idle_result_and_title_dropout_start_nothing_and_preserve_terminal_fence(app, hud):
    completed = []
    app.on_mission_complete(completed.append)
    result = hud.observe(header=QUALIFIED_RESULT)
    assert result.game_state is GameState.MISSION_COMPLETE
    terminal = app._data.terminal_mission_episode
    assert terminal is not None
    for kwargs in ({"header": "The Cayo Perico Heist"},
                   {"header": "Escape Cayo Perico"}):
        held = hud.observe(**kwargs)
        assert held.game_state is GameState.UNKNOWN
        assert app._data.terminal_mission_episode is terminal
        assert_not_started(app)
    assert hud.observe(header=QUALIFIED_RESULT).game_state is GameState.MISSION_COMPLETE
    assert completed == []
    assert_not_started(app)


@pytest.mark.parametrize("first,header", [
    ("Casino Heist", QUALIFIED_RESULT),
    ("Headhunter", QUALIFIED_RESULT),
    ("Cayo Perico Prep", QUALIFIED_RESULT),
    ("Cayo Perico", "HEIST PASSED"),
    ("Headhunter", "HEIST PASSED"),
    ("Go to the location", "HEIST PASSED"),
    ("Cayo Perico", QUALIFIED_RESULT + "\nMISSION FAILED"),
    ("Cayo Perico", "HEIST PASSED\nCayo Perico Prep"),
])
def test_rejected_header_preserves_owner_and_does_not_consume_later_completion(
        app, hud, clock, first, header):
    completed = []
    app.on_mission_complete(completed.append)
    hud.observe(banner=first)
    before = owner_snapshot(app)
    transitions = tuple(app._state_machine.context.transitions)
    clock.value += timedelta(seconds=20)
    rejected = hud.observe(header=header)
    assert rejected.game_state is GameState.UNKNOWN and rejected.state_confidence == 0
    assert rejected.result_header_text == header
    assert owner_snapshot(app) == before
    assert app._activity_tracker.current_activity is before[0]
    assert tuple(app._state_machine.context.transitions) == transitions
    assert app._data.terminal_mission_episode is None
    assert completed == []
    assert_no_accounting(app)
    clock.value += timedelta(seconds=10)
    accepted = hud.observe(banner="MISSION PASSED\n" + first)
    assert accepted.game_state is GameState.MISSION_COMPLETE
    assert completed == [before[0]] and completed[0] is before[0]
    row, = persisted(app)["activities"]
    assert row["duration_seconds"] == 30 and row["earnings"] == 0


@pytest.mark.parametrize("header", [
    "The Cayo Perico Heist", "Agency", "The Cayo Perico Heist\nAgency",
    "Escape Cayo Perico", "Stock: 40%\nSupplies: 60%\nValue: $7000",
    "$1,407,990", "HEIST PASSED", "HEIST PASSED\nCayo Perico Prep",
])
def test_raw_header_has_no_start_business_objective_or_money_authority(app, hud, header):
    result = hud.observe(header=header)
    assert result.game_state is GameState.UNKNOWN
    assert result.result_header_text == header
    assert result.result_header_evidence == ""
    assert result.business is None and result.money is None
    assert app._data.business_states == {}
    assert_not_started(app)
    assert_no_accounting(app)


@pytest.mark.parametrize("source", ["top", "banner", "bottom"])
def test_unqualified_header_cannot_borrow_same_capture_family_to_start(app, hud, source):
    identity = "Escape Cayo Perico" if source == "bottom" else "The Cayo Perico Heist"
    result = hud.observe(header="HEIST PASSED", **{source: identity})
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.result_header_text == "HEIST PASSED"
    assert result.result_header_evidence == ""
    assert_not_started(app)


@pytest.mark.parametrize("source,text", [
    ("top", "Casino Heist"), ("bottom", "Escape Casino Heist"),
    ("top", "Cayo Perico Prep"), ("banner", "Headhunter\nSightseer"),
])
def test_same_capture_conflict_is_uncertain_without_starting(app, hud, source, text):
    result = hud.observe(header=QUALIFIED_RESULT, **{source: text})
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.result_header_evidence == ""
    assert_not_started(app)


def test_primary_business_preserves_fields_despite_header_and_bottom_command(app, hud):
    result = hud.observe(header=QUALIFIED_RESULT, bottom="Escape Cayo Perico", top="Bunker",
                         business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"))
    assert result.game_state is GameState.BUSINESS_COMPUTER
    assert app._data.business_states["bunker"]["stock"] == 40
    assert app._data.business_states["bunker"]["supply"] == 60
    assert app._data.business_states["bunker"]["value"] == 7000
    assert len(hud.grabs) == 4  # One shared HUD grab and three existing business reads.
    assert app._activity_tracker.current_activity is None
    assert_no_accounting(app)


@pytest.mark.parametrize("primary,state", [("MISSION FAILED", GameState.MISSION_FAILED),
                                           ("MISSION PASSED", GameState.MISSION_COMPLETE)])
def test_primary_result_keeps_priority_over_opposing_header(app, hud, primary, state):
    header = QUALIFIED_RESULT if state is GameState.MISSION_FAILED else "HEIST PASSED\nCasino Heist"
    result = hud.observe(top=primary, header=header)
    assert result.game_state is state
    assert result.mission.outcome_scope is None
    assert result.result_header_evidence == ""
    assert_not_started(app)
