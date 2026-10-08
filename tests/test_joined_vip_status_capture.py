"""Joined VIP rows through real capture, application, trackers and SQLite.

Screen pixels and OCR returns are synthetic. Production crop geometry, detector,
application handlers, state machine, parsers and disposable SQLite remain real.
The optional Qt check renders actual widgets; none of this validates Windows
capture/OCR, live gameplay, or the readability of a mission title in an image.
"""

from collections import deque
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
import os

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import assert_not_started, persisted
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_vip_status_capture import (
    BATCH, REGIONS, VIP, VipCaptureHarness, assert_no_footer_side_effects, owner_snapshot,
)


JOINED = "VIPWORKEND 11:324"
SPACED = "VIP WORK END 11:324"
UNTRUSTED = (
    "Sightseer", "Headhunter", "Collect a different package", "Escape Cayo Perico",
    "MISSION PASSED\nSightseer\n$900000", "MISSION FAILED\nHeadhunter",
    "HEIST PASSED\nCayo Perico", "TAKE $999999\nLOOT BAG",
    "Bunker Stock 40% Supplies 60% Value $7000",
)


@pytest.fixture
def hud(app, monkeypatch):
    return VipCaptureHarness(app, monkeypatch)


@pytest.fixture
def paired_huds(app, tmp_path, monkeypatch):
    """Independent app histories, following the existing accounting fixture."""
    other = GTABusinessManager(Settings(tmp_path / "joined" / "config.yaml"))
    other._session_tracker.start_session()
    repository = Repository(str(tmp_path / "joined-accounting.db"))
    assert repository.initialize()
    character = repository.get_or_create_character("Test Player")
    session = repository.start_session(character)
    other._repository = repository
    other._data.character_id = character.id
    other._data.db_session_id = session.id
    try:
        yield (VipCaptureHarness(app, monkeypatch), VipCaptureHarness(other, monkeypatch))
    finally:
        repository.close()


def semantic(value):
    """Remove raw footer and independently measured wall/performance times only.

    Timestamp presence is retained. Duration, identity, balances, bookkeeping,
    confidence, reason, objective evidence and every other field are compared.
    Within-history owner/timestamp retention is asserted separately below.
    """
    ignored = {"vip_status_text", "capture_time_ms", "ocr_time_ms", "total_time_ms"}
    timestamps = {"timestamp", "started_at", "ended_at", "entered_at", "last_money_change_time"}
    if is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, dict):
        return {key: (item is not None if key in timestamps else semantic(item))
                for key, item in value.items() if key not in ignored}
    if isinstance(value, (tuple, list, deque)):
        return [semantic(item) for item in value]
    if isinstance(value, datetime):
        return "timestamp-present"
    return value


def application_snapshot(app):
    return semantic({
        "data": app._data,
        "state_context": app._state_machine.context,
        "current": app._activity_tracker.current_activity,
        "completed": app._activity_tracker.completed_activities,
        "session_stats": app.session_stats,
        "cooldowns": app.cooldown_tracker.get_active_cooldowns(),
        "persisted": persisted(app),
    })


def observe_pair(pair, suffix="11:324", extra="", **kwargs):
    statuses = ("VIP WORK END " + suffix + extra, "VIPWORKEND " + suffix + extra)
    results = [hud.observe(status, **kwargs) for hud, status in zip(pair, statuses)]
    assert semantic(results[0]) == semantic(results[1])
    assert semantic(pair[0].detections[-1]) == semantic(pair[1].detections[-1])
    assert application_snapshot(pair[0].app) == application_snapshot(pair[1].app)
    assert pair[0].ocr_calls == pair[1].ocr_calls
    assert pair[0].batches == pair[1].batches
    assert pair[0].grabs == pair[1].grabs
    assert pair[0].region_calls == pair[1].region_calls
    return results


@pytest.mark.parametrize("status", (JOINED, "vipworkend 0:00", "\tVIPWORKEND\t1273. \t"))
def test_joined_status_starts_unnamed_category_and_never_completes(app, hud, status):
    completions = []
    app.on_mission_complete(completions.append)
    result = hud.observe(status)
    assert result.game_state is GameState.MISSION_ACTIVE
    assert result.mission.mission_type is MissionType.VIP_WORK
    assert result.mission.identity_status == result.activity_identity_status == "type_only"
    assert result.mission.mission_name == ""
    assert result.mission.heist_phase is MissionType.UNKNOWN
    assert result.mission.outcome is result.mission.outcome_scope is None
    assert result.activity_type is ActivityType.VIP_WORK
    assert result.activity_name == "VIP Work"
    assert result.vip_status_text == status and result.vip_status_evidence == "VIP WORK"
    assert result.timer is result.money is None
    assert app._data.current_mission == "VIP Work"
    assert not app._data.mission_objectives.entries
    assert completions == []
    assert_no_footer_side_effects(app)


def test_spaced_joined_capture_and_application_are_equivalent_except_raw_footer(paired_huds):
    results = observe_pair(paired_huds)
    assert results[0].vip_status_text == SPACED
    assert results[1].vip_status_text == JOINED
    assert results[0].game_state is GameState.MISSION_ACTIVE
    for hud in paired_huds:
        assert hud.batches == [BATCH]
        assert len(hud.grabs) == 1 and len(hud.crops[0]) == 9
        assert [region for region, _ in hud.ocr_calls] == [
            REGIONS.mission_text, REGIONS.center_prompt, REGIONS.mission_banner,
            REGIONS.bottom_objective, VIP, REGIONS.result_header,
            REGIONS.money_display, REGIONS.timer_bottom_right,
        ]
        assert [options for region, options in hud.ocr_calls if region == VIP] == [
            {"threshold": False, "invert": True, "scale": 2.0},
        ]


@pytest.mark.parametrize("extra", UNTRUSTED)
def test_adjacent_joined_footer_rows_cannot_gain_identity_result_or_financial_authority(
        paired_huds, extra):
    result = observe_pair(paired_huds, extra="\n" + extra)[1]
    assert result.game_state is GameState.MISSION_ACTIVE
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_name == ""
    assert result.activity_name == "VIP Work"
    assert result.mission.outcome is result.mission.outcome_scope is None
    for hud in paired_huds:
        assert_no_footer_side_effects(hud.app)
        assert not hud.app._data.mission_objectives.entries


@pytest.mark.parametrize("status", (
    "VIPWORKEND", "VIPWORKEND11:324", "VIP WORKEND 11:324", "VIPWORK END 11:324",
    "VIPWORKEND\n11:324", "VIPWORKEND\u00a011:324", "V1PWORKEND 11:324",
    "VIPWORKEND $7000", "VIPWORKEND 0:00 MISSION PASSED", "VIPWORKEND １２:３４",
))
def test_rejected_joined_lookalike_and_unrelated_footer_cannot_start_any_episode(app, hud, status):
    result = hud.observe(status + "\nSightseer\nCollect a new package\n$7000")
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.vip_status_evidence == ""
    assert_not_started(app)
    assert_no_footer_side_effects(app)


@pytest.mark.parametrize("extra", UNTRUSTED)
def test_joined_end_and_untrusted_rows_preserve_existing_owner_money_and_records(
        app, hud, clock, extra):
    hud.observe(SPACED, money="$1000")
    initial = owner_snapshot(app)
    stored = persisted(app)
    clock.value += timedelta(seconds=55)
    for suffix in ("11:324", "0:00"):
        result = hud.observe("VIPWORKEND " + suffix + "\n" + extra)
        assert result.game_state is GameState.MISSION_ACTIVE
        assert result.mission.outcome is result.mission.outcome_scope is None
        assert result.mission.mission_name == ""
        assert owner_snapshot(app) == initial
        assert app.current_money == app._data.mission_start_money == 1000
        assert result.money is result.timer is None
        assert persisted(app) == stored
    assert app._activity_tracker.completed_activities == []


@pytest.mark.parametrize("primary", (
    {"top": "Cayo Perico"}, {"bottom": "Escape Cayo Perico"},
    {"banner": "Security Contract"}, {"top": "Headhunter", "banner": "Sightseer"},
))
def test_joined_status_preserves_cross_family_and_primary_ambiguity(paired_huds, primary):
    observed = observe_pair(paired_huds, **primary)
    assert observed[1].game_state is GameState.UNKNOWN
    assert observed[1].mission.identity_status == "ambiguous"
    for hud in paired_huds:
        assert_not_started(hud.app)
        assert_no_footer_side_effects(hud.app)


def test_joined_status_cannot_displace_business_priority_or_business_values(paired_huds):
    results = observe_pair(
        paired_huds, top="Bunker Stock Supplies",
        business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"),
    )
    assert results[1].game_state is GameState.BUSINESS_COMPUTER
    for hud in paired_huds:
        business = hud.app.get_business_state("bunker")
        assert (business["stock"], business["supply"], business["value"]) == (40, 60, 7000)
        assert VIP not in [region for region, _ in hud.ocr_calls]
        assert hud.app._activity_tracker.current_activity is None
        assert persisted(hud.app)["activities"] == persisted(hud.app)["earnings"] == []


@pytest.mark.parametrize("primary,state", (
    ({"banner": "MISSION PASSED"}, GameState.MISSION_COMPLETE),
    ({"banner": "MISSION FAILED"}, GameState.MISSION_FAILED),
    ({"banner": "HEIST PASSED"}, GameState.UNKNOWN),
    ({"header": "HEIST PASSED"}, GameState.UNKNOWN),
    ({"header": "HEIST PASSED\nCayo Perico"}, GameState.UNKNOWN),
))
def test_primary_results_cannot_borrow_joined_footer_to_invent_an_episode(paired_huds, primary, state):
    results = observe_pair(paired_huds, **primary)
    assert results[1].game_state is state
    for hud in paired_huds:
        assert_not_started(hud.app)
        assert_no_footer_side_effects(hud.app)


@pytest.mark.parametrize("conflict", (
    {"banner": "Cayo Perico"}, {"top": "Headhunter", "banner": "Sightseer"},
    {"banner": "MISSION PASSED\nSightseer"},
    {"banner": "MISSION FAILED\nSightseer"}, {"header": "HEIST PASSED\nCayo Perico"},
))
def test_joined_status_cannot_replace_or_complete_existing_named_owner(app, hud, clock, conflict):
    hud.observe(SPACED, banner="Headhunter", center="Eliminate the targets", money="$1000")
    before = owner_snapshot(app)
    stored = persisted(app)
    callbacks = []
    app.on_mission_complete(callbacks.append)
    clock.value += timedelta(seconds=30)
    observed = hud.observe(JOINED, **conflict)
    assert observed.game_state is GameState.UNKNOWN and observed.state_confidence == 0
    assert owner_snapshot(app) == before
    assert persisted(app) == stored and callbacks == []


@pytest.mark.parametrize("name,family", (
    ("Cayo Perico", MissionType.CAYO_PERICO),
    ("Security Contract", MissionType.SECURITY_CONTRACT),
))
def test_joined_category_cannot_take_ownership_from_an_existing_other_family(
        paired_huds, clock, name, family):
    for hud in paired_huds:
        hud.observe(banner=name, center="Go to the location", money="$1000")
    owners = [owner_snapshot(hud.app) for hud in paired_huds]
    assert all(owner[0] is not None for owner in owners)
    assert all(owner[5] is family for owner in owners)
    clock.value += timedelta(seconds=20)
    result = observe_pair(paired_huds, suffix="0:00", extra="\nSightseer\n$7000")[1]
    assert result.mission.mission_type is MissionType.VIP_WORK
    assert result.activity_name == owners[1][4]
    for hud, owner in zip(paired_huds, owners):
        assert owner_snapshot(hud.app) == owner
        assert persisted(hud.app)["activities"] == persisted(hud.app)["earnings"] == []


@pytest.mark.parametrize("result_name", ("", "Headhunter"))
@pytest.mark.parametrize("label,success", (("MISSION PASSED", True), ("MISSION FAILED", False)))
def test_joined_episode_completion_replay_and_authorized_rearm_match_spaced_history(
        paired_huds, clock, result_name, label, success):
    callbacks = ([], [])
    for hud, callback in zip(paired_huds, callbacks):
        hud.app.on_mission_complete(callback.append)
    observe_pair(paired_huds, money="$1000")
    owners = [owner_snapshot(hud.app) for hud in paired_huds]
    clock.value += timedelta(seconds=45)
    results = observe_pair(paired_huds, suffix="0:00", banner=label + "\n" + result_name,
                           money="$1250")
    assert results[1].game_state is (GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED)
    terminals = [hud.app._data.terminal_mission_episode for hud in paired_huds]
    for hud, callback, owner in zip(paired_huds, callbacks, owners):
        row, = persisted(hud.app)["activities"]
        assert (row["type"], row["name"], row["success"], row["duration_seconds"], row["earnings"]) == (
            "VIP_WORK", result_name or "VIP Work", success, 45, 250 if success else 0,
        )
        assert hud.app._activity_tracker.completed_activities == [owner[0]]
        assert callback == ([owner[0]] if success else [])
    for suffix, extra in (("0:00", ""), ("11:323", ""), *(('0:00', '\n' + row) for row in UNTRUSTED)):
        replay = observe_pair(paired_huds, suffix=suffix, extra=extra)
        assert replay[1].game_state is GameState.UNKNOWN
        for hud, terminal in zip(paired_huds, terminals):
            assert hud.app._data.terminal_mission_episode == terminal
            assert hud.app._activity_tracker.current_activity is None
            assert hud.app._data.mission_start_time is hud.app._data.mission_start_money is None
            assert len(persisted(hud.app)["activities"]) == 1
            assert [row["amount"] for row in persisted(hud.app)["earnings"]] == [250]
    # Losing the row and observing it again is not an episode boundary.
    for hud in paired_huds:
        hud.observe("")
    assert observe_pair(paired_huds)[1].game_state is GameState.UNKNOWN
    observe_pair(paired_huds, banner=label + "\n" + result_name, money="$1250")
    for hud in paired_huds:
        assert len(persisted(hud.app)["activities"]) == 1
        assert hud.app.session_stats.activities_completed == 1
    # A new independent objective, unlike an identical footer row, can rearm.
    clock.value += timedelta(seconds=10)
    restarted = observe_pair(paired_huds, banner=result_name, center="Collect the new package")
    assert restarted[1].game_state is GameState.MISSION_ACTIVE
    for hud, old in zip(paired_huds, owners):
        assert hud.app._activity_tracker.current_activity is not old[0]
        assert hud.app._data.terminal_mission_episode is None
        assert hud.app._data.mission_start_money == 1250
        assert hud.app._data.mission_objectives.entries == {"collect the new package"}
        assert len(persisted(hud.app)["activities"]) == 1


def test_joined_category_refines_only_from_independent_title_and_keeps_baseline(app, hud, clock):
    hud.observe(JOINED, money="$1000")
    initial = owner_snapshot(app)
    clock.value += timedelta(seconds=25)
    result = hud.observe("VIPWORKEND 0:00", banner="Sightseer", center="Collect the packages")
    assert result.activity_name == result.mission.mission_name == "Sightseer"
    assert result.activity_identity_status == "known_name"
    assert owner_snapshot(app)[:4] == initial[:4]
    assert app._data.mission_objectives.entries == {"collect the packages"}
    refined = owner_snapshot(app)
    hud.observe(JOINED + "\nHeadhunter\nMISSION PASSED\n$7000")
    assert owner_snapshot(app) == refined
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


def test_joined_footer_preserves_ordinary_money_gains_spending_missing_reads_and_duplicates(paired_huds):
    observe_pair(paired_huds, extra="\nTAKE $999999", money="$1000", timer="3:21")
    owners = [owner_snapshot(hud.app) for hud in paired_huds]
    for money, change, balance, earnings in (
        ("", 0, 1000, []), ("$1250", 250, 1250, [250]),
        ("$1250", 0, 1250, [250]), ("$900", -350, 900, [250]),
        ("", 0, 900, [250]), ("$950", 50, 950, [250, 50]),
        ("$950", 0, 950, [250, 50]),
    ):
        result = observe_pair(paired_huds, suffix="0:00", extra="\n$123456", money=money,
                              timer="0:00")[1]
        assert result.money_change == change and result.timer.total_seconds == 0
        assert result.game_state is GameState.MISSION_ACTIVE
        for hud, owner in zip(paired_huds, owners):
            assert owner_snapshot(hud.app) == owner
            assert hud.app.current_money == balance
            assert hud.app.session_earnings == hud.app.session_stats.total_earnings == sum(earnings)
            assert [row["amount"] for row in persisted(hud.app)["earnings"]] == earnings
            assert persisted(hud.app)["activities"] == []


def test_joined_terminal_replay_does_not_hide_independent_money_or_duplicate_earnings(app, hud):
    assert hud.observe(JOINED, money="$1000").game_state is GameState.MISSION_ACTIVE
    hud.observe(banner="MISSION PASSED", money="$1250")
    terminal = app._data.terminal_mission_episode
    stored_activities = persisted(app)["activities"]
    assert len(stored_activities) == 1
    assert stored_activities[0]["name"] == "VIP Work"
    assert stored_activities[0]["earnings"] == 250
    for expected_change in (50, 0):
        result = hud.observe("VIPWORKEND 0:00\nCollect a new package\n$999999", money="$1300")
        assert result.game_state is GameState.UNKNOWN
        assert result.money_change == expected_change and app.current_money == 1300
        assert app._data.terminal_mission_episode == terminal
        assert app._activity_tracker.current_activity is None
        assert persisted(app)["activities"] == stored_activities
        assert [row["amount"] for row in persisted(app)["earnings"]] == [250, 50]


def test_joined_completion_listener_replay_is_fenced_but_new_named_owner_survives(app, hud, clock):
    hud.observe(JOINED, money="$1000")
    first = app._activity_tracker.current_activity
    callbacks, replay, next_owners = [], [], []

    def start_next(activity):
        callbacks.append(activity)
        replay.append(hud.observe("VIPWORKEND 0:00\nCollect a new package"))
        hud.observe(JOINED, banner="Sightseer", center="Collect the packages")
        next_owners.append(owner_snapshot(app))

    app.on_mission_complete(start_next)
    clock.value += timedelta(seconds=30)
    hud.observe(JOINED, banner="MISSION PASSED\nHeadhunter", money="$1250")
    assert callbacks == app._activity_tracker.completed_activities == [first]
    assert replay[0].game_state is GameState.UNKNOWN
    assert owner_snapshot(app) == next_owners[0]
    assert next_owners[0][0] is not first
    assert app._data.current_mission == "Sightseer"
    assert app._data.mission_start_money == 1250
    row, = persisted(app)["activities"]
    assert (row["name"], row["duration_seconds"], row["earnings"]) == ("Headhunter", 30, 250)


def test_joined_capture_renders_only_category_in_native_qt(app, hud, native_qt_application):
    if os.environ.get("GTA_RUN_QT_TESTS") != "1" or native_qt_application is None:
        pytest.skip("set GTA_RUN_QT_TESTS=1 for native offscreen display coverage")
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    from src.ui.overlay import OverlayWindow
    from src.ui.widgets.dashboard import DashboardWidget

    app._state = AppState.RUNNING
    app._last_capture_result = hud.observe(JOINED + "\nSightseer\nCollect a package\n$7000")
    dashboard, overlay = DashboardWidget(app), OverlayWindow(app)
    dashboard._timer.stop()
    overlay._update_timer.stop()
    try:
        dashboard._update_display()
        overlay._update_ui()
        native_qt_application.processEvents()
        assert dashboard._activity_card._activity_label.text() == "VIP Work"
        assert dashboard._activity_card._objective_label.text() == ""
        assert overlay._activity_label.text() == "VIP Work"
        assert overlay._timer_label.text() == ""
        assert overlay._state_badge.text() == "MISSION ACTIVE"
        assert_no_footer_side_effects(app)
    finally:
        for widget in (overlay, dashboard):
            if not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        native_qt_application.processEvents()
