"""Saved context stays separate from captured accounting, even under contention."""

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

from src.database.models import Activity, BusinessSnapshot, Character, Earnings, Session
from src.database.repository import DatabaseError, Repository, SessionHistoryItem
from src.utils.exporter import DataExporter


def annotations():
    return import_module("src.database.session_annotations")


def test_annotation_contract_is_available():
    assert find_spec("src.database.session_annotations") is not None
    assert hasattr(Repository, "get_session_annotation")
    assert hasattr(Repository, "save_session_annotation")


@pytest.fixture
def journal(tmp_path):
    repo = Repository(str(tmp_path / "journal.db"))
    assert repo.initialize()
    start = datetime(2026, 1, 2, 12)
    with repo._session_scope() as db:
        owner = Character(name="Saved runner", is_active=True)
        other = Character(name="Other runner", is_active=False)
        db.add_all([owner, other])
        db.flush()
        runs = [Session(character_id=character.id, started_at=start + timedelta(days=index),
                        ended_at=start + timedelta(days=index, hours=1),
                        start_money=1000, end_money=800, total_earnings=-200)
                for index, character in enumerate([owner, owner, other])]
        opened = Session(character_id=owner.id, started_at=start)
        db.add_all([*runs, opened])
        db.flush()
        db.add_all([
            Activity(session_id=runs[0].id, activity_type="VIP_WORK", activity_name="Captured",
                     notes="activity note remains separate", earnings=50, success=True),
            Earnings(session_id=runs[0].id, amount=-200, balance_after=800, source="Captured"),
            BusinessSnapshot(character_id=owner.id, business_type="BUNKER", stock_level=25),
        ])
        fixture = SimpleNamespace(repo=repo, first=runs[0].id, second=runs[1].id,
                                  third=runs[2].id, open=opened.id, owner=owner.id,
                                  other=other.id, output=tmp_path / "export.json")
    yield fixture
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def captured_contents(path):
    with sqlite3.connect(path) as connection:
        # Manual context and SQLite's sequence table are separate from captured
        # accounting; compare the schema and rows of the legacy source tables.
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('characters', 'sessions', 'activities', 'earnings', 'business_snapshots')"
        )]
        return {name: (connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone(),
                       connection.execute(f'SELECT * FROM "{name}" ORDER BY id').fetchall())
                for name in tables}


def save(journal, *, session_id=None, label="Evening run", tags=("Solo",), note="Saved note", revision=0):
    return journal.repo.save_session_annotation(session_id or journal.first, label, tags, note, revision)


def corrupt(journal, field, expression, params=()):
    with sqlite3.connect(journal.repo._db_path) as connection:
        connection.execute(f"UPDATE session_annotations SET {field}={expression} WHERE session_id=?",
                           (*params, journal.first))


def test_initializes_a_pre_annotation_schema_without_rewriting_captured_tables(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE characters (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
                                     created_at DATETIME, is_active BOOLEAN);
            CREATE TABLE sessions (id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL,
                                   started_at DATETIME, ended_at DATETIME, start_money INTEGER,
                                   end_money INTEGER, total_earnings INTEGER,
                                   FOREIGN KEY(character_id) REFERENCES characters(id));
            CREATE TABLE activities (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,
                activity_type VARCHAR(50) NOT NULL, activity_name VARCHAR(200), started_at DATETIME,
                ended_at DATETIME, duration_seconds INTEGER, earnings INTEGER, success BOOLEAN,
                business_type VARCHAR(50), notes VARCHAR(500));
            CREATE TABLE earnings (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL,
                timestamp DATETIME, amount INTEGER NOT NULL, source VARCHAR(100), balance_after INTEGER);
            CREATE TABLE business_snapshots (id INTEGER PRIMARY KEY, character_id INTEGER NOT NULL,
                business_type VARCHAR(50) NOT NULL, timestamp DATETIME, stock_level INTEGER,
                supply_level INTEGER, stock_value INTEGER);
            INSERT INTO characters VALUES (7, 'Legacy', '2020-01-01 12:00:00', 1);
            INSERT INTO sessions VALUES (9, 7, NULL, '2020-01-01 13:00:00', NULL, 900, -100);
            INSERT INTO activities VALUES (3, 9, 'UNKNOWN', 'Legacy job', NULL, NULL, NULL, NULL, NULL, NULL, 'old');
            INSERT INTO earnings VALUES (4, 9, NULL, -100, NULL, NULL);
            INSERT INTO business_snapshots VALUES (5, 7, 'BUNKER', NULL, NULL, NULL, NULL);
        """)
    before = captured_contents(path)
    repo = Repository(str(path))
    assert repo.initialize()
    assert captured_contents(path) == before
    with sqlite3.connect(path) as db:
        columns = db.execute("PRAGMA table_info(session_annotations)").fetchall()
    assert {row[1] for row in columns} == {"session_id", "label", "tags_text", "note", "revision", "updated_at"}
    assert {row[2] for row in columns if row[1] in {"label", "tags_text", "note"}} == {"TEXT"}
    assert repo.get_session_annotation(9) is None
    assert "annotation" not in repo.export_session_data(9)
    stored = repo.save_session_annotation(9, " Legacy journal ", ["Solo"], "notes", 0)
    assert stored.session_id == 9
    assert captured_contents(path) == before
    repo._session_factory.kw["bind"].dispose()


def test_saved_snapshot_is_normalized_immutable_and_detached(journal):
    before = captured_contents(journal.repo._db_path)
    note = "  <b>Café 雪 🚀</b>\nsecond\tline\r\n"
    saved = save(journal, label="  🚀 Evening  ", tags=[" Solo ", "SOLO", "Straße", "STRASSE", "雪"], note=note)
    assert saved.label == "🚀 Evening"
    assert saved.tags == ("Solo", "Straße", "雪")
    assert saved.note == note
    assert saved.revision == 1
    assert saved.updated_at.tzinfo == timezone.utc
    assert journal.repo.get_session_annotation(journal.first) == saved
    with pytest.raises(FrozenInstanceError):
        saved.label = "changed"
    with sqlite3.connect(journal.repo._db_path) as db:
        assert db.execute("SELECT tags_text FROM session_annotations").fetchone() == ("Solo, Straße, 雪",)
    payload = saved.to_dict()
    assert payload == {"available": True, "session_id": journal.first, "label": saved.label,
                       "tags": ["Solo", "Straße", "雪"], "note": note, "revision": 1,
                       "updated_at": saved.updated_at.isoformat()}
    payload["tags"].append("external")
    assert saved.tags == ("Solo", "Straße", "雪")
    assert captured_contents(journal.repo._db_path) == before


def test_code_point_limits_accept_astral_text_and_full_bounds(journal):
    saved = save(journal, label="🚀" * 80, tags=tuple(str(i) + "🚀" * 31 for i in range(8)), note="🚀" * 4000)
    assert len(saved.label) == 80
    assert len(saved.tags) == 8
    assert len(saved.note) == 4000


def test_manually_built_snapshot_detaches_tags_and_serializes_utc():
    tags = ["Solo"]
    stamp = datetime(2026, 1, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    snapshot = annotations().SessionAnnotation(1, "Label", tags, "Note", 1, stamp)
    tags.append("external")
    assert snapshot.tags == ("Solo",)
    assert snapshot.updated_at == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert snapshot.to_dict()["updated_at"] == "2026-01-01T12:00:00+00:00"


def test_exhausted_revision_fails_safely_without_mutating_saved_context(journal):
    save(journal)
    corrupt(journal, "revision", "?", (2**63 - 1,))
    original = journal.repo.get_session_annotation(journal.first)
    assert save(journal, revision=original.revision) == original
    with pytest.raises(DatabaseError, match="revision limit"):
        save(journal, label="unsaved", revision=original.revision)
    assert journal.repo.get_session_annotation(journal.first) == original


@pytest.mark.parametrize("field,bad", [
    ("label", None), ("label", 42), ("label", "🚀" * 81), ("label", "a\nb"),
    ("label", "a\tb"), ("label", "a\u2028b"), ("label", "a\x7fb"), ("label", "\ud800"),
    ("tags", "Solo"), ("tags", None), ("tags", ("",)), ("tags", ("a,b",)),
    ("tags", ("a\nb",)), ("tags", ("a\u2029b",)), ("tags", ("a\x00b",)),
    ("tags", (42,)), ("tags", ("x" * 33,)), ("tags", tuple(str(i) for i in range(9))),
    ("note", None), ("note", b"bytes"), ("note", "🚀" * 4001), ("note", "a\x00b"), ("note", "\udfff"),
    ("session_id", True), ("session_id", 0), ("session_id", -1), ("session_id", "1"),
    ("expected_revision", True), ("expected_revision", -1), ("expected_revision", "0"),
    ("expected_revision", 1.0),
])
def test_invalid_input_is_rejected_before_database_access(field, bad, monkeypatch):
    repo = Repository("unused.db")
    values = dict(session_id=1, label="ok", tags=(), note="", expected_revision=0)
    values[field] = bad
    def forbid_storage(*args, **kwargs):
        pytest.fail("invalid input must not reach SQLite")
    monkeypatch.setattr(repo, "_session_scope", forbid_storage)
    with pytest.raises(ValueError):
        repo.save_session_annotation(**values)


@pytest.mark.parametrize("bad", [True, False, 0, -1, "1", None, 1.5])
def test_read_rejects_invalid_identifiers_before_storage(bad, monkeypatch):
    repo = Repository("unused.db")
    monkeypatch.setattr(repo, "_session_scope", lambda: pytest.fail("invalid ID reached SQLite"))
    with pytest.raises(ValueError):
        repo.get_session_annotation(bad)


def test_revision_conflict_clear_tombstone_and_noop(journal):
    module = annotations()
    original = save(journal)
    assert save(journal, revision=1) == original
    cleared = save(journal, label="", tags=(), note="", revision=1)
    assert (cleared.label, cleared.tags, cleared.note, cleared.revision) == ("", (), "", 2)
    assert journal.repo.get_session_annotation(journal.first) == cleared
    for stale in [0, 1]:
        with pytest.raises(module.SessionAnnotationConflict) as caught:
            save(journal, revision=stale)
        assert caught.value.current == cleared
    assert journal.repo.get_session_annotation(journal.first) == cleared
    assert journal.repo.export_session_data(journal.first)["annotation"] == cleared.to_dict()
    with pytest.raises(module.SessionAnnotationConflict) as caught:
        save(journal, session_id=journal.second, revision=12)
    assert caught.value.current is None
    assert journal.repo.get_session_annotation(journal.second) is None


@pytest.mark.parametrize("kind", ["missing", "open", "orphan", "huge"])
def test_only_completed_sessions_with_available_characters_can_be_annotated(journal, kind):
    target = {"missing": 99999, "open": journal.open, "orphan": journal.third, "huge": 2**100}[kind]
    if kind == "orphan":
        with sqlite3.connect(journal.repo._db_path) as db:
            db.execute("DELETE FROM characters WHERE id=?", (journal.other,))
    before = captured_contents(journal.repo._db_path)
    for action in [lambda: journal.repo.get_session_annotation(target),
                   lambda: save(journal, session_id=target)]:
        with pytest.raises(annotations().SessionAnnotationUnavailable):
            action()
    assert captured_contents(journal.repo._db_path) == before


@pytest.mark.parametrize("mutation", ["DELETE FROM sessions WHERE id=?", "UPDATE sessions SET ended_at=NULL WHERE id=?"])
def test_target_is_revalidated_after_editor_loaded(journal, mutation):
    original = save(journal)
    with sqlite3.connect(journal.repo._db_path) as db:
        db.execute(mutation, (journal.first,))
    with pytest.raises(annotations().SessionAnnotationUnavailable):
        save(journal, label="unwanted", revision=original.revision)
    with sqlite3.connect(journal.repo._db_path) as db:
        assert db.execute("SELECT label, revision FROM session_annotations").fetchone() == (original.label, 1)


@pytest.mark.parametrize("initial_revision", [0, 1])
def test_real_sqlite_concurrent_saves_have_one_winner_and_one_explicit_conflict(journal, initial_revision):
    if initial_revision:
        save(journal)
    barrier = Barrier(2)
    repositories = [Repository(journal.repo._db_path) for _ in range(2)]
    for repo in repositories:
        assert repo.initialize()
    def compete(index):
        repo = repositories[index]
        barrier.wait(timeout=10)
        try:
            return repo.save_session_annotation(journal.first, f"Writer {index}", (), f"Note {index}", initial_revision)
        except annotations().SessionAnnotationConflict as error:
            return error
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, [0, 1]))
        winners = [item for item in results if isinstance(item, annotations().SessionAnnotation)]
        losers = [item for item in results if isinstance(item, annotations().SessionAnnotationConflict)]
        assert len(winners) == len(losers) == 1
        assert winners[0].revision == initial_revision + 1
        assert losers[0].current == winners[0]
        assert journal.repo.get_session_annotation(journal.first) == winners[0]
    finally:
        for repo in repositories:
            repo._session_factory.kw["bind"].dispose()


@pytest.mark.parametrize("existing", [False, True])
def test_commit_failure_rolls_back_all_annotation_fields_and_allows_retry(journal, existing):
    original = save(journal) if existing else None
    engine = journal.repo._session_factory.kw["bind"]
    before = captured_contents(journal.repo._db_path)
    def fail_commit(connection):
        raise OperationalError("COMMIT", {}, RuntimeError("injected commit failure"))
    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(DatabaseError):
            save(journal, label="Partial", tags=("Partial",), note="Partial", revision=int(existing))
    finally:
        event.remove(engine, "commit", fail_commit)
    assert journal.repo.get_session_annotation(journal.first) == original
    assert captured_contents(journal.repo._db_path) == before
    assert save(journal, label="Recovered", revision=int(existing)).label == "Recovered"


@pytest.mark.parametrize("field,expression,params", [
    ("label", "?", ("SECRET\ninvalid",)), ("label", "?", ("x" * 81,)),
    ("label", "?", (" not canonical ",)), ("label", "X'80'", ()),
    ("label", "CAST(X'80' AS TEXT)", ()),
    ("tags_text", "?", ("Solo,Squad",)), ("tags_text", "?", ("Solo, SOLO",)),
    ("tags_text", "?", ("Solo, ",)), ("tags_text", "?", ("SECRET\ninvalid",)),
    ("tags_text", "?", (", ".join(str(i) for i in range(9)),)),
    ("note", "?", ("SECRET\x00invalid",)), ("note", "?", ("x" * 4001,)),
    ("note", "CAST(X'80' AS TEXT)", ()),
    ("revision", "?", (0,)), ("revision", "?", (-1,)), ("revision", "?", (1.5,)),
    ("revision", "?", ("SECRET",)), ("updated_at", "?", ("SECRET",)),
    ("revision", "CAST(X'80' AS TEXT)", ()), ("revision", "X'80'", ()),
    ("updated_at", "?", ("2026-02-31 12:00:00",)), ("updated_at", "?", ("2026-01-01",)),
    ("updated_at", "X'80'", ()), ("updated_at", "NULL", ()),
])
def test_corrupt_annotations_do_not_hide_history_or_captured_export_or_allow_overwrite(journal, field, expression, params):
    save(journal)
    captured = journal.repo.export_session_data(journal.first)
    captured.pop("annotation")
    corrupt(journal, field, expression, params)
    with pytest.raises(annotations().InvalidSessionAnnotation) as caught:
        journal.repo.get_session_annotation(journal.first)
    assert "SECRET" not in str(caught.value)
    with pytest.raises(annotations().InvalidSessionAnnotation):
        save(journal, label="", tags=(), note="", revision=1)
    page = journal.repo.get_completed_session_history()
    row = next(item for item in page.sessions if item.id == journal.first)
    assert page.total == 3
    assert row.annotation_status == "unavailable"
    assert row.annotation_label is None
    assert row.annotation_tags == ()
    exported = journal.repo.export_session_data(journal.first)
    assert exported.pop("annotation") == {"available": False, "error_code": "invalid_annotation"}
    assert exported == captured
    result = DataExporter(journal.repo).export_to_json(journal.first, journal.output)
    assert result.success
    on_disk = json.loads(journal.output.read_text(encoding="utf-8"))
    assert on_disk["annotation"] == {"available": False, "error_code": "invalid_annotation"}
    assert "SECRET" not in journal.output.read_text(encoding="utf-8")


def test_annotation_operational_failure_is_not_successful_absence(journal):
    save(journal)
    with sqlite3.connect(journal.repo._db_path) as db:
        db.execute("DROP TABLE session_annotations")
    with pytest.raises(DatabaseError):
        journal.repo.get_session_annotation(journal.first)
    with pytest.raises(DatabaseError):
        journal.repo.get_completed_session_history()
    assert journal.repo.export_session_data(journal.first) is None
    journal.output.write_text("previous saved export", encoding="utf-8")
    assert not DataExporter(journal.repo).export_to_json(journal.first, journal.output).success
    assert journal.output.read_text(encoding="utf-8") == "previous saved export"


def test_history_projection_search_counts_pages_and_saved_file_exports(journal):
    first = save(journal, label="Week End", tags=("Solo", "雪"), note="LITERAL %_ / \\ café ß")
    second = save(journal, session_id=journal.second, label="Week End", tags=("Squad",), note="Ordinary note")
    save(journal, session_id=journal.third, label="Week End", tags=(), note="Other owner")
    before = captured_contents(journal.repo._db_path)
    result = journal.repo.get_completed_session_history(journal.owner, 1, 0, annotation_query=" week end ")
    assert result.total == 2
    assert [row.id for row in result.sessions] == [journal.second]
    result = journal.repo.get_completed_session_history(journal.owner, 1, 1, annotation_query="WEEK END")
    assert result.total == 2
    assert [row.id for row in result.sessions] == [journal.first]
    row = result.sessions[0]
    assert (row.annotation_label, row.annotation_tags, row.annotation_status) == (first.label, first.tags, "saved")
    assert row.net_change == -200
    assert row.activities_count == 1
    assert journal.repo.get_completed_session_history(journal.owner, 1, 9, annotation_query="week end").total == 2
    for query in ["%_", "/", "\\", "雪", "CAFé", "ß", "Solo, 雪"]:
        page = journal.repo.get_completed_session_history(annotation_query=query)
        assert [item.id for item in page.sessions] == [journal.first]
        assert page.total == 1
    for query in ["cafÉ", "SS", "missing"]:
        assert journal.repo.get_completed_session_history(annotation_query=query).total == 0
    assert journal.repo.get_completed_session_history(annotation_query=" ") == journal.repo.get_completed_session_history()
    assert DataExporter(journal.repo).export_to_json(journal.first, journal.output).success
    exported = json.loads(journal.output.read_text(encoding="utf-8"))
    assert exported["annotation"] == first.to_dict()
    save(journal, label="newly saved", revision=first.revision)
    assert json.loads(journal.output.read_text(encoding="utf-8")) == exported
    assert second.revision == 1
    assert captured_contents(journal.repo._db_path) == before


@pytest.mark.parametrize("query", [42, True, b"bytes", "x" * 201, "a\nb", "a\tb", "\ud800", "a\x00b", "a\u2028b"])
def test_history_query_validation_precedes_database(query, monkeypatch):
    repo = Repository("unused.db")
    monkeypatch.setattr(repo, "_session_scope", lambda: pytest.fail("bad query reached SQLite"))
    with pytest.raises(ValueError):
        repo.get_completed_session_history(annotation_query=query)


def test_history_defaults_preserve_positional_item_construction(journal):
    item = SessionHistoryItem(1, 2, "Name", None, datetime(2026, 1, 1), 0, 0, 0, None, 0)
    assert (item.annotation_label, item.annotation_tags, item.annotation_status) == (None, (), "missing")
    assert all(row.annotation_status == "missing" for row in journal.repo.get_completed_session_history().sessions)
