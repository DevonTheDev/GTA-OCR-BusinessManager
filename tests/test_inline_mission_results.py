"""Complete same-crop title/result segments use only the canonical catalog."""

import pytest

from src.detection.parsers.mission_parser import MissionParser, classify_mission_outcome


LABELS = (
    ("MISSION PASSED", "complete"),
    ("MISSION FAILED", "failed"),
    ("JOB COMPLETE", "complete"),
    ("CONTRACT COMPLETE", "complete"),
)


@pytest.mark.parametrize("name", MissionParser.MISSION_NAMES)
@pytest.mark.parametrize("label,outcome", LABELS)
@pytest.mark.parametrize("title_first", [False, True])
def test_exact_catalog_title_and_explicit_result_are_terminal(name, label, outcome, title_first):
    text = f"{name} {label}" if title_first else f"{label} {name}"
    parser = MissionParser()
    previous = parser.parse("Sightseer")
    reading = parser.parse(text)
    assert reading.outcome == classify_mission_outcome(text) == outcome
    assert reading.identity_status == "known_name"
    assert reading.mission_name == name
    assert reading.mission_type == MissionParser.MISSION_NAMES[name]
    assert reading.candidates == (name,)
    assert not reading.is_active
    assert reading.raw_text == text
    assert parser.get_last_reading() is previous


@pytest.mark.parametrize("text,name,outcome", [
    (" \tMiSsIoN  PaSsEd\t hEaDhUnTeR  ", "Headhunter", "complete"),
    ("MISSION\nFAILED Hostile\nTakeover", "Hostile Takeover", "failed"),
    ("Executive\r\nSearch\tJOB\nCOMPLETE", "Executive Search", "complete"),
    ("CONTRACT COMPLETE Silent &\nSneaky", "Silent & Sneaky", "complete"),
    ("Blow\nUp MISSION\nPASSED", "Blow Up", "complete"),
    ("Go to the location\nHeadhunter MISSION PASSED", "Headhunter", "complete"),
    ("Headhunter MISSION PASSED\nCollect the bonus", "Headhunter", "complete"),
    ("MISSION PASSED Headhunter\nCollect the bonus", "Headhunter", "complete"),
])
def test_wrapped_and_whitespace_normalized_segments_preserve_raw_text(text, name, outcome):
    reading = MissionParser().parse(text)
    assert reading.outcome == classify_mission_outcome(text) == outcome
    assert reading.mission_name == name
    assert reading.raw_text == text
    assert not reading.is_active


@pytest.mark.parametrize("delimiter", ["|", "/", ":", ".", "!", "\n"])
@pytest.mark.parametrize("title_first", [False, True])
def test_only_existing_segment_boundaries_surround_the_new_grammar(delimiter, title_first):
    segment = "Headhunter MISSION PASSED" if title_first else "MISSION PASSED Headhunter"
    text = f"Reward available{delimiter} {segment}{delimiter} Collect the bonus"
    assert MissionParser().parse(text).outcome == classify_mission_outcome(text) == "complete"


@pytest.mark.parametrize("text", [
    "If the mission passed Headhunter, collect the reward",
    "Headhunter if the mission passed, collect the reward",
    "MISSION PASSED if you eliminate the targets",
    "Headhunter MISSION PASSED if you eliminate the targets",
    "MISSION PASSED Headhunter if you eliminate the targets",
    "MISSION PASSED Mystery Job", "Mystery Job MISSION PASSED",
    "MISSION PASSED Headhunter extra", "extra Headhunter MISSION PASSED",
    "Deliver the goods Headhunter MISSION PASSED",
    "MISSION PASSED Headhunter Sightseer", "Headhunter Sightseer MISSION PASSED",
    "MISSION PASSED Headhunter Headhunter",
    "MISSION PASSED Headhunters", "Headhunters MISSION PASSED",
    "MISSION PASSED Head hunter", "MISSION PASSED VIP Work",
    "VIP Work MISSION PASSED", "Headhunter PASSED", "PASSED Headhunter",
    "Headhunter FAILED", "FAILED Headhunter", "Headhunter SUCCESS",
    "SUCCESS Headhunter", "WASTED Headhunter", "Headhunter TIME RAN OUT",
    "Headhunter MISSION PASSED?", "MISSION PASSED Headhunter?",
    "Headhunter MISSION PASSED; collect the reward",
    "MISSION PASSED Headhunter; collect the reward",
    "Did Headhunter MISSION PASSED", "If MISSION PASSED Headhunter",
    "If; MISSION PASSED Headhunter", "If? Headhunter MISSION PASSED",
    "MISSION PASSED Headhunter $25000", "Headhunter MISSION PASSED $25000",
    "Headhunter\nFAILED: Deliver the package", "Headhunter\nSUCCESS: Collect the bonus",
    "Blow up the delivery vehicle MISSION PASSED",
])
def test_partial_prose_unknown_competing_or_unselected_labels_cannot_assert_result(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None
    assert classify_mission_outcome(text) is None
    assert reading.raw_text == text


@pytest.mark.parametrize("crops,outcome", [
    (("MISSION", "PASSED Headhunter"), None),
    (("Headhunter MISSION", "PASSED elsewhere"), None),
    (("MISSION PASSED Hostile", "Takeover"), None),
    (("Hostile", "Takeover MISSION PASSED"), None),
    (("Headhunter", "MISSION PASSED"), "complete"),
    (("Headhunter", "MISSION FAILED"), "failed"),
    (("Headhunter", "MISSION", "PASSED"), "complete"),
    (("MISSION PASSED", "MISSION FAILED", "Headhunter"), "conflicting"),
    (("MISSION PASSED Headhunter MISSION FAILED",), "conflicting"),
    (("MISSION PASSED Headhunter", "Headhunter MISSION FAILED"), "conflicting"),
    (("JOB COMPLETE Headhunter", "Headhunter MISSION FAILED"), "conflicting"),
    (("CONTRACT COMPLETE Headhunter | Headhunter MISSION FAILED",), "conflicting"),
])
def test_independent_crops_and_contradictory_results_keep_existing_rules(crops, outcome):
    parser = MissionParser()
    previous = parser.parse("Sightseer")
    reading = parser.parse_regions(crops)
    assert reading.outcome == outcome
    assert reading.raw_text == "\n".join(crops)
    if outcome is not None:
        assert not reading.is_active
        assert parser.get_last_reading() is previous
    if len(crops) == 1:
        assert classify_mission_outcome(crops[0]) == (None if outcome == "conflicting" else outcome)


@pytest.mark.parametrize("text,outcome", [
    ("MISSION PASSED\nHeadhunter", "complete"),
    ("Headhunter\nMISSION FAILED", "failed"),
    ("MISSION PASSED: Headhunter", "complete"),
    ("Headhunter | JOB COMPLETE", "complete"),
    ("MISSION PASSED $25000", "complete"),
    ("MISSION FAILED - $0", "failed"),
    ("MISSION PASSED: $25,000\nHeadhunter", "complete"),
    ("Headhunter\nPASSED", "complete"),
    ("Headhunter\nFAILED", "failed"),
])
def test_existing_separated_result_payout_and_bare_label_rules(text, outcome):
    assert MissionParser().parse(text).outcome == classify_mission_outcome(text) == outcome


@pytest.mark.parametrize("text", ["MISSION PASSED Sıghtseer", "SİGHTSEER MISSION PASSED"])
def test_unicode_regex_equivalents_do_not_invent_catalog_spellings(text):
    # Unicode IGNORECASE alone admits dotless/dotted I, which the canonical
    # casefold policy does not. Keep outcome and identity normalization aligned.
    reading = MissionParser().parse(text)
    assert reading.outcome is None
    assert reading.mission_name == ""
    assert classify_mission_outcome(text) is None
