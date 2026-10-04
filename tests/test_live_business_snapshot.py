"""Detached live observations are safe to review without changing game or saved data."""

import copy
import sqlite3
import threading
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from importlib.util import find_spec
from itertools import product

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.business_parser import BusinessType
from src.game.businesses import BUSINESSES
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


def domain():
    assert find_spec("src.game.live_business_snapshot") is not None
    return import_module("src.game.live_business_snapshot")


def read(manager, business_id="bunker"):
    method = getattr(manager, "get_live_business_reading_snapshot", None)
    assert callable(method), "The manager must expose the detached snapshot boundary"
    return method(business_id)


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


def measurements(snapshot):
    return snapshot.stock_percent, snapshot.supply_percent, snapshot.stock_value


def factory(**changes):
    arguments = dict(
        business_id="bunker", stock_percent=0, supply_percent=None, stock_value=None,
        updated_at=None, identity_source=None,
        captured_at=datetime(2026, 10, 4, 12, 30, tzinfo=timezone.utc),
    )
    arguments.update(changes)
    return domain().create_live_business_reading_snapshot(**arguments)


def test_snapshot_contract_is_frozen_and_has_only_observed_preview_fields():
    snapshot = factory()
    assert type(snapshot) is domain().LiveBusinessReadingSnapshot
    assert [field.name for field in fields(snapshot)] == [
        "business_id", "stock_percent", "supply_percent", "stock_value", "updated_at",
        "identity_source", "captured_at", "uses_supplies",
    ]
    for field in fields(snapshot):
        with pytest.raises(FrozenInstanceError):
            setattr(snapshot, field.name, object())


@pytest.mark.parametrize("business_id", BUSINESSES)
def test_every_catalog_business_exposes_raw_observations_and_supply_capability(manager, business_id):
    # A raw cache entry may predate supply normalization. Reading must not rewrite it.
    raw = {"stock": 100, "supply": 0, "value": 2**63 - 1}
    manager._data.business_states[business_id] = raw
    snapshot = read(manager, business_id)
    supplies = 0 if BUSINESSES[business_id].uses_supplies else None
    assert measurements(snapshot) == (100, supplies, 2**63 - 1)
    assert snapshot.business_id == business_id
    assert snapshot.uses_supplies is BUSINESSES[business_id].uses_supplies
    assert raw == {"stock": 100, "supply": 0, "value": 2**63 - 1}


OBSERVATIONS = [values for values in product((None, 0, 100), repeat=3)
                if values != (None, None, None)]


@pytest.mark.parametrize("observed", OBSERVATIONS)
def test_partial_observations_and_explicit_zero_never_become_estimates_or_history(manager, observed):
    manager.update_business_state("bunker", 95, 5, 123456)
    manager.update_business_state("bunker", *observed)
    snapshot = read(manager)
    assert measurements(snapshot) == observed
    if observed[0] and observed[2] is None:
        assert manager._optimizer._business_states["bunker"].estimated_value > 0
        assert snapshot.stock_value is None


@pytest.mark.parametrize("raw,expected", [
    ({"stock": 0}, (0, None, None)),
    ({"supply": 100}, (None, 100, None)),
    ({"value": 2**63 - 1}, (None, None, 2**63 - 1)),
])
def test_missing_measurement_keys_remain_unknown(manager, raw, expected):
    manager._data.business_states["bunker"] = raw
    assert measurements(read(manager)) == expected


def test_missing_live_reading_never_falls_back_to_optimizer_or_saved_history(manager, tmp_path):
    repository = Repository(str(tmp_path / "saved.db"))
    assert repository.initialize()
    try:
        character = repository.get_or_create_character("Saved character")
        repository.save_business_checkin(character.id, "bunker", 85, 5, 123456)
        manager._repository = repository
        manager._data.character_id = character.id
        manager._optimizer.update_business_state("bunker", 95, 0, 789)
        assert read(manager) is None
        assert read(manager, "agency") is None
    finally:
        repository.close()


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


@pytest.mark.parametrize("business_id", [
    "", "unknown", "hangar", "BUNKER", " bunker", "bunker ", "bunker\0",
    None, True, 1, 1.0, [], {}, BusinessType.BUNKER, StringSubclass("bunker"),
])
def test_invalid_ids_are_rejected_before_live_state_or_lock_access(manager, business_id):
    class UnavailableLock:
        def __enter__(self):
            pytest.fail("Invalid IDs must be rejected before taking the data lock")

    manager._data_lock = UnavailableLock()
    with pytest.raises(ValueError):
        read(manager, business_id)
    with pytest.raises(ValueError):
        factory(business_id=business_id)


@pytest.mark.parametrize("field", ["stock", "supply", "value"])
@pytest.mark.parametrize("invalid", [
    True, False, -1, "0", 0.0, [], {}, Decimal("0"), IntegerSubclass(0),
    float("nan"), float("inf"),
])
def test_malformed_numbers_are_refused_without_coercion_or_source_mutation(manager, field, invalid):
    raw = {"stock": 50, "supply": 75, "value": 1, field: invalid}
    manager._data.business_states["bunker"] = raw
    with pytest.raises(ValueError):
        read(manager)
    assert manager.get_business_state("bunker") is raw
    assert raw[field] is invalid
    argument = {"stock": "stock_percent", "supply": "supply_percent", "value": "stock_value"}[field]
    with pytest.raises(ValueError):
        factory(**{argument: invalid})


@pytest.mark.parametrize("raw", [
    {}, {"stock": None, "supply": None, "value": None},
    {"stock": 101}, {"supply": 101}, {"value": 2**63},
])
def test_empty_or_out_of_range_readings_are_refused(manager, raw):
    manager._data.business_states["bunker"] = raw
    with pytest.raises(ValueError):
        read(manager)
    assert manager.get_business_state("bunker") is raw


@pytest.mark.parametrize("business_id", [key for key, value in BUSINESSES.items()
                                        if not value.uses_supplies])
@pytest.mark.parametrize("supply", [0, 100, 101, "0"])
def test_unsupported_supplies_cannot_create_an_applicable_observation(manager, business_id, supply):
    manager._data.business_states[business_id] = {"supply": supply}
    with pytest.raises(ValueError):
        read(manager, business_id)


@pytest.mark.parametrize("source", ["manual_entry", "ocr_text", "selected_target"])
def test_known_identity_sources_are_retained(manager, source):
    manager.update_business_state("bunker", value=0, identity_source=source)
    assert read(manager).identity_source == source


@pytest.mark.parametrize("source", [None, "", "unknown", "OCR_TEXT", True, 1, [], {},
                                    StringSubclass("ocr_text")])
def test_unrecognized_identity_metadata_is_unavailable_without_relabeling(manager, source):
    manager._data.business_states["bunker"] = {"stock": 0, "identity_source": source}
    assert read(manager).identity_source is None
    assert manager.get_business_state("bunker")["identity_source"] is source
    assert factory(identity_source=source).identity_source is None


@pytest.mark.parametrize("updated", [
    datetime(2026, 10, 4, 9, 12, 34, 123456),
    datetime(2026, 10, 4, 9, 12, 34, tzinfo=timezone.utc),
    datetime(2026, 10, 4, 9, 12, 34, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    datetime(2026, 11, 1, 1, 30, fold=1),
])
def test_live_update_time_keeps_naive_local_meaning_or_original_offset(manager, updated):
    manager._data.business_states["bunker"] = {"stock": 0, "updated": updated}
    snapshot = read(manager)
    assert snapshot.updated_at == updated
    assert snapshot.updated_at.tzinfo == updated.tzinfo
    assert snapshot.updated_at.fold == updated.fold
    assert factory(updated_at=updated).updated_at == updated


@pytest.mark.parametrize("updated", [None, "2026-10-04T09:12:34Z", "", 0, True,
                                     date(2026, 10, 4), [], {}])
def test_unrecognized_update_time_is_unavailable_and_never_invented(manager, updated):
    manager._data.business_states["bunker"] = {"stock": 0, "updated": updated}
    assert read(manager).updated_at is None
    assert manager.get_business_state("bunker")["updated"] is updated
    assert factory(updated_at=updated).updated_at is None


def test_missing_metadata_is_unavailable_and_capture_time_is_current_utc(manager):
    manager._data.business_states["bunker"] = {"value": 0}
    before = datetime.now(timezone.utc)
    snapshot = read(manager)
    after = datetime.now(timezone.utc)
    assert snapshot.identity_source is snapshot.updated_at is None
    assert snapshot.captured_at.tzinfo is timezone.utc
    assert before <= snapshot.captured_at <= after


@pytest.mark.parametrize("captured", [
    None, "2026-10-04T12:30:00Z", 0, True, date(2026, 10, 4),
    datetime(2026, 10, 4, 12, 30),
    datetime(2026, 10, 4, 12, 30, tzinfo=timezone(timedelta(hours=1))),
])
def test_factory_refuses_unavailable_or_non_utc_capture_time(captured):
    with pytest.raises(ValueError):
        factory(captured_at=captured)


@pytest.mark.parametrize("change", ["replacement", "clear", "old_alias"])
def test_snapshot_remains_detached_after_replacement_clear_or_old_alias_mutation(manager, change):
    manager.set_manual_business_reading("bunker", 50, 0, 2**63 - 1)
    alias = manager.get_business_state("bunker")
    snapshot = read(manager)
    unchanged = copy.deepcopy(snapshot)
    if change == "replacement":
        manager.update_business_state("bunker", value=42, identity_source="ocr_text")
    elif change == "clear":
        manager.clear_business_readings()
    else:
        alias.update(stock=1, supply=100, value=42, updated=None, identity_source="ocr_text")
    assert snapshot == unchanged
    assert measurements(snapshot) == (50, 0, 2**63 - 1)
    assert snapshot.identity_source == "manual_entry"


def test_existing_get_business_state_keeps_mutable_alias_compatibility(manager):
    manager.update_business_state("bunker", stock_percent=50)
    alias = manager.get_business_state("bunker")
    assert manager.get_business_state("bunker") is alias
    read(manager)
    assert manager.get_business_state("bunker") is alias
    alias["stock"] = 0
    assert manager.get_business_state("bunker")["stock"] == 0
    assert read(manager).stock_percent == 0


def start_thread(action, errors):
    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_snapshot_waits_for_in_progress_locked_state_change(manager):
    manager.update_business_state("bunker", 10, 20, 30)
    started, finished = threading.Event(), threading.Event()
    results, errors = [], []

    def read_in_thread():
        started.set()
        try:
            results.append(read(manager))
        finally:
            finished.set()

    reader = None
    try:
        with manager._data_lock:
            reader = start_thread(read_in_thread, errors)
            assert started.wait(2)
            assert not finished.wait(0.05)
            manager._data.business_states["bunker"].update(stock=80, supply=90, value=100)
    finally:
        if reader is not None:
            reader.join(3)
    assert reader is not None and not reader.is_alive() and errors == []
    assert measurements(results[0]) == (80, 90, 100)


@pytest.mark.parametrize("mutation", ["replacement", "clear"])
def test_snapshot_field_read_serializes_public_mutation(manager, mutation):
    entered, release, started, finished = (threading.Event() for _ in range(4))
    results, errors = [], []

    class PausedReading(dict):
        def get(self, key, default=None):
            if key == "stock":
                entered.set()
                assert release.wait(3)
            return super().get(key, default)

    manager._data.business_states["bunker"] = PausedReading(stock=10, supply=20, value=30)

    def mutate():
        started.set()
        try:
            if mutation == "clear":
                manager.clear_business_readings()
            else:
                manager.update_business_state("bunker", 80, 90, 100)
        finally:
            finished.set()

    reader = start_thread(lambda: results.append(read(manager)), errors)
    writer = None
    try:
        assert entered.wait(2)
        writer = start_thread(mutate, errors)
        assert started.wait(2)
        assert not finished.wait(0.05)
    finally:
        release.set()
        reader.join(3)
        if writer is not None:
            writer.join(3)
    assert not reader.is_alive() and writer is not None and not writer.is_alive()
    assert errors == [] and measurements(results[0]) == (10, 20, 30)
    if mutation == "clear":
        assert read(manager) is None
    else:
        assert measurements(read(manager)) == (80, 90, 100)


@pytest.mark.parametrize("selected", [None, "bunker"])
def test_real_manual_then_synthetic_ocr_paths_produce_fresh_unmerged_snapshot(manager, selected):
    manager.set_business_screen_target(selected)
    manager.set_manual_business_reading("bunker", 95, 5, 123456)
    manual = read(manager)
    assert measurements(manual) == (95, 5, 123456)
    assert manual.identity_source == "manual_entry"
    stock = "Stock: 0%" if selected else "Bunker Stock: 0%"
    SyntheticBusinessCapture(manager, [(stock, "", "Value: $0")]).run()
    current = read(manager)
    assert measurements(current) == (0, None, 0)
    assert current.identity_source == ("selected_target" if selected else "ocr_text")
    assert current.updated_at == manager.get_business_state("bunker")["updated"]
    assert current.updated_at.tzinfo is None
    assert measurements(manual) == (95, 5, 123456)


@pytest.mark.parametrize("state", AppState)
def test_snapshot_has_no_accounting_lifecycle_parser_optimizer_or_storage_side_effects(
    manager, tmp_path, state, monkeypatch,
):
    repository = Repository(str(tmp_path / "state.db"))
    assert repository.initialize()
    try:
        character = repository.get_or_create_character("Saved character")
        repository.save_business_checkin(character.id, "bunker", 25, 75, 50, "Saved note")
        repository.set_manual_business_pin(character.id, "bunker", True)
        manager._repository = repository
        manager._data.character_id = character.id
        manager._data.db_session_id = 11
        manager._data.current_money = 1000
        manager._data.session_earnings = 250
        manager._data.current_mission = "Mission in progress"
        manager._data.mission_start_time = datetime.now()
        manager._session_tracker.start_session(start_money=750)
        manager.set_business_screen_target("agency")
        manager.set_manual_business_reading("bunker", 95, 0, 123456)
        manager._business_parser.parse("Bunker Stock: 95%")
        manager._optimizer.set_cooldown("payphone_hit", 20)
        manager._optimizer._scheduler.schedule_sell("bunker", estimated_value=123456)
        manager._state = state
        before_data, before_business = manager.data, business_snapshot(manager)
        settings = copy.deepcopy(manager._settings._config)
        cooldowns = copy.deepcopy(manager._optimizer._cooldowns)
        schedule = copy.deepcopy(manager._optimizer._scheduler.get_upcoming())
        stats, generation = manager.session_stats, manager._business_screen_generation
        stop_marker, last_capture = manager._stop_event.is_set(), manager._last_capture_result
        efficiency, breakdown = object(), object()
        manager._cached_efficiency, manager._cached_breakdown = efficiency, breakdown
        files = {path.relative_to(tmp_path): path.read_bytes()
                 for path in tmp_path.rglob("*") if path.is_file()}
        with sqlite3.connect(tmp_path / "state.db") as db:
            rows = list(db.iterdump())
        for name in ("start", "stop", "pause", "resume", "reset_session", "_initialize_database"):
            monkeypatch.setattr(manager, name, lambda: pytest.fail("Snapshot changed lifecycle"))

        assert measurements(read(manager)) == (95, 0, 123456)
        assert read(manager, "agency") is None

        assert manager.data == before_data and business_snapshot(manager) == before_business
        assert manager.state is state and manager.business_screen_target == "agency"
        assert manager._business_screen_generation == generation
        assert manager._stop_event.is_set() is stop_marker
        assert manager._last_capture_result is last_capture and manager.session_stats is stats
        assert manager._settings._config == settings and manager._repository is repository
        assert manager._optimizer._cooldowns == cooldowns
        assert manager._optimizer._scheduler.get_upcoming() == schedule
        assert manager._cached_efficiency is efficiency and manager._cached_breakdown is breakdown
        assert {path.relative_to(tmp_path): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()} == files
        with sqlite3.connect(tmp_path / "state.db") as db:
            assert list(db.iterdump()) == rows
    finally:
        repository.close()
