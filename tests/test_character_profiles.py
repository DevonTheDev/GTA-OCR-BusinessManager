"""Saved characters can be created independently of capture and live sessions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from importlib import import_module
from importlib.util import find_spec
import sqlite3
from threading import Event

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import OperationalError

from src.database.repository import Repository


def domain():
    return import_module("src.database.character_profiles")


@pytest.fixture
def profile_repo(tmp_path):
    repo = Repository(str(tmp_path / "profiles.db"))
    assert repo.initialize()
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def snapshot(path, *, include_characters=True):
    with sqlite3.connect(path) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if include_characters or row[0] != "characters"]
        return {name: (db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone(),
                       db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall())
                for name in names}


def test_saved_character_contract_is_available():
    assert find_spec("src.database.character_profiles") is not None
    assert hasattr(Repository, "create_saved_character")


def test_empty_database_creation_reopens_and_owns_manual_observations(tmp_path):
    path = tmp_path / "new.db"
    repo = Repository(str(path))
    assert not path.exists()
    saved = repo.create_saved_character("  Café 雪 🚀  ")
    assert (saved.id, saved.name, saved.created) == (1, "Café 雪 🚀", True)
    assert isinstance(saved, domain().SavedCharacterResult)
    with pytest.raises(FrozenInstanceError):
        saved.name = "other"
    assert [(row.id, row.name, row.is_active) for row in repo.get_business_checkin_characters()] == [
        (saved.id, saved.name, False)]
    assert repo.get_business_checkin_board(saved.id).rows == ()
    before_observations = snapshot(path, include_characters=False)
    assert all(not rows for name, (_, rows) in before_observations.items())
    repo.close()
    repo._session_factory.kw["bind"].dispose()
    reopened = Repository(str(path))
    try:
        same = reopened.create_saved_character("Café 雪 🚀")
        assert (same.id, same.name, same.created) == (saved.id, saved.name, False)
        observation = reopened.save_business_checkin(saved.id, "bunker", stock_percent=0,
                                                     note="Manual check-in")
        assert observation.character_id == saved.id
        assert reopened.get_business_checkin_board(saved.id).rows == (observation,)
        assert reopened.get_completed_session_history(saved.id).total == 0
        assert reopened.get_active_character() is None
    finally:
        reopened.close()
        reopened._session_factory.kw["bind"].dispose()


@pytest.mark.parametrize("value", [None, True, 1, 1.5, b"name", [], {}, "", " ", "\u3000\u00a0",
                                  "x" * 51, "🚀" * 51, "\ud800", "\udfff", "bad\0name",
                                  "name\n", "\tname", "bad\rname", "bad\x7fname", "bad\x85name",
                                  "name\u2028", "\u2029name", "bad\x1fname", "\u200b", "\ufeff\u200d"])
def test_invalid_input_cannot_initialize_or_touch_storage(value, tmp_path, monkeypatch):
    path = tmp_path / "must-not-exist.db"
    repo = Repository(str(path))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("invalid input accessed storage"))
    with pytest.raises(domain().CharacterProfileValidationError):
        repo.create_saved_character(value)
    assert not path.exists()


@pytest.mark.parametrize("value, expected", [(" \u3000Name\u00a0 ", "Name"), ("x" * 50, "x" * 50),
                                            ("🚀" * 50, "🚀" * 50), ("A  B", "A  B"),
                                            ("<b>Alice</b>", "<b>Alice</b>"),
                                            ("👩\u200d🚀", "👩\u200d🚀"), ("e\u0301", "e\u0301")])
def test_normalization_preserves_valid_unicode_and_only_trims_edges(profile_repo, value, expected):
    assert domain().normalize_character_name(value) == expected
    result = profile_repo.create_saved_character(value)
    assert result.name == expected


def test_exact_reuse_preserves_every_existing_field_and_other_tables(profile_repo):
    existing = profile_repo.get_or_create_character("Alice")
    session = profile_repo.start_session(existing, start_money=50)
    assert profile_repo.end_session(session.id, end_money=70)
    profile_repo.log_activity(session.id, "CONTACT_MISSION", "Existing", earnings=20)
    profile_repo.log_earning(session.id, 20, "Existing", 70)
    profile_repo.save_business_snapshot(existing.id, "bunker", 30, 40, 1000)
    profile_repo.save_session_annotation(session.id, "Keep", ("old",), "Unchanged", 0)
    profile_repo.save_business_checkin(existing.id, "bunker", note="Keep this")
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("UPDATE characters SET created_at='2000-01-02 03:04:05', is_active=1 WHERE id=?",
                   (existing.id,))
    before = snapshot(profile_repo._db_path)
    reused = profile_repo.create_saved_character(" Alice ")
    assert (reused.id, reused.name, reused.created) == (existing.id, "Alice", False)
    assert snapshot(profile_repo._db_path) == before
    new = profile_repo.create_saved_character("Bob")
    assert new.id != existing.id
    after = snapshot(profile_repo._db_path)
    assert {key: value for key, value in after.items() if key != "characters"} == {
        key: value for key, value in before.items() if key != "characters"}
    assert after["characters"][0] == before["characters"][0]
    assert after["characters"][1][0] == before["characters"][1][0]
    assert after["characters"][1][1][3] == 0


@pytest.mark.parametrize("active", [0, 1, None, 2])
def test_existing_nullable_or_nonstandard_active_flags_are_not_repaired(profile_repo, active):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("INSERT INTO characters (name, is_active, created_at) VALUES ('Existing', ?, NULL)", (active,))
    before = snapshot(profile_repo._db_path)
    result = profile_repo.create_saved_character("Existing")
    assert not result.created
    assert snapshot(profile_repo._db_path) == before


def test_case_whitespace_and_unicode_normalization_are_distinct_identities(profile_repo):
    names = ["Alice", "alice", "A  lice", "A lice", "é", "e\u0301"]
    results = [profile_repo.create_saved_character(name) for name in names]
    assert len({row.id for row in results}) == len(names)
    assert [row.name for row in results] == names
    spaced = profile_repo.get_or_create_character(" Alice ")
    assert spaced.id not in {row.id for row in results}
    assert profile_repo.create_saved_character(" Alice ").id == results[0].id


def test_multiple_legacy_exact_matches_are_safe_ambiguity_without_mutation(profile_repo):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.executemany("INSERT INTO characters (name, is_active) VALUES ('Duplicate', ?)", [(0,), (1,)])
    before = snapshot(profile_repo._db_path)
    with pytest.raises(domain().CharacterProfileAmbiguous) as error:
        profile_repo.create_saved_character("Duplicate")
    assert "ID" in str(error.value)
    assert "Duplicate" not in str(error.value)
    assert snapshot(profile_repo._db_path) == before


def test_capacity_allows_last_slot_and_existing_reuse_but_not_another_insert(profile_repo):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.executemany("INSERT INTO characters (name, is_active) VALUES (?, 0)",
                       [(f"Runner {i}",) for i in range(999)])
    last = profile_repo.create_saved_character("Last slot")
    assert len(profile_repo.get_business_checkin_characters()) == 1000
    before = snapshot(profile_repo._db_path)
    assert profile_repo.create_saved_character("Last slot").id == last.id
    with pytest.raises(domain().CharacterProfileLimitError):
        profile_repo.create_saved_character("One too many")
    assert snapshot(profile_repo._db_path) == before


@pytest.mark.parametrize("bad_id", [0, -1])
def test_invalid_stored_exact_match_never_escapes(profile_repo, bad_id):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("INSERT INTO characters (id, name, is_active) VALUES (?, 'Corrupt', 0)", (bad_id,))
    before = snapshot(profile_repo._db_path)
    with pytest.raises(domain().CharacterProfileUnavailable):
        profile_repo.create_saved_character("Corrupt")
    assert snapshot(profile_repo._db_path) == before


@pytest.mark.parametrize("phase", ["before_commit", "before_flush"])
def test_failed_commit_or_flush_rolls_back_and_retry_creates_once(profile_repo, phase):
    def fail(session, *args):
        raise OperationalError("private SQL", {}, Exception("private/path"))
    event.listen(profile_repo._session_factory, phase, fail)
    before = snapshot(profile_repo._db_path)
    try:
        with pytest.raises(domain().CharacterProfileUnavailable) as error:
            profile_repo.create_saved_character("Retry")
        assert "private" not in str(error.value)
        assert snapshot(profile_repo._db_path) == before
    finally:
        event.remove(profile_repo._session_factory, phase, fail)
    saved = profile_repo.create_saved_character("Retry")
    assert saved.created
    assert len(profile_repo.get_business_checkin_characters()) == 1


def test_reuse_does_not_report_success_when_commit_fails(profile_repo):
    saved = profile_repo.create_saved_character("Existing")
    before = snapshot(profile_repo._db_path)
    def fail(session):
        raise OperationalError("commit", {}, Exception("private/path"))
    event.listen(profile_repo._session_factory, "before_commit", fail)
    try:
        with pytest.raises(domain().CharacterProfileUnavailable):
            profile_repo.create_saved_character("Existing")
    finally:
        event.remove(profile_repo._session_factory, "before_commit", fail)
    assert profile_repo.create_saved_character("Existing").id == saved.id
    assert snapshot(profile_repo._db_path) == before


def test_initialization_failure_is_safe_and_retry_works(tmp_path, monkeypatch):
    repo = Repository(str(tmp_path / "initialization.db"))
    original = repo.initialize
    monkeypatch.setattr(repo, "initialize", lambda: False)
    with pytest.raises(domain().CharacterProfileUnavailable):
        repo.create_saved_character("Retry")
    monkeypatch.setattr(repo, "initialize", original)
    try:
        assert repo.create_saved_character("Retry").created
    finally:
        repo._session_factory.kw["bind"].dispose()


def test_real_unavailable_database_is_safe(tmp_path):
    repo = Repository(str(tmp_path / "missing-parent" / "private.db"))
    with pytest.raises(domain().CharacterProfileUnavailable) as error:
        repo.create_saved_character("Unavailable")
    assert "private" not in str(error.value)
    assert "missing-parent" not in str(error.value)


def test_held_sqlite_lock_fails_safely_then_retry_succeeds(profile_repo):
    engine = profile_repo._session_factory.kw["bind"]
    def immediate_timeout(dbapi_connection, connection_record, connection_proxy):
        dbapi_connection.execute("PRAGMA busy_timeout=0")
    event.listen(engine, "checkout", immediate_timeout)
    before = snapshot(profile_repo._db_path)
    with sqlite3.connect(profile_repo._db_path) as holder:
        holder.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(domain().CharacterProfileUnavailable):
                profile_repo.create_saved_character("Locked")
            assert profile_repo.get_or_create_character("Locked") is None
        finally:
            holder.rollback()
    assert snapshot(profile_repo._db_path) == before
    assert profile_repo.create_saved_character("Locked").created


def test_minimal_legacy_schema_reuse_is_safe_and_insert_does_not_migrate(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER)")
        db.execute("INSERT INTO characters VALUES (12, 'Legacy', 1)")
    repo = Repository(str(path))
    assert repo.initialize()
    before = snapshot(path)
    try:
        assert repo.create_saved_character("Legacy").id == 12
        with pytest.raises(domain().CharacterProfileUnavailable):
            repo.create_saved_character("New")
        assert snapshot(path) == before
    finally:
        repo._session_factory.kw["bind"].dispose()


@pytest.mark.parametrize("winner_method,loser_method,expected_active", [
    ("create_saved_character", "create_saved_character", 0),
    ("get_or_create_character", "create_saved_character", 1),
    ("create_saved_character", "get_or_create_character", 0),
    ("get_or_create_character", "get_or_create_character", 1),
])
def test_cooperating_creators_lock_before_read_and_converge_on_one_id(
        profile_repo, winner_method, loser_method, expected_active):
    second = Repository(profile_repo._db_path)
    assert second.initialize()
    first_engine = profile_repo._session_factory.kw["bind"]
    second_engine = second._session_factory.kw["bind"]
    first_locked, second_attempting, release_first = Event(), Event(), Event()
    first_statements, second_statements = [], []

    def hold_first(conn, cursor, statement, parameters, context, executemany):
        first_statements.append(statement)
        if statement == "BEGIN IMMEDIATE":
            first_locked.set()
            assert release_first.wait(5), "first writer was not released"

    def observe_second(conn, cursor, statement, parameters, context, executemany):
        second_statements.append(statement)
        if statement == "BEGIN IMMEDIATE":
            second_attempting.set()

    event.listen(first_engine, "after_cursor_execute", hold_first)
    event.listen(second_engine, "before_cursor_execute", observe_second)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(getattr(profile_repo, winner_method), "Concurrent")
            try:
                assert first_locked.wait(5), "creator must lock before its first name lookup"
                other = workers.submit(getattr(second, loser_method), "Concurrent")
                assert second_attempting.wait(5), "second creator must attempt the same writer lock"
            finally:
                release_first.set()
            winner, loser = first.result(timeout=5), other.result(timeout=5)
        assert winner.id == loser.id
        assert first_statements[0] == second_statements[0] == "BEGIN IMMEDIATE"
        with sqlite3.connect(profile_repo._db_path) as db:
            assert db.execute("SELECT id, name, is_active FROM characters").fetchall() == [
                (winner.id, "Concurrent", expected_active)]
        if winner_method == "create_saved_character":
            assert winner.created
        if loser_method == "create_saved_character":
            assert not loser.created
    finally:
        release_first.set()
        event.remove(first_engine, "after_cursor_execute", hold_first)
        event.remove(second_engine, "before_cursor_execute", observe_second)
        second.close()
        second_engine.dispose()


@pytest.mark.parametrize("name", ["", " ", "x" * 101, "Legacy\nname"])
def test_legacy_entry_point_retains_permissive_names_active_default_and_detachment(profile_repo, name):
    old = profile_repo.get_or_create_character(name)
    assert old.name == name
    assert old.is_active is True
    assert inspect(old).detached
    assert profile_repo.get_or_create_character(name).id == old.id


def test_legacy_entry_point_keeps_first_match_for_old_duplicates(profile_repo):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.executemany("INSERT INTO characters (id, name, is_active) VALUES (?, 'Old duplicate', ?)",
                       [(4, 0), (8, 1)])
    before = snapshot(profile_repo._db_path)
    returned = profile_repo.get_or_create_character("Old duplicate")
    assert returned.id == 4
    assert returned.is_active is False
    assert inspect(returned).detached
    assert snapshot(profile_repo._db_path) == before


@pytest.mark.parametrize("field,value", [
    ("id", True), ("id", 0), ("id", -1), ("id", 2**63), ("id", 1.0), ("id", "1"),
    ("name", None), ("name", " Leading"), ("name", ""), ("name", "bad\nname"),
    ("name", "x" * 51), ("created", 1), ("created", None),
])
def test_result_rejects_invalid_or_repaired_identity_fields(field, value):
    fields = dict(id=1, name="Saved", created=False)
    fields[field] = value
    with pytest.raises(domain().CharacterProfileUnavailable):
        domain().SavedCharacterResult(**fields)


@pytest.mark.parametrize("raw_name,storage_type", [
    (b"Saved", "blob"), ("Saved", "text"), (None, "null"),
    (b"\xff", "text"), (b"\xed\xa0\x80", "text"), (b"x" * 201, "text"),
    (b"x" * 51, "text"), (b" Leading", "text"), (b"bad\x00name", "text"),
])
def test_bounded_storage_conversion_fails_safely(raw_name, storage_type):
    with pytest.raises(domain().CharacterProfileUnavailable):
        domain().saved_character_from_storage(
            {"id": 1, "name": raw_name, "name_type": storage_type}, created=False)


@pytest.mark.parametrize("replacement", ["'Changed'", "CAST(x'ff' AS TEXT)",
                                         "zeroblob(1000000)", "replace(hex(zeroblob(50000)), '0', 'a')"])
def test_invalid_inserted_storage_is_validated_and_rolled_back(profile_repo, replacement):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute(f"CREATE TRIGGER alter_insert AFTER INSERT ON characters BEGIN "
                   f"UPDATE characters SET name={replacement} WHERE id=NEW.id; END")
    before = snapshot(profile_repo._db_path)
    with pytest.raises(domain().CharacterProfileUnavailable):
        profile_repo.create_saved_character("Intended")
    assert snapshot(profile_repo._db_path) == before
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("DROP TRIGGER alter_insert")
    assert profile_repo.create_saved_character("Intended").created


def test_removed_inserted_row_is_not_acknowledged(profile_repo):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("CREATE TRIGGER erase_insert AFTER INSERT ON characters BEGIN "
                   "DELETE FROM characters WHERE id=NEW.id; END")
    with pytest.raises(domain().CharacterProfileUnavailable):
        profile_repo.create_saved_character("Missing")
    assert profile_repo.get_business_checkin_characters() == ()


def test_sql_syntax_in_name_remains_literal_text(profile_repo):
    name = "O'Connor; DROP TABLE characters;--"
    saved = profile_repo.create_saved_character(name)
    assert saved.name == name
    assert profile_repo.create_saved_character(name).id == saved.id
    assert len(profile_repo.get_business_checkin_characters()) == 1


def test_legacy_case_insensitive_schema_does_not_change_manual_exact_names(tmp_path):
    path = tmp_path / "nocase.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE characters (id INTEGER PRIMARY KEY, name TEXT COLLATE NOCASE, "
                   "is_active INTEGER, created_at DATETIME)")
        db.execute("INSERT INTO characters VALUES (1, 'Alice', 1, NULL)")
    repo = Repository(str(path))
    try:
        result = repo.create_saved_character("alice")
        assert result.created
        assert result.id != 1
        assert repo.create_saved_character("Alice").id == 1
    finally:
        repo._session_factory.kw["bind"].dispose()


@pytest.mark.parametrize("bad_id", ["'text'", "1.5", "NULL"])
def test_malformed_legacy_id_storage_fails_without_repair(tmp_path, bad_id):
    path = tmp_path / "bad-id.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE characters (id, name TEXT, is_active INTEGER)")
        db.execute(f"INSERT INTO characters VALUES ({bad_id}, 'Existing', 1)")
    repo = Repository(str(path))
    assert repo.initialize()
    before = snapshot(path)
    try:
        with pytest.raises(domain().CharacterProfileUnavailable):
            repo.create_saved_character("Existing")
        assert snapshot(path) == before
    finally:
        repo._session_factory.kw["bind"].dispose()


def test_corrupt_unrelated_legacy_names_are_not_decoded_by_lookup_or_capacity(profile_repo):
    with sqlite3.connect(profile_repo._db_path) as db:
        db.execute("INSERT INTO characters (name) VALUES (CAST(x'ff' AS TEXT))")
        db.execute("INSERT INTO characters (name) VALUES (zeroblob(1000000))")
    assert profile_repo.create_saved_character("Valid").created


def test_no_result_is_observable_until_a_separate_connection_sees_committed_identity(profile_repo):
    committing, allow_commit = Event(), Event()
    def hold_commit(session):
        committing.set()
        assert allow_commit.wait(5)
    event.listen(profile_repo._session_factory, "before_commit", hold_commit)
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(profile_repo.create_saved_character, "Committed")
            try:
                assert committing.wait(5)
                assert not future.done()
                with sqlite3.connect(profile_repo._db_path) as db:
                    assert db.execute("SELECT count(*) FROM characters").fetchone() == (0,)
            finally:
                allow_commit.set()
            saved = future.result(timeout=5)
        with sqlite3.connect(profile_repo._db_path) as db:
            assert db.execute("SELECT id, name FROM characters").fetchall() == [(saved.id, saved.name)]
    finally:
        allow_commit.set()
        event.remove(profile_repo._session_factory, "before_commit", hold_commit)
