"""Primary business source ownership through real detector/parser decisions.

Only OCR text and template matches are substituted at their supported inputs.
These conditional template cases make no native OCR or gameplay accuracy claim.
"""

from dataclasses import asdict
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src.detection.ocr_engine import OCRResult
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetector
from src.detection.template_matcher import MatchResult
from src.game.state_machine import GameState


BUSINESS = "Bunker Stock 40% Supplies 60% Value $7000"
BOTTOM = "Escape Cayo Perico."
HEADER = "HEIST PASSED\nThe Cayo Perico Heist"
FOOTER = "VIP WORK END 12:30"
PRIMARY = ("mission", "center", "banner")
INPUTS = {
    "mission": "mission_text_image",
    "center": "center_text_image",
    "banner": "mission_banner_image",
    "bottom": "bottom_objective_image",
    "header": "result_header_image",
    "vip_status": "vip_status_image",
}
SCREEN = np.full((120, 200, 3), 70, dtype=np.uint8)


def detect_sources(texts, *, template="mission_banner", observed=False, available=True):
    """Run actual visual checks, classification, parsing and combination."""
    crops = {source: object() for source in texts}
    calls, events = [], []

    def recognize(image, **kwargs):
        source = next(name for name, crop in crops.items() if crop is image)
        calls.append((source, kwargs))
        return OCRResult(texts[source], None, [])

    match = None if template is None else MatchResult(True, .99, (0, 0), template)
    detector = StateDetector(
        ocr_engine=SimpleNamespace(is_available=available, recognize_preprocessed=recognize),
        template_matcher=SimpleNamespace(match_any=lambda _image, names: (
            match if match is not None and match.template_name in names else None)),
    )
    observation = None
    if observed:
        observation = lambda source, event, payload: events.append((source, event, payload))
    result = detector.detect(SCREEN, **{INPUTS[source]: crop for source, crop in crops.items()},
                             observation=observation)
    return result, calls, events, detector.context


def primary_texts(source, text=BUSINESS):
    return {name: text if name == source else "" for name in PRIMARY}


@pytest.mark.parametrize("observed", [False, True], ids=["ordinary", "observed"])
@pytest.mark.parametrize("source", PRIMARY)
@pytest.mark.parametrize("supplements", [
    {"bottom": BOTTOM}, {"header": HEADER}, {"bottom": BOTTOM, "header": HEADER},
    {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER},
], ids=["bottom", "header", "bottom-header", "all-supplemental"])
def test_primary_business_skips_supplements_despite_strong_generic_template(
        source, supplements, observed):
    baseline, baseline_calls, _, _ = detect_sources(primary_texts(source), observed=observed)
    result, calls, events, context = detect_sources(
        primary_texts(source) | supplements, observed=observed)

    # The existing generic template still establishes an unresolved active state.
    assert baseline.state == GameState.MISSION_ACTIVE
    assert baseline.confidence == .99
    assert baseline.mission.identity_status == "unknown"
    assert baseline.mission.mission_type == MissionType.UNKNOWN
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls
    assert [name for name, _ in calls] == list(PRIMARY)
    assert context.last_state == result.state
    if observed:
        assert [name for name, event, _ in events if event == "request"] == list(PRIMARY)


@pytest.mark.parametrize("source", PRIMARY)
@pytest.mark.parametrize("template", [None, "mission_banner"])
def test_single_primary_business_crop_retains_absent_supplement_baseline(source, template):
    baseline, baseline_calls, _, _ = detect_sources({source: BUSINESS}, template=template)
    result, calls, _, _ = detect_sources(
        {source: BUSINESS, "bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER},
        template=template)
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls
    assert [name for name, _ in calls] == [source]
    assert result.mission.raw_text == BUSINESS
    assert result.state == (GameState.BUSINESS_COMPUTER if template is None
                            else GameState.MISSION_ACTIVE)


@pytest.mark.parametrize("source", PRIMARY)
def test_primary_business_without_templates_keeps_business_classification(source):
    baseline, baseline_calls, _, _ = detect_sources(primary_texts(source), template=None)
    result, calls, _, _ = detect_sources(
        primary_texts(source) | {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER},
        template=None)
    assert result.state == GameState.BUSINESS_COMPUTER
    assert result.confidence == .75
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


@pytest.mark.parametrize("template,state", [
    ("mission_passed", GameState.MISSION_COMPLETE),
    ("mission_failed", GameState.MISSION_FAILED),
    ("business_laptop", GameState.BUSINESS_COMPUTER),
    ("business_computer", GameState.BUSINESS_COMPUTER),
    ("mc_laptop", GameState.BUSINESS_COMPUTER),
    ("bunker_laptop", GameState.BUSINESS_COMPUTER),
])
def test_named_primary_templates_keep_existing_authority(template, state):
    baseline, baseline_calls, _, _ = detect_sources(primary_texts("mission"), template=template)
    result, calls, _, _ = detect_sources(
        primary_texts("mission") | {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER},
        template=template)
    assert result.state == state and result.confidence == .99
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


@pytest.mark.parametrize("template", [None, "mission_banner"])
@pytest.mark.parametrize("supplements,state", [
    ({"bottom": BOTTOM}, GameState.MISSION_ACTIVE),
    ({"header": HEADER}, GameState.MISSION_COMPLETE),
    ({"bottom": BOTTOM, "header": HEADER}, GameState.MISSION_COMPLETE),
])
def test_supplemental_cayo_sources_keep_authority_without_primary_business(
        template, supplements, state):
    result, calls, _, _ = detect_sources(primary_texts("mission", "noise") | supplements,
                                        template=template)
    assert result.state == state
    assert result.mission.mission_type == MissionType.CAYO_PERICO
    assert result.mission.identity_status == "type_only"
    if "bottom" in supplements:
        assert result.bottom_objective_text == BOTTOM
        assert result.bottom_objective_command == "Escape Cayo Perico"
        assert ("bottom", {"threshold": False, "invert": True, "scale": 2.0}) in calls
    if "header" in supplements:
        assert result.confidence == .85
        assert result.mission.outcome_scope == "heist"
        assert result.mission.outcome == "complete"
        assert result.result_header_text == result.result_header_evidence == HEADER
        assert ("header", {"threshold": False, "invert": False, "scale": 2.0}) in calls
    else:
        assert result.confidence == (.8 if template is None else .99)
        assert result.mission.outcome is None
    assert [name for name, _ in calls] == [*PRIMARY, *supplements]


@pytest.mark.parametrize("template", [None, "mission_banner"])
def test_vip_footer_remains_eligible_without_primary_business(template):
    result, calls, _, _ = detect_sources(
        primary_texts("mission", "noise") | {"vip_status": FOOTER}, template=template)
    assert result.state == GameState.MISSION_ACTIVE and result.confidence == .8
    assert result.mission.mission_type == MissionType.VIP_WORK
    assert result.mission.identity_status == "type_only"
    assert result.vip_status_evidence == "VIP WORK"
    assert calls[-1] == ("vip_status", {"threshold": False, "invert": True, "scale": 2.0})


def test_business_vocabulary_does_not_block_recognized_primary_mission():
    result, calls, _, _ = detect_sources(
        primary_texts("mission", "Headhunter\n" + BUSINESS) | {"vip_status": FOOTER})
    assert result.state == GameState.MISSION_ACTIVE
    assert result.mission.identity_status == "known_name"
    assert result.mission.mission_name == "Headhunter"
    assert result.vip_status_evidence == "VIP WORK"
    assert [name for name, _ in calls] == [*PRIMARY, "vip_status"]


@pytest.mark.parametrize("source", PRIMARY)
def test_ambiguous_primary_keeps_existing_unresolved_generic_template_result(source):
    texts = primary_texts(source, "Headhunter\nSightseer")
    baseline, baseline_calls, _, _ = detect_sources(texts)
    result, calls, _, _ = detect_sources(
        texts | {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER})
    assert result.state == GameState.MISSION_ACTIVE and result.confidence == .99
    assert result.mission.identity_status == "ambiguous"
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


@pytest.mark.parametrize("text,state", [
    ("MISSION PASSED", GameState.MISSION_COMPLETE),
    ("MISSION FAILED", GameState.MISSION_FAILED),
    (HEADER, GameState.MISSION_COMPLETE),
])
@pytest.mark.parametrize("source", PRIMARY)
def test_primary_results_still_outrank_generic_active_template(text, state, source):
    texts = primary_texts(source, text)
    baseline, baseline_calls, _, _ = detect_sources(texts)
    result, calls, _, _ = detect_sources(
        texts | {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER})
    assert result.state == state and result.confidence == .85
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


def test_template_only_with_unavailable_ocr_keeps_existing_generic_result():
    baseline, baseline_calls, _, _ = detect_sources({}, available=False)
    result, calls, _, _ = detect_sources(
        {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER}, available=False)
    assert result.state == GameState.MISSION_ACTIVE and result.confidence == .99
    assert result.mission is None
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls == []


@pytest.mark.parametrize("source", PRIMARY)
def test_sample_source_records_truthfully_preserve_skipped_supplements(source):
    from src.detection.detection_sample import DetectionSampleCollector, SOURCE_INDICES

    texts = primary_texts(source) | {"bottom": BOTTOM, "header": HEADER, "vip_status": FOOTER}
    result, calls, events, _ = detect_sources(texts, observed=True)
    collector = DetectionSampleCollector("a" * 32, "b" * 32,
        "2026-10-08T22:00:00+00:00", "2026-10-08T22:00:01+00:00")
    collector.capture({0: SCREEN, **{index: SCREEN for index in SOURCE_INDICES.values()}},
                      dict.fromkeys(SOURCE_INDICES, True), object())
    for name, event, payload in events:
        collector.observe_ocr(name, event, payload)
    collector.stage("candidate", result)
    report = json.loads(collector.finish().json_bytes)
    assert report["mission_observation_status"] == "observed"
    assert report["detector_candidate"]["state"] == "MISSION_ACTIVE"
    assert report["detector_candidate"]["identity"]["status"] == "unknown"
    assert len(calls) == 3
    for name in ("bottom", "header", "vip_status"):
        record = report["sources"][name]
        assert record["region_present"] and record["image_present"]
        assert record["ocr_status"] == "not_requested_by_detector"
        assert record["raw_text"] is record["preprocessing"] is None
