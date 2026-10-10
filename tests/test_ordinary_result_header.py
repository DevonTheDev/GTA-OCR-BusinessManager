"""Exact ordinary header admission; injected OCR is not gameplay accuracy proof."""

from dataclasses import asdict
from datetime import timedelta

import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_result_header_admission import observation
from tests.test_result_header_workflow import (
    assert_no_accounting, hud as hud, owner_snapshot,
)
from tests.test_terminal_mission_identity_ownership import mission_stats
from tests.test_vip_status_capture import VipCaptureHarness


HEADER = "MISSION PASSED"


@pytest.mark.parametrize("raw", [HEADER, "mission passed", "\r\n MiSsIoN\t PASSED \r\n"])
def test_exact_ordinary_header_overrides_visual_menu_without_identity(monkeypatch, raw):
    result, calls, updates, detector = observation(
        monkeypatch, raw, quick=StateDetectionResult(GameState.MENU, .7, "Menu overlay detected"))
    assert (result.state, result.confidence) == (GameState.MISSION_COMPLETE, .85)
    assert result.result_header_text == result.result_header_evidence == raw
    assert result.mission.outcome == "complete" and result.mission.outcome_scope is None
    assert result.mission.identity_status == "unknown" and result.mission.candidates == ()
    assert result.mission.mission_name == "" and result.mission.mission_type is MissionType.UNKNOWN
    assert result.mission.heist_phase is MissionType.UNKNOWN and result.mission.objective == ""
    assert calls[-1] == (raw, {"threshold": False, "invert": False, "scale": 2.0})
    assert updates == [result] and detector._context.last_state is GameState.MISSION_COMPLETE


@pytest.mark.parametrize("raw", [
    "MISSION", "PASSED", "MISSION\nPASSED", "MISSION PASSED!", "MISSION PASSED:",
    "MISSION PASSED\nEnforcers eliminated", "MISSION PASSED\nAll wine barrels protected",
    "MISSION PASSED\nSecurity Contract", "MISSION PASSED\nHeadhunter",
    "MISSION PASSED +$40500", "MISSION PASSED\n$33500", "MISSION PASSED\nMISSION FAILED",
    "If the MISSION PASSED yesterday", "MISSION PASSED |", "MISSION PA55ED",
    "MISSION\u00a0PASSED", "MI\u017f\u017fION PASSED", "MISSION\vPASSED",
])
def test_nonexact_ordinary_header_is_diagnostics_only(monkeypatch, raw):
    baseline, _, _, _ = observation(monkeypatch)
    result, _, _, _ = observation(monkeypatch, raw)
    assert result.state is baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.result_header_text == raw and result.result_header_evidence == ""


def test_header_cannot_join_primary_fragment_to_invent_success(monkeypatch):
    result, _, _, _ = observation(monkeypatch, "PASSED", top="MISSION")
    assert result.state is not GameState.MISSION_COMPLETE
    assert result.result_header_evidence == "" and result.mission.outcome is None


@pytest.mark.parametrize("top,center", [
    ("Bunker Stock 40% Supplies 60% Value $7000", ""),
    ("MISSION FAILED", ""), ("MISSION PASSED", ""), ("HEIST PASSED", ""),
    ("Headhunter", "Sightseer"), ("MISSION PASSED", "MISSION FAILED"),
])
def test_ordinary_header_keeps_primary_business_result_and_ambiguity(monkeypatch, top, center):
    baseline, calls, _, _ = observation(monkeypatch, top=top, center=center)
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, top=top, center=center)
    assert asdict(result) == asdict(baseline) and actual_calls == calls


@pytest.mark.parametrize("first,kind,status", [
    ("Headhunter", ActivityType.VIP_WORK, "known_name"),
    ("Security Contract", ActivityType.SECURITY_CONTRACT, "type_only"),
    ("Go to the location", ActivityType.UNKNOWN, "unknown"),
])
def test_ordinary_header_completes_existing_owner_once_without_identity_or_payout(
        app, hud, clock, first, kind, status):
    completed = []
    app.on_mission_complete(completed.append)
    hud.observe(top=first)
    before = owner_snapshot(app)
    assert before[2] is kind and before[6] == status
    clock.value += timedelta(seconds=45)
    result = hud.observe(header=HEADER)
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.result_header_text == result.result_header_evidence == HEADER
    assert result.mission.identity_status == "unknown" and result.mission.outcome == "complete"
    owner = before[0]
    assert completed == [owner] and completed[0] is owner
    assert (owner.name, owner.activity_type, owner.started_at) == before[1:4]
    assert app._activity_tracker.current_activity is None
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"]) == (
        before[1], kind.name, True, 45)
    # Existing missing-balance policy stores 0; this is not observed zero earnings.
    assert row["earnings"] == 0 and app.current_money is None
    assert persisted(app)["earnings"] == [] and app.session_earnings == 0
    saved = persisted(app)
    assert hud.observe(header=HEADER).game_state is GameState.MISSION_COMPLETE
    assert persisted(app) == saved and completed == [owner]
    assert mission_stats(app) == (1, 1, 0, 0)
    if status != "unknown":
        assert hud.observe(top=first).game_state is GameState.UNKNOWN
        assert app._activity_tracker.current_activity is None and persisted(app) == saved


def test_idle_ordinary_results_never_create_activity_identity_money_or_cooldown(app, hud):
    for _ in range(2):
        result = hud.observe(header=HEADER)
        assert result.game_state is GameState.MISSION_COMPLETE
        assert result.result_header_evidence == HEADER
        assert result.mission.identity_status == "unknown"
        assert app._data.terminal_mission_episode is None
        assert_not_started(app)
        assert_no_accounting(app)


def test_ordinary_completion_retains_old_objective_fence(app, hud):
    hud.observe(banner="Headhunter", top="Eliminate the targets")
    hud.observe(header=HEADER)
    saved = persisted(app)
    old = hud.observe(banner="Headhunter", top="Eliminate the targets")
    assert old.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None and persisted(app) == saved


@pytest.mark.parametrize("primary", ["Headhunter", "Headhunter\nSightseer"])
def test_stale_named_or_ambiguous_primary_cannot_consume_next_owner(app, hud, primary):
    hud.observe(top="Headhunter")
    hud.observe(header=HEADER)
    hud.observe(top="Sightseer")
    before = owner_snapshot(app)
    assert before[1] == "Sightseer"
    saved = persisted(app)
    transitions = tuple(app._state_machine.context.transitions)
    rejected = hud.observe(header=HEADER, banner=primary)
    assert rejected.game_state is GameState.UNKNOWN and rejected.state_confidence == 0
    assert owner_snapshot(app) == before and app._activity_tracker.current_activity is before[0]
    assert persisted(app) == saved
    assert tuple(app._state_machine.context.transitions) == transitions


def test_direct_ordinary_header_revalidation_completes_original_owner(app):
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .8, "start",
                                          mission_text="Headhunter"), CaptureResult())
    owner = app._activity_tracker.current_activity
    result = StateDetectionResult(GameState.MISSION_COMPLETE, .85, "ordinary header",
                                  result_header_text=HEADER, result_header_evidence=HEADER)
    reading = app._mission_reading(result)
    assert reading.outcome == "complete" and reading.identity_status == "unknown"
    capture = CaptureResult()
    app._process_state(result, capture)
    assert app._activity_tracker.completed_activities == [owner]
    assert capture.result_header_evidence == HEADER and capture.mission.outcome == "complete"
    assert persisted(app)["activities"][0]["name"] == "Headhunter"


@pytest.mark.parametrize("raw", ["MISSION PASSED\nHeadhunter", "MISSION PASSED\n$40500",
                                  "MISSION\nPASSED", "MISSION PASSED\nMISSION FAILED"])
def test_direct_nonexact_header_cannot_bypass_revalidation(app, raw):
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .8, "start",
                                          mission_text="Headhunter"), CaptureResult())
    before = owner_snapshot(app)
    result = StateDetectionResult(GameState.MISSION_COMPLETE, .85, "untrusted header",
                                  result_header_text=raw, result_header_evidence=raw)
    capture = CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state is GameState.UNKNOWN and capture.result_header_evidence == ""
    assert capture.result_header_text == raw and owner_snapshot(app) == before
    assert_no_accounting(app)


def test_raw_header_without_admission_cannot_supply_result_or_identity(app):
    result = StateDetectionResult(GameState.UNKNOWN, 0, "diagnostic", result_header_text=HEADER)
    reading = app._mission_reading(result)
    assert reading.outcome is None and reading.identity_status == "unknown"
    app._process_state(result, CaptureResult())
    assert_not_started(app)


def test_ordinary_result_cannot_use_footer_to_reclassify_unresolved_owner(app, monkeypatch):
    hud = VipCaptureHarness(app, monkeypatch)
    hud.observe(top="Go to the location")
    before = owner_snapshot(app)
    result = hud.observe(status="VIP WORK END 12:34", header=HEADER)
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.vip_status_evidence == "VIP WORK" and result.result_header_evidence == HEADER
    assert result.mission.identity_status == "unknown"
    owner, = app._activity_tracker.completed_activities
    assert owner is before[0]
    assert (owner.name, owner.activity_type, owner.started_at) == before[1:4]
    row, = persisted(app)["activities"]
    assert row["name"] == "Go to the location" and row["type"] == "UNKNOWN"
    assert persisted(app)["earnings"] == [] and app.current_money is None


@pytest.mark.parametrize("owns_activity", [False, True], ids=("idle", "existing-owner"))
@pytest.mark.parametrize("state,cached_text", [
    (GameState.MISSION_FAILED, "MISSION FAILED"),
    (GameState.MISSION_ACTIVE, "Headhunter"),
    (GameState.MISSION_COMPLETE, "Headhunter"),
    (GameState.MISSION_COMPLETE, "MISSION FAILED"),
    (GameState.MISSION_COMPLETE, "MISSION PASSED\nHeadhunter\nSightseer"),
    (GameState.MISSION_COMPLETE, "HEIST PASSED\nCayo Perico"),
])
def test_direct_ordinary_header_rejects_inconsistent_state_or_cached_reading(
        app, owns_activity, state, cached_text):
    if owns_activity:
        app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .8, "start",
                                              mission_text="Headhunter"), CaptureResult())
        before = owner_snapshot(app)
    result = StateDetectionResult(state, .85, "inconsistent direct result",
                                  mission=MissionParser().parse(cached_text),
                                  result_header_text=HEADER, result_header_evidence=HEADER)
    capture = CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state is GameState.UNKNOWN and capture.state_confidence == 0
    assert capture.result_header_text == HEADER and capture.result_header_evidence == ""
    if owns_activity:
        assert owner_snapshot(app) == before and app._activity_tracker.current_activity is before[0]
    else:
        assert_not_started(app)
    assert app._data.terminal_mission_episode is None
    assert_no_accounting(app)
