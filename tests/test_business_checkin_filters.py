"""Literal manual-history filters share one bounded SQLite observation."""

import sqlite3
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from src.database.models import Character
from src.database.repository import Repository


def domain():
    return import_module("src.database.business_checkins")


def filters(**kwargs):
    assert hasattr(domain(), "BusinessCheckInHistoryFilters"), "Missing history filter value type"
    return domain().BusinessCheckInHistoryFilters(**kwargs)


@pytest.fixture
def journal(tmp_path):
    repo = Repository(str(tmp_path / "filtered.db"))
    assert repo.initialize()
    with repo._session_scope() as db:
        first = Character(name="Filtered runner", is_active=True)
        second = Character(name="Other runner", is_active=False)
        db.add_all([first, second])
        db.flush()
        result = SimpleNamespace(repo=repo, first=first.id, second=second.id,
                                 path=repo._db_path, engine=repo._session_factory.kw["bind"])
    yield result
    repo.close()
    result.engine.dispose()


def save(journal, **kwargs):
    values = {"character_id": journal.first, "business_id": "bunker", "stock_percent": 0}
    values.update(kwargs)
    return journal.repo.save_business_checkin(**values)


def history(journal, accepted=None, **kwargs):
    return journal.repo.get_business_checkin_history(journal.first, "bunker", filters=accepted, **kwargs)


def set_raw(journal, identifier, assignment, parameters=()):
    with sqlite3.connect(journal.path) as db:
        db.execute(f"UPDATE manual_business_checkins SET {assignment} WHERE id=?", (*parameters, identifier))


def test_filter_contract_normalizes_only_empty_query_and_is_frozen():
    normalized = domain().validate_business_checkin_history_filters if hasattr(
        domain(), "validate_business_checkin_history_filters") else None
    assert normalized is not None, "Missing public history filter validator"
    assert normalized(None) is None
    assert normalized(filters()) is None
    assert normalized(filters(note_query="")) is None
    value = normalized(filters(note_query="  ", recorded_from=date.min, recorded_until=date.max))
    assert value == filters(note_query="  ", recorded_from=date.min, recorded_until=date.max)
    with pytest.raises(FrozenInstanceError):
        value.note_query = "changed"
    assert normalized(filters(note_query="🚀" * 200)).note_query == "🚀" * 200


@pytest.mark.parametrize("query,matched", [
    ("MIXED", [0, 1]), ("mixed", [0, 1]), ("é", [0]), ("É", [1]),
    ("ss", [2]), ("ß", [3]), ("雪🚀", [4]), ("%", [5]), ("_", [6]),
    ("\\", [7]), ("  ", [8]), ("İ", [9]), ("i", [0, 1, 9]),
    ("' OR 1=1 --", [10]), ("absent", []),
])
def test_note_query_is_literal_ascii_folded_and_unicode_exact(journal, query, matched):
    notes = ["Mixed café", "mIXED cafÉ", "ss", "ß", "雪🚀", "100%",
             "an_under", "a\\b", "two  spaces", "İ i", "' OR 1=1 --", ""]
    saved = [save(journal, note=note) for note in notes]
    page = history(journal, filters(note_query=query))
    assert [row.id for row in page.rows] == [saved[i].id for i in reversed(matched)]
    assert page.total == len(matched)
    assert page.filters == filters(note_query=query)


@pytest.mark.parametrize("kwargs", [
    {"note_query": True}, {"note_query": 1}, {"note_query": []},
    {"note_query": "x" * 201}, {"note_query": "🚀" * 201},
    {"note_query": "\ud800"}, {"note_query": "a\0b"}, {"note_query": "a\nb"},
    {"note_query": "a\tb"}, {"note_query": "a\rb"}, {"note_query": "a\x7fb"},
    {"note_query": "a\x85b"}, {"note_query": "a\u2028b"}, {"note_query": "a\u2029b"},
    {"recorded_from": datetime(2026, 1, 1)},  # noqa: DTZ001 - invalid date-only input
    {"recorded_until": datetime(2026, 1, 1, tzinfo=UTC)},
    {"recorded_from": "2026-01-01"}, {"recorded_until": True},
    {"recorded_from": date(2026, 2, 2), "recorded_until": date(2026, 2, 1)},
])
def test_invalid_filters_fail_before_storage(kwargs, monkeypatch):
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    with pytest.raises(domain().BusinessCheckInValidationError):
        repo.get_business_checkin_history(1, "bunker", filters=filters(**kwargs))


@pytest.mark.parametrize("bad", [False, "query", {}, [], 1])
def test_non_filter_objects_fail_before_storage(bad, monkeypatch):
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    with pytest.raises(domain().BusinessCheckInValidationError):
        repo.get_business_checkin_history(1, "bunker", filters=bad)


def test_date_subclasses_are_not_exact_date_bounds(monkeypatch):
    class CustomDate(date):
        pass

    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    with pytest.raises(domain().BusinessCheckInValidationError):
        repo.get_business_checkin_history(1, "bunker", filters=filters(recorded_from=CustomDate(2026, 1, 1)))


def test_matching_note_only_and_zero_unknown_observations_remain_independent(journal):
    observed = save(journal, stock_percent=0, supply_percent=0, stock_value=0, note="known")
    note_only = save(journal, stock_percent=None, note="KNOWN note alone")
    page = history(journal, filters(note_query="known"))
    assert page.rows == (note_only, observed)
    assert page.rows[0].stock_percent is page.rows[0].supply_percent is page.rows[0].stock_value is None
    assert (page.rows[1].stock_percent, page.rows[1].supply_percent, page.rows[1].stock_value) == (0, 0, 0)


@pytest.mark.parametrize("timestamp,expected_day", [
    ("2026-01-02 00:00:00", date(2026, 1, 2)),
    ("2026-01-02T00:00:00Z", date(2026, 1, 2)),
    ("2026-01-01 23:59:59.999999+00:00", date(2026, 1, 1)),
    ("2026-01-02 00:00:00.000001+00:00", date(2026, 1, 2)),
    ("2026-01-02 00:30:00+01:00", date(2026, 1, 1)),
    ("2026-01-01 23:30:00-01:00", date(2026, 1, 2)),
    ("2026-01-02 00:00:20+00:00:30", date(2026, 1, 1)),
    ("2026-01-01 23:59:40-00:00:30", date(2026, 1, 2)),
    ("2026-01-02 00:01:00.499999+00:01:00.5", date(2026, 1, 1)),
    ("2026-01-02 00:01:00.500000+00:01:00.5", date(2026, 1, 2)),
    ("2026-01-02 00:00:00.0000001+00:00", date(2026, 1, 2)),
    ("0001-01-01 00:00:00+00:00", date.min),
    ("9999-12-31 23:59:59.999999+00:00", date.max),
    ("0001-01-01 23:00:00+23:00", date.min),
    ("9999-12-31 00:00:00-23:59", date.max),
])
def test_date_predicate_matches_reader_exact_utc_day(journal, timestamp, expected_day):
    saved = save(journal)
    set_raw(journal, saved.id, "recorded_at=?", (timestamp,))
    unfiltered = history(journal).rows[0]
    assert unfiltered.recorded_at.date() == expected_day
    page = history(journal, filters(recorded_from=expected_day, recorded_until=expected_day))
    assert page.rows == (unfiltered,)
    assert page.total == 1
    if expected_day != date.min:
        assert history(journal, filters(recorded_until=date.fromordinal(expected_day.toordinal() - 1))).total == 0
    if expected_day != date.max:
        assert history(journal, filters(recorded_from=date.fromordinal(expected_day.toordinal() + 1))).total == 0


def test_combined_filters_and_pagination_share_owner_business_and_insertion_order(journal):
    saved = [save(journal, note="match" if n % 2 == 0 else "other") for n in range(9)]
    for n, row in enumerate(saved):
        set_raw(journal, row.id, "recorded_at=?", (f"2026-01-{9 - n:02d} 12:00:00",))
    save(journal, character_id=journal.second, note="match")
    save(journal, business_id="nightclub", note="match")
    accepted = filters(note_query="MATCH", recorded_from=date(2026, 1, 2), recorded_until=date(2026, 1, 8))
    page = history(journal, accepted, offset=1, limit=1)
    assert (page.total, page.offset, page.limit, page.has_more) == (3, 1, 1, True)
    assert [row.id for row in page.rows] == [saved[4].id]
    assert page.character_name == "Filtered runner"
    last = history(journal, accepted, offset=2, limit=1)
    assert [row.id for row in last.rows] == [saved[2].id]
    assert not last.has_more
    empty = history(journal, accepted, offset=1_000_000, limit=100)
    assert (empty.rows, empty.total, empty.has_more) == ((), 3, False)
    assert empty.character_id == journal.first
    assert empty.filters == accepted


def test_empty_filters_retain_exact_report_and_positional_snapshot_compatibility(journal, monkeypatch):
    stamp = datetime(2026, 1, 2, tzinfo=UTC)
    monkeypatch.setattr("src.database.repository.utc_now", lambda: stamp)
    saved = save(journal, note="entry")
    old_call = journal.repo.get_business_checkin_history(journal.first, "bunker", 0, 25)
    old_snapshot = domain().BusinessCheckInPage(journal.first, "Filtered runner", "bunker", stamp,
                                               [saved], 0, 25, 1)
    assert old_call == old_snapshot
    assert old_call.filters is None
    report = old_call.to_report()
    assert report["filters"] == {"character_id": journal.first, "business_id": "bunker"}
    assert set(report["field_notes"]) == {"scope", "unknown", "independence", "recorded_at", "ordering", "stock_value"}
    assert history(journal, filters()).to_report() == report
    assert history(journal, filters(note_query="")).to_report() == report
    assert old_snapshot.to_report() == report


def test_filtered_report_freezes_exact_accepted_filter_context(journal):
    saved = save(journal, note=" MATCH ")
    accepted = filters(note_query=" MATCH ", recorded_from=date.min, recorded_until=date.max)
    page = history(journal, accepted)
    report = page.to_report()
    assert report["filters"] == {
        "character_id": journal.first, "business_id": "bunker", "note_query": " MATCH ",
        "recorded_from": "0001-01-01", "recorded_until": "9999-12-31",
        "note_match_policy": "literal_substring_ascii_case_insensitive_other_unicode_exact",
        "recorded_date_policy": "inclusive_utc_dates",
    }
    assert "note_query" in report["field_notes"]
    assert "recorded_dates" in report["field_notes"]
    assert report["scope"] == "manual_observations"
    report["filters"]["note_query"] = "changed"
    report["rows"].clear()
    save(journal, note=" MATCH later")
    assert page.filters == accepted
    assert page.rows == (saved,)
    assert page.total == 1
    with pytest.raises(FrozenInstanceError):
        page.filters = None


@pytest.mark.parametrize("assignment", [
    "note=CAST(x'ff' AS TEXT)", "note=zeroblob(1000000)",
    "note=replace(hex(zeroblob(50000)), '0', 'a')", "note='bad' || char(0)",
    "note='bad' || char(13)", "note=replace(hex(zeroblob(1001)), '0', 'a')",
])
def test_active_note_predicate_rejects_corruption_even_outside_page(journal, assignment):
    old = save(journal, note="nonmatch")
    latest = save(journal, note="match")
    set_raw(journal, old.id, assignment)
    assert history(journal, filters(recorded_from=date.min), limit=1).rows == (latest,)
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, filters(note_query="match"), limit=1)
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, filters(note_query="match"), offset=99, limit=1)


@pytest.mark.parametrize("assignment", [
    "recorded_at='private invalid date'", "recorded_at=zeroblob(1000000)",
    "recorded_at=CAST(x'ff' AS TEXT)", "recorded_at=1",
    "recorded_at='0001-01-01 00:00:00+01:00'",
    "recorded_at='9999-12-31 23:59:59-01:00'",
    "recorded_at='2026-01-01 00:00:00' || replace(hex(zeroblob(100)), '0', ' ')",
])
def test_active_date_predicate_rejects_corruption_before_note_nonmatch(journal, assignment):
    old = save(journal, note="nonmatch")
    latest = save(journal, note="match")
    set_raw(journal, old.id, assignment)
    assert history(journal, filters(note_query="match"), limit=1).rows == (latest,)
    with pytest.raises(domain().BusinessCheckInDataError) as error:
        history(journal, filters(note_query="match", recorded_from=date.min), limit=1)
    assert "private" not in str(error.value)
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, filters(recorded_until=date.max), offset=99, limit=1)


def test_unrelated_owners_and_businesses_never_enter_predicate_validation(journal):
    kept = save(journal, note="match")
    unrelated = [save(journal, character_id=journal.second, note="match"),
                 save(journal, business_id="nightclub", note="match")]
    for row in unrelated:
        set_raw(journal, row.id, "note=CAST(x'ff' AS TEXT), recorded_at='invalid'")
    assert history(journal, filters(note_query="match", recorded_from=date.min)).rows == (kept,)


def test_nonpredicate_fields_are_validated_only_on_selected_page(journal):
    old = save(journal, note="match")
    latest = save(journal, note="match")
    set_raw(journal, old.id, "stock_percent=101")
    accepted = filters(note_query="match", recorded_from=date.min)
    assert history(journal, accepted, limit=1).rows == (latest,)
    assert history(journal, accepted, offset=99, limit=1).total == 2
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, accepted, offset=1, limit=1)


def test_filtered_owner_count_page_are_one_observation(journal):
    original = [save(journal, note="match") for _ in range(3)]
    with sqlite3.connect(journal.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []

    def write_after_read(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(journal.path) as other:
                other.execute("UPDATE characters SET name='After read' WHERE id=?", (journal.first,))
                other.execute("INSERT INTO manual_business_checkins "
                              "(character_id,business_id,recorded_at,stock_percent,note) "
                              "VALUES (?,'bunker','2026-01-01 00:00:00',99,'match')", (journal.first,))

    event.listen(journal.engine, "after_cursor_execute", write_after_read)
    try:
        page = history(journal, filters(note_query="match", recorded_from=date.min), limit=2)
    finally:
        event.remove(journal.engine, "after_cursor_execute", write_after_read)
    assert len(statements) == 1
    assert page.total == 3
    assert page.character_name == "Filtered runner"
    assert page.rows == tuple(reversed(original))[:2]
    assert history(journal, filters(note_query="match")).total == 4


def test_filtered_queries_return_bounded_payload_and_do_not_decode_other_fields(journal, monkeypatch):
    saved = [save(journal, note="match " + "🚀" * 1994) for _ in range(8)]
    set_raw(journal, saved[0].id, "stock_value=CAST(x'ff' AS TEXT)")
    actual_reader = import_module("src.database.repository").checkin_from_storage
    admitted = []

    def inspect_bounded_record(record):
        admitted.append(record)
        assert len(record["note"]) <= 8001
        assert len(record["recorded_at"]) <= 65
        assert len(record["business_id"]) <= 51
        return actual_reader(record)

    monkeypatch.setattr("src.database.repository.checkin_from_storage", inspect_bounded_record)
    page = history(journal, filters(note_query="MATCH", recorded_from=date.min), limit=2)
    assert len(admitted) == len(page.rows) == 2
    assert page.total == 8


def test_filtered_storage_and_owner_failures_do_not_become_no_matches(journal, monkeypatch):
    accepted = filters(note_query="match", recorded_from=date.min)
    with pytest.raises(domain().BusinessCheckInUnavailable):
        journal.repo.get_business_checkin_history(9999, "bunker", filters=accepted)
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "initialize", lambda: False)
    with pytest.raises(domain().BusinessCheckInUnavailable):
        repo.get_business_checkin_history(1, "bunker", filters=accepted)
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TABLE manual_business_checkins")
    with pytest.raises(domain().BusinessCheckInUnavailable) as error:
        history(journal, accepted)
    assert str(journal.path) not in str(error.value)


def test_failed_filtered_read_does_not_poison_reused_connection(journal):
    saved = save(journal, note="match")
    set_raw(journal, saved.id, "recorded_at='invalid private date'")
    accepted = filters(note_query="match", recorded_from=date.min)
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, accepted)
    set_raw(journal, saved.id, "recorded_at='2026-01-01 00:00:00'")
    assert history(journal, accepted).total == 1
    assert history(journal, filters(note_query="nonmatch")).total == 0
    assert history(journal).total == 1


def test_commit_failure_discards_filtered_snapshot(journal):
    save(journal, note="match")

    def fail_commit(session):
        raise OperationalError("commit", {}, Exception("private failure"))

    event.listen(journal.repo._session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(domain().BusinessCheckInUnavailable) as error:
            history(journal, filters(note_query="match"))
        assert "private" not in str(error.value)
    finally:
        event.remove(journal.repo._session_factory, "before_commit", fail_commit)
    assert history(journal, filters(note_query="match")).total == 1


@pytest.mark.parametrize("field", ["note", "recorded_at"])
def test_active_filter_rejects_null_storage_without_treating_it_as_empty(journal, field):
    old = save(journal, note="nonmatch")
    save(journal, note="match")
    with sqlite3.connect(journal.path) as db:
        db.execute("ALTER TABLE manual_business_checkins RENAME TO original_manual")
        db.execute("CREATE TABLE manual_business_checkins AS SELECT * FROM original_manual")
        db.execute(f"UPDATE manual_business_checkins SET {field}=NULL WHERE id=?", (old.id,))
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, filters(note_query="match", recorded_from=date.min), limit=1)


def test_filter_callbacks_receive_only_bounded_active_predicate_columns(journal, monkeypatch):
    saved = save(journal, note="match")
    set_raw(journal, saved.id, "note=zeroblob(1000000), recorded_at=zeroblob(1000000)")
    actual_matcher = import_module("src.database.repository").checkin_history_match_from_storage
    received = []

    def inspect_arguments(note, note_type, recorded_at, recorded_at_type, accepted):
        received.append((note, recorded_at))
        assert isinstance(note, bytes) and len(note) <= 8001
        if accepted.recorded_from is not None:
            assert isinstance(recorded_at, bytes) and len(recorded_at) <= 65
        else:
            assert recorded_at is recorded_at_type is None
        return actual_matcher(note, note_type, recorded_at, recorded_at_type, accepted)

    monkeypatch.setattr("src.database.repository.checkin_history_match_from_storage", inspect_arguments)
    for accepted in (filters(note_query="match"), filters(note_query="match", recorded_from=date.min)):
        with pytest.raises(domain().BusinessCheckInDataError):
            history(journal, accepted)
    assert received


@pytest.mark.parametrize("fail_at", ["install", "remove"])
def test_callback_lifecycle_failure_invalidates_physical_connection(journal, fail_at):
    save(journal, note="match")
    connections = []

    class FailingCallbackConnection(sqlite3.Connection):
        closed = False

        def create_function(self, name, count, callback, **kwargs):
            if name == "checkin_history_match" and ((callback is None) == (fail_at == "remove")):
                raise sqlite3.OperationalError("private callback registration failure")
            return super().create_function(name, count, callback, **kwargs)

        def close(self):
            self.closed = True
            super().close()

    def connect():
        connection = sqlite3.connect(journal.path, factory=FailingCallbackConnection)
        connections.append(connection)
        return connection

    engine = create_engine("sqlite://", creator=connect)
    repo = Repository(journal.path)
    repo._initialized = True
    repo._session_factory = sessionmaker(bind=engine)
    try:
        with pytest.raises(domain().BusinessCheckInUnavailable) as error:
            repo.get_business_checkin_history(journal.first, "bunker", filters=filters(note_query="match"))
        assert "private" not in str(error.value)
        assert connections and connections[0].closed
    finally:
        repo.close()
        engine.dispose()


def test_callback_is_removed_after_success_and_corrupt_reads(journal):
    saved = save(journal, note="match")
    history(journal, filters(note_query="match"))
    with journal.engine.connect() as connection, pytest.raises(OperationalError):
        connection.exec_driver_sql("SELECT checkin_history_match(x'', 'text', NULL, NULL)")
    set_raw(journal, saved.id, "note=CAST(x'ff' AS TEXT)")
    with pytest.raises(domain().BusinessCheckInDataError):
        history(journal, filters(note_query="match"))
    with journal.engine.connect() as connection, pytest.raises(OperationalError):
        connection.exec_driver_sql("SELECT checkin_history_match(x'', 'text', NULL, NULL)")
