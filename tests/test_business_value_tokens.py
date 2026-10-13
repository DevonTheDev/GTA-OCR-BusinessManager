"""Bounded recognized-text checks; capture/OCR are synthetic, parsing is real."""

import pytest

from src.app import GTABusinessManager
from src.config.settings import Settings
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from src.game.business_readings import normalize_live_business_reading
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


ASSESSED_INVALID_VALUES = [
    "Value:$0.50", "Sell:$123,456.78", "Worth:25e2", "Value:$25E+2",
    "Sell:25e-2", "Worth:$25.e2", "-$25 worth", "+$25 value",
]


@pytest.mark.parametrize("text", ASSESSED_INVALID_VALUES)
def test_assessed_partial_or_signed_value_is_unknown(text):
    reading = BusinessParser().parse(text)
    assert reading.stock_value is None
    assert not reading.has_data
    assert reading.business_type is BusinessType.UNKNOWN
    assert reading.raw_text == text


@pytest.mark.parametrize("text,expected", [
    ("Value:$0", 0), ("Sell:123,456", 123456), ("Worth25", 25),
    ("$25value", 25), ("$25 worth", 25), ("Value:$1,25", 125),
    ("Value:$25.", 25), ("Value:$25each", 25), ("Worth:25.eaches", 25),
    ("vAlUe:\t$ ٢٥", 25), ("$２５worth", 25), ("Sell:25e_name", 25),
    ("- $25 worth", 25), ("−$25 worth", 25),
])
def test_existing_integer_punctuation_and_word_controls(text, expected):
    reading = BusinessParser().parse(text)
    assert reading.stock_value == expected
    assert reading.has_data
    assert reading.raw_text == text


@pytest.mark.parametrize("label", ["Value", "Sell", "Worth"])
@pytest.mark.parametrize("suffix", [
    ".50", "e2", "E+2", "e-2", ".e2", "e", "e+", "E-", ".e", ".E+", "e!",
])
def test_leading_labels_reject_decimal_and_complete_or_incomplete_exponents(label, suffix):
    reading = BusinessParser().parse(f"{label}:$25{suffix}")
    assert reading.stock_value is None
    assert not reading.has_data


@pytest.mark.parametrize("text", [
    "$25.50 worth", "$25e2 value", "$25.e2 worth", "$25e value",
    "$+25 worth", "$-25value", "Value:-$25", "Sell:+$25",
])
def test_previously_rejected_value_forms_remain_unknown(text):
    assert BusinessParser().parse(text).stock_value is None


@pytest.mark.parametrize("text", ["-$ 25 worth", "+$ 25 value"])
def test_reverse_sign_rejection_allows_space_after_the_dollar(text):
    assert BusinessParser().parse(text).stock_value is None


@pytest.mark.parametrize("text,expected", [
    ("Value:$25.50 Value:$75", None),
    ("Sell:$25e2 Sell:$75", None),
    ("Worth:$25.e2 Worth:$75", None),
    ("-$25 worth; $75 worth", None),
    ("+$25 value; $75 value", None),
    ("-$25 worth $75 worth", 75),
    ("+$25 value $75 value", 75),
    ("Value:$25.50 Sell:$40", 40),
    ("Value:$25.50 $35 worth Sell:$40", 35),
    ("-$25 worth Sell:$40", 40),
    ("Sell:$25e2 Worth:$45", 45),
    ("Worth:$40 Sell:$30 $20 worth Value:$10", 10),
    ("Worth:$40 Sell:$30 $20 worth", 20),
    ("Worth:$40 Sell:$30", 30),
])
def test_rejection_keeps_first_match_selection_and_existing_pattern_precedence(text, expected):
    assert BusinessParser().parse(text).stock_value == expected


@pytest.mark.parametrize("value", [2**63 - 1, 2**63])
def test_parser_magnitude_and_downstream_limit_stay_separate(value):
    reading = BusinessParser().parse(f"Bunker Value:${value:,}")
    assert reading.stock_value == value
    assert reading.has_data
    if value == 2**63 - 1:
        assert normalize_live_business_reading("bunker", None, None, value) == (None, None, value)
    else:
        with pytest.raises(ValueError, match="Value must be a whole number"):
            normalize_live_business_reading("bunker", None, None, value)


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


def seed_business_readings(manager):
    for label, business_id in [("Bunker", "bunker"), ("Acid Lab", "acid_lab")]:
        manager.update_business_state(business_id, 75, 50, 900)
        manager._business_parser.parse(f"{label} Stock: 75% Supplies: 50% Value: $900")


@pytest.mark.parametrize("text", ASSESSED_INVALID_VALUES)
@pytest.mark.parametrize("target", [None, "bunker"])
def test_value_only_rejection_preserves_identified_and_unrelated_live_caches(manager, text, target):
    seed_business_readings(manager)
    manager.set_business_screen_target(target)
    before = business_snapshot(manager)
    previous = manager._business_parser.get_last_reading(BusinessType.BUNKER)
    name = "Bunker" if target is None else ""

    SyntheticBusinessCapture(manager, [(name, "", text)]).run()

    assert business_snapshot(manager) == before
    assert manager._business_parser.get_last_reading(BusinessType.BUNKER) is previous


@pytest.mark.parametrize("frame,expected,estimated_value", [
    (("Bunker Stock: 0%", "", "Value:$0.50"), (0, None, None), 0),
    (("Bunker", "Supplies: 0%", "Worth:25e2"), (None, 0, None), None),
    (("Bunker Stock: 50%", "Supplies: 25%", "-$25 worth"), (50, 25, None), 525000),
    (("Bunker Stock: 50%", "", "Value:$0"), (50, None, 0), 0),
    (("Bunker", "", "Value:$0"), (None, None, 0), 0),
])
def test_companions_and_explicit_zero_preserve_observations_and_estimates(
    manager, frame, expected, estimated_value,
):
    seed_business_readings(manager)
    before_app, before_optimizer, before_parser = business_snapshot(manager)

    SyntheticBusinessCapture(manager, [frame]).run()

    state = manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == expected
    assert state["identity_source"] == "ocr_text"
    optimized = manager._optimizer._business_states["bunker"]
    assert (optimized.stock_percent, optimized.supply_percent, optimized.observed_value) == expected
    assert optimized.estimated_value == estimated_value
    reading = manager._business_parser.get_last_reading(BusinessType.BUNKER)
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == expected
    assert reading.raw_text == " ".join(part for part in frame if part)
    assert reading.has_data
    assert set(manager.data.business_states) == set(before_app)
    assert manager.get_business_state("acid_lab") == before_app["acid_lab"]
    assert manager._optimizer._business_states["acid_lab"] == before_optimizer["acid_lab"]
    assert manager._business_parser.get_last_reading(BusinessType.ACID_LAB) == (
        before_parser[BusinessType.ACID_LAB]
    )


@pytest.mark.parametrize("target", [None, "bunker"])
def test_ambiguous_text_and_selected_target_keep_identity_and_other_fields(manager, target):
    seed_business_readings(manager)
    manager.set_business_screen_target(target)
    before = business_snapshot(manager)
    frame = ("Bunker Acid Lab Stock: 100%", "Supplies: 0% 5 units", "Sell:25e-2")

    SyntheticBusinessCapture(manager, [frame]).run()

    if target is None:
        assert business_snapshot(manager) == before
    else:
        state = manager.get_business_state("bunker")
        assert (state["stock"], state["supply"], state["value"]) == (100, 0, None)
        assert state["identity_source"] == "selected_target"
        reading = manager._business_parser.get_last_reading(BusinessType.BUNKER)
        assert reading.business_type is BusinessType.BUNKER
        assert reading.raw_text == " ".join(frame)
        assert reading.product_units == 5
        assert reading.is_full and not reading.is_empty and reading.needs_supplies
        assert manager.get_business_state("acid_lab") == before[0]["acid_lab"]
