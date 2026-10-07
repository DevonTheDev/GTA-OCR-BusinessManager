"""Conservative objective-role contracts using injected OCR observations.

These tests exercise the production detector/parser and app evidence boundaries.
They make no native OCR or real-pixel recognition claims.
"""

from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult, GTABusinessManager
from src.capture.regions import Region, RegionType, ScreenRegions
from src.detection.mission_episode import MissionIdentity, TerminalMissionEpisode
from src.detection.ocr_engine import OCREngine
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app


SCREEN = np.full((120, 200, 3), 70, dtype=np.uint8)


def observation(monkeypatch, bottom=None, *, top="", center="", banner="", template=None,
                available=True, quick=None, parser=None):
    """Replace only image-to-text and visual/template inputs, not text decisions."""
    texts = dict(zip(("mission_text_image", "center_text_image", "mission_banner_image",
                      "bottom_objective_image"), (top, center, banner, bottom)))
    inputs = {key: object() for key, text in texts.items() if text is not None}
    words = {id(inputs[key]): text for key, text in texts.items() if key in inputs}
    calls = []

    def recognize(image, **kwargs):
        calls.append((words[id(image)], kwargs))
        return SimpleNamespace(text=words[id(image)])

    detector = StateDetector(
        ocr_engine=SimpleNamespace(is_available=available, recognize_preprocessed=recognize),
        template_matcher=SimpleNamespace(match_any=lambda _image, names: (
            template if template is not None and template.template_name in names else None)),
    )
    if parser is not None:
        detector._mission_parser = parser
    monkeypatch.setattr(detector, "_quick_state_check", lambda image: quick or
                        StateDetectionResult(GameState.UNKNOWN, 0.0, "No visual evidence"))
    return detector.detect(SCREEN, **inputs), calls


def test_bottom_region_is_configured_without_widening_existing_crops():
    regions = ScreenRegions()
    assert regions.bottom_objective == Region(.25, .90, .50, .10)
    assert regions.get_region(RegionType.BOTTOM_OBJECTIVE) == regions.bottom_objective
    assert regions.get_all_hud_regions()["bottom_objective"] == regions.bottom_objective
    assert regions.mission_text == Region(.25, .02, .50, .08)
    assert regions.center_prompt == Region(.25, .45, .50, .15)
    assert regions.mission_banner == Region(.20, .35, .60, .15)


@pytest.mark.parametrize("raw,command,family", [
    ("Escape Cayo Perico.", "Escape Cayo Perico", MissionType.CAYO_PERICO),
    ("  EsCape  Cayo\nPerico!?  ", "EsCape Cayo Perico", MissionType.CAYO_PERICO),
    ("Go to the Casino Heist", "Go to the Casino Heist", MissionType.CASINO_HEIST),
    ("Go to El Rubio's compound.", "Go to El Rubio's compound", MissionType.CAYO_PERICO),
])
def test_entire_concrete_family_objective_has_bounded_authority(monkeypatch, raw, command, family):
    result, calls = observation(monkeypatch, raw, top="unrecognized top noise")
    assert (result.state, result.confidence) == (GameState.MISSION_ACTIVE, .8)
    assert result.bottom_objective_text == raw
    assert result.bottom_objective_command == command
    assert result.mission_text == "unrecognized top noise"
    assert result.objective_text == result.banner_text == ""
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_type == family
    assert result.mission.heist_phase == MissionType.UNKNOWN
    assert result.mission.outcome is None and result.mission.outcome_scope is None
    assert result.mission.raw_text == "unrecognized top noise\n" + command
    assert calls[-1] == (raw, {"threshold": False, "invert": True, "scale": 2.0})
    assert len(calls) == 4
    assert all(kwargs == {"invert": True, "scale": 2.0} for _, kwargs in calls[:-1])


@pytest.mark.parametrize("raw", [
    "", " \n ", "Agency", "Cayo Perico", "Headhunter", "Go to the location",
    "Retrieve the package.", "Escape", "Take out",
    "Go to heist prep", "Go to heist finale", "Escape Cayo Perico and Casino Heist",
    "Escape Cayo Perico.\nPlayer Take\nAlex $25000",
    "Player Take\nAlex $25000\nEscape Cayo Perico.",
    "Escape Cayo Perico\nPlayer Take", "Escape Cayo Perico\nTotal Take",
    "Escape Cayo Perico $", "Escape Cayo Perico €", "Escape Cayo Perico £",
    "Escape Cayo Perico ¥", "Escape Cayo Perico 1", "Escape Cayo Perico ١",
    "Escape Cayo Perico. MISSION PASSED", "Escape Cayo Perico\nHEIST PASSED",
    "Escape Cayo Perico\nMISSION FAILED", "Escape Cayo Perico\nGo to the location",
    "Escape Cayo Perico\nRP", "Escape Cayo Perico\nPlatinum", "Escape Cayo Perico\nCONTINUE",
    "Escape Cayo Perico\n rp! ", "Escape Cayo Perico\n  PLATINUM?", "Escape Cayo Perico\nContinue.",
])
def test_rejected_bottom_text_never_enters_main_classifier_or_reading(monkeypatch, raw):
    baseline, _ = observation(monkeypatch)
    result, calls = observation(monkeypatch, raw)
    assert result.bottom_objective_text == raw
    assert result.bottom_objective_command == ""
    assert result.state == baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.mission_text == result.objective_text == result.banner_text == ""
    assert len(calls) == 4


@pytest.mark.parametrize("top,center,banner", [
    ("Headhunter", "", ""), ("", "", "Casino Heist"),
    ("", "Security contract", ""), ("Heist Prep", "", "Casino Heist"),
])
def test_bottom_identity_conflict_abstains_even_with_strong_active_template(monkeypatch, top, center, banner):
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    result, _ = observation(monkeypatch, "Escape Cayo Perico.", top=top, center=center,
                            banner=banner, template=template)
    assert result.state == GameState.UNKNOWN and result.confidence == 0.0
    assert result.mission.identity_status == "ambiguous"
    assert result.bottom_objective_text == "Escape Cayo Perico."
    assert result.bottom_objective_command == ""


@pytest.mark.parametrize("top,center,banner", [
    ("Bunker Stock 40% Supplies 60% Value $7000", "", ""),
    ("MISSION PASSED", "", ""), ("", "MISSION FAILED", ""),
    ("", "", "HEIST PASSED\nCayo Perico"),
])
def test_primary_business_and_results_remain_exact_and_skip_bottom_ocr(monkeypatch, top, center, banner):
    baseline, baseline_calls = observation(monkeypatch, top=top, center=center, banner=banner)
    result, calls = observation(monkeypatch, "Escape Cayo Perico.", top=top, center=center, banner=banner)
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls
    assert result.state in (GameState.BUSINESS_COMPUTER, GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)


@pytest.mark.parametrize("template_name", ["business_computer", "mission_passed", "mission_failed"])
def test_primary_template_authority_skips_bottom(monkeypatch, template_name):
    template = SimpleNamespace(matched=True, confidence=.99, template_name=template_name)
    baseline, baseline_calls = observation(monkeypatch, template=template)
    result, calls = observation(monkeypatch, "Escape Cayo Perico.", template=template)
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


@pytest.mark.parametrize("top,center", [
    ("HEIST PASSED", ""), ("MISSION PASSED", "MISSION FAILED"),
    ("Headhunter", "Sightseer"), ("Cayo Perico Prep", "Cayo Perico Finale"),
])
def test_main_outcome_scope_and_ambiguity_cannot_borrow_bottom_identity(monkeypatch, top, center):
    baseline, _ = observation(monkeypatch, top=top, center=center)
    result, _ = observation(monkeypatch, "Escape Cayo Perico.", top=top, center=center)
    assert result.state == baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.bottom_objective_command == ""


@pytest.mark.parametrize("top,center,banner", [("Cayo", "Perico", ""), ("", "Cayo", "Perico")])
def test_bottom_fragments_cannot_join_other_crops_to_supply_identity(monkeypatch, top, center, banner):
    result, _ = observation(monkeypatch, "Escape", top=top, center=center, banner=banner)
    assert result.bottom_objective_command == ""
    assert result.mission.identity_status == "unknown"


@pytest.mark.parametrize("top,expected", [("Cayo Perico Prep", GameState.HEIST_PREP),
                                          ("Cayo Perico Finale", GameState.HEIST_FINALE)])
def test_compatible_main_phase_is_preserved(monkeypatch, top, expected):
    result, _ = observation(monkeypatch, "Escape Cayo Perico.", top=top)
    assert result.state == expected
    assert result.bottom_objective_command == "Escape Cayo Perico"


def test_missing_bottom_and_unavailable_backend_do_not_add_ocr_work(monkeypatch):
    result, calls = observation(monkeypatch)
    assert result.bottom_objective_text == result.bottom_objective_command == ""
    assert len(calls) == 3
    result, calls = observation(monkeypatch, "Escape Cayo Perico.", available=False)
    assert result.bottom_objective_text == result.bottom_objective_command == ""
    assert calls == []


@pytest.mark.parametrize("last,quick", [(GameState.UNKNOWN, GameState.LOADING),
                                        (GameState.MISSION_ACTIVE, GameState.IDLE)])
def test_combination_copies_both_bottom_fields_for_visual_or_context_winner(last, quick):
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=False))
    detector._context.last_state = last
    ocr = StateDetectionResult(GameState.UNKNOWN, 0.0, "OCR", bottom_objective_text="raw",
                               bottom_objective_command="accepted")
    combined = detector._combine_results(
        StateDetectionResult(quick, .9 if quick == GameState.LOADING else .5, "visual"), ocr, None)
    assert combined.bottom_objective_text == "raw"
    assert combined.bottom_objective_command == "accepted"


def test_threshold_keyword_preserves_positional_defaults_and_allows_grayscale(monkeypatch):
    engine = OCREngine.__new__(OCREngine)
    received = []
    monkeypatch.setattr(engine, "recognize", lambda image: received.append(image) or SimpleNamespace(text=""))
    image = np.arange(600, dtype=np.uint8).reshape(20, 30)
    image = np.repeat(image[:, :, None], 3, axis=2)
    original = image.copy()
    engine.recognize_preprocessed(image, True, 2.0)
    engine.recognize_preprocessed(image, True, 2.0, threshold=False)
    assert len(received) == 2
    assert set(np.unique(received[0])) <= {0, 255}
    assert len(np.unique(received[1])) > 2
    expected = engine.preprocess_for_ocr(image, threshold=False, invert=True, scale=2.0)
    np.testing.assert_array_equal(received[1][:, :, 0], expected)
    np.testing.assert_array_equal(image, original)


def test_app_evidence_and_type_only_display_use_accepted_command_not_raw(app):
    reading = MissionParser().parse("Cayo Perico")
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "OCR", mission_text="noise",
                                  mission=reading, bottom_objective_text="Go to the bank $5000",
                                  bottom_objective_command="Escape Cayo Perico")
    assert GTABusinessManager._objective_evidence(result).entries == {"escape cayo perico"}
    assert GTABusinessManager._mission_display_name(result, reading) == "Escape Cayo Perico"
    result.bottom_objective_command = ""
    assert GTABusinessManager._objective_evidence(result).entries == set()
    assert GTABusinessManager._mission_display_name(result, reading) == "noise"


def test_named_identity_display_keeps_precedence_over_bottom_command():
    reading = MissionParser().parse("The Big Con")
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "OCR", mission=reading,
                                  bottom_objective_command="Go to the Diamond Casino")
    assert GTABusinessManager._mission_display_name(result, reading) == "The Big Con"


def test_direct_guarded_result_copies_bottom_diagnostics_without_starting(app):
    reading = MissionParser().parse("Cayo Perico")
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "OCR", mission=reading,
                                  bottom_objective_text="Escape Cayo Perico.",
                                  bottom_objective_command="Escape Cayo Perico")
    app._data.terminal_mission_episode = TerminalMissionEpisode(
        MissionIdentity(family=MissionType.CAYO_PERICO), GTABusinessManager._objective_evidence(result))
    capture = CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0.0
    assert capture.bottom_objective_text == result.bottom_objective_text
    assert capture.bottom_objective_command == result.bottom_objective_command
    assert app._activity_tracker.current_activity is None


def test_fallback_reading_uses_only_accepted_bottom_command(app):
    result = StateDetectionResult(GameState.MISSION_ACTIVE, .8, "direct producer",
                                  bottom_objective_text="MISSION PASSED Headhunter $5000",
                                  bottom_objective_command="Escape Cayo Perico")
    reading = app._mission_reading(result)
    assert reading.mission_type == MissionType.CAYO_PERICO
    assert reading.outcome is None and reading.outcome_scope is None
    result.bottom_objective_command = ""
    assert app._mission_reading(result).identity_status == "unknown"


def test_normalized_command_must_retain_its_own_concrete_family(monkeypatch):
    # The catalog's Blow Up title requires its own boundary. A raw line break
    # can establish that boundary while the normalized full command loses it.
    raw = "Escape Blow Up\nBay"
    assert MissionParser().parse(raw).mission_name == "Blow Up"
    assert MissionParser().parse("Escape Blow Up Bay").identity_status == "unknown"
    for top in ("", "Cayo Perico"):
        baseline, _ = observation(monkeypatch, top=top)
        result, _ = observation(monkeypatch, raw, top=top)
        assert result.bottom_objective_command == ""
        assert result.state == baseline.state and result.confidence == baseline.confidence
        assert result.mission == baseline.mission


@pytest.mark.parametrize("merged_text", ["unresolved text", "Heist Prep"])
def test_merged_reading_must_still_have_concrete_accepted_identity(monkeypatch, merged_text):
    class UnresolvedMergeParser(MissionParser):
        def parse_regions(self, texts):
            texts = tuple(texts)
            # Simulate the parser declining only the final independent-source
            # merge, after both bottom admission readings had concrete identity.
            if len(texts) == 4:
                return super().parse_regions((merged_text,))
            return super().parse_regions(texts)

    baseline, _ = observation(monkeypatch, top="noise")
    result, _ = observation(monkeypatch, "Escape Cayo Perico.", top="noise",
                            parser=UnresolvedMergeParser())
    assert result.state == baseline.state and result.confidence == baseline.confidence
    assert result.mission == baseline.mission
    assert result.bottom_objective_command == ""
