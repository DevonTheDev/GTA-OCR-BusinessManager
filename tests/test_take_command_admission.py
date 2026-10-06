"""Take-only admission through the actual OCR detector, without image OCR.

The audit controls are synthetic text. RETAINED_CAYO_TEXTS are the previously
recorded real-image diagnostic transcription, not a new recognition run or
redistributed image. These tests do not establish native gameplay accuracy.
"""

from types import SimpleNamespace

import pytest

from src.detection.state_detector import StateDetector
from src.game.state_machine import GameState


TAKE_CASES = (
    ("clean_take_out", ("Take out the guards",), True),
    ("clean_take", ("Take the briefcase.",), True),
    ("wrapped_command", ("Take\nout the guards",), True),
    ("labeled_command", ("Objective: Take the briefcase",), True),
    ("bare_take", ("Take",), False),
    ("bare_take_out", ("Take out",), False),
    ("table_heading", ("Secondary Targets Take: $406354",), False),
    ("final_take_table", ("Your Final Take: $1407990",), False),
    ("embedded_prose", ("You can take the reward after finishing.",), False),
    ("prose_instruction", ("The guard says to take the briefcase.",), False),
    ("cross_crop_fragment", ("Take", "out the guards"), False),
    ("cross_crop_incomplete", ("Take out the", "guards"), False),
    ("unrelated_clean_command", ("Primary Target Take: $990000", "Bring the briefcase"), False),
    ("other_existing_verb", ("Drive to the checkpoint",), True),
    ("other_existing_bare_cue", ("Wait",), True),
    ("noisy_leading_text_tradeoff", ("Score 42\nTake the briefcase",), False),
    ("punctuation_after_verb_tradeoff", ("Take, the briefcase",), False),
)

RETAINED_CAYO_TEXTS = (
    "",
    "LIMARY Ss! AI GEU Jane? i Aaeene\nSecondary Targets Take: $406354\n"
    "teva eae war te BRAS\nRéncing}Ree: ‘$148;685:\n"
    "BESS geeatenrnasiencs gaa, | ¢ me 2) yEERAROQOR",
    "| Approach Vehicle ‘Kosatie |\nSS6ondan arqete Take: $406:354",
)


def crop_texts(texts, offset=0):
    """Place independent source strings in the three existing crop slots."""
    regions = ["", "", ""]
    for index, text in enumerate(texts):
        regions[(index + offset) % 3] = text
    return tuple(regions)


def detect_texts(texts):
    """Stand in only for the OCR backend; run the real OCR state classifier."""
    images = [object(), object(), object()]
    words = dict(zip(map(id, images), texts))
    backend = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=words[id(image)]),
    )
    return StateDetector(ocr_engine=backend)._ocr_state_check(*images)


@pytest.mark.parametrize("offset", range(3), ids=("top", "center", "banner"))
@pytest.mark.parametrize("label,texts,admitted", TAKE_CASES, ids=[case[0] for case in TAKE_CASES])
def test_audit_take_admission_controls_use_actual_detector(label, texts, admitted, offset):
    regions = crop_texts(texts, offset)
    observed = detect_texts(regions)
    assert observed.state is (GameState.MISSION_ACTIVE if admitted else GameState.UNKNOWN)
    assert observed.confidence == (0.7 if admitted else 0.0)
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.outcome is None
    assert (observed.mission_text, observed.objective_text, observed.banner_text) == regions


def test_retained_real_cayo_table_text_cannot_supply_generic_mission_start():
    observed = detect_texts(RETAINED_CAYO_TEXTS)
    assert observed.state is GameState.UNKNOWN
    assert observed.confidence == 0.0
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.objective == ""
    assert not observed.mission.is_active
    assert observed.mission.outcome is None
    assert (observed.mission_text, observed.objective_text, observed.banner_text) == RETAINED_CAYO_TEXTS


@pytest.mark.parametrize("keyword", (
    "go to", "get to", "reach", "find", "locate", "steal", "deliver", "drop off",
    "destroy", "eliminate", "kill", "protect", "defend", "escort", "wait", "survive",
    "escape", "hack", "collect", "pick up", "lose the cops", "lose wanted", "return to",
    "enter", "search", "investigate", "board", "drive", "fly", "land", "follow",
    "photograph", "source", "acquire", "intercept", "retrieve",
))
def test_each_other_existing_generic_keyword_keeps_admission(keyword):
    observed = detect_texts(("Secondary Targets Take: $406354", keyword, ""))
    assert observed.state is GameState.MISSION_ACTIVE
    assert observed.confidence == 0.7


@pytest.mark.parametrize("regions,state,confidence", (
    (("Take", "Headhunter", ""), GameState.MISSION_ACTIVE, 0.8),
    (("Take", "Hostile Takeover", ""), GameState.MISSION_ACTIVE, 0.8),
    (("Take", "Headhunter\nDeliver the goods", ""), GameState.MISSION_ACTIVE, 0.8),
    (("Take", "Customer vehicle", ""), GameState.MISSION_ACTIVE, 0.8),
    (("Take", "Cayo Perico\nPrep", ""), GameState.HEIST_PREP, 0.8),
    (("Take", "Casino Heist\nFinale", ""), GameState.HEIST_FINALE, 0.8),
    (("Take", "MISSION PASSED", "Headhunter"), GameState.MISSION_COMPLETE, 0.85),
    (("Take", "MISSION FAILED", "Headhunter"), GameState.MISSION_FAILED, 0.85),
    (("Take", "MISSION PASSED", "MISSION FAILED"), GameState.UNKNOWN, 0.0),
    (("Take", "Headhunter", "Sightseer"), GameState.UNKNOWN, 0.0),
    (("Take", "heist passed", ""), GameState.UNKNOWN, 0.0),
    (("Take", "Deliver the goods", ""), GameState.SELLING, 0.75),
    (("Take", "Product value", ""), GameState.BUSINESS_COMPUTER, 0.75),
))
def test_specific_identity_result_delivery_and_business_priorities(regions, state, confidence):
    observed = detect_texts(regions)
    assert observed.state is state
    assert observed.confidence == confidence


@pytest.mark.parametrize("text", ("Mistake", "Takeover", "Undertake", "Takeaway"))
def test_take_inside_a_word_still_cannot_supply_generic_admission(text):
    observed = detect_texts((text, "", ""))
    assert observed.state is GameState.UNKNOWN
    assert observed.confidence == 0.0
