"""Result-header role contracts with injected OCR, never native OCR claims."""

from dataclasses import asdict
from inspect import signature
from types import SimpleNamespace

import numpy as np
import pytest

from src.capture.regions import Region, RegionType, ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState


SCREEN = np.full((120, 200, 3), 70, dtype=np.uint8)
HEADER = "heist Passed\nThe Cayo Perico Heist"


def observation(monkeypatch, header=None, *, top="", center="", banner="", bottom=None,
                template=None, available=True, quick=None, last=GameState.UNKNOWN):
    texts = dict(zip(("mission_text_image", "center_text_image", "mission_banner_image",
                      "bottom_objective_image", "result_header_image"),
                     (top, center, banner, bottom, header)))
    inputs = {key: object() for key, text in texts.items() if text is not None}
    words = {id(inputs[key]): text for key, text in texts.items() if key in inputs}
    calls, updates = [], []

    def recognize(image, **kwargs):
        calls.append((words[id(image)], kwargs))
        return SimpleNamespace(text=words[id(image)])

    detector = StateDetector(
        ocr_engine=SimpleNamespace(is_available=available, recognize_preprocessed=recognize),
        template_matcher=SimpleNamespace(match_any=lambda _image, names: (
            template if template is not None and template.template_name in names else None)),
    )
    detector._context.last_state = last
    monkeypatch.setattr(detector, "_quick_state_check", lambda image: quick or
                        StateDetectionResult(GameState.UNKNOWN, 0.0, "No visual evidence"))
    update = detector._update_context

    def record_update(result):
        updates.append(result)
        update(result)

    monkeypatch.setattr(detector, "_update_context", record_update)
    # On the baseline, omit the absent optional API so failures assert the
    # missing behavior rather than terminating with an unexpected keyword.
    if "result_header_image" not in signature(detector.detect).parameters:
        inputs.pop("result_header_image", None)
    result = detector.detect(SCREEN, **inputs)
    return result, calls, updates, detector


def test_result_region_is_independent_and_original_regions_stay_exact():
    regions = ScreenRegions()
    assert getattr(regions, "result_header", None) == Region(.20, .12, .60, .20)
    assert regions.get_region(RegionType.RESULT_HEADER) == regions.result_header
    assert regions.get_all_hud_regions()["result_header"] == regions.result_header
    assert regions.mission_text == Region(.25, .02, .50, .08)
    assert regions.mission_banner == Region(.20, .35, .60, .15)
    assert regions.center_prompt == Region(.25, .45, .50, .15)
    assert regions.bottom_objective == Region(.25, .90, .50, .10)


@pytest.mark.parametrize("text,family", [
    (HEADER, MissionType.CAYO_PERICO),
    ("HEIST\nPASSED\nCayo Perico", MissionType.CAYO_PERICO),
    ("HEIST PASSED\nThe Big Con", MissionType.CASINO_HEIST),
    ("HEIST PASSED\nDoomsday Heist", MissionType.DOOMSDAY),
])
def test_self_qualified_header_has_result_authority_and_separate_provenance(monkeypatch, text, family):
    result, calls, updates, detector = observation(monkeypatch, text, top="unrecognized noise")
    assert (result.state, result.confidence) == (GameState.MISSION_COMPLETE, .85)
    assert result.result_header_text == result.result_header_evidence == text
    assert result.mission_text == "unrecognized noise"
    assert result.objective_text == result.banner_text == ""
    assert result.mission.mission_type == family
    assert result.mission.outcome_scope == "heist" and result.mission.outcome == "complete"
    assert result.mission.heist_phase == MissionType.UNKNOWN
    assert result.mission.raw_text == "unrecognized noise\n" + text
    assert calls[-1] == (text, {"threshold": False, "invert": False, "scale": 2.0})
    assert len(calls) == 4
    assert all(options == {"invert": True, "scale": 2.0} for _, options in calls[:-1])
    assert updates == [result] and updates[0] is result
    assert detector._context.last_state == GameState.MISSION_COMPLETE
    assert detector._context.in_mission_since is None


@pytest.mark.parametrize("raw", ["", " \n ", "The Cayo Perico Heist", "Agency", "Bunker Stock 40%",
    "The Cayo Perico Heist\nAgency", "Escape Cayo Perico", "Go to the location",
    "Your Final Take $1,407,990", "MISSION PASSED\nCayo Perico", "MISSION FAILED\nCayo Perico",
    "HEIST", "PASSED", "If the heist passed yesterday, go to Cayo Perico.",
])
def test_header_without_scoped_heist_result_is_diagnostics_only(monkeypatch, raw):
    baseline, _, _, _ = observation(monkeypatch)
    result, calls, _, _ = observation(monkeypatch, raw)
    assert getattr(result, "result_header_text", None) == raw
    assert result.result_header_evidence == ""
    assert result.state == baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.mission_text == result.objective_text == result.banner_text == ""
    assert len(calls) == 4


@pytest.mark.parametrize("raw", ["HEIST PASSED", "HEIST\nPASSED", "HEIST PASSED\nCayo",
    "HEIST PASSED\nHeist Finale", "HEIST PASSED\nCayo Perico Prep",
    "HEIST PASSED\nCayo Perico\nCasino Heist", "HEIST PASSED\nCayo Perico\nHeadhunter",
    "HEIST PASSED\nCayo Perico\nMISSION FAILED", "HEIST PASSED\nHeadhunter",
    "HEIST PASSED\nHeist Prep", "HEIST PASSED\nCayo Perico Prep\nFinale",
])
@pytest.mark.parametrize("top,bottom", [("Cayo Perico", None), ("", "Escape Cayo Perico")])
def test_unqualified_or_conflicting_header_cannot_borrow_identity(monkeypatch, raw, top, bottom):
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    result, _, updates, detector = observation(monkeypatch, raw, top=top, bottom=bottom, template=template)
    assert (result.state, result.confidence) == (GameState.UNKNOWN, 0.0)
    assert result.result_header_text == raw and result.result_header_evidence == ""
    assert updates == [result] and detector._context.last_state == GameState.UNKNOWN


@pytest.mark.parametrize("top,center,banner", [
    ("Bunker Stock 40% Supplies 60% Value $7000", "", ""),
    ("MISSION PASSED", "", ""), ("", "MISSION FAILED", ""),
    ("", "", "HEIST PASSED\nCayo Perico"), ("HEIST PASSED", "", ""),
    ("MISSION PASSED", "MISSION FAILED", ""),
    ("Headhunter", "Sightseer", ""), ("Cayo Perico Prep", "Cayo Perico Finale", ""),
])
def test_primary_business_result_and_uncertainty_keep_priority(monkeypatch, top, center, banner):
    baseline, calls, _, _ = observation(monkeypatch, top=top, center=center, banner=banner)
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, top=top, center=center, banner=banner)
    assert asdict(result) == asdict(baseline)
    assert actual_calls == calls


@pytest.mark.parametrize("name", ["business_computer", "mission_passed", "mission_failed"])
def test_existing_primary_template_authority_skips_header(monkeypatch, name):
    template = SimpleNamespace(matched=True, confidence=.99, template_name=name)
    baseline, calls, _, _ = observation(monkeypatch, template=template)
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, template=template)
    assert asdict(result) == asdict(baseline) and actual_calls == calls


@pytest.mark.parametrize("top,bottom", [("Casino Heist", None), ("Headhunter", None),
    ("Heist Prep", None), ("Cayo Perico Prep", None), ("", "Go to the Casino Heist"),
])
def test_qualified_header_conflicts_with_primary_or_admitted_bottom(monkeypatch, top, bottom):
    result, _, _, _ = observation(monkeypatch, HEADER, top=top, bottom=bottom)
    assert (result.state, result.confidence) == (GameState.UNKNOWN, 0.0)
    assert result.result_header_text == HEADER and result.result_header_evidence == ""
    assert "conflict" in result.reason.lower()


@pytest.mark.parametrize("top,phase", [("Cayo Perico", MissionType.UNKNOWN),
    ("Cayo Perico Finale", MissionType.HEIST_FINALE)])
def test_compatible_primary_and_admitted_bottom_preserve_explicit_phase(monkeypatch, top, phase):
    result, calls, _, _ = observation(monkeypatch, HEADER, top=top, bottom="Escape Cayo Perico")
    assert result.state == GameState.MISSION_COMPLETE
    assert result.mission.heist_phase == phase
    assert result.bottom_objective_command == "Escape Cayo Perico"
    assert result.result_header_evidence == HEADER
    assert calls[-2][1] == {"threshold": False, "invert": True, "scale": 2.0}


def test_conflicting_bottom_remains_uncertain_without_header_rescue(monkeypatch):
    baseline, calls, _, _ = observation(monkeypatch, top="Casino Heist", bottom="Escape Cayo Perico")
    result, actual_calls, _, _ = observation(monkeypatch, HEADER, top="Casino Heist", bottom="Escape Cayo Perico")
    assert asdict(result) == asdict(baseline) and actual_calls == calls
    assert result.state == GameState.UNKNOWN


def test_missing_crop_and_unavailable_backend_keep_optional_input_compatible(monkeypatch):
    result, calls, _, _ = observation(monkeypatch)
    assert getattr(result, "result_header_text", None) == ""
    assert result.result_header_evidence == "" and len(calls) == 3
    result, calls, _, _ = observation(monkeypatch, HEADER, available=False)
    assert result.result_header_text == result.result_header_evidence == "" and calls == []


@pytest.mark.parametrize("last,quick", [(GameState.UNKNOWN, GameState.LOADING),
                                         (GameState.MISSION_ACTIVE, GameState.IDLE)])
def test_combiner_preserves_header_provenance_for_visual_and_context_winners(last, quick):
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=False))
    detector._context.last_state = last
    ocr = StateDetectionResult(GameState.UNKNOWN, 0.0, "OCR")
    ocr.result_header_text, ocr.result_header_evidence = "raw", "accepted"
    result = detector._combine_results(
        StateDetectionResult(quick, .9 if quick == GameState.LOADING else .5, "visual"), ocr, None)
    assert getattr(result, "result_header_text", None) == "raw"
    assert getattr(result, "result_header_evidence", None) == "accepted"
