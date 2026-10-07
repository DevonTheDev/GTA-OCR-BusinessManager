"""Strict VIP footer admission with synthetic OCR, never gameplay accuracy."""

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult, GTABusinessManager
from src.capture.regions import Region, RegionType, ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app


MARKER = "VIP WORK"
ACTUAL_HEADHUNTER = "TARGETS REMAINING 1\nvip woRK END 1273.\n<_< i ~"
ACTUAL_SIGHTSEER = "PACKAGES REMAINING 1\ne\nVIPWORKEND 11:324"


def detect(raw, *, top="", center="", banner="", bottom=None, header=None, available=True,
           template=None):
    calls, updates = [], []
    crops = {name: object() for name in ("top", "center", "banner", "bottom", "header", "status")}
    texts = dict(zip(crops, (top, center, banner, bottom, header, raw)))

    def recognize(image, **kwargs):
        name = next(key for key, crop in crops.items() if crop is image)
        calls.append((name, kwargs))
        return SimpleNamespace(text=texts[name])

    detector = StateDetector(
        ocr_engine=SimpleNamespace(is_available=available, recognize_preprocessed=recognize),
        template_matcher=SimpleNamespace(match_any=lambda _image, names: (
            template if template is not None and template.template_name in names else None)),
    )
    update = detector._update_context

    def record_update(result):
        updates.append(result)
        update(result)

    detector._update_context = record_update
    assert "vip_status_image" in inspect.signature(detector.detect).parameters
    result = detector.detect(
        np.full((120, 200, 3), 70, dtype=np.uint8),
        mission_text_image=crops["top"], center_text_image=crops["center"],
        mission_banner_image=crops["banner"],
        bottom_objective_image=crops["bottom"] if bottom is not None else None,
        result_header_image=crops["header"] if header is not None else None,
        vip_status_image=crops["status"] if raw is not None else None,
    )
    assert updates == [result]
    assert detector.context.last_state == result.state
    return result, calls


def test_optional_status_provenance_and_fixed_region_are_declared():
    for result in (CaptureResult(), StateDetectionResult(GameState.UNKNOWN, 0.0, "empty")):
        assert getattr(result, "vip_status_text", None) == ""
        assert getattr(result, "vip_status_evidence", None) == ""
    regions = ScreenRegions()
    assert getattr(regions, "vip_status", None) == Region(.82, .88, .17, .12)
    assert regions.get_region(RegionType.VIP_STATUS) == regions.vip_status
    assert regions.get_all_hud_regions()["vip_status"] == regions.vip_status
    assert regions.timer_bottom_right == Region(.85, .90, .14, .06)


@pytest.mark.parametrize("raw", [
    "VIP WORK END", "vip work end", " \tVIP\tWORK  END\t ",
    "VIP WORK END 12:30", "VIP WORK END 0:00", "VIP WORK END 1273.",
    "VIP WORK END 11:324", "VIP WORK END 1234567890123456", ACTUAL_HEADHUNTER,
    "TARGETS REMAINING 1\r\nVIP WORK END 12:30\r\n",
    "\n".join(["x" * 128] * 3 + ["x" * 112, "VIP WORK END"]),
    "\n".join(["noise"] * 7 + ["VIP WORK END"]),
])
def test_complete_status_row_admits_only_fixed_category(raw):
    result, calls = detect(raw)
    assert result.state == GameState.MISSION_ACTIVE and result.confidence > .6
    assert result.vip_status_text == raw and result.vip_status_evidence == MARKER
    reading = result.mission
    assert reading.mission_type == MissionType.VIP_WORK and reading.identity_status == "type_only"
    assert reading.mission_name == reading.objective == ""
    assert reading.heist_phase == MissionType.UNKNOWN
    assert reading.outcome is reading.outcome_scope is None and reading.is_active
    assert reading.raw_text == MARKER
    assert not GTABusinessManager._objective_evidence(result).entries
    assert calls[-1] == ("status", {"threshold": False, "invert": True, "scale": 2.0})
    assert [name for name, _ in calls].count("status") == 1


@pytest.mark.parametrize("raw", [
    "", "END", "END 0:00", "0:00", "TARGETS REMAINING 1", "PACKAGES REMAINING 1",
    ACTUAL_SIGHTSEER, "VIPWORKEND", "V1P WORK END", "VIP W0RK END", "VIP WORK EN",
    "IP WORK END", "VIP WORK", "VIP WORK ENDING", "VIP WORK ENDS", "VIP WORK END abc",
    "VIP WORK END 12:30abc", "VIP WORK END $5000", "VIP WORK END +$5000",
    "VIP WORK END 5,000", "VIP WORK END 5000 dollars", "VIP WORK END Take 5000",
    "VIP WORK END 12:30 Collect the package", "VIP WORK END: 12:30", "VIP WORK END.",
    "The VIP WORK END label shows a countdown.", "Show VIP WORK END", "VIP WORK END label",
    "XVIP WORK END", "VIP WORK ENDX", "[VIP WORK END]", "VIP WORK END | MISSION PASSED",
    "VIP\nWORK END 12:30", "VIP WORK\nEND 12:30", "VIP\rWORK END", "VIP\vWORK END",
    "VİP WORK END", "VıP WORK END", "VIP\u00a0WORK END", "VIP WORK END １２:３０",
    "VIP WORK END 12345678901234567", "VIP WORK END " + "1" * 129,
    "\n".join(["noise"] * 8 + ["VIP WORK END"]), "x" * 129 + "\nVIP WORK END",
    "\n".join(["é" * 64] * 4 + ["VIP WORK END"]),
    "\n".join(["x" * 128] * 3 + ["x" * 112, "VIP WORK END"]) + "\n",
    "TAKE $1,396,354\nLOOT BAG\nTEAM LIVES 0", "Bunker Stock 40% Supplies 60% Value $7000",
    "Headhunter\nMISSION PASSED\nCollect the package\n$5000",
])
def test_malformed_unbounded_or_unrelated_status_has_no_authority(raw):
    result, calls = detect(raw)
    assert result.state not in (GameState.MISSION_ACTIVE, GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
    assert result.vip_status_text == raw and result.vip_status_evidence == ""
    assert result.mission is None or result.mission.identity_status == "unknown"
    assert not GTABusinessManager._objective_evidence(result).entries
    assert [name for name, _ in calls].count("status") == 1


def test_other_footer_lines_cannot_name_finish_or_supply_objectives():
    raw = "Headhunter\nMISSION PASSED\nCollect the package\nTAKE $5000\nVIP WORK END 0:00"
    result, _ = detect(raw)
    assert result.state == GameState.MISSION_ACTIVE
    assert result.mission.raw_text == MARKER and result.mission.mission_name == ""
    assert result.mission.objective == "" and result.mission.outcome is None
    assert not GTABusinessManager._objective_evidence(result).entries


@pytest.mark.parametrize("top,state", [
    ("Bunker Stock 40% Supplies 60% Value $7000", GameState.BUSINESS_COMPUTER),
    ("MISSION PASSED", GameState.MISSION_COMPLETE), ("MISSION FAILED", GameState.MISSION_FAILED),
    ("HEIST PASSED", GameState.UNKNOWN), ("Headhunter\nCayo Perico", GameState.UNKNOWN),
])
def test_primary_business_result_and_ambiguity_remain_protected(top, state):
    result, calls = detect(ACTUAL_HEADHUNTER, top=top)
    assert result.state == state
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert not any(name == "status" for name, _ in calls)
    assert result.mission.raw_text == top


def test_primary_business_evidence_cannot_supply_status_identity_despite_active_template():
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    result, calls = detect(ACTUAL_HEADHUNTER, top="Bunker Stock 40% Supplies 60% Value $7000",
                           template=template)
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert result.mission.identity_status == "unknown"
    assert not any(name == "status" for name, _ in calls)


@pytest.mark.parametrize("name", ["mission_passed", "mission_failed", "business_laptop"])
def test_primary_protected_template_skips_status_ocr(name):
    template = SimpleNamespace(matched=True, confidence=.99, template_name=name)
    result, calls = detect(ACTUAL_HEADHUNTER, template=template)
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert not any(name == "status" for name, _ in calls)


@pytest.mark.parametrize("source", ["top", "bottom"])
def test_status_conflicts_with_independent_family_conservatively(source):
    kwargs = {source: "Cayo Perico" if source == "top" else "Escape Cayo Perico"}
    result, _ = detect(ACTUAL_HEADHUNTER, **kwargs)
    assert result.state == GameState.UNKNOWN and result.confidence == 0.0
    assert result.mission.identity_status == "ambiguous"
    assert set(result.mission.candidates) == {"CAYO_PERICO", "VIP_WORK"}
    assert result.vip_status_evidence == MARKER


def test_status_never_stitches_split_label_across_sources():
    result, _ = detect("WORK END 12:30", top="VIP")
    assert result.vip_status_evidence == ""
    assert result.mission.identity_status == "unknown"


@pytest.mark.parametrize("header", ["HEIST PASSED", "HEIST PASSED\nThe Cayo Perico Heist"])
def test_header_final_veto_survives_status_identity(header):
    result, calls = detect(ACTUAL_HEADHUNTER, header=header)
    assert result.state == GameState.UNKNOWN and result.confidence == 0.0
    assert result.vip_status_evidence == MARKER and result.result_header_text == header
    assert result.result_header_evidence == ""
    assert [name for name, _ in calls][-2:] == ["status", "header"]


@pytest.mark.parametrize("raw,available", [(None, True), ("VIP WORK END", False)])
def test_missing_crop_or_unavailable_ocr_has_no_status_evidence(raw, available):
    result, calls = detect(raw, available=available)
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert not any(name == "status" for name, _ in calls)


@pytest.mark.parametrize("raw", ["\ud800\nVIP WORK END", "VIP WORK END\n\udfff"])
def test_invalid_unicode_footer_fails_closed_without_losing_primary_observation(raw):
    result, calls = detect(raw, top="Headhunter")
    assert result.state == GameState.MISSION_ACTIVE
    assert result.mission.mission_name == "Headhunter"
    assert result.vip_status_text == raw and result.vip_status_evidence == ""
    assert [name for name, _ in calls].count("status") == 1


def fallback_result(**kwargs):
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "synthetic status", **kwargs)
    result.vip_status_text = ACTUAL_HEADHUNTER
    result.vip_status_evidence = MARKER
    return result


def test_app_fallback_merges_marker_and_displays_stable_category(app):
    result = fallback_result(mission_text="Assassinate the final target")
    reading = app._mission_reading(result)
    assert reading.mission_type == MissionType.VIP_WORK and reading.identity_status == "type_only"
    assert reading.mission_name == "" and ACTUAL_HEADHUNTER not in reading.raw_text
    assert app._mission_display_name(result, reading) == "VIP Work"
    assert app._objective_evidence(result).entries == frozenset()


@pytest.mark.parametrize("top", ["MISSION PASSED", "MISSION FAILED", "HEIST PASSED",
                                    "Headhunter\nCayo Perico"])
def test_app_fallback_does_not_lend_status_identity_to_protected_reading(app, top):
    result = fallback_result(mission_text=top)
    reading = app._mission_reading(result)
    assert reading.raw_text == top
    assert reading.mission_type != MissionType.VIP_WORK


@pytest.mark.parametrize("state", [GameState.BUSINESS_COMPUTER, GameState.MISSION_COMPLETE,
                                    GameState.MISSION_FAILED])
def test_app_fallback_does_not_lend_status_identity_to_protected_state(app, state):
    result = fallback_result()
    result.state = state
    reading = app._mission_reading(result)
    assert reading.identity_status == "unknown" and reading.raw_text == ""


def test_app_fallback_never_parses_raw_or_arbitrary_admitted_footer(app):
    result = fallback_result()
    for evidence in ("", ACTUAL_HEADHUNTER, "Headhunter\nMISSION PASSED", MARKER + "\nTake $5000"):
        result.vip_status_evidence = evidence
        assert app._mission_reading(result).identity_status == "unknown"
        assert not app._objective_evidence(result).entries


def test_direct_callback_propagates_status_provenance(app):
    result, capture = fallback_result(), CaptureResult()
    app._process_state(result, capture)
    assert capture.vip_status_text == ACTUAL_HEADHUNTER and capture.vip_status_evidence == MARKER
    assert app._activity_tracker.current_activity.name == "VIP Work"
