"""Source-derived label policy, using text only and no copied game imagery.

The exact observed transcription is ``heist passed / The Cayo Perico Heist``.
Other combinations below are synthetic boundary and ownership controls.
"""

from types import SimpleNamespace

import pytest

from src.detection.mission_episode import objective_evidence
from src.detection.parsers.mission_parser import MissionParser, MissionType, classify_mission_outcome
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState


OBSERVED_CAYO_RESULT = "heist passed\nThe Cayo Perico Heist"


def test_observed_cayo_label_is_scoped_success_without_inventing_finale():
    parser = MissionParser()
    previous = parser.parse("Cayo Perico\nGo to the compound")
    reading = parser.parse(OBSERVED_CAYO_RESULT)
    assert reading.outcome == classify_mission_outcome(OBSERVED_CAYO_RESULT) == "complete"
    assert reading.outcome_scope == "heist"
    assert reading.raw_text == OBSERVED_CAYO_RESULT
    assert reading.mission_type == MissionType.CAYO_PERICO
    assert reading.heist_phase == MissionType.UNKNOWN
    assert reading.identity_status == "type_only"
    assert reading.mission_name == ""
    assert not reading.is_active
    assert parser.get_last_reading() is previous


@pytest.mark.parametrize("text,kind,phase", [
    ("HEIST\nPASSED\nCayo\nPerico", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    (" HEIST \t PaSsEd !\nCayo Perico", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico | HEIST PASSED", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico\nHEIST PASSED +$10", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("heist passed\nCasino Heist", MissionType.CASINO_HEIST, MissionType.UNKNOWN),
    ("heist passed\nThe Big Con", MissionType.CASINO_HEIST, MissionType.UNKNOWN),
    ("heist passed\nData Breaches", MissionType.DOOMSDAY, MissionType.UNKNOWN),
    ("heist passed\nHeist finale", MissionType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("heist passed\nFinale", MissionType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("heist passed\nCayo Perico Finale", MissionType.CAYO_PERICO, MissionType.HEIST_FINALE),
])
def test_complete_scoped_label_uses_only_explicit_identity_axes(text, kind, phase):
    reading = MissionParser().parse(text)
    assert reading.outcome == classify_mission_outcome(text) == "complete"
    assert reading.outcome_scope == "heist"
    assert reading.mission_type == kind and reading.heist_phase == phase
    assert not reading.is_active


@pytest.mark.parametrize("text", [
    "heist passed", "HEIST\nPASSED", "heist passed\nMystery Heist",
    "heist passed\nHeadhunter", "HEIST\nPASSED\nHeadhunter",
    "heist passed\nCayo Perico Prep", "HEIST\nPASSED\nHeist Prep",
    "heist passed\nThe Big Con\nSetup", "heist passed\nCayo Perico\nCasino Heist",
    "heist passed\nThe Big Con\nSilent & Sneaky",
    "heist passed\nCayo Perico\nHeadhunter",
    "heist passed\nCayo Perico\nPrep\nFinale",
])
def test_unqualified_or_conflicting_identity_stays_uncertain_and_inactive(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None and classify_mission_outcome(text) is None
    assert reading.outcome_scope == "heist"
    assert not reading.is_active


@pytest.mark.parametrize("text", [
    "If the heist passed\nCayo Perico", "heist passed if you escape\nCayo Perico",
    "heist passedness\nCayo Perico", "preheist passed\nCayo Perico",
    "heist passed The Cayo Perico Heist", "The Cayo Perico Heist heist passed",
    "heist failed\nCayo Perico", "heist complete\nCayo Perico",
])
def test_scope_does_not_expand_to_prose_inline_titles_typos_or_unobserved_labels(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None and classify_mission_outcome(text) is None
    assert reading.outcome_scope is None


@pytest.mark.parametrize("crops,outcome,scope", [
    (("heist passed", "The Cayo Perico Heist"), "complete", "heist"),
    (("HEIST\nPASSED", "Finale"), "complete", "heist"),
    (("heist passed", "Cayo", "Perico"), None, "heist"),
    (("heist", "passed Cayo Perico"), None, None),
    # An independent complete bare label retains the established generic rule.
    (("heist", "PASSED", "Cayo Perico Prep"), "complete", None),
])
def test_independent_crops_can_combine_evidence_but_never_phrase_fragments(crops, outcome, scope):
    reading = MissionParser().parse_regions(crops)
    assert reading.outcome == outcome and reading.outcome_scope == scope
    assert reading.raw_text == "\n".join(crops)


@pytest.mark.parametrize("crops", [
    (OBSERVED_CAYO_RESULT + "\nMISSION FAILED",),
    ("HEIST\nPASSED\nMISSION FAILED\nCayo Perico",),
    ("heist passed\nCayo Perico", "FAILED"),
    ("heist passed", "MISSION FAILED"),
    ("HEIST PASSED MISSION FAILED\nCayo Perico",),
])
def test_contradictory_results_are_rejected(crops):
    reading = MissionParser().parse_regions(crops)
    assert reading.outcome == "conflicting"
    assert not reading.is_active
    if len(crops) == 1:
        assert classify_mission_outcome(crops[0]) is None


@pytest.mark.parametrize("label,outcome", [
    ("MISSION PASSED", "complete"), ("JOB COMPLETE", "complete"),
    ("CONTRACT COMPLETE", "complete"), ("MISSION FAILED", "failed"),
    ("PASSED", "complete"),
])
def test_ordinary_result_vocabulary_keeps_its_unscoped_behavior(label, outcome):
    text = label + "\nCayo Perico Prep"
    reading = MissionParser().parse(text)
    assert reading.outcome == classify_mission_outcome(text) == outcome
    assert reading.outcome_scope is None
    assert reading.heist_phase == MissionType.HEIST_PREP


@pytest.mark.parametrize("text", [
    "heist passed", "HEIST\nPASSED", "heist passed\nCayo Perico Prep",
    "heist passed\nCayo Perico\nCasino Heist", "heist passed\nHeadhunter",
])
@pytest.mark.parametrize("template_state", [GameState.MISSION_ACTIVE, GameState.MISSION_COMPLETE])
def test_uncertain_scoped_label_cannot_be_promoted_by_visual_or_template_evidence(text, template_state):
    ocr = SimpleNamespace(is_available=True, recognize_preprocessed=lambda *_a, **_k: SimpleNamespace(text=text))
    detector = StateDetector(SimpleNamespace(match_any=lambda *_a: None), ocr)
    reading = detector._ocr_state_check(None, None, object())
    assert reading.state == GameState.UNKNOWN and reading.confidence == 0
    result = detector._combine_results(
        StateDetectionResult(GameState.MISSION_ACTIVE, 0.9, "visual"), reading,
        StateDetectionResult(template_state, 0.99, "template"),
    )
    assert result.state == GameState.UNKNOWN and result.confidence == 0
    assert result.banner_text == text


def test_heist_label_does_not_hide_commands_in_consumed_episode():
    evidence = objective_evidence(("Go to the compound\nHEIST\nPASSED\nEscape the island",))
    assert evidence.entries == {"go to the compound", "escape the island"}
    assert not evidence.has_new(objective_evidence(("Escape the island",)))


@pytest.mark.parametrize("prose", [
    "If the heist passed earlier, retry the mission.",
    "The guide says the heist passed yesterday.",
    "If the heist\npassed earlier, retry the mission.",
    "The guide explains when the heist passed",
])
@pytest.mark.parametrize("independent_crop", [False, True])
def test_heist_success_in_prose_cannot_override_an_ordinary_failure(prose, independent_crop):
    crops = ("MISSION FAILED\nCayo Perico", prose) if independent_crop else (
        "MISSION FAILED\nCayo Perico\n" + prose,
    )
    reading = MissionParser().parse_regions(crops)
    assert reading.outcome == "failed" and reading.outcome_scope is None
    assert not reading.is_active
    if not independent_crop:
        assert classify_mission_outcome(crops[0]) == "failed"


@pytest.mark.parametrize("pair", [
    "HEIST PASSED MISSION FAILED", "MISSION FAILED HEIST PASSED",
    "HEIST\nPASSED MISSION\nFAILED", "MISSION FAILED HEIST\nPASSED",
])
def test_only_complete_adjacent_status_segments_establish_collapsed_conflict(pair):
    text = pair + "\nCayo Perico"
    assert MissionParser().parse(text).outcome == "conflicting"
    assert classify_mission_outcome(text) is None


@pytest.mark.parametrize("text", [
    "The guide says HEIST PASSED MISSION FAILED\nCayo Perico",
    "HEIST PASSED MISSION FAILED if you retry\nCayo Perico",
    "MISSION FAILED HEIST PASSED yesterday\nCayo Perico",
])
def test_adjacent_status_words_in_prose_are_not_a_complete_conflicting_segment(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None and reading.outcome_scope is None
    assert classify_mission_outcome(text) is None


@pytest.mark.parametrize("text", [
    "heıst passed\nCayo Perico", "heİst passed\nCayo Perico",
    "heıst passed MISSION FAILED\nCayo Perico",
    "MISSION FAILED heİst passed\nCayo Perico",
])
def test_new_scoped_labels_reject_extra_unicode_ignorecase_equivalences(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None and reading.outcome_scope is None
    assert classify_mission_outcome(text) is None
