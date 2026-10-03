"""Manual observations remain independent of captured business/accounting data."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
import json
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.models import Character
from src.database.repository import Repository


def domain():
    return import_module("src.database.business_checkins")


def test_business_checkin_contract_is_available():
    assert find_spec("src.database.business_checkins") is not None
    for method in ("save_business_checkin", "get_business_checkin_characters",
                   "get_business_checkin_board", "get_business_checkin_history"):
        assert hasattr(Repository, method)


@pytest.fixture
def journal(tmp_path):
    repo = Repository(str(tmp_path / "manual.db"))
    assert repo.initialize()
    with repo._session_scope() as db:
        first = Character(name="Saved runner", is_active=True)
        second = Character(name="Other runner", is_active=False)
        db.add_all([first, second])
        db.flush()
        result = SimpleNamespace(repo=repo, first=first.id, second=second.id,
                                 path=repo._db_path, engine=repo._session_factory.kw["bind"])
    yield result
    repo.close()
    result.engine.dispose()


def save(journal, **kwargs):
    values = dict(character_id=journal.first, business_id="bunker", stock_percent=0)
    values.update(kwargs)
    return journal.repo.save_business_checkin(**values)


def source_tables(path):
    with sqlite3.connect(path) as db:
        names = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('manual_business_checkins', 'sqlite_sequence')"
        )]
        return {name: (db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone(),
                       db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
                for name in names}


def test_save_is_append_only_unknown_is_not_zero_and_no_values_carry_forward(journal):
    before = source_tables(journal.path)
    first = save(journal, stock_percent=35, supply_percent=0,
                 stock_value=2**63 - 1, note="  <b>Café 雪 🚀</b>\nnext\tline ")
    second = save(journal, stock_percent=None, note="Only my own note")
    other = save(journal, character_id=journal.second, stock_percent=100)
    assert first.stock_value == 2**63 - 1
    assert first.supply_percent == 0
    assert second.stock_percent is second.supply_percent is second.stock_value is None
    assert first.note == "  <b>Café 雪 🚀</b>\nnext\tline "
    assert first.id < second.id < other.id
    assert first.recorded_at.tzinfo == timezone.utc
    assert journal.repo.get_business_checkin_board(journal.first).rows == (second,)
    assert journal.repo.get_business_checkin_history(journal.first, "bunker").rows == (second, first)
    assert journal.repo.get_business_checkin_board(journal.second).rows == (other,)
    assert source_tables(journal.path) == before
    with pytest.raises(FrozenInstanceError):
        first.note = "changed"


@pytest.mark.parametrize("field,value", [
    ("character_id", True), ("character_id", 0), ("character_id", -1),
    ("character_id", 2**63), ("character_id", 1.0),
    ("business_id", "future-business"), ("business_id", "BUNKER"),
    ("business_id", None), ("business_id", "bunker\0"),
    ("stock_percent", True), ("stock_percent", 1.0), ("stock_percent", -1),
    ("stock_percent", 101), ("supply_percent", "0"), ("supply_percent", False),
    ("stock_value", 2**63), ("stock_value", -1), ("stock_value", True),
    ("stock_value", 1.0), ("note", None), ("note", "x" * 2001),
    ("note", "\ud800"), ("note", "bad\0note"), ("note", "bad\rnote"),
    ("note", "bad\x7fnote"),
])
def test_invalid_save_is_rejected_before_database_access(field, value, monkeypatch):
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    kwargs = dict(character_id=1, business_id="bunker", stock_percent=0)
    kwargs[field] = value
    with pytest.raises(domain().BusinessCheckInValidationError):
        repo.save_business_checkin(**kwargs)


def test_manual_validation_limits_unicode_and_explicit_observation(journal):
    with pytest.raises(domain().BusinessCheckInValidationError):
        journal.repo.save_business_checkin(journal.first, "bunker", note=" \n\t ")
    saved = save(journal, stock_percent=100, supply_percent=100, stock_value=0, note="🚀" * 2000)
    assert len(saved.note) == 2000
    assert domain().normalize_business_checkin(None, 0, None, " ") == (None, 0, None, " ")


@pytest.mark.parametrize("text,expected", [("", None), ("  ", None), ("0", 0),
                                          ("0001", 1), ("100", 100)])
def test_percent_parser_accepts_only_explicit_ascii_numbers(text, expected):
    assert domain().parse_checkin_percent(text) == expected


@pytest.mark.parametrize("text", [None, True, 1, "-1", "+1", "1.0", "1e2", "1,000",
                                 "$1", "١", "１", "10%", "101", " 1", "1 ", "9" * 10000])
def test_percent_parser_rejects_ambiguous_or_out_of_range_input(text):
    with pytest.raises(domain().BusinessCheckInValidationError):
        domain().parse_checkin_percent(text)


def test_value_parser_is_exact_at_sqlite_integer_boundary():
    assert domain().parse_checkin_value(str(2**63 - 1)) == 2**63 - 1
    assert domain().parse_checkin_value("0" * 10000 + "1") == 1
    with pytest.raises(domain().BusinessCheckInValidationError):
        domain().parse_checkin_value(str(2**63))


def test_latest_and_history_follow_insertion_when_clock_ties_or_moves_backwards(journal, monkeypatch):
    times = iter([datetime(2026, 1, 2, tzinfo=timezone.utc)] * 2
                 + [datetime(2025, 1, 1, tzinfo=timezone.utc)] * 3)
    monkeypatch.setattr("src.database.repository.utc_now", lambda: next(times))
    rows = [save(journal, stock_percent=index) for index in range(3)]
    assert journal.repo.get_business_checkin_board(journal.first).rows == (rows[-1],)
    assert journal.repo.get_business_checkin_history(journal.first, "bunker").rows == tuple(reversed(rows))


def test_history_is_bounded_and_has_consistent_empty_page_context(journal):
    saved = [save(journal, stock_percent=i) for i in range(8)]
    page = journal.repo.get_business_checkin_history(journal.first, "bunker", offset=2, limit=3)
    assert page.rows == tuple(reversed(saved))[2:5]
    assert (page.offset, page.limit, page.total, page.has_more) == (2, 3, 8, True)
    assert page.character_name == "Saved runner"
    empty = journal.repo.get_business_checkin_history(journal.first, "bunker", offset=99, limit=3)
    assert (empty.rows, empty.total, empty.has_more) == ((), 8, False)
    assert journal.repo.get_business_checkin_board(journal.second).rows == ()


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"offset": True}, {"offset": 1000001},
                                    {"offset": 1.0}, {"limit": 0}, {"limit": 101},
                                    {"limit": False}, {"limit": "1"},
                                    {"business_id": ""}, {"business_id": "x" * 51}])
def test_invalid_history_request_never_accesses_storage(kwargs, monkeypatch):
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    values = dict(character_id=1, business_id="bunker")
    values.update(kwargs)
    with pytest.raises(domain().BusinessCheckInValidationError):
        repo.get_business_checkin_history(**values)


def test_missing_characters_and_storage_fail_strictly(journal, monkeypatch):
    with pytest.raises(domain().BusinessCheckInUnavailable):
        save(journal, character_id=9999)
    with pytest.raises(domain().BusinessCheckInUnavailable):
        journal.repo.get_business_checkin_board(9999)
    with pytest.raises(domain().BusinessCheckInUnavailable):
        journal.repo.get_business_checkin_history(9999, "bunker")
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: False)
    with pytest.raises(domain().BusinessCheckInUnavailable) as error:
        repo.get_business_checkin_characters()
    assert "must-not-create" not in str(error.value)


def test_reports_freeze_context_rows_and_utc(journal):
    saved = save(journal)
    board = journal.repo.get_business_checkin_board(journal.first)
    page = journal.repo.get_business_checkin_history(journal.first, "bunker", limit=1)
    for snapshot in (board, page):
        report = snapshot.to_report()
        assert report["format_version"] == 1
        assert report["scope"] == "manual_observations"
        assert report["character"] == {"id": journal.first, "name": "Saved runner"}
        assert report["rows"][0]["recorded_at"].endswith("+00:00")
        assert report["captured_at"].endswith("+00:00")
        assert json.loads(json.dumps(report))["rows"][0]["stock_percent"] == 0
        report["rows"].clear()
        assert snapshot.rows == (saved,)
    save(journal, stock_percent=99)
    assert board.to_report()["rows"][0]["stock_percent"] == 0
    assert page.to_report()["pagination"] == {"offset": 0, "limit": 1, "total": 1,
                                              "has_more": False, "rows_exported": 1}


def test_unknown_valid_business_id_is_readable_and_labeled_without_game_inferences(journal):
    saved = save(journal)
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE manual_business_checkins SET business_id='retired_business' WHERE id=?", (saved.id,))
    board = journal.repo.get_business_checkin_board(journal.first)
    assert board.rows[0].business_id == "retired_business"
    assert domain().business_label("retired_business") == "retired_business"
    assert domain().business_label("bunker") == "Bunker"
    assert journal.repo.get_business_checkin_history(journal.first, "retired_business").total == 1


@pytest.mark.parametrize("expression", [
    "stock_percent=101", "supply_percent=-1", "stock_value=1.5",
    "stock_value=CAST(x'ff' AS TEXT)", "note=CAST(x'ff' AS TEXT)",
    "note=zeroblob(1000000)", "note=replace(hex(zeroblob(50000)), '0', 'a')",
    "note='bad' || char(0) || 'note'", "note=NULL",
    "note=' ' , stock_percent=NULL", "business_id='bad/id'",
    "business_id=CAST(x'ff' AS TEXT)", "recorded_at='not a timestamp'",
    "recorded_at=1", "recorded_at=zeroblob(1000000)",
])
def test_corrupt_latest_or_page_row_fails_instead_of_falling_back(journal, expression):
    save(journal, stock_percent=10)
    latest = save(journal, stock_percent=20)
    with sqlite3.connect(journal.path) as db:
        if "NULL" in expression and expression == "note=NULL":
            # Exercise invalid nullable text via a rebuilt disposable manual table.
            db.execute("ALTER TABLE manual_business_checkins RENAME TO original_manual")
            db.execute("CREATE TABLE manual_business_checkins AS SELECT * FROM original_manual")
        db.execute(f"UPDATE manual_business_checkins SET {expression} WHERE id=?", (latest.id,))
    with pytest.raises(domain().BusinessCheckInDataError) as error:
        journal.repo.get_business_checkin_board(journal.first)
    assert "ff" not in str(error.value)
    if not expression.startswith("business_id="):
        with pytest.raises(domain().BusinessCheckInDataError):
            journal.repo.get_business_checkin_history(journal.first, "bunker")


def test_commit_failure_returns_no_saved_record_and_rolls_back(journal):
    def fail_commit(session):
        raise OperationalError("commit", {}, Exception("private path must not leak"))
    event.listen(journal.repo._session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(domain().BusinessCheckInUnavailable) as error:
            save(journal)
        assert "private path" not in str(error.value)
    finally:
        event.remove(journal.repo._session_factory, "before_commit", fail_commit)
    assert journal.repo.get_business_checkin_history(journal.first, "bunker").total == 0


def test_parallel_saves_append_both_without_lost_observations(journal):
    ready = Barrier(2)
    def append(value):
        ready.wait()
        return save(journal, stock_percent=value)
    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = list(workers.map(append, [10, 20]))
    assert len({row.id for row in rows}) == 2
    assert journal.repo.get_business_checkin_history(journal.first, "bunker").rows == tuple(
        sorted(rows, key=lambda row: row.id, reverse=True))


def test_character_choices_are_detached_sorted_and_bounded(journal):
    with journal.repo._session_scope() as db:
        db.add(Character(name="Other runner", is_active=True))
    choices = journal.repo.get_business_checkin_characters()
    assert [(r.name, r.id) for r in choices] == sorted((r.name, r.id) for r in choices)
    assert [r.is_active for r in choices] == [False, True, True]
    with pytest.raises(FrozenInstanceError):
        choices[0].name = "changed"
    with sqlite3.connect(journal.path) as db:
        db.executemany("INSERT INTO characters(name,is_active) VALUES (?,0)", [(str(i),) for i in range(998)])
    with pytest.raises(domain().BusinessCheckInLimitError):
        journal.repo.get_business_checkin_characters()


def test_board_refuses_more_than_256_groups(journal):
    with sqlite3.connect(journal.path) as db:
        db.executemany("INSERT INTO manual_business_checkins "
                       "(character_id,business_id,recorded_at,stock_percent,note) "
                       "VALUES (?,?,'2026-01-01 00:00:00',0,'')",
                       [(journal.first, f"unknown_{i}") for i in range(257)])
    with pytest.raises(domain().BusinessCheckInLimitError):
        journal.repo.get_business_checkin_board(journal.first)


def test_initialization_adds_only_manual_storage_to_legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    repo = Repository(str(path))
    assert repo.initialize()
    repo._session_factory.kw["bind"].dispose()

    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE manual_business_checkins")
        db.execute("INSERT INTO characters(id,name,is_active) VALUES (9,'Legacy character',1)")
        db.execute("INSERT INTO business_snapshots(character_id,business_type,stock_level) VALUES (9,'BUNKER',88)")
    before = source_tables(path)
    repo = Repository(str(path))
    assert repo.initialize()
    assert repo.get_business_checkin_board(9).rows == ()
    assert repo.save_business_checkin(9, "bunker", note="Manual observation").character_id == 9
    assert source_tables(path) == before
    with sqlite3.connect(path) as db:
        indexes = db.execute("PRAGMA index_list(manual_business_checkins)").fetchall()
        assert any([r[2] for r in db.execute(f'PRAGMA index_info("{index[1]}")')] ==
                   ["character_id", "business_id", "id"] for index in indexes)
    repo._session_factory.kw["bind"].dispose()


def test_deleted_character_cannot_slip_between_preflight_and_insert(journal):
    statements = []
    deleted = False
    def remove_owner(connection, cursor, statement, parameters, context, many):
        nonlocal deleted
        statements.append(statement)
        if "INSERT INTO manual_business_checkins" in statement and not deleted:
            deleted = True
            with sqlite3.connect(journal.path) as other:
                other.execute("DELETE FROM characters WHERE id=?", (journal.first,))
    event.listen(journal.engine, "before_cursor_execute", remove_owner)
    try:
        with pytest.raises(domain().BusinessCheckInUnavailable):
            save(journal)
    finally:
        event.remove(journal.engine, "before_cursor_execute", remove_owner)
    with sqlite3.connect(journal.path) as db:
        assert db.execute("SELECT COUNT(*) FROM manual_business_checkins").fetchone() == (0,)
    assert len(statements) == 1


@pytest.mark.parametrize("view", ["history", "board"])
def test_count_page_and_character_are_one_observation_under_concurrent_writes(journal, view):
    original = [save(journal, stock_percent=n) for n in (1, 2, 3)]
    with sqlite3.connect(journal.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []
    def concurrent_write(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(journal.path) as other:
                other.execute("UPDATE characters SET name='After observation' WHERE id=?", (journal.first,))
                other.execute("INSERT INTO manual_business_checkins "
                              "(character_id,business_id,recorded_at,stock_percent,note) "
                              "VALUES (?,'bunker','2026-01-01 00:00:00',99,'later')", (journal.first,))
    event.listen(journal.engine, "after_cursor_execute", concurrent_write)
    try:
        if view == "history":
            snapshot = journal.repo.get_business_checkin_history(journal.first, "bunker", limit=2)
            assert snapshot.total == 3
            assert snapshot.rows == tuple(reversed(original))[:2]
        else:
            snapshot = journal.repo.get_business_checkin_board(journal.first)
            assert snapshot.rows == (original[-1],)
        assert snapshot.character_name == "Saved runner"
        assert len(statements) == 1
    finally:
        event.remove(journal.engine, "after_cursor_execute", concurrent_write)
    fresh = journal.repo.get_business_checkin_history(journal.first, "bunker")
    assert fresh.total == 4
    assert fresh.character_name == "After observation"


def test_page_does_not_fetch_corrupt_payload_outside_its_range(journal):
    first = save(journal)
    latest = save(journal, stock_percent=25)
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE manual_business_checkins SET note=CAST(x'ff' AS TEXT) WHERE id=?", (first.id,))
    page = journal.repo.get_business_checkin_history(journal.first, "bunker", limit=1)
    assert page.total == 2
    assert page.rows == (latest,)
    assert journal.repo.get_business_checkin_board(journal.first).rows == (latest,)
    with pytest.raises(domain().BusinessCheckInDataError):
        journal.repo.get_business_checkin_history(journal.first, "bunker", offset=1, limit=1)


@pytest.mark.parametrize("expression", ["name=CAST(x'ff' AS TEXT)", "name=zeroblob(50000)"])
def test_corrupt_character_text_never_leaks_driver_decoding_errors(journal, expression):
    with sqlite3.connect(journal.path) as db:
        db.execute(f"UPDATE characters SET {expression} WHERE id=?", (journal.first,))
    for read in (journal.repo.get_business_checkin_characters,
                 lambda: journal.repo.get_business_checkin_board(journal.first),
                 lambda: journal.repo.get_business_checkin_history(journal.first, "bunker")):
        with pytest.raises(domain().BusinessCheckInDataError):
            read()


def test_upper_boundary_character_and_record_identifiers_remain_exact(journal):
    with sqlite3.connect(journal.path) as db:
        db.execute("INSERT INTO characters(id,name,is_active) VALUES (?,'Maximum',0)", (2**63 - 1,))
    saved = save(journal, character_id=2**63 - 1, stock_value=2**63 - 1)
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE manual_business_checkins SET id=? WHERE id=?", (2**63 - 1, saved.id))
    assert journal.repo.get_business_checkin_board(2**63 - 1).rows[0].id == 2**63 - 1
    assert journal.repo.get_business_checkin_history(2**63 - 1, "bunker").rows[0].stock_value == 2**63 - 1
    with pytest.raises(domain().BusinessCheckInUnavailable):
        save(journal, character_id=2**63 - 1)


def test_manually_constructed_snapshots_detach_rows_and_normalize_all_timestamps():
    stamp = datetime(2026, 1, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    record = domain().BusinessCheckIn(1, 1, "bunker", stamp, 0, None, None, "")
    rows = [record]
    board = domain().BusinessCheckInBoard(1, "Name", stamp, rows)
    page = domain().BusinessCheckInPage(1, "Name", "bunker", stamp, rows, 0, 25, 1)
    rows.clear()
    for snapshot in (board, page):
        assert snapshot.rows == (record,)
        assert snapshot.captured_at == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        assert snapshot.rows[0].recorded_at.tzinfo == timezone.utc


def test_additive_creation_supports_minimal_legacy_character_schema(tmp_path):
    path = tmp_path / "minimal.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE characters(id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER)")
        db.execute("INSERT INTO characters VALUES (1,'Minimal old character',1)")
        db.execute("CREATE TABLE business_snapshots(id INTEGER PRIMARY KEY, custom_legacy TEXT)")
        db.execute("INSERT INTO business_snapshots VALUES (1,'Do not migrate this schema')")
    before = source_tables(path)
    repo = Repository(str(path))
    assert repo.initialize()
    saved = repo.save_business_checkin(1, "bunker", stock_percent=0)
    assert repo.get_business_checkin_board(1).rows == (saved,)
    after = source_tables(path)
    assert all(after[name] == values for name, values in before.items())
    repo._session_factory.kw["bind"].dispose()
