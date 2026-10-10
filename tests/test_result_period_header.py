"""One observed standalone ASCII period, using production parsing and callbacks.

The public rescue still's production result_header text was exactly
``MISSION PASSED.`` (Tesseract mean word confidence 0.79382072). These tests
inject that text at the OCR boundary; they are not new OCR or gameplay proof.
The visible subtitle and publisher's mission name confer no parser authority.
"""

from dataclasses import asdict
from datetime import timedelta

import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetectionResult, is_ordinary_result_header
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_result_header_admission import observation
from tests.test_result_header_workflow import assert_no_accounting, owner_snapshot
from tests.test_terminal_mission_identity_ownership import mission_stats
from tests.test_vip_status_capture import hud as hud  # noqa: PLC0414


HEADER = "MISSION PASSED."
NEGATIVES = (
    "MISSION PASSED..", "MISSION PASSED...", "MISSION PASSED.!",
    "MISSION PASSED!", "MISSION PASSED:", "MISSION PASSED .",
    "MISSION PASSED\u2024", "MISSION PASSED\uff0e", "MISSION PASSED\u2026",
    "MISSION\nPASSED.", "MISSION\vPASSED.", "MISSION\u00a0PASSED.",
    "MI\u017f\u017fION PASSED.", "MISSION PA55ED.", "MISSION PASSED. EXTRA",
    "'MISSION PASSED.'", '"MISSION PASSED."', "\u201cMISSION PASSED.\u201d",
    "If the MISSION PASSED. yesterday", "Objective: MISSION PASSED.",
    "Go to the MISSION PASSED.", "VIP WORK END 12:34\nMISSION PASSED.",
    "ASSASSINATION BONUS COMPLETE", "ASSASSINATION BONUS\nCOMPLETE",
    "MISSION PASSED.\nASSASSINATION BONUS COMPLETE",
    "MISSION PASSED. +$40500", "MISSION PASSED.$40500", "MISSION PASSED.\n$40500",
    "MISSION PASSED.\nClient rescued", "MISSION PASSED.\nHeadhunter",
    "MISSION PASSED.\nSecurity Contract", "MISSION PASSED.\nMISSION FAILED",
    "MISSION PASSED.\n2 of 3 armaments delivered",
    "MISSION PASSED\n2 of 3 armaments delivered.",
    "MISSION PASSED\n.", ".MISSION PASSED", "MISSION PASSED. |",
)


def test_observed_period_parser_admission_is_generic_success():
    reading = MissionParser().parse(HEADER)
    assert reading.outcome == "complete" and reading.outcome_scope is None
    assert reading.mission_type is reading.heist_phase is MissionType.UNKNOWN
    assert reading.mission_name == reading.objective == ""
    assert reading.identity_status == "unknown" and reading.candidates == ()
    assert is_ordinary_result_header(reading)


@pytest.mark.parametrize("raw", [HEADER, "mission passed.", "\r\n MiSsIoN\t PASSED. \r\n"])
def test_period_header_overrides_visual_menu_without_name_or_type(monkeypatch, raw):
    result, calls, updates, detector = observation(
        monkeypatch, raw, quick=StateDetectionResult(GameState.MENU, .7, "Menu overlay detected"))
    assert (result.state, result.confidence) == (GameState.MISSION_COMPLETE, .85)
    assert result.result_header_text == result.result_header_evidence == raw
    assert result.mission.outcome == "complete" and result.mission.outcome_scope is None
    assert result.mission.identity_status == "unknown" and result.mission.candidates == ()
    assert result.mission.mission_name == result.mission.objective == ""
    assert result.mission.mission_type is result.mission.heist_phase is MissionType.UNKNOWN
    assert calls[-1] == (raw, {"threshold": False, "invert": False, "scale": 2.0})
    assert updates == [result] and detector._context.last_state is GameState.MISSION_COMPLETE


@pytest.mark.parametrize("raw", NEGATIVES)
def test_period_lookalikes_and_extra_content_have_no_header_authority(monkeypatch, raw):
    assert not is_ordinary_result_header(MissionParser().parse(raw))
    baseline, _, _, _ = observation(monkeypatch)
    result, _, _, _ = observation(monkeypatch, raw)
    assert result.state is baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.result_header_text == raw and result.result_header_evidence == ""


@pytest.mark.parametrize("top,center,banner", [
    ("Bunker Stock 40% Supplies 60% Value $7000", "", ""),
    ("MISSION FAILED", "", ""), ("MISSION PASSED", "", ""),
    ("", "", "MISSION PASSED\nHeadhunter"), ("", "", "HEIST PASSED\nCayo Perico"),
    ("Headhunter", "Sightseer", ""), ("MISSION PASSED", "MISSION FAILED", ""),
])
def test_period_header_preserves_stronger_primary_results_and_conflicts(
        monkeypatch, top, center, banner):
    baseline, calls, _, _ = observation(monkeypatch, top=top, center=center, banner=banner)
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, top=top, center=center, banner=banner)
    assert asdict(result) == asdict(baseline) and actual_calls == calls


@pytest.mark.parametrize("header", ["MISSION PASSED", "MISSION PASSED\n2 of 3 armaments delivered",
                                  "HEIST PASSED\nCayo Perico"])
def test_existing_accepted_result_headers_keep_their_meaning(monkeypatch, header):
    result, _, _, _ = observation(monkeypatch, header)
    assert result.state is GameState.MISSION_COMPLETE and result.confidence == .85
    assert result.result_header_evidence == header and result.mission.outcome == "complete"
    assert result.mission.outcome_scope == ("heist" if header.startswith("HEIST") else None)


@pytest.mark.parametrize("first,kind,status", [
    ("Headhunter", ActivityType.VIP_WORK, "known_name"),
    ("Security Contract", ActivityType.SECURITY_CONTRACT, "type_only"),
    ("Go to the location", ActivityType.UNKNOWN, "unknown"),
])
def test_period_capture_completes_existing_owner_once_with_no_inferred_money(
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
    assert completed == [before[0]] and completed[0] is before[0]
    assert (before[0].name, before[0].activity_type, before[0].started_at) == before[1:4]
    assert app._activity_tracker.current_activity is None
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"]) == (
        before[1], kind.name, True, 45)
    # Stored zero is the existing missing-balance policy, not observed payout.
    assert row["earnings"] == 0 and app.current_money is None
    assert persisted(app)["earnings"] == [] and app.session_earnings == 0
    saved = persisted(app)
    hud.observe(header=HEADER)
    assert persisted(app) == saved and completed == [before[0]]
    assert mission_stats(app) == (1, 1, 0, 0)


def test_period_direct_app_callback_completes_original_owner(app):
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .8, "start",
                                          mission_text="Headhunter"), CaptureResult())
    owner = app._activity_tracker.current_activity
    callbacks = []
    app.on_mission_complete(callbacks.append)
    capture = CaptureResult()
    app._process_state(StateDetectionResult(
        GameState.MISSION_COMPLETE, .85, "period result",
        result_header_text=HEADER, result_header_evidence=HEADER), capture)
    assert callbacks == [owner] and callbacks[0] is owner
    assert app._activity_tracker.completed_activities == [owner]
    assert capture.result_header_evidence == HEADER and capture.mission.outcome == "complete"
    assert persisted(app)["activities"][0]["name"] == "Headhunter"


def test_period_ownerless_results_never_create_activity_money_or_cooldown(app, hud):
    for _ in range(2):
        result = hud.observe(header=HEADER)
        assert result.game_state is GameState.MISSION_COMPLETE
        assert result.result_header_evidence == HEADER and result.mission.identity_status == "unknown"
        assert app._data.terminal_mission_episode is None
        assert_not_started(app)
        assert_no_accounting(app)


@pytest.mark.parametrize("raw", NEGATIVES)
def test_period_direct_revalidation_rejects_nonexact_header(app, hud, raw):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    capture = CaptureResult()
    app._process_state(StateDetectionResult(GameState.MISSION_COMPLETE, .85, "untrusted header",
        result_header_text=raw, result_header_evidence=raw), capture)
    assert capture.game_state is GameState.UNKNOWN and capture.state_confidence == 0
    assert capture.result_header_text == raw and capture.result_header_evidence == ""
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


@pytest.mark.parametrize("confidence", [0, .6, float("nan"), float("inf"), True])
def test_period_direct_result_preserves_confidence_guard(app, hud, confidence):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    app._process_state(StateDetectionResult(GameState.MISSION_COMPLETE, confidence, "weak result",
        result_header_text=HEADER, result_header_evidence=HEADER), CaptureResult())
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


@pytest.mark.parametrize("source", ["bottom", "status"])
def test_period_in_objective_or_sidebar_source_cannot_complete_owner(app, hud, source):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    result = hud.observe(**{source: HEADER})
    assert result.result_header_evidence == ""
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


def test_unadmitted_period_diagnostic_cannot_supply_result(app, hud):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    result = StateDetectionResult(GameState.UNKNOWN, 0, "diagnostic", result_header_text=HEADER)
    assert app._mission_reading(result).outcome is None
    app._process_state(result, CaptureResult())
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


def test_period_callback_replay_deduplicates_balance_and_activity(app, hud, clock):
    callbacks, snapshots = [], []

    def complete(activity):
        callbacks.append(activity)
        # Record callback-visible state; assert outside the app's caught callback.
        snapshots.append((app._data.mission_start_time, len(persisted(app)["activities"])))
        hud.observe(header=HEADER, money="$1250")

    app.on_mission_complete(complete)
    hud.observe(top="Headhunter", money="$1000")
    owner = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=45)
    result = hud.observe(header=HEADER, money="$1250")
    assert result.game_state is GameState.MISSION_COMPLETE
    assert callbacks == [owner] and snapshots == [(None, 1)]
    assert app._activity_tracker.completed_activities == [owner]
    row, = persisted(app)["activities"]
    assert (row["name"], row["duration_seconds"], row["earnings"]) == ("Headhunter", 45, 250)
    assert [entry["amount"] for entry in persisted(app)["earnings"]] == [250]
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert mission_stats(app) == (1, 1, 0, 0)


@pytest.mark.parametrize("complete_nested", [False, True])
def test_period_callback_new_owner_survives_and_callback_arguments_stay_owned(
        app, hud, clock, complete_nested):
    events, started = [], []

    def first(activity):
        events.append(("first", activity.name))
        if activity.name == "Headhunter":
            hud.observe(top="Sightseer")
            started.append(app._activity_tracker.current_activity)
            if complete_nested:
                clock.value += timedelta(seconds=30)
                hud.observe(header=HEADER)

    app.on_mission_complete(first)
    app.on_mission_complete(lambda activity: events.append(("second", activity.name)))
    hud.observe(top="Headhunter")
    clock.value += timedelta(seconds=45)
    assert hud.observe(header=HEADER).game_state is GameState.MISSION_COMPLETE
    assert len(started) == 1 and started[0] is not None
    if not complete_nested:
        assert app._activity_tracker.current_activity is started[0]
        assert app._data.mission_start_time == clock.value
        assert events == [("first", "Headhunter"), ("second", "Headhunter")]
        clock.value += timedelta(seconds=30)
        hud.observe(header=HEADER)
    else:
        assert events == [("first", "Headhunter"), ("first", "Sightseer"),
                          ("second", "Sightseer"), ("second", "Headhunter")]
    assert [(row["name"], row["duration_seconds"], row["earnings"])
            for row in persisted(app)["activities"]] == [("Headhunter", 45, 0), ("Sightseer", 30, 0)]
    assert persisted(app)["earnings"] == [] and mission_stats(app) == (2, 2, 0, 0)


@pytest.mark.parametrize("stale", ["Headhunter", "Headhunter\nSightseer"])
def test_period_stale_primary_cannot_consume_next_owner(app, hud, stale):
    hud.observe(top="Headhunter")
    hud.observe(header=HEADER)
    hud.observe(top="Sightseer")
    before = owner_snapshot(app)
    saved = persisted(app)
    result = hud.observe(header=HEADER, banner=stale)
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert owner_snapshot(app) == before and persisted(app) == saved
