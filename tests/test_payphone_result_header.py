"""Header-only Payphone Hit grammar, with injected text and no OCR execution."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionParser, MissionReading, MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_result_header_admission import observation
from tests.test_vip_status_admission import detect


TECH = "The Tech Entrepreneur"
COFOUNDER = "The Cofounder"
HEADER = "hit COMPLEGE\n" + TECH


def header_reading(parser, raw):
    # On the prerequisite baseline, assert the absent semantics rather than
    # failing with a missing-method exception.
    return getattr(parser, "parse_result_header", parser.parse)(raw)


def result(raw=HEADER, *, reading=None, evidence=None, state=GameState.MISSION_COMPLETE, **kwargs):
    return StateDetectionResult(
        state, .85, "synthetic admitted header", mission=reading,
        result_header_text=raw, result_header_evidence=raw if evidence is None else evidence,
        **kwargs,
    )


@pytest.mark.parametrize("name", [TECH, COFOUNDER, "The Popstar"])
@pytest.mark.parametrize("label", ["hit complete", "hit COMPLEGE"])
@pytest.mark.parametrize("layout", ["{}\n{}", " \t{} \t\r\n\t{} \t\r\n"])
def test_whole_header_selects_existing_name_and_never_caches_an_active_result(name, label, layout):
    parser = MissionParser()
    active = parser.parse("Headhunter")
    raw = layout.format(label, name)
    reading = header_reading(parser, raw)
    assert (reading.mission_name, reading.mission_type, reading.identity_status) == (
        name, MissionType.PAYPHONE_HIT, "known_name",
    )
    assert (reading.outcome, reading.outcome_scope, reading.is_active) == ("complete", "payphone_hit", False)
    assert reading.heist_phase is MissionType.UNKNOWN
    assert reading.raw_text == raw
    assert parser._last_reading is active


MALFORMED = [
    "hit complete", "hit complege", "hit complete\nThe Tech", "hit complege\nEntrepreneur",
    "hit complete\nHeadhunter", "hit complete\nPayphone Hit", "hit complete\nThe Cofounder!",
    "hit complete\nThe Tech\nEntrepreneur", "hit\ncomplete\nThe Cofounder",
    "hit complete The Cofounder", "The Cofounder\nhit complete", "hit completed\nThe Cofounder",
    "hit completc\nThe Cofounder", "hit complete.\nThe Cofounder", "not hit complete\nThe Cofounder",
    "If hit complete appears, continue\nThe Cofounder", "Assassination Bonus: COMPLETE\nThe Cofounder",
    "+$85000", "hit complete\nThe Cofounder\n+$85000", "hit complete\nThe Cofounder\nVIP WORK",
    "hit complete\nThe Cofounder\nHeist Finale", "hit complete\nThe Cofounder\nThe Tech Entrepreneur",
    "hit complete\nThe Cofounder\nMISSION FAILED", "hit complete\nThe Cofounder\nHEIST PASSED",
]


@pytest.mark.parametrize("raw", MALFORMED)
def test_header_grammar_never_admits_unobserved_forms_or_borrowed_identity(raw):
    reading = header_reading(MissionParser(), raw)
    assert reading.outcome != "complete"
    assert not reading.is_active


@pytest.mark.parametrize("raw", ["hit complete", "hit complege\nThe Tech", "hit complete\nHeadhunter"])
def test_complete_but_unqualified_label_vetoes_active_template_and_independent_name(monkeypatch, raw):
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    observed, _, _, _ = observation(monkeypatch, raw, top=TECH, template=template)
    assert observed.state is GameState.UNKNOWN and observed.confidence == 0
    assert observed.result_header_evidence == "" and observed.result_header_text == raw
    assert observed.mission.outcome_scope == "payphone_hit"
    assert not observed.mission.is_active


@pytest.mark.parametrize("raw", [HEADER, "hit complete\n" + COFOUNDER])
def test_qualified_header_alone_can_override_controlled_menu_cue(monkeypatch, raw):
    # This is a controlled visual branch, not a new evaluation of source pixels.
    quick = StateDetectionResult(GameState.MENU, .7, "controlled menu cue")
    observed, calls, updates, _ = observation(monkeypatch, raw, quick=quick)
    assert observed.state is GameState.MISSION_COMPLETE
    assert observed.result_header_text == observed.result_header_evidence == raw
    assert observed.mission.outcome_scope == "payphone_hit"
    assert updates == [observed]
    assert len(calls) == 4 and calls[-1] == (raw, {"threshold": False, "invert": False, "scale": 2.0})


@pytest.mark.parametrize("top", ["Bunker Stock 40% Supplies 60% Value $7000", "MISSION PASSED",
    "MISSION FAILED", "HEIST PASSED", "Headhunter\nSightseer"])
def test_primary_business_result_and_ambiguity_remain_protected(monkeypatch, top):
    old, calls, _, _ = observation(monkeypatch, top=top)
    new, actual_calls, _, _ = observation(monkeypatch, HEADER, top=top)
    assert asdict(new) == asdict(old) and calls == actual_calls


@pytest.mark.parametrize("name", ["business_computer", "mission_passed", "mission_failed"])
def test_protected_template_keeps_existing_unread_header_policy(monkeypatch, name):
    template = SimpleNamespace(matched=True, confidence=.99, template_name=name)
    old, calls, _, _ = observation(monkeypatch, template=template)
    new, actual_calls, _, _ = observation(monkeypatch, HEADER, template=template)
    assert asdict(new) == asdict(old) and calls == actual_calls


@pytest.mark.parametrize("source", ["top", "center", "banner", "bottom", "status"])
def test_nonheader_alias_has_no_new_result_authority_and_primary_titles_keep_baseline_behavior(source):
    arguments = {source: HEADER}
    observed, _ = detect(arguments.pop("status", ""), **arguments)
    assert observed.state is not GameState.MISSION_COMPLETE
    assert observed.mission is None or (observed.mission.outcome is None and observed.mission.outcome_scope is None)
    if source in ("top", "center", "banner"):
        assert observed.state is GameState.MISSION_ACTIVE
        assert observed.mission.mission_name == TECH


@pytest.mark.parametrize("primary", [COFOUNDER, "Headhunter", "Heist Finale", "Payphone Hit\nHeist Prep"])
def test_qualified_header_cannot_override_independent_identity_conflicts(monkeypatch, primary):
    observed, _, _, _ = observation(monkeypatch, HEADER, top=primary)
    assert observed.state is GameState.UNKNOWN and observed.result_header_evidence == ""


def test_prose_or_bonus_does_not_suppress_separate_true_failure(monkeypatch):
    for raw in ("If hit complete appears, continue", "Assassination Bonus: COMPLETE"):
        observed, _, _, _ = observation(monkeypatch, raw, top="MISSION FAILED\n" + TECH)
        assert observed.state is GameState.MISSION_FAILED


def test_direct_fallback_preserves_header_role_and_excludes_header_objectives(app):
    reading = app._mission_reading(result())
    assert (reading.mission_name, reading.outcome, reading.outcome_scope) == (TECH, "complete", "payphone_hit")
    assert not app._objective_evidence(result(raw=HEADER + "\nGo to the location")).entries
    ignored = app._mission_reading(result(evidence=""))
    assert ignored.identity_status == "unknown" and ignored.outcome_scope is None


@pytest.mark.parametrize("case", ["missing_evidence", "mismatched_raw", "forged_name", "forged_scope", "active"])
def test_direct_cached_reading_cannot_bypass_independent_header_validation(app, case):
    reading = MissionReading(mission_type=MissionType.PAYPHONE_HIT, mission_name=TECH,
        identity_status="known_name", candidates=(TECH,), outcome="complete", outcome_scope="payphone_hit",
        keywords_found=[TECH.casefold()], raw_text=HEADER)
    observed = result(reading=reading)
    if case == "missing_evidence":
        observed.result_header_evidence = ""
    elif case == "mismatched_raw":
        observed.result_header_text = "hit complete"
    elif case == "forged_name":
        observed.mission = replace(reading, mission_name=COFOUNDER)
    elif case == "forged_scope":
        observed.mission = replace(reading, outcome_scope=None)
    else:
        observed.state = GameState.MISSION_ACTIVE
    captured = CaptureResult()
    app._process_state(observed, captured)
    assert captured.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert app._data.terminal_mission_episode is None


def test_mixed_scopes_and_explicit_failure_fail_closed_without_fuzzy_aliases():
    parser = MissionParser()
    method = getattr(parser, "parse_regions")
    if not hasattr(parser, "parse_result_header"):
        reading = method(("HEIST PASSED\nCayo Perico", HEADER))
    else:
        reading = method(("HEIST PASSED\nCayo Perico",), result_header=HEADER)
    assert reading.outcome == "conflicting" and not reading.is_active
