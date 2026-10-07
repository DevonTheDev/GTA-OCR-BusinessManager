"""VIP status text through the real capture, detector, app and SQLite workflow.

Only native screen acquisition and the OCR text boundary are replaced. These
tests prove source ownership and bookkeeping, not real gameplay OCR quality.
"""

from datetime import timedelta

import numpy as np
import pytest

from src.app import CaptureResult, GTABusinessManager
from src.capture.regions import Region, ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import CaptureHarness, assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414


REGIONS = ScreenRegions()
VIP = Region(.82, .88, .17, .12)
BATCH = [REGIONS.full_screen, REGIONS.money_display, REGIONS.mission_text,
         REGIONS.center_prompt, REGIONS.timer_bottom_right, REGIONS.mission_banner,
         REGIONS.bottom_objective, REGIONS.result_header, VIP]
HEADHUNTER_STATUS = "TARGETS REMAINING 1\nvip woRK END 1273.\n<_< i ~"
MARKER = "VIP WORK END 12:34"


class VipCaptureHarness(CaptureHarness):
    def observe(self, status="", *, top="", center="", banner="", bottom="",
                header="", money="", timer="", business=None):
        self.texts = {VIP: status, REGIONS.mission_text: top,
                      REGIONS.center_prompt: center, REGIONS.mission_banner: banner,
                      REGIONS.bottom_objective: bottom, REGIONS.result_header: header,
                      REGIONS.money_display: money, REGIONS.timer_bottom_right: timer}
        if business is not None:
            self.texts.update(zip(REGIONS.get_business_regions().values(), business))
        return self.app._do_capture_cycle()


@pytest.fixture
def hud(app, monkeypatch):
    return VipCaptureHarness(app, monkeypatch)


def owner_snapshot(app):
    current = app._activity_tracker.current_activity
    return (current, current.started_at if current else None,
            app._data.mission_start_time, app._data.mission_start_money,
            app._data.current_mission, app._data.mission_identity_type,
            app._data.mission_identity_status, app._data.mission_heist_phase,
            frozenset(app._data.mission_objectives.entries))


def assert_no_footer_side_effects(app):
    assert app._data.business_states == {}
    assert app.current_money is None
    assert app.session_start_money is None
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app.session_stats.activities_completed == 0
    assert app._activity_tracker.completed_activities == []
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


@pytest.mark.parametrize("resolution", ((1280, 720), (1920, 1080), (2560, 1440)))
def test_vip_status_is_ninth_independent_crop_from_same_screen_grab(app, monkeypatch, resolution):
    assert getattr(ScreenRegions(), "vip_status", None) == VIP
    hud = VipCaptureHarness(app, monkeypatch, resolution)
    width, height = resolution
    hud.frame[:, :, 0] = np.arange(width, dtype=np.uint16) % 256
    hud.frame[:, :, 1] = (np.arange(height, dtype=np.uint16) % 256)[:, None]
    hud.observe()
    assert hud.batches == [BATCH]
    assert hud.grabs == [{"left": -200, "top": 30, "width": width, "height": height}]
    assert hud.waits == [True]
    for index, region in enumerate(BATCH):
        left, top, right, bottom = region.to_absolute(width, height)
        np.testing.assert_array_equal(hud.crops[0][index], hud.frame[top:bottom, left:right, :3])
    assert not np.shares_memory(hud.crops[0][8], hud.crops[0][0])
    assert not np.shares_memory(hud.crops[0][8], hud.crops[0][4])


def test_spaced_status_starts_only_unnamed_vip_category_without_timer_or_objective(app, hud):
    observed = hud.observe(HEADHUNTER_STATUS)
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.mission.mission_type is MissionType.VIP_WORK
    assert observed.mission.identity_status == "type_only"
    assert observed.mission.mission_name == ""
    assert observed.mission.heist_phase is MissionType.UNKNOWN
    assert observed.mission.outcome is observed.mission.outcome_scope is None
    assert observed.activity_type is ActivityType.VIP_WORK
    assert observed.activity_name == "VIP Work"
    assert observed.vip_status_text == HEADHUNTER_STATUS
    assert observed.vip_status_evidence == "VIP WORK"
    assert observed.timer is None and observed.money is None
    assert app.current_money is app._data.mission_start_money is None
    assert not app._data.mission_objectives.entries
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []
    assert [call for call in hud.ocr_calls if call[0] == VIP] == [
        (VIP, {"threshold": False, "invert": True, "scale": 2.0}),
    ]
    assert_no_footer_side_effects(app)


@pytest.mark.parametrize("status", ("VIP WORK END", "VIP WORK END 0:00", MARKER,
                                  HEADHUNTER_STATUS))
def test_end_and_countdown_never_finish_category_only_activity(app, hud, clock, status):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    hud.observe(MARKER, money="$1000")
    before = owner_snapshot(app)
    stored = persisted(app)
    clock.value += timedelta(seconds=50)
    observed = hud.observe(status)
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.mission.outcome is observed.mission.outcome_scope is None
    assert owner_snapshot(app) == before
    assert observed.timer is None
    assert observed.money is None and app.current_money == 1000
    assert persisted(app) == stored
    assert callbacks == []


@pytest.mark.parametrize("name", ("Headhunter", "Sightseer"))
def test_real_primary_title_refines_status_owner_without_resetting_baseline(app, hud, clock, name):
    hud.observe(MARKER, money="$1000")
    current = app._activity_tracker.current_activity
    initial = owner_snapshot(app)
    clock.value += timedelta(seconds=25)
    observed = hud.observe("VIP WORK END 0:00", banner=name, center="Go to the location")
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.activity_name == name
    assert observed.activity_identity_status == "known_name"
    assert observed.mission.mission_name == name
    assert app._activity_tracker.current_activity is current
    assert owner_snapshot(app)[:4] == initial[:4]
    assert app._data.mission_objectives.entries == {"go to the location"}
    refined = owner_snapshot(app)
    clock.value += timedelta(seconds=10)
    repeated = hud.observe(HEADHUNTER_STATUS)
    assert repeated.game_state is GameState.MISSION_ACTIVE
    assert repeated.activity_name == name
    assert owner_snapshot(app) == refined
    assert app.session_earnings == 0
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


@pytest.mark.parametrize("status", (
    "TAKE $7000\nLOOT BAG", "$999999", "TARGETS REMAINING 1", "PACKAGES COLLECTED 1/3",
    "Headhunter", "Sightseer", "Escape Cayo Perico", "Bunker Stock 40% Supplies 60% Value $7000",
    "MISSION PASSED\nHeadhunter\n$7000", "HEIST PASSED\nCayo Perico",
    "VIPWORKEND11:324", "VIP WORK EN", "VIP WORK ENDLESS 12:34", "VIP WORK\nEND 12:34",
    "VIP\nWORK END 12:34", "If VIP WORK END 12:34 appears, continue",
    "VIP WORK END 12:34 and collect the reward", "VIP WORK END $7000",
))
def test_unadmitted_footer_cannot_start_name_objective_business_result_or_cash(app, hud, status):
    before = persisted(app)
    observed = hud.observe(status)
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert observed.vip_status_text == status
    assert observed.vip_status_evidence == ""
    assert observed.timer is observed.money is None
    assert_not_started(app)
    assert_no_footer_side_effects(app)
    assert hud.region_calls == []
    assert persisted(app) == before


@pytest.mark.parametrize("extra", (
    "TAKE $7000\nLOOT BAG", "$999999", "TARGETS REMAINING 1",
    "Headhunter", "Sightseer", "Go to the location", "Escape Cayo Perico",
    "Bunker Stock 40% Supplies 60% Value $7000", "MISSION PASSED\nHeadhunter",
    "HEIST PASSED\nCayo Perico",
))
def test_adjacent_footer_rows_never_gain_name_objective_or_accounting_authority(app, hud, extra):
    observed = hud.observe(MARKER + "\n" + extra)
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.mission.mission_name == ""
    assert observed.mission.mission_type is MissionType.VIP_WORK
    assert observed.mission.identity_status == "type_only"
    assert observed.mission.outcome is observed.mission.outcome_scope is None
    assert observed.activity_name == "VIP Work"
    assert observed.timer is observed.money is None
    assert not app._data.mission_objectives.entries
    assert_no_footer_side_effects(app)


def test_money_and_timer_remain_owned_by_their_separate_crops(app, hud):
    observed = hud.observe("VIP WORK END 1273.\nTAKE $999999", money="$1000", timer="3:21")
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.timer.total_seconds == 201
    assert observed.money.display_value == app.current_money == 1000
    assert observed.money_change == 0
    assert app._data.mission_start_money == 1000
    assert persisted(app)["earnings"] == []
    assert [call for call in hud.ocr_calls if call[0] == REGIONS.timer_bottom_right] == [
        (REGIONS.timer_bottom_right, {"invert": True, "scale": 2.0}),
    ]
    observed = hud.observe("VIP WORK END 0:00\n$123456", money="$1250", timer="0:00")
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.timer.total_seconds == 0
    assert observed.money_change == 250
    assert app.current_money == 1250 and app._data.mission_start_money == 1000
    assert [row["amount"] for row in persisted(app)["earnings"]] == [250]
    assert persisted(app)["activities"] == []


@pytest.mark.parametrize("primary", ({"top": "Cayo Perico"}, {"bottom": "Escape Cayo Perico"},
                                    {"banner": "Security Contract"}))
def test_status_conflicting_with_independent_family_cannot_select_an_owner(app, hud, primary):
    observed = hud.observe(MARKER, **primary)
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert observed.mission.identity_status == "ambiguous"
    assert_not_started(app)
    assert_no_footer_side_effects(app)


def test_status_never_repairs_ambiguous_primary_titles(app, hud):
    observed = hud.observe(MARKER, top="Headhunter", banner="Sightseer")
    assert observed.game_state is GameState.UNKNOWN
    assert observed.mission.identity_status == "ambiguous"
    assert VIP not in [region for region, _ in hud.ocr_calls]
    assert_not_started(app)


def test_primary_business_screen_keeps_original_values_and_ocr_ownership(app, hud):
    observed = hud.observe(MARKER, top="Bunker Stock Supplies",
                           business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"))
    assert observed.game_state is GameState.BUSINESS_COMPUTER
    business = app.get_business_state("bunker")
    assert (business["stock"], business["supply"], business["value"]) == (40, 60, 7000)
    assert hud.region_calls == [(region, False) for region in REGIONS.get_business_regions().values()]
    assert VIP not in [region for region, _ in hud.ocr_calls]
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


@pytest.mark.parametrize("primary", ({"banner": "Cayo Perico"},
                                    {"top": "Headhunter", "banner": "Sightseer"}))
def test_conflicting_observation_preserves_existing_named_owner_and_objectives(app, hud, primary):
    hud.observe(MARKER, banner="Headhunter", center="Eliminate the targets", money="$1000")
    before = owner_snapshot(app)
    stored = persisted(app)
    observed = hud.observe(MARKER, **primary)
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert observed.mission.identity_status == "ambiguous"
    assert owner_snapshot(app) == before
    assert persisted(app) == stored


@pytest.mark.parametrize("label,state", (("MISSION PASSED", GameState.MISSION_COMPLETE),
                                        ("MISSION FAILED", GameState.MISSION_FAILED)))
def test_primary_result_cannot_obtain_category_from_status_or_invent_start(app, hud, label, state):
    observed = hud.observe(MARKER, banner=label)
    assert observed.game_state is state
    assert observed.mission.mission_type is MissionType.UNKNOWN
    assert observed.mission.identity_status == "unknown"
    assert observed.vip_status_evidence == ""
    assert VIP not in [region for region, _ in hud.ocr_calls]
    assert app._data.terminal_mission_episode is None
    assert_not_started(app)
    assert_no_footer_side_effects(app)


@pytest.mark.parametrize("source", ("banner", "header"))
def test_unqualified_heist_result_remains_unknown_and_cannot_borrow_status_family(app, hud, source):
    observed = hud.observe(MARKER, **{source: "HEIST PASSED"})
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert_not_started(app)
    assert_no_footer_side_effects(app)
    if source == "header":
        calls = [region for region, _ in hud.ocr_calls]
        assert calls.index(VIP) < calls.index(REGIONS.result_header)
        assert observed.vip_status_evidence == "VIP WORK"
        assert observed.result_header_evidence == ""
    else:
        assert VIP not in [region for region, _ in hud.ocr_calls]


@pytest.mark.parametrize("header", ("HEIST PASSED", "HEIST PASSED\nCayo Perico"))
def test_independent_heist_header_cannot_consume_active_vip_owner(app, hud, header):
    hud.observe(MARKER, money="$1000")
    before = owner_snapshot(app)
    stored = persisted(app)
    observed = hud.observe(MARKER, header=header)
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert observed.result_header_text == header
    assert observed.result_header_evidence == ""
    assert owner_snapshot(app) == before
    assert persisted(app) == stored


@pytest.mark.parametrize("wrong_result", ("MISSION PASSED\nSightseer", "MISSION FAILED\nSightseer",
                                         "HEIST PASSED\nCayo Perico"))
def test_wrong_owner_result_cannot_finish_refined_vip_even_with_status(app, hud, clock, wrong_result):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    hud.observe(MARKER, money="$1000")
    hud.observe(MARKER, banner="Headhunter", center="Eliminate the targets")
    before = owner_snapshot(app)
    stored = persisted(app)
    clock.value += timedelta(seconds=20)
    observed = hud.observe(MARKER, banner=wrong_result)
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert owner_snapshot(app) == before
    assert persisted(app) == stored
    assert callbacks == []


@pytest.mark.parametrize("label,success", (("MISSION PASSED", True), ("MISSION FAILED", False)))
def test_result_completes_once_and_countdown_or_status_gap_never_rearms(app, hud, clock, label, success):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    hud.observe(MARKER, money="$1000")
    current = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=45)
    result = hud.observe(MARKER, banner=label + "\nHeadhunter", money="$1500")
    assert result.game_state is (GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED)
    row, = persisted(app)["activities"]
    assert (row["type"], row["name"], row["success"], row["duration_seconds"], row["earnings"]) == (
        "VIP_WORK", "Headhunter", success, 45, 500 if success else 0,
    )
    assert app._activity_tracker.completed_activities == [current]
    assert callbacks == ([current] if success else [])
    before = persisted(app)
    terminal = app._data.terminal_mission_episode
    for status in ("VIP WORK END 12:33", "", "VIPWORKEND11:324", "VIP WORK END 0:00",
                   HEADHUNTER_STATUS, MARKER + "\nCollect a different package",
                   MARKER + "\nSightseer"):
        observed = hud.observe(status)
        assert observed.game_state is GameState.UNKNOWN
        assert observed.state_confidence == 0
        assert app._activity_tracker.current_activity is None
        assert app._data.mission_start_time is app._data.mission_start_money is None
        assert app._data.terminal_mission_episode == terminal
    hud.observe(MARKER, banner=label + "\nHeadhunter")
    assert persisted(app) == before
    assert app.session_stats.activities_completed == 1
    assert callbacks == ([current] if success else [])


def test_unnamed_primary_result_keeps_category_owner_and_fences_status_replay(app, hud, clock):
    hud.observe(MARKER)
    current = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=30)
    observed = hud.observe(MARKER, banner="MISSION PASSED")
    assert observed.game_state is GameState.MISSION_COMPLETE
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["duration_seconds"], row["earnings"]) == (
        "VIP Work", "VIP_WORK", 30, 0,
    )
    assert app._activity_tracker.completed_activities == [current]
    assert app._data.terminal_mission_episode.identity.name == ""
    assert app._data.terminal_mission_episode.identity.family is MissionType.VIP_WORK
    assert hud.observe("VIP WORK END 0:00").game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == [row]


def test_completion_listener_new_named_owner_survives_outer_status_capture(app, hud, clock):
    hud.observe(MARKER, money="$1000")
    first = app._activity_tracker.current_activity
    callbacks = []
    new_owners = []

    def start_next(activity):
        callbacks.append(activity)
        hud.observe(MARKER, banner="Sightseer", center="Collect the packages")
        new_owners.append(owner_snapshot(app))

    app.on_mission_complete(start_next)
    clock.value += timedelta(seconds=30)
    hud.observe(MARKER, banner="MISSION PASSED\nHeadhunter", money="$1250")
    assert callbacks == [first]
    assert app._activity_tracker.completed_activities == [first]
    assert len(new_owners) == 1 and new_owners[0][0] is not first
    assert owner_snapshot(app) == new_owners[0]
    assert app._data.current_mission == "Sightseer"
    assert app._data.mission_start_money == 1250
    assert app._data.mission_objectives.entries == {"collect the packages"}
    row, = persisted(app)["activities"]
    assert (row["name"], row["duration_seconds"], row["earnings"]) == ("Headhunter", 30, 250)


def test_completion_listener_status_replay_is_fenced_before_it_can_reenter(app, hud):
    hud.observe(MARKER)
    current = app._activity_tracker.current_activity
    callbacks = []
    observations = []

    def replay_status(activity):
        callbacks.append(activity)
        observations.append(hud.observe("VIP WORK END 0:00\nCollect a new package"))

    app.on_mission_complete(replay_status)
    hud.observe(MARKER, banner="MISSION PASSED\nHeadhunter")
    assert callbacks == [current]
    assert len(observations) == 1
    assert observations[0].game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is app._data.mission_start_money is None
    assert app._activity_tracker.completed_activities == [current]
    assert len(persisted(app)["activities"]) == app.session_stats.activities_completed == 1


def test_missing_optional_status_image_preserves_primary_capture_workflow(app, hud, monkeypatch):
    capture_batch = hud.capture.capture_multiple_regions

    def missing_status(regions):
        images = capture_batch(regions)
        images[8] = None
        return images

    monkeypatch.setattr(hud.capture, "capture_multiple_regions", missing_status)
    observed = hud.observe(MARKER, banner="Headhunter", money="$1000")
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.activity_name == "Headhunter"
    assert observed.vip_status_text == observed.vip_status_evidence == ""
    assert VIP not in [region for region, _ in hud.ocr_calls]
    assert app.current_money == app._data.mission_start_money == 1000
    assert hud.batches == [BATCH] and len(hud.grabs) == 1


def test_source_models_default_to_no_status_evidence():
    for result in (CaptureResult(), StateDetectionResult(GameState.UNKNOWN, 0, "empty")):
        assert getattr(result, "vip_status_text", None) == ""
        assert getattr(result, "vip_status_evidence", None) == ""


def test_direct_fallback_uses_only_fixed_category_evidence_and_never_raw_footer(app):
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "synthetic direct caller")
    result.vip_status_text = "Headhunter\nMISSION PASSED\nEscape Cayo Perico\n$5000"
    result.vip_status_evidence = "VIP WORK"
    reading = app._mission_reading(result)
    assert reading.mission_type is MissionType.VIP_WORK and reading.identity_status == "type_only"
    assert reading.mission_name == ""
    assert reading.outcome is reading.outcome_scope is None
    assert not GTABusinessManager._objective_evidence(result).entries
    result.vip_status_evidence = ""
    reading = app._mission_reading(result)
    assert reading.identity_status == "unknown" and reading.mission_type is MissionType.UNKNOWN
    assert reading.outcome is None


@pytest.mark.parametrize("outcome", ("MISSION PASSED", "HEIST PASSED"))
def test_direct_fallback_cannot_lend_status_category_to_terminal_evidence(app, outcome):
    result = StateDetectionResult(GameState.MISSION_COMPLETE, .8, "synthetic direct caller",
                                  banner_text=outcome)
    result.vip_status_text = MARKER
    result.vip_status_evidence = "VIP WORK"
    reading = app._mission_reading(result)
    assert reading.mission_type is MissionType.UNKNOWN
    assert reading.mission_name == ""
    assert reading.identity_status == "unknown"


@pytest.mark.parametrize("evidence", ("Headhunter", "VIP WORK END 12:34", "VIP WORK\nHeadhunter",
                                     "VIP WORK\nMISSION PASSED"))
def test_direct_fallback_refuses_noncanonical_status_evidence(app, evidence):
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "synthetic direct caller")
    result.vip_status_text = MARKER
    result.vip_status_evidence = evidence
    reading = app._mission_reading(result)
    assert reading.mission_type is MissionType.UNKNOWN
    assert reading.mission_name == "" and reading.identity_status == "unknown"
    assert reading.outcome is reading.outcome_scope is None


def test_direct_start_and_replay_guard_propagate_status_fields_without_raw_authority(app, hud):
    state = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "synthetic direct caller")
    state.vip_status_text = MARKER + "\nTAKE $5000\nEscape Cayo Perico"
    state.vip_status_evidence = "VIP WORK"
    capture = CaptureResult()
    app._process_state(state, capture)
    assert capture.vip_status_text == state.vip_status_text
    assert capture.vip_status_evidence == "VIP WORK"
    assert capture.mission.mission_type is MissionType.VIP_WORK
    assert app._data.current_mission == "VIP Work"
    assert not app._data.mission_objectives.entries
    hud.observe(banner="MISSION PASSED\nHeadhunter")
    before = persisted(app)
    replay = CaptureResult()
    app._process_state(state, replay)
    assert replay.game_state is GameState.UNKNOWN and replay.state_confidence == 0
    assert replay.vip_status_text == state.vip_status_text
    assert replay.vip_status_evidence == "VIP WORK"
    assert replay.mission.mission_type is MissionType.VIP_WORK
    assert app._activity_tracker.current_activity is None
    assert persisted(app) == before
