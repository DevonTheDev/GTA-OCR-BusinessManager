"""Header evidence propagation through capture models and direct app callers."""

from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult, GTABusinessManager
from src.capture.regions import ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


HEADER = "HEIST PASSED\nThe Cayo Perico Heist"


def header_result(state=GameState.MISSION_COMPLETE, *, raw=HEADER, admitted=HEADER, top=""):
    result = StateDetectionResult(state, .85, "independent result", mission_text=top)
    result.result_header_text, result.result_header_evidence = raw, admitted
    return result


def test_capture_models_declare_optional_source_defaults():
    for result in (CaptureResult(), StateDetectionResult(GameState.UNKNOWN, 0.0, "empty")):
        assert getattr(result, "result_header_text", None) == ""
        assert getattr(result, "result_header_evidence", None) == ""


def test_direct_fallback_uses_accepted_header_and_ignores_unadmitted_raw(app):
    result = header_result(raw="Headhunter\nMISSION FAILED\n$5000")
    reading = app._mission_reading(result)
    assert reading.mission_type == MissionType.CAYO_PERICO
    assert reading.outcome == "complete" and reading.outcome_scope == "heist"
    assert reading.raw_text == HEADER
    result.result_header_evidence = ""
    reading = app._mission_reading(result)
    assert reading.identity_status == "unknown" and reading.outcome is None


def test_direct_terminal_fallback_checks_active_owner_and_copies_both_fields(app):
    app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .8, "start", mission_text="Headhunter"),
                       CaptureResult())
    current = app._activity_tracker.current_activity
    started = current.started_at
    result, capture = header_result(), CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0.0
    assert capture.result_header_text == capture.result_header_evidence == HEADER
    assert capture.mission.mission_type == MissionType.CAYO_PERICO
    assert app._activity_tracker.current_activity is current and current.started_at == started
    assert app._repository.export_session_data(app._data.db_session_id)["activities"] == []


def test_direct_accepted_callback_propagates_header_and_fences_without_start(app):
    result, capture = header_result(), CaptureResult()
    app._process_state(result, capture)
    assert getattr(capture, "result_header_text", None) == HEADER
    assert capture.result_header_evidence == HEADER
    assert capture.mission.outcome_scope == "heist" and capture.mission.outcome == "complete"
    assert app._data.terminal_mission_episode.identity.family == MissionType.CAYO_PERICO
    assert app._activity_tracker.current_activity is None
    assert app.session_stats.activities_completed == 0


def test_direct_active_callback_cannot_turn_admitted_result_into_start(app):
    app._process_state(header_result(GameState.MISSION_ACTIVE), CaptureResult())
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None


def test_raw_and_admitted_header_commands_never_become_objective_evidence():
    result = header_result(raw="Go to the location", admitted=HEADER + "\nEscape Cayo Perico")
    assert not GTABusinessManager._objective_evidence(result).entries
    result.objective_text = "Collect the briefcase"
    assert GTABusinessManager._objective_evidence(result).entries == {"collect the briefcase"}


def test_capture_accepts_missing_optional_header_image_and_propagates_evidence(app):
    regions = ScreenRegions()
    observed_kwargs, batches = [], []
    detected = header_result()
    screen = np.full((120, 200, 3), 70, dtype=np.uint8)

    def capture(requested):
        batches.append(requested)
        return {i: screen if region == regions.full_screen else None for i, region in enumerate(requested)}

    def detect(_screen, **kwargs):
        observed_kwargs.append(kwargs)
        return detected

    app._capture = SimpleNamespace(regions=regions, capture_multiple_regions=capture)
    app._state_detector = SimpleNamespace(detect=detect)
    app._ocr = SimpleNamespace(is_available=False)
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()
    result = app._do_capture_cycle()
    assert "result_header_image" in observed_kwargs[0] and observed_kwargs[0]["result_header_image"] is None
    assert "vip_status_image" in observed_kwargs[0] and observed_kwargs[0]["vip_status_image"] is None
    assert batches == [[regions.full_screen, regions.money_display, regions.mission_text,
                        regions.center_prompt, regions.timer_bottom_right, regions.mission_banner,
                        regions.bottom_objective, regions.result_header, regions.vip_status]]
    assert len(batches[0]) == 9
    assert batches[0][7] == regions.result_header
    assert batches[0][8] == regions.vip_status
    assert result.result_header_text == result.result_header_evidence == HEADER
    assert app._activity_tracker.current_activity is None
    assert result.money is None and result.money_change == 0


@pytest.mark.parametrize("evidence", ["HEIST PASSED", "HEIST PASSED\nCayo", "Cayo Perico",
    "HEIST PASSED\nCayo Perico Prep", "HEIST PASSED\nHeist Finale"])
@pytest.mark.parametrize("state", [GameState.MISSION_COMPLETE, GameState.MISSION_ACTIVE])
def test_direct_header_admission_cannot_borrow_primary_family(app, evidence, state):
    result, capture = header_result(state, raw=evidence, admitted=evidence, top="Cayo Perico"), CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0.0
    assert app._activity_tracker.current_activity is None
    assert app._data.terminal_mission_episode is None
    assert capture.result_header_text == evidence
    assert capture.result_header_evidence == ""
