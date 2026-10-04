"""Manual live replacements preserve unknowns, saved history and capture ordering."""

import copy
import logging
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from src.game.businesses import BUSINESSES
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    messages = [record.getMessage() for record in caplog.get_records("call")]
    assert not any("Error processing business computer:" in text for text in messages)
    assert not any("Capture cycle error:" in text for text in messages)


def seed(manager):
    for business_id in ("bunker", "agency"):
        manager.update_business_state(business_id, 95, 0, 123456)
        manager._business_parser.parse(
            "Stock: 95% Supplies: 0% Value: $123,456",
            business_hint=BusinessType[business_id.upper()],
        )
    manager._business_parser.parse("Hangar Stock: 60%", business_hint=BusinessType.HANGAR)


def values(manager, business_id="bunker"):
    state = manager.get_business_state(business_id)
    return state["stock"], state["supply"], state["value"]


def assert_replaced(manager, expected, business_id="bunker"):
    assert values(manager, business_id) == expected
    assert manager.get_business_state(business_id)["identity_source"] == "manual_entry"
    optimized = manager._optimizer._business_states[business_id]
    assert (optimized.stock_percent, optimized.supply_percent) == expected[:2]
    if expected[2] is not None:
        assert optimized.estimated_value == expected[2]
    elif expected[0] is None:
        assert optimized.estimated_value is None


def run_in_thread(action, errors):
    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    return threading.Thread(target=run, daemon=True)


def test_parser_forgets_only_selected_evidence_and_can_read_again():
    parser = BusinessParser()
    old = parser.parse("Bunker Stock: 95%")
    other = parser.parse("Agency Stock: 75%")

    parser.clear_reading(BusinessType.BUNKER)

    assert parser.get_last_reading(BusinessType.BUNKER) is None
    assert parser.get_all_last_readings() == {BusinessType.AGENCY: other}
    assert old.stock_level == 95
    parser.clear_reading(BusinessType.BUNKER)
    fresh = parser.parse("Bunker Stock: 25%")
    assert parser.get_last_reading(BusinessType.BUNKER) is fresh


@pytest.mark.parametrize("state", list(AppState))
@pytest.mark.parametrize("stop_marker", [False, True])
def test_manual_replacement_preserves_lifecycle_target_and_other_live_state(
    manager, state, stop_marker, monkeypatch,
):
    seed(manager)
    manager._state = state
    manager.set_business_screen_target("agency")
    if stop_marker:
        manager._stop_event.set()
    manager._data.current_money = 1000
    manager._data.session_earnings = 250
    manager._data.character_id = 7
    manager._data.db_session_id = 11
    manager._data.current_mission = "A mission in progress"
    manager._data.mission_start_time = datetime.now()
    manager._session_tracker.start_session(start_money=750)
    manager._optimizer.set_cooldown("payphone_hit", 20)
    scheduled = manager._optimizer._scheduler.schedule_check(
        "bunker", datetime.now() + timedelta(hours=1),
    )
    before = manager.data
    old_other = manager.get_business_state("agency")
    old_optimized_other = manager._optimizer._business_states["agency"]
    parser_before = manager._business_parser.get_all_last_readings()
    stats = manager.session_stats
    cooldowns = copy.deepcopy(manager._optimizer._cooldowns)
    generation = manager._business_screen_generation
    settings = copy.deepcopy(manager._settings._config)
    efficiency, breakdown = object(), object()
    manager._cached_efficiency, manager._cached_breakdown = efficiency, breakdown
    components = manager._business_parser, manager._optimizer
    for method in ("start", "stop", "pause", "resume", "reset_session", "_initialize_database"):
        monkeypatch.setattr(manager, method, lambda: pytest.fail("Entry changed lifecycle"))

    manager.set_manual_business_reading("bunker", value=0)

    assert_replaced(manager, (None, None, 0))
    before.business_states["bunker"] = manager.get_business_state("bunker")
    assert manager.data == before
    assert manager.get_business_state("agency") is old_other
    assert manager._optimizer._business_states["agency"] is old_optimized_other
    del parser_before[BusinessType.BUNKER]
    assert manager._business_parser.get_all_last_readings() == parser_before
    assert manager.state is state and manager.business_screen_target == "agency"
    assert manager._stop_event.is_set() is stop_marker
    assert manager._business_screen_generation == generation + 1
    assert manager.session_stats is stats
    assert manager._settings._config == settings
    assert manager._optimizer._cooldowns == cooldowns
    assert manager._optimizer._scheduler._scheduled == [scheduled]
    assert (manager._business_parser, manager._optimizer) == components
    assert manager._cached_efficiency is efficiency and manager._cached_breakdown is breakdown


@pytest.mark.parametrize("expected", [
    (0, None, None), (100, None, None), (None, 0, None), (None, 100, None),
    (None, None, 0), (None, None, 2**63 - 1), (25, 75, None),
    (25, None, 0), (None, 75, 123), (0, 0, 0), (100, 100, 123),
])
def test_manual_entry_replaces_every_field_instead_of_merging_old_observations(manager, expected):
    seed(manager)
    old = manager.get_business_state("bunker")

    manager.set_manual_business_reading("bunker", *expected)

    assert_replaced(manager, expected)
    assert manager.get_business_state("bunker") is not old
    assert manager.get_business_state("bunker")["updated"] >= old["updated"]


@pytest.mark.parametrize("business_id", list(BUSINESSES))
def test_manual_entry_supports_every_catalog_business_without_changing_target(manager, business_id):
    manager.set_business_screen_target("agency")
    manager.set_manual_business_reading(business_id, stock_percent=0)
    assert_replaced(manager, (0, None, None), business_id)
    assert manager.business_screen_target == "agency"


@pytest.mark.parametrize("invalid", [
    {"business_id": "unknown", "value": 1},
    {"business_id": "hangar", "value": 1},
    {"business_id": "BUNKER", "value": 1},
    {"business_id": " bunker", "value": 1},
    {"business_id": "bunker ", "value": 1},
    {"business_id": [], "value": 1},
    {"business_id": {}, "value": 1},
    {"business_id": True, "value": 1},
    {}, {"stock_percent": -1}, {"stock_percent": 101},
    {"stock_percent": True}, {"stock_percent": 50.0}, {"stock_percent": "50"},
    {"stock_percent": []}, {"supply_percent": -1}, {"supply_percent": 101},
    {"supply_percent": False}, {"supply_percent": 1.0}, {"supply_percent": "1"},
    {"value": -1}, {"value": 2**63}, {"value": True}, {"value": 1.0},
    {"value": "1"}, {"value": {}},
    {"business_id": "agency", "stock_percent": 20, "supply_percent": 0},
    {"business_id": "nightclub", "supply_percent": 50},
    {"business_id": "vehicle_warehouse", "supply_percent": 0},
    {"business_id": "special_cargo", "supply_percent": 0},
])
def test_invalid_manual_entry_leaves_all_observations_and_generation_untouched(manager, invalid):
    seed(manager)
    manager.set_business_screen_target("agency")
    before, data_before = business_snapshot(manager), manager.data
    generation = manager._business_screen_generation
    arguments = {"business_id": "bunker", **invalid}

    with pytest.raises(ValueError):
        manager.set_manual_business_reading(**arguments)

    assert business_snapshot(manager) == before
    assert manager.data == data_before
    assert manager._business_screen_generation == generation
    assert manager.business_screen_target == "agency"
    assert manager.state is AppState.STOPPED and not manager._stop_event.is_set()


@pytest.mark.parametrize("manual", [False, True])
def test_optimizer_computation_failure_cannot_publish_half_of_a_reading(manager, monkeypatch, manual):
    seed(manager)
    before = business_snapshot(manager)
    generation = manager._business_screen_generation
    monkeypatch.setattr(BUSINESSES["bunker"], "max_value", object())
    update = manager.set_manual_business_reading if manual else manager.update_business_state

    with pytest.raises(TypeError):
        update("bunker", stock_percent=50)

    # The domain object is shared with the optimizer, so restore it for comparison.
    monkeypatch.undo()
    assert business_snapshot(manager) == before
    assert manager._business_screen_generation == generation


def test_generic_update_accepts_optional_observations_and_manual_provenance_without_retiring_ocr(
    manager,
):
    seed(manager)
    generation = manager._business_screen_generation
    parser_before = manager._business_parser.get_all_last_readings()

    manager.update_business_state("bunker", value=0, identity_source="manual_entry")

    assert_replaced(manager, (None, None, 0))
    assert manager._business_screen_generation == generation
    assert manager._business_parser.get_all_last_readings() == parser_before


def test_generic_update_normalizes_irrelevant_supplies(manager):
    manager.update_business_state("agency", 50, 75, 0)
    assert values(manager, "agency") == (50, None, 0)
    assert manager._optimizer._business_states["agency"].supply_percent is None


@pytest.mark.parametrize("arguments", [
    {"business_id": "unknown", "stock_percent": 50},
    {"business_id": "bunker", "stock_percent": True},
    {"business_id": "bunker", "stock_percent": 101},
    {"business_id": "bunker", "value": 2**63},
    {"business_id": "bunker", "value": 1, "identity_source": "unknown_source"},
])
def test_invalid_generic_update_cannot_change_live_state(manager, arguments):
    seed(manager)
    before = business_snapshot(manager)
    with pytest.raises(ValueError):
        manager.update_business_state(**arguments)
    assert business_snapshot(manager) == before


@pytest.mark.parametrize("frame,expected", [
    (("Stock: 25%", "", ""), (25, None, None)),
    (("", "Supplies: 0%", ""), (None, 0, None)),
    (("", "", "Value: $0"), (None, None, 0)),
    (("Stock: 25%", "", "Value: $0"), (25, None, 0)),
])
def test_fresh_partial_ocr_replaces_manual_with_unknowns_and_logs_without_errors(
    manager, frame, expected, caplog,
):
    caplog.set_level(logging.INFO)
    manager.set_business_screen_target("bunker")
    manager.set_manual_business_reading("bunker", 95, 5, 123456)

    SyntheticBusinessCapture(manager, [frame]).run()

    assert values(manager) == expected
    assert manager.get_business_state("bunker")["identity_source"] == "selected_target"
    optimized = manager._optimizer._business_states["bunker"]
    assert (optimized.stock_percent, optimized.supply_percent) == expected[:2]
    assert "Business assigned to selected target: BUNKER" in caplog.text


@pytest.mark.parametrize("text", ["Stock: 50%", "Hangar Stock: 50%"])
def test_unsupported_automatic_ocr_does_not_create_uncatalogued_live_cards(manager, text):
    SyntheticBusinessCapture(manager, [(text, "", "")]).run()
    assert manager.data.business_states == {}
    assert manager._optimizer._business_states == {}


def test_irrelevant_supply_only_ocr_preserves_last_usable_live_card(manager):
    manager.set_business_screen_target("agency")
    manager.set_manual_business_reading("agency", value=123)
    before = copy.deepcopy((manager.data.business_states, manager._optimizer._business_states))

    SyntheticBusinessCapture(manager, [("", "Supplies: 0%", "")]).run()

    assert (manager.data.business_states, manager._optimizer._business_states) == before


@pytest.mark.parametrize("boundary,index", [("capture", 1), ("ocr", 1), ("ocr", 3)])
@pytest.mark.parametrize("selected", [None, "bunker"])
def test_manual_entry_retires_pending_ocr_before_parser_but_accepts_fresh_batch(
    manager, boundary, index, selected,
):
    seed(manager)
    manager.set_business_screen_target(selected)
    entered, release = threading.Event(), threading.Event()

    def pause_boundary(kind, count):
        if (kind, count) == (boundary, index):
            entered.set()
            assert release.wait(3)

    capture = SyntheticBusinessCapture(manager, [
        ("Bunker Stock: 95%", "Supplies: 0%", "Value: $123,456"),
        ("Bunker Stock: 30%", "", "Value: $0"),
    ], hook=pause_boundary)
    errors = []
    worker = run_in_thread(capture.run, errors)
    worker.start()
    try:
        assert entered.wait(2)
        manager.set_manual_business_reading("bunker", value=42)
        manual = business_snapshot(manager)
        assert_replaced(manager, (None, None, 42))
        assert manager._business_parser.get_last_reading(BusinessType.BUNKER) is None
        assert manager.business_screen_target == selected
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and errors == []
    assert capture.observations[0][1] == manual
    assert values(manager) == (30, None, 0)
    assert manager.get_business_state("bunker")["identity_source"] == (
        "selected_target" if selected else "ocr_text"
    )
    assert manager._business_parser.get_last_reading(BusinessType.BUNKER).stock_level == 30


def test_rejected_manual_entry_does_not_retire_pending_ocr(manager):
    manager.set_business_screen_target("bunker")

    def reject_entry(kind, count):
        if (kind, count) == ("ocr", 1):
            with pytest.raises(ValueError):
                manager.set_manual_business_reading("bunker", stock_percent=101)

    SyntheticBusinessCapture(manager, [("Stock: 50%", "", "")], hook=reject_entry).run()
    assert values(manager) == (50, None, None)


@pytest.mark.parametrize("boundary", ["parser", "optimizer"])
def test_manual_entry_waits_for_admitted_ocr_then_replaces_only_its_selected_business(
    manager, boundary, monkeypatch,
):
    seed(manager)
    manager.set_business_screen_target("bunker")
    entered, release, started, finished = (threading.Event() for _ in range(4))
    component, method = ((manager._business_parser, "parse") if boundary == "parser"
                         else (manager._optimizer, "update_business_state"))
    original = getattr(component, method)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(component, method, blocked)
    capture = SyntheticBusinessCapture(manager, [("Stock: 95%", "Supplies: 0%", "Value: $42")])
    errors = []

    def replace():
        started.set()
        try:
            manager.set_manual_business_reading("bunker", stock_percent=20)
        finally:
            finished.set()

    worker = run_in_thread(capture.run, errors)
    writer = run_in_thread(replace, errors)
    worker.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert started.wait(2)
        assert not finished.wait(0.05)
    finally:
        release.set()
        worker.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not worker.is_alive() and not writer.is_alive() and errors == []
    assert_replaced(manager, (20, None, None))
    assert manager._business_parser.get_last_reading(BusinessType.BUNKER) is None
    assert manager.get_business_state("agency")["stock"] == 95
    assert manager._business_parser.get_last_reading(BusinessType.AGENCY).stock_level == 95
    assert manager.business_screen_target == "bunker"


def test_manual_entry_serializes_with_recommendation_snapshot(manager, monkeypatch):
    seed(manager)
    entered, release, started, finished = (threading.Event() for _ in range(4))
    original = manager._optimizer._priority_calc.calculate_sell_priority

    def blocked_priority(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager._optimizer._priority_calc, "calculate_sell_priority", blocked_priority)
    results, errors = [], []

    def replace():
        started.set()
        try:
            manager.set_manual_business_reading("bunker", value=0)
        finally:
            finished.set()

    reader = run_in_thread(lambda: results.append(manager.recommendations), errors)
    writer = run_in_thread(replace, errors)
    reader.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert started.wait(2)
        assert not finished.wait(0.05)
    finally:
        release.set()
        reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not reader.is_alive() and not writer.is_alive() and errors == []
    assert any(rec.business_type == "bunker" for rec in results[0])
    assert all(rec.business_type != "bunker" for rec in manager.recommendations)
    assert_replaced(manager, (None, None, 0))


def test_manual_live_replacement_never_changes_saved_rows_or_files(manager, tmp_path):
    repository = Repository(str(tmp_path / "test.db"))
    assert repository.initialize()
    try:
        character = repository.get_or_create_character("Synthetic Player")
        repository.set_active_character(character.id)
        repository.save_business_checkin(character.id, "bunker", 10, 20, 300, "Saved history")
        repository.set_manual_business_pin(character.id, "bunker", True)
        manager._repository = repository
        manager._data.character_id = character.id
        manager.set_business_screen_target("agency")
        manager._optimizer._scheduler.schedule_sell("bunker", estimated_value=123456)
        schedule_before = copy.deepcopy(manager._optimizer._scheduler.get_upcoming())
        files_before = {
            path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob("*") if path.is_file()
        }
        with sqlite3.connect(tmp_path / "test.db") as connection:
            rows_before = list(connection.iterdump())

        manager.set_manual_business_reading("bunker", value=0)

        assert_replaced(manager, (None, None, 0))
        assert manager.business_screen_target == "agency"
        assert manager.data.character_id == character.id
        assert manager._optimizer._scheduler.get_upcoming() == schedule_before
        assert {
            path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob("*") if path.is_file()
        } == files_before
        with sqlite3.connect(tmp_path / "test.db") as connection:
            assert list(connection.iterdump()) == rows_before
        fresh = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
        assert fresh.data.business_states == {}
    finally:
        repository.close()
