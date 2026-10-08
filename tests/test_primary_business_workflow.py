"""Primary-business source ownership through the actual application pipeline.

Only native capture pixels, backend OCR text and configured template matches are
synthetic. Production capture geometry, detector/parser, both app guards, worker,
selection, callbacks and disposable SQLite remain in use. These checks establish
logical admission boundaries, not real-image or native Windows gameplay accuracy.
"""

import dataclasses
import json
import os
from types import SimpleNamespace

import pytest

from src.detection.parsers.mission_parser import MissionType
from src.detection.template_matcher import MatchResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_detection_sample_workflows import (
    REGIONS, assert_source_truth, clock as clock, runs as runs, semantic,
)
from tests.test_terminal_mission_identity_ownership import mission_stats


BUSINESS = "Bunker Stock 40% Supplies 60% Value $7000"
BOTTOM = "Escape Cayo Perico."
HEADER = "HEIST PASSED\nThe Cayo Perico Heist"
PRIMARY = {
    "top": REGIONS.mission_text,
    "center": REGIONS.center_prompt,
    "banner": REGIONS.mission_banner,
}
SUPPLEMENTAL = (REGIONS.bottom_objective, REGIONS.result_header, REGIONS.vip_status)
LOGICAL_KEYS = (
    "data", "session", "persisted", "state", "detector_context", "money_parser",
    "current", "current_identity", "completed", "cooldowns", "events", "results",
    "rate_calls", "cycle_error",
)


def configure_generic_template(run):
    """Supply the supported matcher contract, never the combined detector result."""
    calls = []

    def match_any(image, names):
        calls.append((semantic(image), tuple(names)))
        if "mission_banner" in names:
            return MatchResult(True, .99, (0, 0), "mission_banner")
        return None

    run.manager._state_detector._templates = SimpleNamespace(match_any=match_any)
    return calls


def prepare(run, owner, *, absent=False):
    if owner == "cayo":
        run.frame(banner="Cayo Perico")
        assert run.manager._activity_tracker.current_activity.activity_type is ActivityType.CAYO_PERICO
    if absent:
        # ScreenCapture's supported None entries retain the shared native grab
        # while providing no supplemental image to the detector.
        run.hud.capture._regions = dataclasses.replace(
            run.hud.capture.regions, bottom_objective=None, result_header=None, vip_status=None,
        )
    return configure_generic_template(run)


def capture_primary(run, primary, sources, *, armed, owner):
    manager, hud = run.manager, run.hud
    before_owner = manager._activity_tracker.current_activity
    before_start = manager._data.mission_start_time
    before_money = manager._data.mission_start_money
    before_grabs, before_detects = len(hud.grabs), len(hud.detector_calls)
    guard_calls = []
    accounting_calls = []
    original_guard = manager._guard_mission_observation
    original_cooldown = manager._start_activity_cooldown
    original_persist = manager._persist_activity

    def guard(candidate):
        result = original_guard(candidate)
        guard_calls.append((candidate, result))
        return result

    def cooldown(activity):
        accounting_calls.append(("cooldown", semantic(activity)))
        return original_cooldown(activity)

    def persist(*args, **kwargs):
        accounting_calls.append(("persist", semantic(args), semantic(kwargs)))
        return original_persist(*args, **kwargs)

    manager._guard_mission_observation = guard
    manager._start_activity_cooldown = cooldown
    manager._persist_activity = persist
    kwargs = {primary: BUSINESS}
    kwargs.update(bottom=BOTTOM if sources in {"bottom", "both"} else "",
                  header=HEADER if sources in {"header", "both"} else "",
                  vip_status="VIP WORK END 12:34")
    request = run.frame(armed=armed, **kwargs)
    assert not manager._stop_event.error
    assert len(hud.grabs) == before_grabs + 1
    assert len(hud.detector_calls) == before_detects + 1
    assert hud.region_calls == []
    assert len(guard_calls) == 2
    # Both guards run actual logic and retain the original admitted candidate.
    assert all(candidate is result for candidate, result in guard_calls)
    result = run.results[-1]
    snapshot = run.snapshot()
    logical = {key: snapshot[key] for key in LOGICAL_KEYS}
    sample = None
    if armed:
        status = manager.get_detection_sample_status()
        assert status.state == "ready", status
        assert status.token is request[0].token
        frozen = manager.claim_detection_sample_for_save(request[0].token)
        assert frozen is not None
        sample = json.loads(frozen.json_bytes)
    return {
        "run": run, "result": result, "logical": logical, "sample": sample,
        "before_owner": before_owner, "before_start": before_start,
        "before_money": before_money, "guards": guard_calls, "owner": owner,
        "ocr": hud.ocr_calls[run.last_ocr_start:], "accounting_calls": accounting_calls,
    }


def assert_no_supplemental_effect(observed):
    run, result = observed["run"], observed["result"]
    manager = run.manager
    candidate = run.hud.detections[-1]
    assert candidate.state is result.game_state is GameState.MISSION_ACTIVE
    assert candidate.confidence == result.state_confidence == .99
    assert candidate.reason == result.state_reason == "Matched template: mission_banner"
    assert result.mission.identity_status == "unknown"
    assert result.mission.mission_type is MissionType.UNKNOWN
    assert result.mission.mission_name == ""
    assert result.mission.outcome is None and result.mission.outcome_scope is None
    assert result.bottom_objective_text == result.bottom_objective_command == ""
    assert result.result_header_text == result.result_header_evidence == ""
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert manager._data.terminal_mission_episode is None
    assert not manager._data.mission_objectives.entries
    current = manager._activity_tracker.current_activity
    assert current is not None  # The generic-template unresolved start remains valid.
    if observed["owner"] == "fresh":
        assert current.activity_type is ActivityType.UNKNOWN
        assert manager._data.mission_identity_status == "unknown"
        assert manager._data.mission_identity_type is MissionType.UNKNOWN
    else:
        assert current is observed["before_owner"]
        assert current.activity_type is ActivityType.CAYO_PERICO
        assert manager._data.mission_start_time == observed["before_start"]
        assert manager._data.mission_start_money == observed["before_money"]
        assert manager._data.mission_identity_status == "type_only"
    assert manager._activity_tracker.completed_activities == []
    assert observed["accounting_calls"] == []
    assert manager.cooldown_tracker.get_active_cooldowns() == []
    assert mission_stats(manager) == (0, 0, 0, 0)
    assert manager.session_earnings == manager.session_stats.total_earnings == 0
    saved = manager._repository.export_session_data(manager._data.db_session_id)
    assert saved["activities"] == saved["earnings"] == []
    assert not any(event[0] == "complete" for event in run.events)
    assert [entry[0] for entry in observed["ocr"]] == [
        REGIONS.mission_text, REGIONS.center_prompt, REGIONS.mission_banner,
        REGIONS.money_display, REGIONS.timer_bottom_right,
    ]
    assert not any(entry[0] in SUPPLEMENTAL for entry in observed["ocr"])
    sample = observed["sample"]
    if sample is not None:
        assert_source_truth(sample, run)
        for source in ("bottom", "header", "vip_status"):
            record = sample["sources"][source]
            assert record["region_present"] is True and record["image_present"] is True
            assert record["ocr_status"] == "not_requested_by_detector"
            assert record["raw_text"] is record["confidence"] is record["preprocessing"] is None
        assert sample["first_guard_changed"] is False
        assert sample["detector_candidate"] == sample["first_guarded"]
        assert sample["processed"]["state"] == "MISSION_ACTIVE"
        assert sample["processed"]["identity"]["status"] == "unknown"
        selection = sample["selection"]
        assert selection["ownership"] == ("new" if observed["owner"] == "fresh" else "retained")
        if observed["owner"] == "cayo":
            assert selection["before"]["activity"] == selection["after"]["activity"]


@pytest.mark.parametrize("primary", PRIMARY)
@pytest.mark.parametrize("sources", ("bottom", "header", "both"))
@pytest.mark.parametrize("owner", ("fresh", "cayo"))
@pytest.mark.parametrize("armed", (False, True), ids=("ordinary", "sample-armed"))
def test_primary_business_matches_absent_supplements_through_selection_and_accounting(
        runs, primary, sources, owner, armed):
    control, observed = runs(), runs()
    control_matches = prepare(control, owner, absent=True)
    observed_matches = prepare(observed, owner)
    expected = capture_primary(control, primary, sources, armed=armed, owner=owner)
    actual = capture_primary(observed, primary, sources, armed=armed, owner=owner)
    assert_no_supplemental_effect(actual)
    assert actual["logical"] == expected["logical"]
    assert semantic(actual["ocr"]) == semantic(expected["ocr"])
    assert observed_matches == control_matches
    assert len(observed_matches) == 1
    # Exact primary OCR observations reach the real parser regardless of crop.
    assert getattr(actual["result"], {"top": "mission_text", "center": "objective_text",
                                     "banner": "banner_text"}[primary]) == BUSINESS


@pytest.mark.parametrize("primary", PRIMARY)
@pytest.mark.parametrize("owner", ("fresh", "cayo"))
def test_sample_arming_preserves_business_cycle_calls_selection_and_accounting(runs, primary, owner):
    cycles = []
    matches = []
    for armed in (False, True):
        run = runs()
        matches.append(prepare(run, owner))
        cycle = capture_primary(run, primary, "both", armed=armed, owner=owner)
        assert_no_supplemental_effect(cycle)
        cycles.append(cycle)
    assert cycles[0]["logical"] == cycles[1]["logical"]
    assert semantic(cycles[0]["ocr"]) == semantic(cycles[1]["ocr"])
    assert matches[0] == matches[1]


@pytest.mark.parametrize("sources", ("bottom", "header", "both"))
@pytest.mark.parametrize("owner", ("fresh", "cayo"))
@pytest.mark.parametrize("armed", (False, True), ids=("ordinary", "sample-armed"))
def test_no_business_keeps_legitimate_bottom_and_header_admission(runs, sources, owner, armed):
    run = runs()
    matches = prepare(run, owner)
    before_grabs = len(run.hud.grabs)
    current = run.manager._activity_tracker.current_activity
    request = run.frame(armed=armed, bottom=BOTTOM if sources in {"bottom", "both"} else "",
                        header=HEADER if sources in {"header", "both"} else "")
    assert len(run.hud.grabs) == before_grabs + 1 and len(matches) == 1
    assert not run.manager._stop_event.error
    result = run.results[-1]
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_type is MissionType.CAYO_PERICO
    assert run.hud.region_calls == []
    saved = run.manager._repository.export_session_data(run.manager._data.db_session_id)
    if sources == "bottom":
        assert result.game_state is GameState.MISSION_ACTIVE
        assert result.bottom_objective_command == BOTTOM.rstrip(".")
        assert run.manager._activity_tracker.current_activity.activity_type is ActivityType.CAYO_PERICO
        assert saved["activities"] == []
    else:
        assert result.game_state is GameState.MISSION_COMPLETE
        assert result.result_header_evidence == HEADER
        assert run.manager._activity_tracker.current_activity is None
        if owner == "cayo":
            assert run.manager._activity_tracker.completed_activities == [current]
            assert len(saved["activities"]) == 1
            assert mission_stats(run.manager) == (1, 1, 0, 0)
            # Cayo's baseline app mapping has no active cooldown entry.
            assert run.manager.cooldown_tracker.get_active_cooldowns() == []
            assert sum(event[0] == "complete" for event in run.events) == 1
        else:
            assert saved["activities"] == []
            assert mission_stats(run.manager) == (0, 0, 0, 0)
    assert saved["earnings"] == []
    if armed:
        sample = run.manager.claim_detection_sample_for_save(request[0].token)
        assert sample is not None
        document = json.loads(sample.json_bytes)
        assert_source_truth(document, run)
        assert document["processed"]["state"] == result.game_state.name


@pytest.mark.parametrize("armed", (False, True), ids=("ordinary", "sample-armed"))
def test_generic_template_ambiguous_start_contract_stays_unresolved(runs, armed):
    run = runs()
    matches = configure_generic_template(run)
    run.frame(armed=armed, top="Headhunter Sightseer", bottom=BOTTOM, header=HEADER)
    result = run.results[-1]
    assert result.game_state is GameState.MISSION_ACTIVE and result.state_confidence == .99
    assert result.mission.identity_status == "ambiguous"
    assert run.manager._data.mission_identity_status == "ambiguous"
    assert run.manager._activity_tracker.current_activity is not None
    assert run.manager._data.terminal_mission_episode is None
    assert result.bottom_objective_command == result.result_header_evidence == ""
    assert len(matches) == 1


@pytest.mark.parametrize("owner", ("fresh", "cayo"))
@pytest.mark.parametrize("armed", (False, True), ids=("ordinary", "sample-armed"))
def test_native_qt_displays_the_same_owner_for_business_with_and_without_supplements(
        runs, monkeypatch, native_qt_application, owner, armed):
    if os.environ.get("GTA_RUN_QT_TESTS") != "1":
        pytest.skip("set GTA_RUN_QT_TESTS=1 for offscreen primary-business display checks")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from src.config import settings as settings_module
    from src.ui.main_window import MainWindow

    labels = []
    for absent in (True, False):
        run = runs()
        prepare(run, owner, absent=absent)
        monkeypatch.setattr(settings_module, "_settings", run.manager._settings)
        window = MainWindow(run.manager)
        window.resize(900, 650)
        window.show()
        try:
            actual = capture_primary(run, "banner", "both", armed=armed, owner=owner)
            if not absent:
                assert_no_supplemental_effect(actual)
            window._update_ui()
            window._dashboard._update_display()
            native_qt_application.processEvents()
            label = window._dashboard._activity_card._activity_label
            assert label.textFormat() == Qt.TextFormat.PlainText
            assert not window.findChildren(QtWidgets.QDialog)
            labels.append(label.text())
            if armed:
                status = window.findChild(QtWidgets.QLabel, "detection_sample_status")
                assert status is not None and run.manager.get_detection_sample_status().sample_id in status.text()
        finally:
            for child in window.findChildren(QtWidgets.QWidget):
                if hasattr(child, "_timer"):
                    child._timer.stop()
            window._update_timer.stop()
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            native_qt_application.processEvents()
    assert labels[0] == labels[1] == ("Mission Active" if owner == "fresh" else "Cayo Perico")
