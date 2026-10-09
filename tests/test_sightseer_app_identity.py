"""The shared Sightseer phone app is not evidence of the VIP mission's name."""

import pytest

from src.detection.parsers.mission_parser import MissionParser, MissionReading, MissionType


APP_INSTRUCTION = "Use the Sightseer app to access the security cameras."


@pytest.mark.parametrize("text", [
    APP_INSTRUCTION,
    "Sightseer app",
    "  sIgHtSeEr\t APP  ",
    "Use the Sightseer\r\napp to access the security cameras.",
    "Use the Sightseer app to find the first drop-off.",
    "Use the Sightseer app to find the second drop-off.",
    "Use the Sightseer app to find the final drop-off.",
    "Use the Sightseer app to locate the package.",
    "Sightseer app\nOpen the Sightseer app on your phone.",
    'Use the "Sightseer" app to access the security cameras.',
    "Use the 'Sightseer' app to access the security cameras.",
    "Use the ‘Sightseer’ app to access the security cameras.",
    "Use the “Sightseer” app to access the security cameras.",
    'Use the "Sightseer app" to access the security cameras.',
    'Use the "Sightseer"\napp to access the security cameras.',
])
def test_app_references_do_not_establish_a_mission_identity(text):
    parser = MissionParser()
    previous = parser.parse("Headhunter")

    reading = parser.parse(text)

    assert getattr(reading, "sightseer_app_reference", None) is True
    assert reading.identity_status == "unknown"
    assert reading.mission_type is MissionType.UNKNOWN
    assert reading.mission_name == ""
    assert reading.candidates == ()
    assert reading.keywords_found == []
    assert not reading.has_mission
    assert not reading.is_active
    assert reading.outcome is None
    assert reading.raw_text == text
    assert parser.get_last_reading() is previous


@pytest.mark.parametrize("category,kind,phase", [
    ("Cayo Perico", MissionType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico prep: Gather intel", MissionType.CAYO_PERICO, MissionType.HEIST_PREP),
    ("Sell mission", MissionType.SELL_MISSION, MissionType.UNKNOWN),
    ("VIP work", MissionType.VIP_WORK, MissionType.UNKNOWN),
])
@pytest.mark.parametrize("separate_crops", [False, True])
def test_app_reference_leaves_explicit_category_evidence_intact(
    category, kind, phase, separate_crops,
):
    crops = (category, APP_INSTRUCTION)
    reading = MissionParser().parse_regions(crops if separate_crops else ("\n".join(crops),))

    assert reading.identity_status == "type_only"
    assert reading.mission_type is kind
    assert reading.heist_phase is phase
    assert reading.mission_name == ""
    assert reading.candidates == (kind.name,)
    assert "sightseer" not in reading.keywords_found
    assert reading.is_active


@pytest.mark.parametrize("text", [
    "Sightseer",
    'Mission: "SIGHTSEER"',
    f"Sightseer\n{APP_INSTRUCTION}",
    f"{APP_INSTRUCTION}\nSightseer",
    f"VIP WORK\nSightseer\nSIGHTSEER\n{APP_INSTRUCTION}",
    f"Sightseer | {APP_INSTRUCTION}",
    "Sightseer\nOpen the app on your phone.",
    "Sightseer appearance",
    "Sightseer applet",
    "Sightseer apps",
])
def test_independent_title_occurrences_remain_known_names(text):
    reading = MissionParser().parse(text)

    assert reading.identity_status == "known_name"
    assert reading.mission_type is MissionType.VIP_WORK
    assert reading.mission_name == "Sightseer"
    assert reading.candidates == ("Sightseer",)
    assert "sightseer" in reading.keywords_found
    assert reading.is_active


@pytest.mark.parametrize("name", ["Sightseer", "Headhunter"])
@pytest.mark.parametrize("banner,outcome", [
    ("MISSION PASSED", "complete"),
    ("MISSION FAILED", "failed"),
])
@pytest.mark.parametrize("title_first", [False, True])
def test_app_reference_preserves_the_named_result_owner(name, banner, outcome, title_first):
    result = f"{name} {banner}" if title_first else f"{banner} {name}"
    reading = MissionParser().parse(f"{result}\n{APP_INSTRUCTION}")

    assert reading.identity_status == "known_name"
    assert reading.mission_name == name
    assert reading.candidates == (name,)
    assert reading.outcome == outcome
    assert not reading.is_active


@pytest.mark.parametrize("banner,outcome", [
    ("MISSION PASSED", "complete"),
    ("MISSION FAILED", "failed"),
    ("MISSION PASSED\nMISSION FAILED", "conflicting"),
])
def test_app_name_does_not_own_a_separate_generic_result(banner, outcome):
    reading = MissionParser().parse(f"Sightseer app\n{banner}")

    assert reading.identity_status == "unknown"
    assert reading.mission_name == ""
    assert reading.outcome == outcome
    assert not reading.has_mission
    assert not reading.is_active


@pytest.mark.parametrize("other,candidates", [
    ("Headhunter", ("Headhunter", "Sightseer")),
    ("Cayo Perico", ("CAYO_PERICO", "Sightseer")),
])
@pytest.mark.parametrize("separate_crops", [False, True])
def test_true_conflicting_identity_remains_ambiguous(other, candidates, separate_crops):
    crops = ("Sightseer", APP_INSTRUCTION, other)
    reading = MissionParser().parse_regions(crops if separate_crops else ("\n".join(crops),))

    assert reading.identity_status == "ambiguous"
    assert reading.candidates == candidates
    assert reading.mission_type is MissionType.UNKNOWN
    assert reading.mission_name == ""
    assert not reading.has_mission
    assert not reading.is_active


@pytest.mark.parametrize("text", [
    "Sightseers app", "presightseer app", "Sightseer2 app", "Sightseer_app",
    "Sıghtseer app", "SİGHTSEER app", "Retrieve the package.", "Collect the packages.",
])
def test_partial_words_and_package_objectives_do_not_invent_the_name(text):
    reading = MissionParser().parse(text)

    assert reading.identity_status == "unknown"
    assert reading.mission_name == ""
    assert reading.keywords_found == []


@pytest.mark.parametrize("crops", [
    ("Sightseer", "app"),
    ('"Sightseer"', "app"),
    ("Sightseer", APP_INSTRUCTION),
    (APP_INSTRUCTION, "Sightseer"),
])
def test_app_qualification_cannot_cross_independent_crop_boundaries(crops):
    reading = MissionParser().parse_regions(crops)

    assert reading.identity_status == "known_name"
    assert reading.mission_name == "Sightseer"
    assert reading.mission_type is MissionType.VIP_WORK
    assert reading.candidates == ("Sightseer",)
    assert reading.raw_text == "\n".join(crops)


def test_name_fragments_are_not_joined_across_crops():
    reading = MissionParser().parse_regions(("Sight", "seer app"))

    assert reading.identity_status == "unknown"
    assert reading.mission_name == ""


@pytest.mark.parametrize("text", ["Use the Sightseer app.", "Retrieve the package."])
def test_app_filter_does_not_expand_the_objective_verb_policy(text):
    assert MissionParser().parse(text).objective == ""


def test_app_reference_metadata_defaults_to_false():
    assert getattr(MissionReading(), "sightseer_app_reference", None) is False


@pytest.mark.parametrize("crops", [
    (),
    ("",),
    ("Sightseer",),
    ("Sightseer", "app"),
    ('"Sightseer"', "app"),
    ("Sight", "seer app"),
    ("Sightseer appearance",),
    ("Sightseer applet",),
    ("Sightseer app2",),
    ("Sightseer_app",),
    ("Sightseers app",),
    ("presightseer app",),
    ("Sıghtseer app",),
    ("SİGHTSEER app",),
    ("Headhunter", "Open the app on your phone."),
    ("Collect the packages.",),
])
def test_app_reference_metadata_requires_the_exact_qualifier_in_one_crop(crops):
    reading = MissionParser().parse_regions(crops)

    assert getattr(reading, "sightseer_app_reference", None) is False


@pytest.mark.parametrize("identity,status", [
    ("Sightseer", "known_name"),
    ("Headhunter", "known_name"),
    ("Cayo Perico", "type_only"),
])
@pytest.mark.parametrize("app_first", [False, True])
@pytest.mark.parametrize("separate_crops", [False, True])
def test_app_reference_metadata_coexists_with_independent_identity(
    identity, status, app_first, separate_crops,
):
    crops = (APP_INSTRUCTION, identity) if app_first else (identity, APP_INSTRUCTION)
    reading = MissionParser().parse_regions(crops if separate_crops else ("\n".join(crops),))

    assert getattr(reading, "sightseer_app_reference", None) is True
    assert reading.identity_status == status
    assert reading.mission_name == (identity if status == "known_name" else "")
    assert reading.is_active


def test_app_reference_metadata_is_not_carried_from_previous_observations():
    parser = MissionParser()

    app_reading = parser.parse(f"Sightseer\n{APP_INSTRUCTION}")
    assert getattr(app_reading, "sightseer_app_reference", None) is True
    assert parser.get_last_reading() is app_reading

    unrelated = parser.parse("Open the app on your phone.")
    assert getattr(unrelated, "sightseer_app_reference", None) is False
    assert parser.get_last_reading() is app_reading

    title = parser.parse("Sightseer")
    assert getattr(title, "sightseer_app_reference", None) is False
    assert parser.get_last_reading() is title
