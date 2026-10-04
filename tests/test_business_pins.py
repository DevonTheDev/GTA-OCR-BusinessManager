"""Manual pin preferences stay bounded, independent and transactionally merged."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
import sqlite3
from threading import Barrier, Event

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.repository import Repository


def domain():
    return import_module("src.database.business_pins")


@pytest.fixture
def pin_repo(tmp_path):
    repo = Repository(str(tmp_path / "pins.db"))
    assert repo.initialize()
    with sqlite3.connect(repo._db_path) as db:
        db.executemany("INSERT INTO characters(id,name,is_active) VALUES (?,?,?)",
                       [(1, "Same name", 1), (2, "Same name", 0),
                        (2**63 - 1, "Maximum identifier", 0)])
        db.execute("INSERT INTO business_snapshots(character_id,business_type,stock_level) "
                   "VALUES (1,'BUNKER',88)")
        db.execute("INSERT INTO manual_business_checkins "
                   "(character_id,business_id,recorded_at,note) "
                   "VALUES (1,'bunker','2026-01-01 00:00:00','Keep this observation')")
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def contents(path, *, exclude_pins=False):
    with sqlite3.connect(path) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if not exclude_pins or row[0] != "manual_business_pins"]
        return {name: (db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone(),
                       db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
                for name in names}


def stored_pins(repo):
    with sqlite3.connect(repo._db_path) as db:
        return db.execute("SELECT character_id,business_id FROM manual_business_pins "
                          "ORDER BY character_id,business_id").fetchall()


def test_pin_contract_is_available():
    assert find_spec("src.database.business_pins") is not None
    assert hasattr(Repository, "get_manual_business_pins")
    assert hasattr(Repository, "set_manual_business_pin")


def test_reads_return_empty_detached_context_without_preference_rows(pin_repo):
    before = contents(pin_repo._db_path)
    snapshot = pin_repo.get_manual_business_pins(1)
    assert isinstance(snapshot, domain().ManualBusinessPins)
    assert (snapshot.character_id, snapshot.character_name, snapshot.business_ids) == (1, "Same name", ())
    assert snapshot.captured_at.tzinfo == timezone.utc
    assert contents(pin_repo._db_path) == before
    assert pin_repo._db_session is None
    with pytest.raises(FrozenInstanceError):
        snapshot.character_name = "Other"


def test_pin_unpin_reopen_and_duplicate_names_use_exact_owner_id(pin_repo):
    before = contents(pin_repo._db_path, exclude_pins=True)
    original = pin_repo.set_manual_business_pin(1, "bunker", True)
    both = pin_repo.set_manual_business_pin(1, "acid_lab", True)
    other = pin_repo.set_manual_business_pin(2, "bunker", True)
    maximum = pin_repo.set_manual_business_pin(2**63 - 1, "acid_lab", True)
    assert original.business_ids == ("bunker",)
    assert both.business_ids == ("acid_lab", "bunker")
    assert (other.character_id, other.business_ids) == (2, ("bunker",))
    assert maximum.character_id == 2**63 - 1
    assert pin_repo.set_manual_business_pin(1, "bunker", False).business_ids == ("acid_lab",)
    assert pin_repo.get_manual_business_pins(2).business_ids == ("bunker",)
    assert contents(pin_repo._db_path, exclude_pins=True) == before
    reopened = Repository(pin_repo._db_path)
    try:
        assert reopened.get_manual_business_pins(1).business_ids == ("acid_lab",)
        assert reopened.get_manual_business_pins(2**63 - 1).business_ids == ("acid_lab",)
    finally:
        reopened.close()
        reopened._session_factory.kw["bind"].dispose()


def test_idempotent_desired_state_does_not_rewrite_any_rows(pin_repo):
    engine = pin_repo._session_factory.kw["bind"]
    pin_repo.set_manual_business_pin(1, "bunker", True)
    before = contents(pin_repo._db_path)
    statements = []
    def collect(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", collect)
    try:
        assert pin_repo.set_manual_business_pin(1, "bunker", True).business_ids == ("bunker",)
        assert pin_repo.set_manual_business_pin(1, "acid_lab", False).business_ids == ("bunker",)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    assert contents(pin_repo._db_path) == before


@pytest.mark.parametrize("field,value", [
    ("character_id", True), ("character_id", False), ("character_id", 0),
    ("character_id", -1), ("character_id", 2**63), ("character_id", 1.0),
    ("character_id", "1"), ("character_id", None), ("character_id", []),
    ("business_id", ""), ("business_id", "retired_business"),
    ("business_id", "BUNKER"), ("business_id", "bunker\0"),
    ("business_id", "bad/id"), ("business_id", " x"),
    ("business_id", "x" * 51), ("business_id", "\ud800"),
    ("business_id", b"bunker"), ("business_id", None),
    ("pinned", 0), ("pinned", 1), ("pinned", 1.0),
    ("pinned", "true"), ("pinned", None), ("pinned", []),
])
def test_invalid_write_never_initializes_storage(field, value, tmp_path, monkeypatch):
    path = tmp_path / "must-not-exist.db"
    repo = Repository(str(path))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    values = dict(character_id=1, business_id="bunker", pinned=True)
    values[field] = value
    with pytest.raises(domain().BusinessPinValidationError):
        repo.set_manual_business_pin(**values)
    assert not path.exists()


@pytest.mark.parametrize("value", [True, False, 0, -1, 2**63, 1.0, "1", None, []])
def test_invalid_read_never_initializes_storage(value, tmp_path, monkeypatch):
    path = tmp_path / "must-not-exist.db"
    repo = Repository(str(path))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    with pytest.raises(domain().BusinessPinValidationError):
        repo.get_manual_business_pins(value)
    assert not path.exists()


def test_unknown_legacy_pins_remain_readable_and_removable_without_observations(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.executemany("INSERT INTO manual_business_pins VALUES (?,?)",
                       [(1, "unknown-z"), (1, "Unknown.a"), (1, "x" * 50)])
    assert pin_repo.get_manual_business_pins(1).business_ids == ("Unknown.a", "unknown-z", "x" * 50)
    assert pin_repo.set_manual_business_pin(1, "unknown-z", False).business_ids == ("Unknown.a", "x" * 50)
    assert pin_repo.set_manual_business_pin(1, "absent_unknown", False).business_ids == ("Unknown.a", "x" * 50)


def test_missing_or_removed_owner_is_unavailable_even_with_orphan_pins(pin_repo):
    pin_repo.set_manual_business_pin(1, "bunker", True)
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DELETE FROM characters WHERE id=1")
    before = contents(pin_repo._db_path)
    for operation in (lambda: pin_repo.get_manual_business_pins(1),
                      lambda: pin_repo.set_manual_business_pin(1, "acid_lab", True),
                      lambda: pin_repo.set_manual_business_pin(1, "bunker", False),
                      lambda: pin_repo.get_manual_business_pins(9999)):
        with pytest.raises(domain().BusinessPinUnavailable):
            operation()
    assert contents(pin_repo._db_path) == before


@pytest.mark.parametrize("expression", [
    "name=CAST(x'ff' AS TEXT)", "name=zeroblob(1000000)",
    "name=replace(hex(zeroblob(50000)), '0', 'a')", "name=NULL",
])
def test_corrupt_owner_is_a_safe_data_error_for_read_and_write(pin_repo, expression):
    with sqlite3.connect(pin_repo._db_path) as db:
        # Nullable legacy schema lets NULL corruption reach the admission boundary.
        db.execute("ALTER TABLE characters RENAME TO original_characters")
        db.execute("CREATE TABLE characters AS SELECT * FROM original_characters")
        db.execute(f"UPDATE characters SET {expression} WHERE id=1")
    for operation in (lambda: pin_repo.get_manual_business_pins(1),
                      lambda: pin_repo.set_manual_business_pin(1, "bunker", True)):
        with pytest.raises(domain().BusinessPinDataError) as error:
            operation()
        assert "SELECT" not in str(error.value)
        assert str(pin_repo._db_path) not in str(error.value)
    assert stored_pins(pin_repo) == []


@pytest.mark.parametrize("expression", [
    "business_id=CAST(x'ff' AS TEXT)", "business_id=zeroblob(1000000)",
    "business_id=replace(hex(zeroblob(50000)), '0', 'a')", "business_id=NULL",
    "business_id='bad/id'", "business_id='bunker' || char(0)", "business_id=1",
    "business_id=''", "character_id='1'", "character_id=1.0",
])
def test_corrupt_pin_storage_fails_closed_without_mutation(pin_repo, expression):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DROP TABLE manual_business_pins")
        # No affinity on business_id catches actual integer rather than text coercion.
        db.execute("CREATE TABLE manual_business_pins(character_id NUMERIC, business_id)")
        db.execute("INSERT INTO manual_business_pins VALUES (1,'bunker')")
        if expression.startswith("character_id="):
            db.execute("DROP TABLE manual_business_pins")
            affinity = "REAL" if expression.endswith("1.0") else "TEXT"
            db.execute(f"CREATE TABLE manual_business_pins(character_id {affinity}, business_id)")
            db.execute("INSERT INTO manual_business_pins VALUES (1,'bunker')")
        db.execute(f"UPDATE manual_business_pins SET {expression}")
    for operation in (lambda: pin_repo.get_manual_business_pins(1),
                      lambda: pin_repo.set_manual_business_pin(1, "acid_lab", True),
                      lambda: pin_repo.set_manual_business_pin(1, "bunker", False)):
        with pytest.raises(domain().BusinessPinDataError):
            operation()


def test_duplicate_corrupt_pin_rows_are_not_silently_deduplicated(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DROP TABLE manual_business_pins")
        db.execute("CREATE TABLE manual_business_pins(character_id INTEGER, business_id TEXT)")
        db.executemany("INSERT INTO manual_business_pins VALUES (1,?)", [("bunker",), ("bunker",)])
    with pytest.raises(domain().BusinessPinDataError):
        pin_repo.get_manual_business_pins(1)


def test_corrupt_duplicate_owner_identity_is_not_silently_chosen(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DROP TABLE characters")
        db.execute("CREATE TABLE characters(id INTEGER, name TEXT)")
        db.executemany("INSERT INTO characters VALUES (1,?)", [("First",), ("Second",)])
    for operation in (lambda: pin_repo.get_manual_business_pins(1),
                      lambda: pin_repo.set_manual_business_pin(1, "bunker", True)):
        with pytest.raises(domain().BusinessPinDataError):
            operation()
    assert stored_pins(pin_repo) == []


@pytest.mark.parametrize("affinity,stored_id", [("TEXT", "1"), ("REAL", 1.0)])
def test_actual_noninteger_owner_storage_is_not_coerced(pin_repo, affinity, stored_id):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DROP TABLE characters")
        db.execute(f"CREATE TABLE characters(id {affinity}, name TEXT)")
        db.execute("INSERT INTO characters VALUES (?, 'Owner')", (stored_id,))
    with pytest.raises(domain().BusinessPinDataError):
        pin_repo.get_manual_business_pins(1)
    with pytest.raises(domain().BusinessPinDataError):
        pin_repo.set_manual_business_pin(1, "bunker", True)


def test_legacy_owner_name_uses_bounded_utf8_bytes_without_renaming(pin_repo):
    name = "🚀" * 1024
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("UPDATE characters SET name=? WHERE id=1", (name,))
    assert pin_repo.set_manual_business_pin(1, "bunker", True).character_name == name
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("UPDATE characters SET name=? WHERE id=1", (name + "x",))
    with pytest.raises(domain().BusinessPinDataError):
        pin_repo.get_manual_business_pins(1)


def test_cap_allows_known_reuse_and_unknown_removal_but_not_new_pin(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.executemany("INSERT INTO manual_business_pins VALUES (1,?)",
                       [("bunker",)] + [(f"old_{i:03}",) for i in range(255)])
    assert len(pin_repo.get_manual_business_pins(1).business_ids) == 256
    assert len(pin_repo.set_manual_business_pin(1, "bunker", True).business_ids) == 256
    with pytest.raises(domain().BusinessPinLimitError):
        pin_repo.set_manual_business_pin(1, "acid_lab", True)
    assert len(pin_repo.set_manual_business_pin(1, "old_000", False).business_ids) == 255
    assert len(pin_repo.set_manual_business_pin(1, "acid_lab", True).business_ids) == 256
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("INSERT INTO manual_business_pins VALUES (1,'too_many')")
    with pytest.raises(domain().BusinessPinLimitError):
        pin_repo.get_manual_business_pins(1)
    with pytest.raises(domain().BusinessPinLimitError):
        pin_repo.set_manual_business_pin(1, "bunker", False)


def test_initialization_is_additive_to_minimal_legacy_schema(tmp_path):
    path = tmp_path / "minimal.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE characters(id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO characters VALUES (9,'Old owner')")
        db.execute("CREATE TABLE business_snapshots(id INTEGER PRIMARY KEY, legacy TEXT)")
        db.execute("INSERT INTO business_snapshots VALUES (1,'Keep legacy schema and rows')")
    before = contents(path)
    repo = Repository(str(path))
    try:
        assert repo.get_manual_business_pins(9).business_ids == ()
        assert repo.set_manual_business_pin(9, "bunker", True).business_ids == ("bunker",)
        after = contents(path)
        assert all(after[name] == original for name, original in before.items())
        with sqlite3.connect(path) as db:
            columns = db.execute("PRAGMA table_info(manual_business_pins)").fetchall()
            assert [(row[1], row[5]) for row in columns] == [("character_id", 1), ("business_id", 2)]
            keys = db.execute("PRAGMA foreign_key_list(manual_business_pins)").fetchall()
            assert any((row[2], row[3], row[4]) == ("characters", "character_id", "id") for row in keys)
            assert len(columns) == 2
    finally:
        repo.close()
        repo._session_factory.kw["bind"].dispose()


def test_existing_database_adds_pin_table_without_other_changes(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("DROP TABLE manual_business_pins")
    before = contents(pin_repo._db_path)
    reopened = Repository(pin_repo._db_path)
    try:
        assert reopened.initialize()
        assert reopened.get_manual_business_pins(1).business_ids == ()
        after = contents(pin_repo._db_path)
        assert set(after) - set(before) == {"manual_business_pins"}
        assert all(after[name] == value for name, value in before.items())
    finally:
        reopened.close()
        reopened._session_factory.kw["bind"].dispose()


def test_commit_failure_rolls_back_and_never_returns_snapshot(pin_repo):
    engine = pin_repo._session_factory.kw["bind"]
    def fail_commit(connection):
        assert stored_pins(pin_repo) == []
        raise OperationalError("private SQL", {}, Exception("private path"))
    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(domain().BusinessPinUnavailable) as error:
            pin_repo.set_manual_business_pin(1, "bunker", True)
        assert "private" not in str(error.value)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert stored_pins(pin_repo) == []
    assert engine.pool.checkedout() == 0
    assert pin_repo.set_manual_business_pin(1, "bunker", True).business_ids == ("bunker",)
    assert stored_pins(pin_repo) == [(1, "bunker")]


def test_failed_unpin_commit_retains_original_pin(pin_repo):
    pin_repo.set_manual_business_pin(1, "bunker", True)
    engine = pin_repo._session_factory.kw["bind"]
    def fail_commit(connection):
        assert stored_pins(pin_repo) == [(1, "bunker")]
        raise OperationalError("private SQL", {}, Exception("private path"))
    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(domain().BusinessPinUnavailable):
            pin_repo.set_manual_business_pin(1, "bunker", False)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert stored_pins(pin_repo) == [(1, "bunker")]
    assert engine.pool.checkedout() == 0


def test_trigger_corruption_rolls_back_validated_write(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.execute("CREATE TRIGGER corrupt_pin AFTER INSERT ON manual_business_pins "
                   "BEGIN UPDATE manual_business_pins SET business_id='bad/id' "
                   "WHERE character_id=NEW.character_id; END")
    with pytest.raises(domain().BusinessPinDataError):
        pin_repo.set_manual_business_pin(1, "bunker", True)
    assert stored_pins(pin_repo) == []
    assert pin_repo._session_factory.kw["bind"].pool.checkedout() == 0


def test_failed_initialization_is_safe_without_raw_storage_details(tmp_path, monkeypatch):
    repo = Repository(str(tmp_path / "private.db"))
    monkeypatch.setattr(repo, "initialize", lambda: False)
    for operation in (lambda: repo.get_manual_business_pins(1),
                      lambda: repo.set_manual_business_pin(1, "bunker", True)):
        with pytest.raises(domain().BusinessPinUnavailable) as error:
            operation()
        assert "private" not in str(error.value)


def test_snapshots_detach_ids_and_normalize_offset_and_legacy_utc():
    ids = ["bunker"]
    stamp = datetime(2026, 1, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    snapshot = domain().ManualBusinessPins(1, "Name", stamp, ids)
    ids.clear()
    assert snapshot.business_ids == ("bunker",)
    assert snapshot.captured_at == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert domain().ManualBusinessPins(1, "Name", datetime(2026, 1, 1), ()).captured_at.tzinfo == timezone.utc


def test_reads_capture_owner_and_pins_in_one_sqlite_observation(pin_repo):
    pin_repo.set_manual_business_pin(1, "bunker", True)
    engine = pin_repo._session_factory.kw["bind"]
    with sqlite3.connect(pin_repo._db_path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []
    def change_after_read(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(pin_repo._db_path) as other:
                other.execute("UPDATE characters SET name='Later' WHERE id=1")
                other.execute("INSERT INTO manual_business_pins VALUES (1,'acid_lab')")
    event.listen(engine, "after_cursor_execute", change_after_read)
    try:
        snapshot = pin_repo.get_manual_business_pins(1)
        assert snapshot.character_name == "Same name"
        assert snapshot.business_ids == ("bunker",)
        assert len(statements) == 1
    finally:
        event.remove(engine, "after_cursor_execute", change_after_read)
    assert pin_repo.get_manual_business_pins(1).character_name == "Later"
    assert pin_repo.get_manual_business_pins(1).business_ids == ("acid_lab", "bunker")


@pytest.mark.parametrize("second_business", ["acid_lab", "bunker"])
def test_actual_competing_writers_merge_distinct_changes_or_reuse_same_pin(pin_repo, second_business):
    barrier = Barrier(2)
    workers = [Repository(pin_repo._db_path), Repository(pin_repo._db_path)]
    for worker in workers:
        assert worker.initialize()
    def write(worker, business_id):
        barrier.wait(timeout=5)
        return worker.set_manual_business_pin(1, business_id, True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(write, workers[0], "bunker")
            second = pool.submit(write, workers[1], second_business)
            returned = [first.result(timeout=10), second.result(timeout=10)]
        expected = tuple(sorted({"bunker", second_business}))
        assert pin_repo.get_manual_business_pins(1).business_ids == expected
        assert expected in [snapshot.business_ids for snapshot in returned]
        assert all(worker._session_factory.kw["bind"].pool.checkedout() == 0 for worker in workers)
    finally:
        for worker in workers:
            worker.close()
            worker._session_factory.kw["bind"].dispose()


def test_competing_new_pins_cannot_overflow_last_available_slot(pin_repo):
    with sqlite3.connect(pin_repo._db_path) as db:
        db.executemany("INSERT INTO manual_business_pins VALUES (1,?)",
                       [(f"old_{i:03}",) for i in range(255)])
    barrier = Barrier(2)
    workers = [Repository(pin_repo._db_path), Repository(pin_repo._db_path)]
    for worker in workers:
        assert worker.initialize()
    def write(worker, business_id):
        barrier.wait(timeout=5)
        try:
            return worker.set_manual_business_pin(1, business_id, True)
        except domain().BusinessPinLimitError:
            return "full"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, worker, business_id)
                       for worker, business_id in zip(workers, ("bunker", "acid_lab"))]
            results = [future.result(timeout=10) for future in futures]
        assert results.count("full") == 1
        snapshot = pin_repo.get_manual_business_pins(1)
        assert len(snapshot.business_ids) == 256
        assert len(set(snapshot.business_ids) & {"bunker", "acid_lab"}) == 1
        assert all(worker._session_factory.kw["bind"].pool.checkedout() == 0 for worker in workers)
    finally:
        for worker in workers:
            worker.close()
            worker._session_factory.kw["bind"].dispose()


@pytest.mark.parametrize("first_desired", [True, False])
def test_opposite_writes_follow_actual_serialized_transaction_order(pin_repo, first_desired):
    if not first_desired:
        pin_repo.set_manual_business_pin(1, "bunker", True)
    workers = [Repository(pin_repo._db_path), Repository(pin_repo._db_path)]
    for worker in workers:
        assert worker.initialize()
    first_locked, second_attempting = Event(), Event()
    def hold_first(connection, cursor, statement, parameters, context, many):
        if statement == "BEGIN IMMEDIATE":
            first_locked.set()
            assert second_attempting.wait(5)
    def mark_second(connection, cursor, statement, parameters, context, many):
        if statement == "BEGIN IMMEDIATE":
            assert first_locked.wait(5)
            second_attempting.set()
    event.listen(workers[0]._session_factory.kw["bind"], "after_cursor_execute", hold_first)
    event.listen(workers[1]._session_factory.kw["bind"], "before_cursor_execute", mark_second)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(workers[0].set_manual_business_pin, 1, "bunker", first_desired)
            assert first_locked.wait(5)
            second = pool.submit(workers[1].set_manual_business_pin, 1, "bunker", not first_desired)
            assert first.result(timeout=10).business_ids == (("bunker",) if first_desired else ())
            assert second.result(timeout=10).business_ids == (() if first_desired else ("bunker",))
        assert pin_repo.get_manual_business_pins(1).business_ids == (() if first_desired else ("bunker",))
    finally:
        for worker in workers:
            worker.close()
            worker._session_factory.kw["bind"].dispose()


def test_owner_removed_before_writer_lock_is_never_pinned(pin_repo):
    engine = pin_repo._session_factory.kw["bind"]
    def delete_before_lock(connection, cursor, statement, parameters, context, many):
        if statement == "BEGIN IMMEDIATE":
            with sqlite3.connect(pin_repo._db_path) as other:
                other.execute("DELETE FROM characters WHERE id=1")
    event.listen(engine, "before_cursor_execute", delete_before_lock)
    try:
        with pytest.raises(domain().BusinessPinUnavailable):
            pin_repo.set_manual_business_pin(1, "bunker", True)
    finally:
        event.remove(engine, "before_cursor_execute", delete_before_lock)
    assert stored_pins(pin_repo) == []


def test_close_and_repeated_calls_release_fresh_sessions(pin_repo):
    engine = pin_repo._session_factory.kw["bind"]
    for desired in (True, False, True):
        snapshot = pin_repo.set_manual_business_pin(1, "bunker", desired)
        pin_repo.close()
        assert snapshot.business_ids == (("bunker",) if desired else ())
        assert pin_repo.get_manual_business_pins(1).business_ids == snapshot.business_ids
        assert pin_repo._db_session is None
        assert engine.pool.checkedout() == 0
