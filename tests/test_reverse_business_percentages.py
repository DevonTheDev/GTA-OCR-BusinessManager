"""Fixed recognized-text boundaries, using the existing synthetic business loop."""

import pytest

from src.app import GTABusinessManager
from src.config.settings import Settings
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


@pytest.mark.parametrize("text,stock,supply", [
    ("5% stock", 5, None),
    ("5% supplies", None, 5),
    ("0% stock", 0, None),
    ("100% supplies", None, 100),
    ("-5% stock", None, None),
    ("-5% supplies", None, None),
    ("1.5% stock", None, None),
    ("1.5% supplies", None, None),
    ("1e2% stock", None, None),
    ("1e2% supplies", None, None),
    ("Stock: 5%", 5, None),
    ("Supplies: 5%", None, 5),
])
def test_assessed_reverse_percentage_examples(text, stock, supply):
    reading = BusinessParser().parse(text)
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == (
        stock, supply, None,
    )
    assert reading.has_data is (stock is not None or supply is not None)
    assert reading.business_type is BusinessType.UNKNOWN
    assert reading.raw_text == text


@pytest.mark.parametrize("label,field", [("stock", "stock_level"), ("supplies", "supply_level")])
@pytest.mark.parametrize("number", [
    "-25", "+25", "1.25", "1,25", ".25", ",25", "1E25", "1e+25", "1e-25",
    "1.e25", "1,e25",
])
def test_connected_numeric_suffixes_cannot_restart_at_a_later_digit(label, field, number):
    reading = BusinessParser().parse(f"{number}% {label}")
    assert getattr(reading, field) is None
    assert not reading.has_data


@pytest.mark.parametrize("label,field", [("stock", "stock_level"), ("supplies", "supply_level")])
@pytest.mark.parametrize("prefix", ["", "(", ":", "value", "VALUE", "e", "E", "5/"])
def test_reverse_percentages_keep_ordinary_word_punctuation_and_slash_starts(label, field, prefix):
    reading = BusinessParser().parse(f"{prefix}25% {label}")
    assert getattr(reading, field) == 25
    assert reading.has_data


@pytest.mark.parametrize("text,stock,supply", [
    ("25% stock Stock: 75%", 75, None),
    ("25% supplies Supplies: 75%", None, 75),
    ("25% stock Product: 75", 75, None),
    ("25% stock Stock: 1/2", 50, None),
    ("25% supplies Supplies: 1/2", None, 50),
])
def test_reverse_boundaries_keep_existing_field_pattern_precedence(text, stock, supply):
    reading = BusinessParser().parse(text)
    assert (reading.stock_level, reading.supply_level) == (stock, supply)


@pytest.mark.parametrize("number", ["-25", "1.25", "1e25"])
def test_known_business_cache_rejects_no_data_but_keeps_valid_companion_fields(number):
    parser = BusinessParser()
    previous = parser.parse("Bunker Stock: 75% Supplies: 50% Value: $900")
    unrelated = parser.parse("Acid Lab Stock: 30%")
    before = parser.get_all_last_readings()
    text = f"Bunker {number}% stock {number}% supplies"

    rejected = parser.parse(text)
    assert rejected.business_type is BusinessType.BUNKER
    assert not rejected.has_data
    assert parser.get_all_last_readings() == before
    assert parser.get_last_reading(BusinessType.BUNKER) is previous
    assert parser.get_last_reading(BusinessType.ACID_LAB) is unrelated

    partial = parser.parse(f"{text} Value: $0")
    assert (partial.stock_level, partial.supply_level, partial.stock_value) == (None, None, 0)
    assert partial.has_data
    assert parser.get_last_reading(BusinessType.BUNKER) is partial
    assert parser.get_last_reading(BusinessType.ACID_LAB) is unrelated


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


def seed_business_readings(manager):
    for label, business_id in [("Bunker", "bunker"), ("Acid Lab", "acid_lab")]:
        manager.update_business_state(business_id, 75, 50, 900)
        manager._business_parser.parse(f"{label} Stock: 75% Supplies: 50% Value: $900")


@pytest.mark.parametrize("number", ["-25", "1.25", "1e25"])
def test_reverse_suffix_only_capture_preserves_all_existing_live_caches(manager, number):
    seed_business_readings(manager)
    before = business_snapshot(manager)
    previous = manager._business_parser.get_last_reading(BusinessType.BUNKER)

    SyntheticBusinessCapture(manager, [(f"Bunker {number}% stock", f"{number}% supplies", "")]).run()

    assert business_snapshot(manager) == before
    assert manager._business_parser.get_last_reading(BusinessType.BUNKER) is previous


@pytest.mark.parametrize("frame,expected", [
    (("Bunker -25% stock", "Supplies: 0%", ""), (None, 0, None)),
    (("Bunker Stock: 0%", "1.25% supplies", ""), (0, None, None)),
    (("Bunker 1e25% stock", "1e25% supplies", "Value: $0"), (None, None, 0)),
])
def test_reverse_suffix_rejection_still_publishes_valid_partial_readings(manager, frame, expected):
    seed_business_readings(manager)
    before_app, before_optimizer, before_parser = business_snapshot(manager)

    SyntheticBusinessCapture(manager, [frame]).run()

    state = manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == expected
    assert state["identity_source"] == "ocr_text"
    optimized = manager._optimizer._business_states["bunker"]
    # Missing value is still estimated from observed stock, including zero.
    optimized_value = 0 if expected[0] == 0 else expected[2]
    assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == (
        expected[0], expected[1], optimized_value,
    )
    reading = manager._business_parser.get_last_reading(BusinessType.BUNKER)
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == expected
    assert reading.has_data
    assert reading.raw_text == " ".join(part for part in frame if part)
    assert set(manager.data.business_states) == set(before_app)
    assert manager.get_business_state("acid_lab") == before_app["acid_lab"]
    assert manager._optimizer._business_states["acid_lab"] == before_optimizer["acid_lab"]
    assert manager._business_parser.get_last_reading(BusinessType.ACID_LAB) == (
        before_parser[BusinessType.ACID_LAB]
    )
