"""One locked capture yields detached catalog-ordered live observations."""

import copy
from datetime import datetime, timedelta, timezone
import sqlite3
import threading

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.game.businesses import BUSINESSES
from tests.test_business_screen_target import business_snapshot
from tests.test_live_business_snapshot import start_thread


@pytest.fixture
def manager(tmp_path):
    return GTABusinessManager(Settings(tmp_path / "settings.yaml"))


def read(manager):
    method = getattr(manager, "get_live_business_reading_snapshots", None)
    assert callable(method), "The manager must capture all live readings under one lock"
    return method()


def test_empty_cache_and_non_catalog_keys_return_empty_without_history_fallback(manager):
    manager._optimizer.update_business_state("bunker", 95, 0, 12345)
    assert read(manager) == ()
    manager._data.business_states.update({"future-business": object(), "hangar": {}, "bunker": None})
    assert read(manager) == ()


def test_capture_has_catalog_order_one_utc_stamp_and_one_lock(manager, monkeypatch):
    for business_id in reversed(BUSINESSES):
        manager._data.business_states[business_id] = {"stock": 0, "supply": 100, "value": 2**63 - 1}
    instant = datetime(2026, 10, 4, 15, 30, tzinfo=timezone.utc)
    calls, entries = [], []
    real_lock = manager._data_lock

    class OneLock:
        def __enter__(self):
            entries.append(1)
            return real_lock.__enter__()

        def __exit__(self, *args):
            return real_lock.__exit__(*args)

    class Clock:
        @staticmethod
        def now(tz):
            assert tz is timezone.utc
            calls.append(1)
            assert len(entries) == 1
            return instant

    manager._data_lock = OneLock()
    monkeypatch.setattr("src.app.datetime", Clock)
    snapshots = read(manager)
    assert type(snapshots) is tuple
    assert [row.business_id for row in snapshots] == list(BUSINESSES)
    assert len(calls) == len(entries) == 1
    assert all(row.captured_at is instant for row in snapshots)
    for row in snapshots:
        assert (row.stock_percent, row.stock_value) == (0, 2**63 - 1)
        assert row.supply_percent == (100 if BUSINESSES[row.business_id].uses_supplies else None)
        assert row.uses_supplies is BUSINESSES[row.business_id].uses_supplies


@pytest.mark.parametrize("source", ["manual_entry", "ocr_text", "selected_target", None, "invalid", {}])
@pytest.mark.parametrize("updated", [datetime(2026, 10, 4, 8, 0),
                                     datetime(2026, 10, 4, 8, 0, tzinfo=timezone(timedelta(hours=5))),
                                     None, "invalid"])
def test_original_update_time_and_recognized_source_normalization_are_preserved(manager, source, updated):
    manager._data.business_states["bunker"] = {
        "stock": 0, "updated": updated, "identity_source": source,
    }
    row, = read(manager)
    assert row.updated_at == (updated if isinstance(updated, datetime) else None)
    assert row.identity_source == (source if source in ("manual_entry", "ocr_text", "selected_target") else None)
    assert row.captured_at.tzinfo is timezone.utc
    assert row.supply_percent is row.stock_value is None


@pytest.mark.parametrize("invalid", [object(), [], "invalid", {}, {"stock": None}, {"stock": True},
                                    {"stock": 101}, {"stock": 0, "supply": "0"}, {"value": 2**63}])
def test_one_malformed_catalog_state_aborts_capture_without_partial_return_or_mutation(manager, invalid):
    ids = list(BUSINESSES)
    manager._data.business_states[ids[0]] = {"stock": 50}
    manager._data.business_states[ids[-1]] = invalid
    states = manager._data.business_states.copy()
    output = []
    with pytest.raises(ValueError):
        output.extend(read(manager))
    assert output == []
    assert manager._data.business_states == states
    assert manager._data.business_states[ids[-1]] is invalid


def test_unsupported_supply_only_reading_aborts_entire_capture(manager):
    manager._data.business_states["bunker"] = {"stock": 0}
    unsupported = next(key for key, business in BUSINESSES.items() if not business.uses_supplies)
    manager._data.business_states[unsupported] = {"supply": 50}
    with pytest.raises(ValueError):
        read(manager)


def test_returned_batch_detaches_from_replacement_clear_and_old_aliases(manager):
    manager.set_manual_business_reading("bunker", 90, 0, 12345)
    manager.set_manual_business_reading("nightclub", 10, value=0)
    alias = manager.get_business_state("bunker")
    before = read(manager)
    copied = copy.deepcopy(before)
    manager.update_business_state("bunker", value=42)
    manager.clear_business_readings()
    alias.update(stock=0, supply=100, value=0, identity_source="ocr_text", updated=None)
    assert before == copied and len(before) == 2
    assert read(manager) == ()


def test_batch_waits_for_an_in_progress_locked_change(manager):
    manager.update_business_state("bunker", stock_percent=10)
    started, finished = threading.Event(), threading.Event()
    results, errors = [], []

    def capture():
        started.set()
        try:
            results.append(read(manager))
        finally:
            finished.set()

    thread = None
    try:
        with manager._data_lock:
            thread = start_thread(capture, errors)
            assert started.wait(2) and not finished.wait(0.05)
            manager._data.business_states["bunker"] = {"stock": 80}
            manager._data.business_states["nightclub"] = {"value": 0}
    finally:
        if thread is not None:
            thread.join(3)
    assert not thread.is_alive() and errors == []
    values = {row.business_id: row for row in results[0]}
    assert values["bunker"].stock_percent == 80 and values["nightclub"].stock_value == 0


@pytest.mark.parametrize("mutation", ["clear", "replacement"])
def test_complete_batch_serializes_public_mutation_between_rows(manager, mutation):
    entered, release, started, finished = (threading.Event() for _ in range(4))
    results, errors = [], []
    ids = list(BUSINESSES)[:2]

    class PausedReading(dict):
        def get(self, key, default=None):
            if key == "stock":
                entered.set()
                assert release.wait(3)
            return super().get(key, default)

    manager._data.business_states[ids[0]] = PausedReading(stock=10)
    manager._data.business_states[ids[1]] = {"stock": 20}

    def mutate():
        started.set()
        try:
            if mutation == "clear":
                manager.clear_business_readings()
            else:
                manager.update_business_state(ids[1], stock_percent=90)
        finally:
            finished.set()

    reader = start_thread(lambda: results.append(read(manager)), errors)
    writer = None
    try:
        assert entered.wait(2)
        writer = start_thread(mutate, errors)
        assert started.wait(2) and not finished.wait(0.05)
    finally:
        release.set()
        reader.join(3)
        if writer is not None:
            writer.join(3)
    assert not reader.is_alive() and writer is not None and not writer.is_alive()
    assert errors == [] and [row.stock_percent for row in results[0]] == [10, 20]
    if mutation == "clear":
        assert read(manager) == ()
    else:
        assert read(manager)[1].stock_percent == 90


@pytest.mark.parametrize("state", AppState)
def test_capture_has_no_live_accounting_settings_or_persistence_effects(manager, tmp_path, state):
    repo = Repository(str(tmp_path / "saved.db"))
    assert repo.initialize()
    try:
        owner = repo.get_or_create_character("Saved runner")
        repo.save_business_checkin(owner.id, "agency", stock_value=999)
        manager._repository = repo
        manager._data.character_id = owner.id
        manager._data.db_session_id = 7
        manager._data.current_money = 1500
        manager._data.session_earnings = 250
        manager._data.current_mission = "Current mission"
        manager._session_tracker.start_session(start_money=1250)
        manager.set_business_screen_target("agency")
        manager.set_manual_business_reading("bunker", 90, 0, 12345)
        manager.set_manual_business_reading("nightclub", value=0)
        manager._state = state
        before_data = manager.data
        before_business = business_snapshot(manager)
        before_config = copy.deepcopy(manager._settings._config)
        stats, generation = manager.session_stats, manager._business_screen_generation
        before_files = {path.relative_to(tmp_path): path.read_bytes()
                        for path in tmp_path.rglob("*") if path.is_file()}
        with sqlite3.connect(tmp_path / "saved.db") as db:
            before_rows = list(db.iterdump())
        snapshots = read(manager)
        assert {row.business_id for row in snapshots} == {"bunker", "nightclub"}
        assert manager.data == before_data and business_snapshot(manager) == before_business
        assert manager._settings._config == before_config
        assert manager.state is state and manager.business_screen_target == "agency"
        assert manager.session_stats is stats and manager._business_screen_generation == generation
        assert {path.relative_to(tmp_path): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()} == before_files
        with sqlite3.connect(tmp_path / "saved.db") as db:
            assert list(db.iterdump()) == before_rows
    finally:
        repo.close()
