"""Recognized business names take priority without adding OCR or screen detection."""

import copy
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.app import GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from src.game.businesses import BUSINESSES
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


EXPLICIT_LABELS = [
    ("Cocaine Lockup", BusinessType.COCAINE),
    ("Meth Lab", BusinessType.METH),
    ("Counterfeit Cash", BusinessType.CASH),
    ("Weed Farm", BusinessType.WEED),
    ("Document Forgery", BusinessType.DOCUMENTS),
    ("Bunker", BusinessType.BUNKER),
    ("Nightclub Warehouse", BusinessType.NIGHTCLUB),
    ("Agency", BusinessType.AGENCY),
    ("Acid Lab", BusinessType.ACID_LAB),
    ("Vehicle Warehouse", BusinessType.VEHICLE_WAREHOUSE),
    ("Special Cargo Warehouse", BusinessType.SPECIAL_CARGO),
    ("Special Cargo", BusinessType.SPECIAL_CARGO),
    ("Nightclub", BusinessType.NIGHTCLUB),
    ("Hangar", BusinessType.HANGAR),
    ("Auto Shop", BusinessType.AUTO_SHOP),
]


def test_explicit_examples_cover_every_catalog_display_name():
    assert {name for name, _ in EXPLICIT_LABELS[:11]} == {
        business.name for business in BUSINESSES.values()
    }


@pytest.mark.parametrize("label,business_type", EXPLICIT_LABELS)
@pytest.mark.parametrize("variant", ["canonical", "mixed_case_whitespace"])
def test_explicit_names_win_over_generic_words(label, business_type, variant):
    if variant == "mixed_case_whitespace":
        label = " \t\r\n ".join(label.swapcase().split())
    text = f"coke lab warehouse cargo security ({label}) Stock: 5/10 Supplies: 3/4 Value: $0"
    parser = BusinessParser()
    reading = parser.parse(text)
    assert reading.business_type is business_type
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == (50, 75, 0)
    assert reading.raw_text == text and reading.has_data
    assert parser.get_last_reading(business_type) is reading


@pytest.mark.parametrize("labels", [
    "Acid Lab\nMeth Lab",
    "Meth Lab\nAcid Lab",
    "Vehicle Warehouse\nNightclub Warehouse",
    "Special Cargo Warehouse\nHangar",
    "Bunker\nAgency",
    "Auto Shop\nBunker",
])
def test_distinct_explicit_types_are_ambiguous_across_all_text(labels):
    parser = BusinessParser()
    text = f"{labels} Stock: 50% Supplies: 0% Value: $123 5 units"
    reading = parser.parse(text)
    assert reading.business_type is BusinessType.UNKNOWN
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == (50, 0, 123)
    assert reading.product_units == 5 and reading.needs_supplies
    assert reading.raw_text == text and reading.has_data
    assert parser.get_all_last_readings() == {}


@pytest.mark.parametrize("labels,business_type", [
    ("Acid Lab ACID\tLAB Acid Lab", BusinessType.ACID_LAB),
    ("Special Cargo Warehouse Special Cargo", BusinessType.SPECIAL_CARGO),
    ("Nightclub Warehouse Nightclub", BusinessType.NIGHTCLUB),
])
def test_repeated_names_and_same_type_aliases_are_not_ambiguous(labels, business_type):
    assert BusinessParser().parse(f"{labels} Stock: 50%").business_type is business_type


@pytest.mark.parametrize("text,business_type", [
    ("lab", BusinessType.METH),
    ("warehouse", BusinessType.NIGHTCLUB),
    ("cargo", BusinessType.HANGAR),
    ("acid", BusinessType.ACID_LAB),
    ("research", BusinessType.BUNKER),
    ("security", BusinessType.AGENCY),
    ("special", BusinessType.UNKNOWN),
    ("Acid Laboratory", BusinessType.METH),
    ("Vehicle Warehouses", BusinessType.NIGHTCLUB),
    ("Special Cargonaut", BusinessType.HANGAR),
    ("Preacid Lab", BusinessType.METH),
    ("Acid-Lab", BusinessType.METH),
    ("Auto-Shop", BusinessType.UNKNOWN),
    ("Bunkers", BusinessType.BUNKER),
    ("_Acid Lab", BusinessType.METH),
])
def test_fragments_and_generic_words_keep_the_existing_fallback(text, business_type):
    assert BusinessParser().parse(f"{text} Stock: 50%").business_type is business_type


@pytest.mark.parametrize("hint,text", [
    (BusinessType.BUNKER, "Acid Lab Meth Lab"),
    (BusinessType.VEHICLE_WAREHOUSE, "lab warehouse cargo"),
    (BusinessType.UNKNOWN, "Acid Lab"),
])
def test_supplied_hint_keeps_its_existing_authority(hint, text):
    assert BusinessParser().parse(f"{text} Stock: 50%", business_hint=hint).business_type is hint


@pytest.mark.parametrize("measurements,expected,units,has_data", [
    ("Stock: 0/10 Value: $0", (0, None, 0), None, True),
    ("Supplies: 0%", (None, 0, None), None, True),
    ("Value: $0", (None, None, 0), None, True),
    ("5 units", (None, None, None), 5, False),
    ("", (None, None, None), None, False),
])
def test_explicit_identity_preserves_zero_missing_and_cache_admission(
    measurements, expected, units, has_data,
):
    parser = BusinessParser()
    text = f"Acid Lab {measurements}"
    reading = parser.parse(text)
    assert reading.business_type is BusinessType.ACID_LAB
    assert (reading.stock_level, reading.supply_level, reading.stock_value) == expected
    assert reading.product_units == units and reading.has_data is has_data
    assert reading.raw_text == text
    assert reading.is_empty is (expected[0] == 0)
    assert reading.needs_supplies is (expected[1] == 0)
    assert parser.get_last_reading(BusinessType.ACID_LAB) is (reading if has_data else None)


def test_ambiguity_preserves_all_existing_parser_readings():
    parser = BusinessParser()
    for business_type in (BusinessType.ACID_LAB, BusinessType.METH, BusinessType.BUNKER):
        parser.parse("Stock: 25%", business_hint=business_type)
    before = parser.get_all_last_readings()
    reading = parser.parse("Acid Lab Meth Lab Stock: 0% Value: $0")
    assert reading.business_type is BusinessType.UNKNOWN and reading.has_data
    assert parser.get_all_last_readings() == before
    assert all(parser.get_last_reading(key) is value for key, value in before.items())


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


@pytest.mark.parametrize("label,business_id,old_id", [
    ("Acid Lab", "acid_lab", "meth"),
    ("Vehicle Warehouse", "vehicle_warehouse", "nightclub"),
    ("Special Cargo", "special_cargo", "hangar"),
    ("Special Cargo Warehouse", "special_cargo", "nightclub"),
])
def test_automatic_dispatch_updates_correct_live_consumers_without_accounting(
    manager, tmp_path, label, business_id, old_id,
):
    repository = Repository(str(tmp_path / "saved.db"))
    assert repository.initialize()
    try:
        character = repository.get_or_create_character("Saved player")
        repository.save_business_checkin(character.id, business_id, 25, None, 123, "Saved")
        manager._repository = repository
        manager._data.character_id = character.id
        manager._data.current_money = 1000
        manager._data.session_earnings = 200
        manager.update_business_state("bunker", 75, 50, 900)
        if old_id in BUSINESSES:
            manager.update_business_state(old_id, 60, None, 800)
        old_reading = manager._business_parser.parse(
            "Stock: 60% Value: $800", business_hint=BusinessType[old_id.upper()],
        )
        before_app, before_optimizer, _ = business_snapshot(manager)
        scheduler = manager._optimizer._scheduler
        scheduler.schedule_sell("bunker", estimated_value=900)
        scheduler.schedule_resupply("bunker", datetime.now() + timedelta(hours=1))
        before_schedule = copy.deepcopy(scheduler.get_upcoming())
        before_activities = copy.deepcopy(manager._activity_tracker.get_recent_activities(100))
        with sqlite3.connect(tmp_path / "saved.db") as connection:
            before_records = list(connection.iterdump())

        capture = SyntheticBusinessCapture(manager, [(f"{label} Stock: 0/10", "", "Value: $0")])
        capture.run()

        state = manager.get_business_state(business_id)
        assert (state["stock"], state["supply"], state["value"]) == (0, None, 0)
        assert state["identity_source"] == "ocr_text"
        optimized = manager._optimizer._business_states[business_id]
        assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == (
            0, None, 0,
        )
        preview = manager.get_live_business_reading_snapshot(business_id)
        assert (preview.stock_percent, preview.supply_percent, preview.stock_value) == (0, None, 0)
        assert preview.business_id == business_id and preview.identity_source == "ocr_text"
        reading = manager._business_parser.get_last_reading(BusinessType[business_id.upper()])
        assert reading.raw_text == f"{label} Stock: 0/10 Value: $0"
        assert manager._business_parser.get_last_reading(BusinessType[old_id.upper()]) is old_reading
        assert set(manager.data.business_states) == set(before_app) | {business_id}
        for key in before_app:
            assert manager.get_business_state(key) == before_app[key]
            assert manager._optimizer._business_states[key] == before_optimizer[key]
        assert (manager.data.current_money, manager.data.session_earnings) == (1000, 200)
        assert manager._activity_tracker.get_recent_activities(100) == before_activities
        assert scheduler.get_upcoming() == before_schedule
        with sqlite3.connect(tmp_path / "saved.db") as connection:
            assert list(connection.iterdump()) == before_records
        assert len(capture.business_batches) == 1 and len(capture.ocr_calls) == 3
    finally:
        repository.close()


@pytest.mark.parametrize("frame", [
    ("Acid Lab Stock: 0%", "Meth Lab Supplies: 0%", "Value: $0"),
    ("Acid Lab", "", ""),
    ("Acid Lab 5 units", "", ""),
])
def test_ambiguous_or_measurement_free_dispatch_preserves_existing_caches(manager, frame):
    for business_id in ("acid_lab", "meth", "bunker"):
        manager.update_business_state(business_id, 75, 50, 900)
        manager._business_parser.parse("Stock: 75%", business_hint=BusinessType[business_id.upper()])
    before = business_snapshot(manager)
    SyntheticBusinessCapture(manager, [frame]).run()
    assert business_snapshot(manager) == before


@pytest.mark.parametrize("label,business_type", [
    ("Hangar", BusinessType.HANGAR),
    ("Auto Shop", BusinessType.AUTO_SHOP),
])
def test_parser_only_names_do_not_publish_uncataloged_live_cards(manager, label, business_type):
    manager.update_business_state("bunker", 75, 50, 900)
    before_app, before_optimizer, _ = business_snapshot(manager)
    SyntheticBusinessCapture(manager, [(f"{label} Stock: 50%", "", "")]).run()
    assert manager.data.business_states == before_app
    assert manager._optimizer._business_states == before_optimizer
    assert manager._business_parser.get_last_reading(business_type).stock_level == 50


def test_selected_target_overrides_distinct_labels_across_crops(manager):
    manager.set_business_screen_target("vehicle_warehouse")
    SyntheticBusinessCapture(manager, [
        ("Acid Lab Stock: 50%", "Meth Lab Supplies: 75%", "Value: $0"),
    ]).run()
    state = manager.get_business_state("vehicle_warehouse")
    assert (state["stock"], state["supply"], state["value"]) == (50, None, 0)
    assert state["identity_source"] == "selected_target"
    assert set(manager._business_parser.get_all_last_readings()) == {BusinessType.VEHICLE_WAREHOUSE}
