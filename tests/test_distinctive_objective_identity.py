"""One source-backed complete objective association, with synthetic boundaries.

These text tests exercise real parser/detector policy, not OCR accuracy. The
observed command supplies a family association, never a transcribed Cayo title.
"""

from types import SimpleNamespace

import pytest

from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.game.state_machine import GameState
from tests.test_bottom_objective_admission import observation as bottom_observation
from tests.test_result_header_admission import observation as header_observation


COMMAND = "Go to El Rubio's compound"
EVIDENCE = "go to el rubio's compound"


@pytest.mark.parametrize("raw,objective", [
    (COMMAND, COMMAND),
    (COMMAND + ".", COMMAND),
    (COMMAND + "!?", COMMAND),
    ("  GO\tTO EL\nRUBIO'S  COMPOUND. \r\n", "GO TO EL RUBIO'S COMPOUND"),
    ("\nGo to\nEl Rubio's\ncompound!\n", COMMAND),
    ("Go to El Rubio's compound  .", COMMAND),
])
def test_complete_observed_command_supplies_family_and_original_evidence(raw, objective):
    reading = MissionParser().parse(raw)
    assert reading.mission_type is MissionType.CAYO_PERICO
    assert reading.identity_status == "type_only"
    assert reading.mission_name == "" and reading.candidates == ("CAYO_PERICO",)
    assert reading.heist_phase is MissionType.UNKNOWN
    assert reading.outcome is None and reading.outcome_scope is None
    assert reading.has_mission and reading.is_active
    assert reading.raw_text == raw and reading.objective == objective
    assert reading.keywords_found == [EVIDENCE]
    assert "cayo" not in reading.raw_text.casefold()


@pytest.mark.parametrize("sources", [
    ("unrecognized primary text", COMMAND + "."),
    (COMMAND + ".", "unrecognized primary text"),
    ("", COMMAND, "", COMMAND + "!"),
])
def test_independent_complete_crop_is_resolved_without_rewriting_sources(sources):
    reading = MissionParser().parse_regions(text for text in sources)
    assert reading.mission_type is MissionType.CAYO_PERICO
    assert reading.identity_status == "type_only" and reading.mission_name == ""
    assert reading.heist_phase is MissionType.UNKNOWN and reading.outcome is None
    assert reading.raw_text == "\n".join(text for text in sources if text)
    assert reading.objective == COMMAND and reading.keywords_found == [EVIDENCE]


@pytest.mark.parametrize("raw", [
    "Go to the location", "Go to the compound", "Retrieve the package.",
    "El Rubio", "El Rubio's compound", "compound", "Go to El Rubio", "Go to El Rubio's",
    "Get to El Rubio's compound", "Escape El Rubio's compound", "Go El Rubio's compound",
    "Go to El Rubio compound", "Go to El Rublo's compound", "Go to El Rubio's compounds",
    "Go to El Rubio’s compound", "Go to El Rubio‘s compound", "Go to El Rubioʼs compound",
    "Go to El Rubıo's compound", "Go to El Rubİo's compound",
    "If you go to El Rubio's compound, wait", "You must go to El Rubio's compound",
    "Objective: Go to El Rubio's compound", '"Go to El Rubio\'s compound"',
    "Go to El Rubio's compound now", "Go to El Rubio's compound and wait",
    "Go to El Rubio's compound. Then wait", "Go to El Rubio's compound\nAgency",
    "Go to El Rubio's compound\nPlayer Take", "Go to El Rubio's compound\nTotal Take",
    "Go to El Rubio's compound\nRP", "Go to El Rubio's compound\nPlatinum",
    "Go to El Rubio's compound\nCONTINUE", "Go to El Rubio's compound $",
    "Go to El Rubio's compound €", "Go to El Rubio's compound £",
    "Go to El Rubio's compound ¥", "Go to El Rubio's compound 1",
    "Go to El Rubio's compound ١", "Go to El Rubio's compound;",
    "Go to El Rubio's compound:", "Go to El Rubio's compound . ?",
])
def test_near_matches_do_not_gain_identity(raw):
    reading = MissionParser().parse(raw)
    assert reading.mission_type is MissionType.UNKNOWN and reading.mission_name == ""
    assert reading.identity_status == "unknown" and reading.candidates == ()
    assert reading.heist_phase is MissionType.UNKNOWN
    assert reading.keywords_found == [] and reading.raw_text == raw
    assert not reading.has_mission and not reading.is_active


@pytest.mark.parametrize("sources", [
    ("Go", "to El Rubio's compound"),
    ("Go to", "El Rubio's compound"),
    ("Go to El", "Rubio's compound"),
    ("Go to El Rubio's", "compound"),
    ("Go to El", "Rubio's", "compound."),
])
def test_phrase_fragments_cannot_cross_independent_crop_boundaries(sources):
    reading = MissionParser().parse_regions(sources)
    assert reading.raw_text == "\n".join(sources)
    assert reading.identity_status == "unknown" and not reading.has_mission
    assert reading.keywords_found == []
    # Those same bytes are a supported wrapped command only in one actual crop.
    assert MissionParser().parse(reading.raw_text).mission_type is MissionType.CAYO_PERICO


@pytest.mark.parametrize("label,kind,name", [
    ("Casino Heist", MissionType.CASINO_HEIST, ""),
    ("The Big Con", MissionType.CASINO_HEIST, "The Big Con"),
    ("Headhunter", MissionType.VIP_WORK, "Headhunter"),
    ("Cayo Perico", MissionType.CAYO_PERICO, ""),
])
def test_surrounding_explicit_labels_keep_their_prior_identity(label, kind, name):
    raw = COMMAND + "\n" + label
    reading = MissionParser().parse(raw)
    assert reading.mission_type is kind and reading.mission_name == name
    assert reading.identity_status == ("known_name" if name else "type_only")
    assert reading.raw_text == raw and EVIDENCE not in reading.keywords_found


@pytest.mark.parametrize("label,candidates", [
    ("Casino Heist", ("CASINO_HEIST", "CAYO_PERICO")),
    ("The Big Con", ("CAYO_PERICO", "The Big Con")),
    ("Headhunter", ("CAYO_PERICO", "Headhunter")),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_independent_conflicting_identity_remains_ambiguous(label, candidates, reverse):
    sources = (label, COMMAND) if reverse else (COMMAND, label)
    reading = MissionParser().parse_regions(sources)
    assert reading.identity_status == "ambiguous" and reading.candidates == candidates
    assert reading.mission_type is MissionType.UNKNOWN and reading.mission_name == ""
    assert reading.heist_phase is MissionType.UNKNOWN and not reading.is_active
    assert reading.raw_text == "\n".join(sources) and EVIDENCE in reading.keywords_found


@pytest.mark.parametrize("phase_text,phase", [
    ("Heist Prep", MissionType.HEIST_PREP),
    ("Finale", MissionType.HEIST_FINALE),
    ("Setup your business", MissionType.UNKNOWN),
])
def test_independent_explicit_phase_keeps_existing_semantics(phase_text, phase):
    reading = MissionParser().parse_regions((COMMAND, phase_text))
    assert reading.mission_type is MissionType.CAYO_PERICO
    assert reading.identity_status == "type_only" and reading.heist_phase is phase
    assert reading.mission_name == "" and reading.outcome is None


@pytest.mark.parametrize("result,outcome,scope", [
    ("HEIST PASSED", "complete", "heist"),
    ("MISSION PASSED", "complete", None),
    ("MISSION FAILED", "failed", None),
    ("MISSION PASSED\nMISSION FAILED", "conflicting", None),
])
def test_independent_result_evidence_retains_existing_parser_resolution(result, outcome, scope):
    # parse_regions has no source-role knowledge. The detector separately guards
    # bottom/result-header authority, as exercised below.
    parser = MissionParser()
    active = parser.parse(COMMAND)
    reading = parser.parse_regions((COMMAND, result))
    assert reading.mission_type is MissionType.CAYO_PERICO
    assert reading.outcome == outcome and reading.outcome_scope == scope
    assert reading.heist_phase is MissionType.UNKNOWN and not reading.is_active
    assert parser.get_last_reading() is active


@pytest.mark.parametrize("result,outcome,scope", [
    ("HEIST PASSED", None, "heist"),
    ("MISSION PASSED", "complete", None),
    ("MISSION FAILED", "failed", None),
])
def test_result_in_same_crop_prevents_distinctive_association(result, outcome, scope):
    raw = COMMAND + ".\n" + result
    reading = MissionParser().parse(raw)
    assert reading.identity_status == "unknown" and not reading.has_mission
    assert reading.outcome == outcome and reading.outcome_scope == scope
    assert reading.keywords_found == [] and reading.raw_text == raw


def test_last_active_reading_does_not_lend_identity_to_later_unknown_text():
    parser = MissionParser()
    active = parser.parse(COMMAND)
    assert active.mission_type is MissionType.CAYO_PERICO
    for text in ("El Rubio's compound", "Go to the compound", "HEIST PASSED"):
        reading = parser.parse(text)
        assert reading.identity_status == "unknown" and not reading.has_mission
        assert parser.get_last_reading() is active


def test_detector_retains_raw_bottom_text_and_only_real_command_evidence(monkeypatch):
    raw = "  GO TO EL\nRUBIO'S COMPOUND.  "
    result, _ = bottom_observation(monkeypatch, raw)
    assert (result.state, result.confidence) == (GameState.MISSION_ACTIVE, .8)
    assert result.bottom_objective_text == raw
    assert result.bottom_objective_command == "GO TO EL RUBIO'S COMPOUND"
    assert result.mission.raw_text == result.bottom_objective_command
    assert result.mission.objective == result.bottom_objective_command
    assert result.mission.keywords_found == [EVIDENCE]
    assert result.mission.mission_name == "" and result.mission.heist_phase is MissionType.UNKNOWN


@pytest.mark.parametrize("suffix", [
    " $", " €", " £", " ¥", " 1", " ١", "\nRP", "\nPlatinum", "\nContinue.",
    "\nPlayer Take", "\nTotal Take", "\nAgency", "\nGo to the location",
    ". MISSION PASSED", "\nHEIST PASSED", "\nMISSION FAILED",
])
def test_contaminated_bottom_command_is_rejected_without_identity(monkeypatch, suffix):
    result, _ = bottom_observation(monkeypatch, COMMAND + suffix)
    assert result.bottom_objective_text == COMMAND + suffix
    assert result.bottom_objective_command == ""
    assert result.mission is None
    assert result.state is GameState.UNKNOWN and result.confidence == 0.0


def test_bottom_association_cannot_override_primary_scoped_result(monkeypatch):
    result, calls = bottom_observation(monkeypatch, COMMAND, top="HEIST PASSED")
    assert result.state is GameState.UNKNOWN and result.confidence == 0.0
    assert result.mission.identity_status == "unknown" and result.mission.outcome is None
    assert result.mission.outcome_scope == "heist"
    assert result.bottom_objective_text == result.bottom_objective_command == ""
    assert len(calls) == 3


@pytest.mark.parametrize("header", ["HEIST PASSED", "HEIST\nPASSED",
                                  "HEIST PASSED\n" + COMMAND])
def test_bottom_association_cannot_qualify_an_independent_result_header(monkeypatch, header):
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    result, _, updates, _ = header_observation(
        monkeypatch, header, bottom=COMMAND + ".", template=template,
    )
    assert result.bottom_objective_command == COMMAND
    assert result.mission.mission_type is MissionType.CAYO_PERICO
    assert result.mission.outcome is None and result.mission.heist_phase is MissionType.UNKNOWN
    assert result.state is GameState.UNKNOWN and result.confidence == 0.0
    assert result.result_header_text == header and result.result_header_evidence == ""
    assert updates == [result]
