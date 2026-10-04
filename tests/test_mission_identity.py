"""Conservative identity and outcome rules using repository-sourced OCR strings."""

import pytest

from src.detection.parsers import mission_parser
from src.detection.parsers.mission_parser import MissionParser, MissionReading, MissionType
from src.game.missions import CONTACT_MISSIONS, SECURITY_CONTRACTS, VIP_WORK


CATALOG_MISSIONS = [
    (info.name, kind)
    for catalog, kind in (
        (CONTACT_MISSIONS, MissionType.CONTACT_MISSION),
        (VIP_WORK, MissionType.VIP_WORK),
        (SECURITY_CONTRACTS, MissionType.SECURITY_CONTRACT),
    )
    for info in catalog.values()
]


@pytest.mark.parametrize("name,kind", CATALOG_MISSIONS)
def test_catalog_title_has_canonical_identity(name, kind):
    reading = MissionParser().parse(name.upper())
    assert reading.mission_type is kind
    assert reading.mission_name == name
    assert reading.identity_status == "known_name"
    assert reading.candidates == (name,)
    assert reading.is_active and reading.has_mission


@pytest.mark.parametrize("name,kind", [
    ("Asset Recovery", MissionType.VIP_WORK),
    ("Executive Search", MissionType.VIP_WORK),
    ("Jailbreak", MissionType.MC_CONTRACT),
    ("Torched", MissionType.MC_CONTRACT),
    ("Fragile Goods", MissionType.MC_CONTRACT),
    ("Outrider", MissionType.MC_CONTRACT),
    ("Gun Running", MissionType.MC_CONTRACT),
    ("Asset Protection", MissionType.SECURITY_CONTRACT),
    ("Vehicle Recovery", MissionType.SECURITY_CONTRACT),
    ("The Popstar", MissionType.PAYPHONE_HIT),
    ("The Tech Entrepreneur", MissionType.PAYPHONE_HIT),
    ("The Cofounder", MissionType.PAYPHONE_HIT),
    ("Business Battle", MissionType.FREEMODE_EVENT),
    ("King of the Castle", MissionType.FREEMODE_EVENT),
    ("Hunt the Beast", MissionType.FREEMODE_EVENT),
    ("The Big Con", MissionType.CASINO_HEIST),
    ("Silent & Sneaky", MissionType.CASINO_HEIST),
    ("Data Breaches", MissionType.DOOMSDAY),
])
def test_specific_titles_from_existing_parser_keywords(name, kind):
    reading = MissionParser().parse(name)
    assert reading.mission_type is kind
    assert reading.mission_name == name
    assert reading.identity_status == "known_name"
    assert reading.heist_phase is MissionType.UNKNOWN


@pytest.mark.parametrize("objective", [
    "Deliver the goods", "Take out the targets", "Acquire the product",
    "Collect the bonus", "Steal supplies", "Drop off the merchandise",
])
def test_known_name_wins_over_generic_objective_vocabulary(objective):
    reading = MissionParser().parse(f"Headhunter\n{objective}")
    assert reading.mission_type is MissionType.VIP_WORK
    assert reading.mission_name == "Headhunter"
    assert reading.identity_status == "known_name"


@pytest.mark.parametrize("text", [
    "  hOsTiLe\t TAKEOVER  ", "Hostile\nTakeover",
    'Mission: "HOSTILE TAKEOVER"',
])
def test_case_whitespace_and_line_wrapping_preserve_canonical_name(text):
    reading = MissionParser().parse(text)
    assert reading.mission_name == "Hostile Takeover"
    assert reading.mission_type is MissionType.VIP_WORK
    assert reading.raw_text == text
    assert "hostile takeover" in reading.keywords_found


@pytest.mark.parametrize("text", [
    "take", "cut", "bonus", "acquire", "deliver", "reward", "cash", "+$500",
    "Deliver the goods", "Deliver the vehicle", "Take out the targets",
    "Steal supplies", "Acquire the product", "Go to the Casino",
    "Planning board", "compound", "vault", "facility", "Kosatka", "Avenger",
    "Act 1", "scope out", "aggressive", "headhunters", "preheadhunter",
    "Hostile Takeovers", "Headhunler", 'Mission: "Unknown Job"',
    "preparationist", "finalement", "Setup your business", "",
    " \t\n", "ordinary unrelated text", "Blow up the delivery vehicle",
])
def test_generic_objectives_noise_and_partial_words_do_not_invent_identity(text):
    reading = MissionParser().parse(text)
    assert reading.mission_type is MissionType.UNKNOWN
    assert reading.mission_name == ""
    assert reading.identity_status == "unknown"
    assert reading.candidates == ()
    assert not reading.is_active
    assert not reading.has_mission
    assert reading.raw_text == text


@pytest.mark.parametrize("text,kind", [
    ("Contact mission", MissionType.CONTACT_MISSION),
    ("VIP work", MissionType.VIP_WORK),
    ("VIP challenge", MissionType.VIP_WORK),
    ("MC contract", MissionType.MC_CONTRACT),
    ("Clubhouse contract", MissionType.MC_CONTRACT),
    ("Sell mission", MissionType.SELL_MISSION),
    ("Resupply", MissionType.RESUPPLY),
    ("Supply run", MissionType.RESUPPLY),
    ("Security contract", MissionType.SECURITY_CONTRACT),
    ("Payphone hit", MissionType.PAYPHONE_HIT),
    ("Customer vehicle", MissionType.AUTO_SHOP_DELIVERY),
    ("SERVICE\nVEHICLE", MissionType.AUTO_SHOP_DELIVERY),
    ("Auto Shop", MissionType.AUTO_SHOP_DELIVERY),
    ("Exotic exports", MissionType.AUTO_SHOP_DELIVERY),
    ("Club promotion", MissionType.NIGHTCLUB_PROMOTION),
    ("Nightclub promotion", MissionType.NIGHTCLUB_PROMOTION),
    ("Freemode event", MissionType.FREEMODE_EVENT),
])
def test_explicit_category_phrases_do_not_invent_names(text, kind):
    reading = MissionParser().parse(text)
    assert reading.mission_type is kind
    assert reading.mission_name == ""
    assert reading.identity_status == "type_only"
    assert reading.candidates == (kind.name,)
    assert reading.is_active and reading.has_mission


@pytest.mark.parametrize("text,family,phase", [
    ("Cayo Perico", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico prep: Gather intel", MissionType.CAYO_PERICO, MissionType.HEIST_PREP),
    ("Cayo Perico\nTake out the guard", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Casino Heist setup", MissionType.CASINO_HEIST, MissionType.HEIST_PREP),
    ("Casino Heist\nFinale", MissionType.CASINO_HEIST, MissionType.HEIST_FINALE),
    ("Doomsday preparation", MissionType.DOOMSDAY, MissionType.HEIST_PREP),
    ("Doomsday\nTake 50%\nCut 25%", MissionType.DOOMSDAY, MissionType.UNKNOWN),
    ("heist prep: scope out island", MissionType.HEIST_PREP, MissionType.HEIST_PREP),
    ("heist finale", MissionType.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("PREP", MissionType.HEIST_PREP, MissionType.HEIST_PREP),
    (" \tPREP\r\n", MissionType.HEIST_PREP, MissionType.HEIST_PREP),
    ("SETUP", MissionType.HEIST_PREP, MissionType.HEIST_PREP),
    ("Finale: Escape", MissionType.HEIST_FINALE, MissionType.HEIST_FINALE),
])
def test_family_and_explicit_phase_are_separate(text, family, phase):
    reading = MissionParser().parse(text)
    assert reading.mission_type is family
    assert reading.heist_phase is phase
    assert reading.identity_status == "type_only"
    assert reading.mission_name == ""


@pytest.mark.parametrize("text,candidates", [
    ("Headhunter\nSightseer", ("Headhunter", "Sightseer")),
    ("Headhunter\nRooftop Rumble", ("Headhunter", "Rooftop Rumble")),
    ("VIP work\nSell mission", ("SELL_MISSION", "VIP_WORK")),
    ("Headhunter\nSell mission", ("Headhunter", "SELL_MISSION")),
    ("Cayo Perico\nCasino Heist", ("CASINO_HEIST", "CAYO_PERICO")),
    ("Cayo Perico\nPrep\nFinale", ("CAYO_PERICO", "HEIST_FINALE", "HEIST_PREP")),
])
def test_strong_conflicts_are_explicit_and_do_not_choose_by_order(text, candidates):
    parser = MissionParser()
    for source in (text, "\n".join(reversed(text.splitlines()))):
        reading = parser.parse(source)
        assert reading.mission_type is MissionType.UNKNOWN
        assert reading.heist_phase is MissionType.UNKNOWN
        assert reading.mission_name == ""
        assert reading.identity_status == "ambiguous"
        assert reading.candidates == candidates
        assert not reading.is_active and not reading.has_mission


def test_agreeing_category_and_repeated_name_are_one_identity():
    reading = MissionParser().parse("VIP WORK\nHeadhunter\nHEADHUNTER")
    assert reading.identity_status == "known_name"
    assert reading.mission_type is MissionType.VIP_WORK
    assert reading.mission_name == "Headhunter"
    assert reading.candidates == ("Headhunter",)


def test_raw_text_and_original_case_objective_survive_normalization():
    text = "  Headhunter\r\n  Go to LSIA and\nDestroy the Merryweather SUV.  "
    reading = MissionParser().parse(text)
    assert reading.raw_text == text
    assert reading.objective == "Go to LSIA and Destroy the Merryweather SUV"
    assert reading.mission_name == "Headhunter"


def test_unknown_text_has_no_made_up_title_or_objective():
    reading = MissionParser().parse('Job: "The Unknown"')
    assert reading.mission_name == ""
    assert reading.objective == ""


def test_last_reading_tracks_only_unambiguous_active_identity():
    parser = MissionParser()
    assert parser.get_last_reading() is None
    known = parser.parse("Headhunter")
    assert parser.get_last_reading() is known
    for text in ("", "Take the package", "Headhunter\nSightseer", "MISSION PASSED\nSightseer"):
        parser.parse(text)
        assert parser.get_last_reading() is known
    category = parser.parse("Customer vehicle")
    assert parser.get_last_reading() is category


def test_reading_defaults_are_noncommittal():
    reading = MissionReading()
    assert reading.identity_status == "unknown"
    assert reading.candidates == ()
    assert reading.heist_phase is MissionType.UNKNOWN
    assert reading.outcome is None


@pytest.mark.parametrize("identity,status", [
    ("Headhunter", "known_name"),
    ("Customer vehicle", "type_only"),
    ("", "unknown"),
    ("Headhunter\nSightseer", "ambiguous"),
])
@pytest.mark.parametrize("banner,outcome", [
    ("MISSION PASSED", "complete"),
    ("MISSION FAILED", "failed"),
    ("MISSION PASSED\nMISSION FAILED", "conflicting"),
    ("MISSION PASSED: MISSION FAILED", "conflicting"),
    ("MISSION PASSED MISSION FAILED", "conflicting"),
])
def test_result_evidence_is_preserved_separately_from_identity(identity, status, banner, outcome):
    parser = MissionParser()
    previous = parser.parse("Rooftop Rumble")
    text = "\n".join(part for part in (identity, banner) if part)
    reading = parser.parse(text)
    assert reading.outcome == outcome
    assert reading.identity_status == status
    assert reading.raw_text == text
    assert not reading.is_active
    assert parser.get_last_reading() is previous
    assert mission_parser.classify_mission_outcome(text) == (
        None if outcome == "conflicting" else outcome
    )


@pytest.mark.parametrize("text", ["Headhunter", "Customer vehicle", "Go to the airport", ""])
def test_absent_outcome_is_distinct_from_conflicting_evidence(text):
    reading = MissionParser().parse(text)
    assert reading.outcome is None


@pytest.mark.parametrize("text,outcome", [
    ("MISSION PASSED", "complete"),
    ("Mission\nPassed\n$20,000", "complete"),
    ("MISSION PASSED: $20,000", "complete"),
    ("JOB COMPLETE", "complete"),
    ("Contract complete", "complete"),
    ("PASSED\n+$20,000", "complete"),
    ("MISSION FAILED", "failed"),
    ("Mission\nFailed", "failed"),
    ("FAILED", "failed"),
    ("WASTED", "failed"),
    ("BUSTED", "failed"),
    ("Time ran out", "failed"),
    ("Product lost", "failed"),
    ("Associate died", "failed"),
    ("Target escaped", "failed"),
])
def test_explicit_bounded_outcome_evidence(text, outcome):
    classifier = getattr(mission_parser, "classify_mission_outcome", None)
    assert callable(classifier), "Outcome classification must be shared with StateDetector"
    assert classifier(text) == outcome
    parser = MissionParser()
    assert parser.is_mission_complete(text) is (outcome == "complete")
    assert parser.is_mission_failed(text) is (outcome == "failed")


@pytest.mark.parametrize("text", [
    "bonus", "reward", "cash", "+$500", "+RP", "Cash reward bonus",
    "Deliver the reward", "Take the bonus", "Escape before time ran out",
    "Destroy the target", "The goods must be delivered", "lost", "destroyed",
    "successfully deliver the goods", "passedness", "uncompleted", "unfailed",
    "If the mission failed, try again", "Mission not completed", "",
    "MISSION PASSED\nMISSION FAILED", "JOB COMPLETE\nWASTED",
    "MISSION PASSED: MISSION FAILED", "MISSION PASSED. MISSION FAILED.",
    "MISSION PASSED / MISSION FAILED", "MISSION PASSED MISSION FAILED",
    "Success bonus available", "Failed to deliver", "Passed the checkpoint",
    "Mission passedness", "Job completeable", "SUCCESS: Collect the bonus",
    "FAILED: Deliver the package",
    "Objective complete", "SUCCESS", "COMPLETED", "Well done!",
])
def test_incidental_objective_and_conflicting_text_is_not_terminal(text):
    classifier = getattr(mission_parser, "classify_mission_outcome", None)
    assert callable(classifier), "Outcome classification must be shared with StateDetector"
    assert classifier(text) is None
    parser = MissionParser()
    assert not parser.is_mission_complete(text)
    assert not parser.is_mission_failed(text)
