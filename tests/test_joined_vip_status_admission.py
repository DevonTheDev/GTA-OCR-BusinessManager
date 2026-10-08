"""Exact joined-footer admission through the detector, using synthetic OCR.

The saved Sightseer OCR is evidence of word-gap loss, not a readable title or
timer. These checks establish text admission only, never gameplay accuracy.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.detection.parsers.mission_parser import MissionType
from src.game.state_machine import GameState
from tests.test_vip_status_admission import ACTUAL_SIGHTSEER, MARKER, detect


JOINED_ROW = "VIPWORKEND 0:00"


@pytest.mark.parametrize("raw", [
    ACTUAL_SIGHTSEER, JOINED_ROW, "vipworkend 11:324", "ViPwOrKeNd\t1273.",
    " \tVIPWORKEND \t12:30\t ", "VIPWORKEND 0", "VIPWORKEND 1:..::",
    "VIPWORKEND 1234567890123456",
    "PACKAGES REMAINING 1\r\nVIPWORKEND 11:324\r\n",
])
def test_joined_row_admits_only_unnamed_active_vip_category(raw):
    result, calls = detect(raw)

    assert result.state is GameState.MISSION_ACTIVE
    assert result.confidence == .8
    assert result.vip_status_text == raw
    assert result.vip_status_evidence == MARKER
    assert result.mission.mission_type is MissionType.VIP_WORK
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_name == result.mission.objective == ""
    assert result.mission.heist_phase is MissionType.UNKNOWN
    assert result.mission.is_active
    assert result.mission.outcome is result.mission.outcome_scope is None
    assert result.mission.raw_text == MARKER
    assert result.bottom_objective_command == result.result_header_evidence == ""
    assert not result.timer_visible
    assert [call for call in calls if call[0] == "status"] == [
        ("status", {"threshold": False, "invert": True, "scale": 2.0}),
    ]


@pytest.mark.parametrize("raw", [
    "VIPWORKEND", "VIPWORKEND \t", "VIPWORK END 12:30", "VIP WORKEND 12:30",
    "VIPWORKEND11:324", "VIPWORKEND 12:30abc", "VIPWORKEND: 12:30",
    "VIPWORKEND $5000", "VIPWORKEND +$5000", "VIPWORKEND 5,000",
    "VIPWORKEND 5000 dollars", "VIPWORKEND Take 5000",
    "VIPWORKEND 12:30 Collect the package", "VIPWORKEND 12:30 | MISSION PASSED",
    "VIPWORKEND 12:30 MISSION FAILED", "The VIPWORKEND 12:30 label",
    "Show VIPWORKEND 12:30", "XVIPWORKEND 12:30", "VIPWORKENDX 12:30",
    "[VIPWORKEND 12:30]", "VIPWORKENDING 12:30", "VIPWORKENDS 12:30",
    "V1PWORKEND 12:30", "VIPW0RKEND 12:30", "VIPWORKEN 12:30",
    "VIP\nWORKEND 12:30", "VIPWORK\nEND 12:30", "VIPWORKEND\n12:30",
    "VIPWORKEND\r12:30", "VIPWORKEND\v12:30", "VIPWORKEND\f12:30",
    "VİPWORKEND 12:30", "VıPWORKEND 12:30", "VIPWORKEND 12:30",
    "VIPWORKEND\u00a012:30", "VIPWORKEND\u200312:30", "VIPWORKEND １２:３０",
    "VIPWORKEND ١٢:٣٠", "VIPWORKEND 12：30", "VIPWORKEND 12:30\u00a0",
    "\u00a0VIPWORKEND 12:30", "VIPWORKEND 12:30\x00", "VIPWORKEND .12:30",
    "VIPWORKEND :12:30", "VIPWORKEND -12:30", "VIPWORKEND 12: 30",
    "VIPWORKEND 12345678901234567", "VIPWORKEND " + "1" * 129,
    "\ud800\nVIPWORKEND 12:30", "VIPWORKEND 12:30\n\udfff",
])
def test_nearby_joined_forms_have_no_footer_authority(raw):
    result, calls = detect(raw)

    assert result.state not in (GameState.MISSION_ACTIVE, GameState.MISSION_COMPLETE,
                                GameState.MISSION_FAILED)
    assert result.vip_status_text == raw
    assert result.vip_status_evidence == ""
    assert result.mission is None or result.mission.identity_status == "unknown"
    if result.mission is not None:
        assert result.mission.mission_name == result.mission.objective == ""
        assert result.mission.outcome is result.mission.outcome_scope is None
    assert result.bottom_objective_command == result.result_header_evidence == ""
    assert [name for name, _ in calls].count("status") == 1


@pytest.mark.parametrize("at_limit,over_limit", [
    ("\n".join(["noise"] * 7 + [JOINED_ROW]),
     "\n".join(["noise"] * 8 + [JOINED_ROW])),
    (JOINED_ROW.ljust(128), JOINED_ROW.ljust(129)),
    ("é" * 64 + "\n" + JOINED_ROW, "é" * 64 + "x\n" + JOINED_ROW),
    ("\n".join(["x" * 128] * 3 + ["x" * (512 - 388 - len(JOINED_ROW)), JOINED_ROW]),
     "\n".join(["x" * 128] * 3 + ["x" * (512 - 388 - len(JOINED_ROW)), JOINED_ROW]) + "\n"),
    ("\n".join(["é" * 64] * 3 + ["é" * 54 + "x", JOINED_ROW]),
     "\n".join(["é" * 64] * 3 + ["é" * 54 + "xx", JOINED_ROW])),
], ids=["eight_rows", "ascii_row_bytes", "utf8_row_bytes", "ascii_total_bytes",
        "utf8_total_bytes"])
def test_joined_row_keeps_exact_existing_line_and_byte_budgets(at_limit, over_limit):
    accepted, _ = detect(at_limit)
    rejected, _ = detect(over_limit)

    assert accepted.state is GameState.MISSION_ACTIVE
    assert accepted.vip_status_evidence == MARKER
    assert rejected.state is GameState.UNKNOWN
    assert rejected.vip_status_evidence == ""
    assert rejected.vip_status_text == over_limit


@pytest.mark.parametrize("source", ["top", "center", "banner", "bottom", "header"])
def test_joined_label_in_any_other_source_cannot_supply_vip_category(source):
    result, _ = detect("", **{source: "VIPWORKEND 11:324"})

    assert result.state is GameState.UNKNOWN
    assert result.vip_status_evidence == ""
    assert result.mission is None or result.mission.identity_status == "unknown"
    assert result.bottom_objective_command == result.result_header_evidence == ""


@pytest.mark.parametrize("source", ["top", "center", "banner", "bottom", "header"])
def test_joined_label_cannot_borrow_numeric_suffix_from_another_source(source):
    result, _ = detect("VIPWORKEND", **{source: "11:324"})

    assert result.state is GameState.UNKNOWN
    assert result.vip_status_evidence == ""
    assert result.mission is None or result.mission.identity_status == "unknown"


@pytest.mark.parametrize("top,state", [
    ("Bunker Stock 40% Supplies 60% Value $7000", GameState.BUSINESS_COMPUTER),
    ("MISSION PASSED", GameState.MISSION_COMPLETE),
    ("MISSION FAILED", GameState.MISSION_FAILED),
    ("HEIST PASSED", GameState.UNKNOWN),
    ("Headhunter\nCayo Perico", GameState.UNKNOWN),
])
def test_joined_row_cannot_change_protected_primary_evidence(top, state):
    result, calls = detect(ACTUAL_SIGHTSEER, top=top)
    without_footer, without_calls = detect(None, top=top)

    assert result == without_footer
    assert result.state is state
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert calls == without_calls
    assert not any(name == "status" for name, _ in calls)


@pytest.mark.parametrize("name", ["mission_passed", "mission_failed", "business_laptop"])
def test_joined_row_cannot_change_protected_template(name):
    template = SimpleNamespace(matched=True, confidence=.99, template_name=name)
    result, calls = detect(ACTUAL_SIGHTSEER, template=template)
    without_footer, without_calls = detect(None, template=template)

    assert result == without_footer
    assert calls == without_calls
    assert not any(name == "status" for name, _ in calls)


def test_independent_business_fields_remain_protected_under_active_template():
    template = SimpleNamespace(matched=True, confidence=.99, template_name="mission_banner")
    kwargs = dict(top="Bunker Stock 40% Supplies 60% Value $7000", template=template)
    result, calls = detect(ACTUAL_SIGHTSEER, **kwargs)
    without_footer, without_calls = detect(None, **kwargs)

    assert result == without_footer
    assert calls == without_calls
    assert result.vip_status_text == result.vip_status_evidence == ""


@pytest.mark.parametrize("source", ["top", "center", "banner", "bottom"])
def test_joined_row_keeps_conflicting_independent_family_ambiguous(source):
    other_family = "Escape Cayo Perico" if source == "bottom" else "Cayo Perico"
    result, _ = detect(ACTUAL_SIGHTSEER, **{source: other_family})

    assert result.state is GameState.UNKNOWN
    assert result.confidence == 0
    assert result.vip_status_evidence == MARKER
    assert result.mission.identity_status == "ambiguous"
    assert set(result.mission.candidates) == {"CAYO_PERICO", "VIP_WORK"}


@pytest.mark.parametrize("header", ["HEIST PASSED", "HEIST PASSED\nThe Cayo Perico Heist"])
def test_independent_result_header_still_vetoes_joined_category(header):
    result, calls = detect(ACTUAL_SIGHTSEER, header=header)

    assert result.state is GameState.UNKNOWN
    assert result.confidence == 0
    assert result.vip_status_evidence == MARKER
    assert result.result_header_text == header
    assert result.result_header_evidence == ""
    assert [name for name, _ in calls][-2:] == ["status", "header"]


def test_adjacent_footer_rows_stay_untrusted_when_joined_row_is_admitted():
    raw = ("Sightseer\nHeadhunter\nMISSION PASSED\nEscape Cayo Perico\n"
           "Collect the package\nBunker Stock 40% Supplies 60% Value $7000\n"
           "TAKE $5000\n" + JOINED_ROW)
    result, calls = detect(raw)
    category_only, category_calls = detect(JOINED_ROW)

    assert result == replace(category_only, vip_status_text=raw)
    assert calls == category_calls
    assert result.state is GameState.MISSION_ACTIVE
    assert result.mission.raw_text == MARKER
    assert result.mission.mission_name == result.mission.objective == ""
    assert result.mission.outcome is result.mission.outcome_scope is None


@pytest.mark.parametrize("raw,available", [(None, True), (ACTUAL_SIGHTSEER, False)])
def test_missing_footer_or_unavailable_ocr_never_creates_joined_category(raw, available):
    result, calls = detect(raw, available=available)

    assert result.state is GameState.UNKNOWN
    assert result.vip_status_text == result.vip_status_evidence == ""
    assert not any(name == "status" for name, _ in calls)
