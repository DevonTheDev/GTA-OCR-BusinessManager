"""Independent OCR crops must contribute complete mission evidence."""

from itertools import permutations

import pytest

from src.detection.parsers.mission_parser import MissionParser, MissionType


@pytest.mark.parametrize("regions", [
    ("Executive", "Search"),
    ("Hostile", "Takeover"),
    ("Customer", "vehicle"),
    ("Casino", "Heist"),
    ("VIP", "work"),
    ("Security", "contract"),
])
def test_separate_crop_fragments_cannot_form_identity(regions):
    for sources in permutations(regions):
        reading = MissionParser().parse_regions(sources)
        assert reading.identity_status == "unknown"
        assert reading.mission_type is MissionType.UNKNOWN
        assert reading.mission_name == ""
        assert reading.candidates == ()
        assert reading.keywords_found == []
        assert not reading.has_mission
        assert not reading.is_active


@pytest.mark.parametrize("text,name,kind", [
    ("Executive\nSearch", "Executive Search", MissionType.VIP_WORK),
    ("Customer\nvehicle", "", MissionType.AUTO_SHOP_DELIVERY),
    ("Casino\nHeist", "", MissionType.CASINO_HEIST),
])
def test_wrapped_evidence_inside_one_crop_still_matches(text, name, kind):
    reading = MissionParser().parse_regions(["", text, ""])
    assert reading.mission_name == name
    assert reading.mission_type is kind
    assert reading.is_active
    assert reading.raw_text == text


@pytest.mark.parametrize("regions", [
    ("JOB", "COMPLETE"),
    ("CONTRACT", "COMPLETE"),
    ("Time ran", "out"),
    ("Product", "lost"),
    ("Associate", "died"),
    ("Target", "escaped"),
    ("MISSION", "PASSED: $20,000"),
])
def test_separate_crop_fragments_cannot_form_outcome(regions):
    for sources in permutations(regions):
        parser = MissionParser()
        reading = parser.parse_regions(("Headhunter", *sources))
        assert reading.outcome is None
        assert reading.is_active
        assert parser.get_last_reading() is reading


@pytest.mark.parametrize("text,outcome", [
    ("JOB\nCOMPLETE", "complete"),
    ("Time ran\nout", "failed"),
    ("MISSION\nPASSED: $20,000", "complete"),
])
def test_wrapped_outcome_inside_one_crop_still_matches(text, outcome):
    reading = MissionParser().parse_regions(["Headhunter", text])
    assert reading.outcome == outcome
    assert reading.mission_name == "Headhunter"
    assert not reading.is_active


@pytest.mark.parametrize("label,outcome", [("PASSED", "complete"), ("FAILED", "failed")])
def test_independently_complete_bare_outcome_label_is_preserved(label, outcome):
    reading = MissionParser().parse_regions(["MISSION", label])
    assert reading.outcome == outcome


def test_matching_name_and_category_crops_combine_without_duplicates():
    for sources in permutations(("Headhunter", "VIP work", "HEADHUNTER")):
        reading = MissionParser().parse_regions(sources)
        assert reading.mission_name == "Headhunter"
        assert reading.mission_type is MissionType.VIP_WORK
        assert reading.identity_status == "known_name"
        assert reading.candidates == ("Headhunter",)
        assert reading.keywords_found == ["headhunter", "vip work"]
        assert reading.is_active


def test_agreeing_category_crops_combine_without_inventing_name():
    for sources in permutations(("Customer vehicle", "SERVICE VEHICLE", "Auto Shop")):
        reading = MissionParser().parse_regions(sources)
        assert reading.mission_name == ""
        assert reading.mission_type is MissionType.AUTO_SHOP_DELIVERY
        assert reading.identity_status == "type_only"
        assert reading.candidates == ("AUTO_SHOP_DELIVERY",)
        assert reading.keywords_found == ["auto shop", "customer vehicle", "service vehicle"]


@pytest.mark.parametrize("family,kind", [
    ("Casino Heist", MissionType.CASINO_HEIST),
    ("Cayo Perico", MissionType.CAYO_PERICO),
    ("Doomsday", MissionType.DOOMSDAY),
])
@pytest.mark.parametrize("label,phase", [
    ("PREP", MissionType.HEIST_PREP),
    ("SETUP: Gather intel", MissionType.HEIST_PREP),
    ("heist preparation", MissionType.HEIST_PREP),
    ("Finale", MissionType.HEIST_FINALE),
])
def test_heist_family_and_independent_phase_label_combine(family, kind, label, phase):
    for sources in permutations((family, label)):
        reading = MissionParser().parse_regions(sources)
        assert reading.mission_type is kind
        assert reading.heist_phase is phase
        assert reading.identity_status == "type_only"
        assert reading.candidates == (kind.name,)
        assert reading.is_active


def test_named_heist_agrees_with_family_and_independent_phase():
    for sources in permutations(("The Big Con", "Casino Heist", "Finale")):
        reading = MissionParser().parse_regions(sources)
        assert reading.mission_name == "The Big Con"
        assert reading.mission_type is MissionType.CASINO_HEIST
        assert reading.heist_phase is MissionType.HEIST_FINALE
        assert reading.candidates == ("The Big Con",)
        assert reading.keywords_found == ["casino heist", "finale", "the big con"]


def test_heist_family_does_not_promote_incidental_phase_word_in_another_crop():
    for sources in permutations(("Casino Heist", "Finish the setup")):
        reading = MissionParser().parse_regions(sources)
        assert reading.mission_type is MissionType.CASINO_HEIST
        assert reading.heist_phase is MissionType.UNKNOWN
        assert reading.keywords_found == ["casino heist"]


@pytest.mark.parametrize("regions,candidates", [
    (("Headhunter", "Sightseer"), ("Headhunter", "Sightseer")),
    (("Headhunter", "Sell mission"), ("Headhunter", "SELL_MISSION")),
    (("VIP work", "Sell mission"), ("SELL_MISSION", "VIP_WORK")),
    (("Cayo Perico", "Casino Heist"), ("CASINO_HEIST", "CAYO_PERICO")),
    (("Cayo Perico", "Prep", "Finale"), ("CAYO_PERICO", "HEIST_FINALE", "HEIST_PREP")),
    (("Headhunter", "Prep"), ("HEIST_PREP", "Headhunter")),
])
def test_conflicting_crop_identities_are_ambiguous_in_every_order(regions, candidates):
    keyword_sets = []
    for sources in permutations(regions):
        reading = MissionParser().parse_regions(sources)
        assert reading.identity_status == "ambiguous"
        assert reading.candidates == candidates
        assert reading.mission_type is MissionType.UNKNOWN
        assert reading.heist_phase is MissionType.UNKNOWN
        assert reading.mission_name == ""
        assert not reading.is_active
        assert not reading.has_mission
        keyword_sets.append(reading.keywords_found)
    assert all(keywords == keyword_sets[0] for keywords in keyword_sets)


@pytest.mark.parametrize("regions,outcome", [
    (("Headhunter", "MISSION PASSED", "JOB COMPLETE"), "complete"),
    (("Headhunter", "MISSION FAILED", "WASTED"), "failed"),
    (("Headhunter", "MISSION PASSED", "MISSION FAILED"), "conflicting"),
])
def test_crop_results_combine_conservatively_in_every_order(regions, outcome):
    for sources in permutations(regions):
        parser = MissionParser()
        previous = parser.parse("Rooftop Rumble")
        reading = parser.parse_regions(sources)
        assert reading.outcome == outcome
        assert reading.mission_name == "Headhunter"
        assert not reading.is_active
        assert parser.get_last_reading() is previous


def test_conflicting_identities_and_outcomes_remain_independent():
    reading = MissionParser().parse_regions([
        "Headhunter\nSightseer", "MISSION PASSED\nMISSION FAILED",
    ])
    assert reading.identity_status == "ambiguous"
    assert reading.candidates == ("Headhunter", "Sightseer")
    assert reading.outcome == "conflicting"
    assert not reading.is_active


@pytest.mark.parametrize("regions,objective", [
    (("Headhunter", "Deliver the goods", "VIP work"), "Deliver the goods"),
    (("Go to", "LSIA"), ""),
    (("Go", "to LSIA"), ""),
    (("Headhunter", "Go to LSIA and\nDestroy the SUV.", "Escape the police"),
     "Go to LSIA and Destroy the SUV"),
    (("Escape the police", "Go to LSIA"), "Escape the police"),
])
def test_objective_is_first_nonempty_per_crop_objective(regions, objective):
    reading = MissionParser().parse_regions(regions)
    assert reading.objective == objective


def test_raw_text_preserves_nonempty_source_strings_and_generator_order():
    regions = ("", "  Headhunter\r\n ", "", " \t", "Go to LSIA\nThen escape.", "")
    reading = MissionParser().parse_regions(text for text in regions)
    assert reading.raw_text == "  Headhunter\r\n \n \t\nGo to LSIA\nThen escape."
    assert reading.objective == "Go to LSIA Then escape"
    assert reading.mission_name == "Headhunter"


@pytest.mark.parametrize("regions", [(), ("",), ("", ""), ("", " \t\n", "")])
def test_empty_crops_do_not_supply_identity_or_replace_history(regions):
    parser = MissionParser()
    previous = parser.parse("Headhunter")
    reading = parser.parse_regions(regions)
    assert reading.identity_status == "unknown"
    assert reading.outcome is None
    assert reading.objective == ""
    assert reading.raw_text == "\n".join(text for text in regions if text)
    assert parser.get_last_reading() is previous


def test_conflicting_regions_never_replace_last_active_reading():
    parser = MissionParser()
    previous = parser.parse("Rooftop Rumble")
    reading = parser.parse_regions(["Headhunter", "Sightseer"])
    assert reading.identity_status == "ambiguous"
    assert parser.get_last_reading() is previous


def test_last_reading_changes_only_after_regions_are_combined():
    parser = MissionParser()
    previous = parser.parse("Rooftop Rumble")

    def regions():
        yield "Headhunter"
        assert parser.get_last_reading() is previous
        yield "VIP work"
        assert parser.get_last_reading() is previous
        yield "Deliver the goods"

    reading = parser.parse_regions(regions())
    assert parser.get_last_reading() is reading
    assert reading.raw_text == "Headhunter\nVIP work\nDeliver the goods"
    assert reading.objective == "Deliver the goods"
    assert reading.keywords_found == ["headhunter", "vip work"]


@pytest.mark.parametrize("text", [
    "", " \t\n", "Executive\nSearch", "Customer\nvehicle",
    "Casino Heist\nFinish the setup", "Setup your business",
    "The Big Con\nFinale", "Headhunter\nSightseer", "Blow up the delivery vehicle",
    "  Headhunter\r\n  Go to LSIA and\nDestroy the Merryweather SUV.  ",
    "MISSION PASSED\nMISSION FAILED", "Job\nComplete",
])
def test_parse_and_single_region_share_rules_and_raw_text(text):
    reading = MissionParser().parse_regions([text])
    assert reading == MissionParser().parse(text)
    assert reading.raw_text == text
