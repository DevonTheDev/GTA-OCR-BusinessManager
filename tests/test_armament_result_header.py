"""One observed armament subtitle; injected OCR is not live gameplay proof.

The exact two-line value was read by production preprocessing on the local
Armament-8.jpg guide still. Original/derived image bytes are not shipped here.
"""

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
from tests.test_result_header_workflow import assert_no_accounting, owner_snapshot
from tests.test_terminal_mission_identity_ownership import mission_stats
from tests.test_vip_status_capture import hud as hud  # noqa: PLC0414


HEADER = "MISSION PASSED\n2 of 3 armaments delivered"
NEGATIVES = (
    HEADER + " $40500", HEADER + "\n$40500", HEADER + "\nHeadhunter",
    HEADER + "\nSecurity Contract", HEADER + "\nGo to the location",
    HEADER + "\nMISSION FAILED", HEADER + "!", HEADER + "\nVIP WORK",
    HEADER.replace("2 of 3", "1 of 3"), HEADER.replace("2 of 3", "3 of 3"),
    HEADER.replace("2 of 3", "2 of 4"), HEADER.replace("2 of 3", "02 of 3"),
    "2 of 3 armaments delivered", "PASSED\n2 of 3 armaments delivered",
    "MISSION\nPASSED\n2 of 3 armaments delivered",
    "MISSION PASSED 2 of 3 armaments delivered",
    "MISSION PASSED\t2 of 3 armaments delivered",
    "MISSION PASSED\n2 of 3 armaments\ndelivered",
    "MISSION PASSED\n2 of 3\narmaments delivered",
    "2 of 3 armaments delivered\nMISSION PASSED",
    HEADER.replace("of 3", "of\u00a03"), HEADER.replace("armaments", "armamentſ"),
    HEADER.replace("\n", "\v"), HEADER.replace("\n", "\u2028"),
    "MISSION FAILED\n2 of 3 armaments delivered",
)


@pytest.mark.parametrize("raw", [
    HEADER, "mission passed\n2 OF 3 ARMAMENTS DELIVERED",
    "\r\n MiSsIoN\t PASSED \r\n 2\tof  3\tarmaments  delivered \r\n",
])
def test_observed_armament_header_overrides_phone_without_identity(monkeypatch, raw):
    result, calls, updates, detector = observation(
        monkeypatch, raw,
        quick=StateDetectionResult(GameState.PHONE, .6, "Phone UI detected"),
    )
    assert (result.state, result.confidence) == (GameState.MISSION_COMPLETE, .85)
    assert result.result_header_text == result.result_header_evidence == raw
    assert result.mission.outcome == "complete" and result.mission.outcome_scope is None
    assert result.mission.identity_status == "unknown" and result.mission.candidates == ()
    assert result.mission.mission_name == result.mission.objective == ""
    assert result.mission.mission_type is result.mission.heist_phase is MissionType.UNKNOWN
    assert calls[-1] == (raw, {"threshold": False, "invert": False, "scale": 2.0})
    assert updates == [result] and detector._context.last_state is GameState.MISSION_COMPLETE


@pytest.mark.parametrize("raw", NEGATIVES)
def test_other_armament_counts_layouts_and_extra_text_remain_diagnostic(monkeypatch, raw):
    baseline, _, _, _ = observation(monkeypatch)
    result, _, _, _ = observation(monkeypatch, raw)
    assert result.state is baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.result_header_text == raw and result.result_header_evidence == ""


@pytest.mark.parametrize("top,center", [
    ("Bunker Stock 40% Supplies 60% Value $7000", ""),
    ("MISSION FAILED", ""), ("MISSION PASSED", ""),
    ("HEIST PASSED", ""), ("HEIST PASSED\nCayo Perico", ""),
    ("Headhunter", "Sightseer"), ("MISSION PASSED", "MISSION FAILED"),
])
def test_armament_header_preserves_primary_guards(monkeypatch, top, center):
    baseline, calls, _, _ = observation(monkeypatch, top=top, center=center)
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, top=top, center=center)
    assert asdict(result) == asdict(baseline) and actual_calls == calls


@pytest.mark.parametrize("first,kind,status", [
    ("Headhunter", ActivityType.VIP_WORK, "known_name"),
    ("Security Contract", ActivityType.SECURITY_CONTRACT, "type_only"),
    ("Go to the location", ActivityType.UNKNOWN, "unknown"),
])
def test_armament_header_completes_current_owner_once_without_identity_or_money(
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
    assert result.mission.objective == "" and result.money is None
    owner = before[0]
    assert completed == [owner] and completed[0] is owner
    assert (owner.name, owner.activity_type, owner.started_at) == before[1:4]
    assert app._activity_tracker.current_activity is None
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"], row["duration_seconds"]) == (
        before[1], kind.name, True, 45)
    # Existing missing-balance storage policy; no observed zero payout.
    assert row["earnings"] == 0 and app.current_money is None
    assert persisted(app)["earnings"] == [] and app.session_earnings == 0
    saved = persisted(app)
    assert hud.observe(header=HEADER).game_state is GameState.MISSION_COMPLETE
    assert persisted(app) == saved and completed == [owner]
    assert mission_stats(app) == (1, 1, 0, 0)


def test_armament_header_without_owner_creates_no_activity_or_accounting(app, hud):
    for _ in range(2):
        result = hud.observe(header=HEADER)
        assert result.game_state is GameState.MISSION_COMPLETE
        assert result.result_header_evidence == HEADER
        assert result.mission.identity_status == "unknown"
        assert app._data.terminal_mission_episode is None
        assert_not_started(app)
        assert_no_accounting(app)


@pytest.mark.parametrize("raw", NEGATIVES)
def test_direct_armament_header_revalidation_rejects_nonexact_values(app, hud, raw):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    capture = CaptureResult()
    app._process_state(StateDetectionResult(
        GameState.MISSION_COMPLETE, .85, "untrusted header",
        result_header_text=raw, result_header_evidence=raw), capture)
    assert capture.game_state is GameState.UNKNOWN and capture.state_confidence == 0
    assert capture.result_header_text == raw and capture.result_header_evidence == ""
    assert owner_snapshot(app) == before and app._activity_tracker.current_activity is before[0]
    assert_no_accounting(app)


def test_direct_armament_header_uses_same_admission_and_retains_original_owner(app, hud):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    capture = CaptureResult()
    result = StateDetectionResult(GameState.MISSION_COMPLETE, .85, "armament header",
                                  result_header_text=HEADER, result_header_evidence=HEADER)
    assert app._mission_reading(result).identity_status == "unknown"
    app._process_state(result, capture)
    assert capture.result_header_evidence == HEADER and capture.mission.outcome == "complete"
    assert app._activity_tracker.completed_activities == [before[0]]
    assert persisted(app)["activities"][0]["name"] == "Headhunter"


@pytest.mark.parametrize("state,cached", [
    (GameState.MISSION_FAILED, "MISSION FAILED"),
    (GameState.MISSION_ACTIVE, "Headhunter"),
    (GameState.MISSION_COMPLETE, "Headhunter"),
    (GameState.MISSION_COMPLETE, "MISSION FAILED"),
    (GameState.MISSION_COMPLETE, "MISSION PASSED\nHeadhunter\nSightseer"),
    (GameState.MISSION_COMPLETE, "HEIST PASSED\nCayo Perico"),
])
def test_direct_armament_header_keeps_cached_result_consistency_guard(app, hud, state, cached):
    hud.observe(top="Headhunter")
    before = owner_snapshot(app)
    capture = CaptureResult()
    app._process_state(StateDetectionResult(
        state, .85, "inconsistent direct result", mission=MissionParser().parse(cached),
        result_header_text=HEADER, result_header_evidence=HEADER), capture)
    assert capture.game_state is GameState.UNKNOWN and capture.result_header_evidence == ""
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


def test_armament_result_excludes_footer_identity_and_objective_authority(app, hud):
    hud.observe(top="Go to the location")
    before = owner_snapshot(app)
    result = hud.observe(header=HEADER, status="VIP WORK END 12:34")
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.vip_status_evidence == "VIP WORK" and result.result_header_evidence == HEADER
    assert result.mission.identity_status == "unknown" and result.mission.objective == ""
    owner, = app._activity_tracker.completed_activities
    assert owner is before[0] and owner.activity_type is ActivityType.UNKNOWN
    assert app._data.terminal_mission_episode.objectives.entries == before[-1]
    assert persisted(app)["earnings"] == [] and app.current_money is None


@pytest.mark.parametrize("stale", ["Headhunter", "Headhunter\nSightseer"])
def test_stale_identity_with_armament_result_cannot_consume_new_owner(app, hud, stale):
    hud.observe(top="Headhunter", center="Eliminate the targets")
    assert hud.observe(header=HEADER).game_state is GameState.MISSION_COMPLETE
    saved = persisted(app)
    old = hud.observe(top="Headhunter", center="Eliminate the targets")
    assert old.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None and persisted(app) == saved
    hud.observe(top="Sightseer")
    before = owner_snapshot(app)
    transitions = tuple(app._state_machine.context.transitions)
    rejected = hud.observe(header=HEADER, banner=stale)
    assert rejected.game_state is GameState.UNKNOWN and rejected.state_confidence == 0
    assert owner_snapshot(app) == before and app._activity_tracker.current_activity is before[0]
    assert tuple(app._state_machine.context.transitions) == transitions
    assert persisted(app) == saved


def test_armament_completion_callback_replay_cannot_duplicate_accounting(app, hud):
    events = []

    def completed(activity):
        events.append(activity)
        assert app._data.mission_start_time is None
        assert len(persisted(app)["activities"]) == 1
        hud.observe(header=HEADER)

    app.on_mission_complete(completed)
    hud.observe(top="Headhunter")
    owner = app._activity_tracker.current_activity
    assert hud.observe(header=HEADER).game_state is GameState.MISSION_COMPLETE
    assert events == [owner] and app._activity_tracker.completed_activities == [owner]
    assert len(persisted(app)["activities"]) == 1 and mission_stats(app) == (1, 1, 0, 0)


@pytest.mark.parametrize("complete_nested", [False, True])
def test_armament_completion_callback_keeps_new_owner_and_callback_arguments(
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
    assert started and started[0] is not None
    if not complete_nested:
        assert app._activity_tracker.current_activity is started[0]
        assert app._data.mission_start_time == clock.value
        assert events == [("first", "Headhunter"), ("second", "Headhunter")]
        clock.value += timedelta(seconds=30)
        hud.observe(header=HEADER)
    else:
        assert events == [("first", "Headhunter"), ("first", "Sightseer"),
                          ("second", "Sightseer"), ("second", "Headhunter")]
    assert app._activity_tracker.current_activity is None
    assert [(row["name"], row["duration_seconds"], row["earnings"])
            for row in persisted(app)["activities"]] == [
                ("Headhunter", 45, 0), ("Sightseer", 30, 0)]
    assert persisted(app)["earnings"] == [] and mission_stats(app) == (2, 2, 0, 0)
