"""Pure evidence contracts independent of parser display and mutable activities."""

from dataclasses import FrozenInstanceError

import pytest

from src.detection.mission_episode import (
    MissionIdentity, ObjectiveEvidence, TerminalMissionEpisode, objective_evidence,
)
from src.detection.parsers.mission_parser import MissionParser, MissionType


@pytest.mark.parametrize("text", [
    "Headhunter\nEliminate the targets\nMISSION PASSED",
    "VIP\nWORK\nEliminate the targets\nMISSION\nPASSED",
    "Eliminate the targets | MISSION PASSED",
    "Eliminate the targets: MISSION PASSED +$25,000",
    "Eliminate\nthe targets",
    " ELIMINATE  THE   TARGETS. ",
])
def test_wrapping_and_recognized_labels_preserve_normalized_objective(text):
    assert objective_evidence((text,)).entries == {"eliminate the targets"}


@pytest.mark.parametrize("texts", [
    ("If you eliminate the targets, you earn money",),
    ("If you\neliminate the targets, you earn money",),
    ("You must eliminate the targets",),
    ("Go", "to the location"),
    ("Eliminate", "the targets"),
    ("Go to",), ("Eliminate the",), ("Hunt the Beast",),
])
def test_explanations_and_independent_fragments_supply_no_objective(texts):
    assert objective_evidence(texts) == ObjectiveEvidence()


@pytest.mark.parametrize("old,new", [
    (("Go to the location\nEliminate the targets",), ("Go to the location Eliminate the targets",)),
    (("Go to the location Eliminate the targets",), ("Eliminate the targets",)),
    (("Go to the location Eliminate the targets",), ("Eliminate the targets\nGo to the location",)),
    (("Go to the location", "Eliminate the targets"), ("Eliminate the targets",)),
])
def test_multiple_commands_reorder_coalesce_or_disappear_without_freshness(old, new):
    assert not objective_evidence(old).has_new(objective_evidence(new))


def test_parser_display_objective_is_independent():
    text = "Headhunter\nEliminate the targets | MISSION PASSED"
    reading = MissionParser().parse(text)
    assert objective_evidence((text,)).entries == {"eliminate the targets"}
    assert reading.objective == "Eliminate the targets | MISSION PASSED"


def test_entries_are_bounded_and_overflow_cannot_become_freshness():
    evidence = objective_evidence((f"Collect package {index}" for index in range(128)))
    assert len(evidence.entries) == 128 and not evidence.overflow
    assert evidence.has_new(objective_evidence(("Collect another package",)))
    overflow = evidence.include(objective_evidence(("Collect another package",)))
    assert len(overflow.entries) == 128 and overflow.overflow
    assert overflow.entries == evidence.entries  # Never evict earlier observations.
    assert not overflow.has_new(objective_evidence(("Eliminate the targets",)))
    assert overflow.include(ObjectiveEvidence()).overflow
    huge = objective_evidence((f"Collect package {index}" for index in range(10000)))
    assert len(huge.entries) == 128 and huge.overflow


def test_identity_and_evidence_are_immutable_values():
    identity = MissionIdentity("Headhunter", MissionType.VIP_WORK)
    episode = TerminalMissionEpisode(identity, objective_evidence(("Go to the location",)))
    with pytest.raises(FrozenInstanceError):
        identity.name = "Sightseer"
    with pytest.raises(FrozenInstanceError):
        episode.identity = MissionIdentity()
    assert not hasattr(episode.objectives.entries, "add")


@pytest.mark.parametrize("other,shared,compatible", [
    (MissionIdentity(), False, True),
    (MissionIdentity(family=MissionType.VIP_WORK), True, True),
    (MissionIdentity("Sightseer", MissionType.VIP_WORK), False, False),
    (MissionIdentity(family=MissionType.SECURITY_CONTRACT), False, False),
    (MissionIdentity(phase=MissionType.HEIST_PREP), False, False),
])
def test_identity_compatibility_is_not_equality(other, shared, compatible):
    identity = MissionIdentity("Headhunter", MissionType.VIP_WORK)
    assert identity.compatible(other) is compatible
    assert identity.shares_identity(other) is shared


@pytest.mark.parametrize("suffix", [
    "\nMISSION PASSED\n$25000", " MISSION PASSED +$25,000",
    "\nMISSION\nFAILED\n25000", "\nIf you deliver them you earn money",
    " If you deliver them you earn money", "\nYou earn money for completing it",
])
def test_result_payout_or_explanation_suffix_loss_cannot_fabricate_freshness(suffix):
    old = objective_evidence(("Eliminate the targets" + suffix,))
    assert old.entries == {"eliminate the targets"}
    assert not old.has_new(objective_evidence(("Eliminate the targets",)))


@pytest.mark.parametrize("text", [
    "Deliver the customer vehicle", "Deliver any customer vehicle",
    "Deliver the\ncustomer vehicle", "Go to Cayo Perico", "Go to the auto shop",
])
def test_known_labels_inside_complete_commands_are_kept_as_objectives(text):
    evidence = objective_evidence((text,))
    assert evidence.entries == {" ".join(text.casefold().split())}
    assert ObjectiveEvidence().has_new(evidence)


def test_shorter_observation_is_not_new_just_because_trailing_text_disappeared():
    old = objective_evidence(("Eliminate the targets near the warehouse",))
    assert not old.has_new(objective_evidence(("Eliminate the targets",)))


def test_unseparated_title_suffix_loss_is_conservatively_old_evidence():
    old = objective_evidence(("Eliminate the targets Headhunter",))
    assert not old.has_new(objective_evidence(("Eliminate the targets",)))


@pytest.mark.parametrize("parts", [
    ("Take out", "the targets"), ("Wait for", "the signal"),
    ("Go to", "the location"), ("Pick up", "the package"),
    ("Drop off", "the goods"), ("Take", "out the targets"),
])
def test_longest_verb_and_preposition_require_their_own_crop_object(parts):
    assert objective_evidence(parts) == ObjectiveEvidence()


@pytest.mark.parametrize("footer", [
    "JP +15\nRP +2500", "You earned $25000", "+$25,000 TOTAL",
])
@pytest.mark.parametrize("position", ["before", "after"])
def test_result_boundary_footer_and_label_dropout_preserve_all_commands(footer, position):
    command = "Eliminate the targets"
    old = (command + "\nMISSION PASSED\n" + footer if position == "before"
           else "MISSION PASSED\n" + command + "\n" + footer)
    evidence = objective_evidence((old,))
    for new in (command, command + "\n" + footer, command + "\n+$99 TOTAL"):
        assert not evidence.has_new(objective_evidence((new,)))
    assert evidence.has_new(objective_evidence(("Go to the airport",)))


@pytest.mark.parametrize("old,new", [
    ("Eliminate the targets\nHeadhunter", "Eliminate the targets Headhunter"),
    ("Eliminate the targets", "Eliminate the targets VIP Work"),
    ("Eliminate the targets", "Eliminate the targets near the warehouse"),
])
def test_objective_extension_alone_is_conservatively_not_fresh(old, new):
    assert not objective_evidence((old,)).has_new(objective_evidence((new,)))


def test_commands_on_both_sides_of_result_boundary_are_remembered():
    evidence = objective_evidence(("Go to the location\nMISSION PASSED\nEliminate the targets",))
    assert not evidence.has_new(objective_evidence(("Go to the location", "Eliminate the targets")))


def test_reward_footer_does_not_hide_a_later_objective_in_same_crop():
    evidence = objective_evidence((
        "Go to the location\nMISSION PASSED\nJP +15\nRP +2500\nEliminate the targets",
    ))
    assert not evidence.has_new(objective_evidence(("Go to the location", "Eliminate the targets")))


@pytest.mark.parametrize("text", ["Take out the targets", "Wait for the signal", "Collect\n$25000"])
def test_complete_commands_with_multiword_verbs_or_numeric_objects_remain_fresh(text):
    assert ObjectiveEvidence().has_new(objective_evidence((text,)))


def test_title_before_wrapped_numeric_command_does_not_turn_its_object_into_footer():
    evidence = objective_evidence(("Headhunter\nCollect\n$25000",))
    assert evidence.entries == {"collect $25000"}
