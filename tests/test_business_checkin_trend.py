"""Complete, bounded manual-history captures and exact endpoint summaries."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.business_checkins import (
    BusinessCheckIn, BusinessCheckInDataError, BusinessCheckInHistoryFilters, BusinessCheckInLimitError,
    BusinessCheckInUnavailable, BusinessCheckInValidationError, SQLITE_MAX_INTEGER,
)
from src.database.models import Character
from src.database.repository import Repository


STAMP = datetime(2026, 10, 4, 12, 34, 56, 123456, tzinfo=timezone.utc)


def domain():
    assert find_spec("src.database.business_checkin_trend") is not None, "Missing trend domain"
    return import_module("src.database.business_checkin_trend")


@pytest.fixture
def journal(tmp_path):
    repo = Repository(str(tmp_path / "trend.db"))
    assert repo.initialize()
    with repo._session_scope() as db:
        first = Character(name="Saved runner <b>雪</b>", is_active=True)
        second = Character(name="Private other runner", is_active=False)
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


def capture(journal, **kwargs):
    assert hasattr(Repository, "get_business_checkin_trend"), "Missing trend repository capture"
    values = dict(character_id=journal.first, business_id="bunker")
    values.update(kwargs)
    return journal.repo.get_business_checkin_trend(**values)


def row(identifier=1, **kwargs):
    values = dict(id=identifier, character_id=1, business_id="bunker", recorded_at=STAMP,
                  stock_percent=None, supply_percent=None, stock_value=None, note="note only")
    values.update(kwargs)
    return BusinessCheckIn(**values)


def snapshot(rows=(), **kwargs):
    values = dict(character_id=1, character_name="Saved <b>雪</b>", business_id="bunker",
                  captured_at=STAMP, rows=rows)
    values.update(kwargs)
    return domain().BusinessCheckInTrend(**values)


def set_raw(journal, identifier, assignment, parameters=()):
    with sqlite3.connect(journal.path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(f"UPDATE manual_business_checkins SET {assignment} WHERE id=?", (*parameters, identifier))


def contents(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def test_trend_contract_is_available():
    assert domain().MAX_CHECKIN_TREND_ROWS == 1000
    assert hasattr(Repository, "get_business_checkin_trend")


def test_trend_limit_remains_a_manual_checkin_limit_with_filter_guidance():
    error = domain().BusinessCheckInTrendLimitError()
    assert isinstance(error, BusinessCheckInLimitError)
    assert "1000" in str(error)
    assert "Narrow the applied history filters" in str(error)


@pytest.mark.parametrize("count", [0, 1, 1000, 1001])
@pytest.mark.parametrize("accepted", [None, BusinessCheckInHistoryFilters("match", date.min)])
def test_capture_accepts_all_matches_or_refuses_without_partial_snapshot(journal, count, monkeypatch, accepted):
    with sqlite3.connect(journal.path) as db:
        db.executemany("INSERT INTO manual_business_checkins "
                       "(character_id,business_id,recorded_at,stock_percent,note) "
                       "VALUES (?,'bunker',?,0,'match')",
                       [(journal.first, STAMP.isoformat())] * count)
    monkeypatch.setattr(journal.repo, "get_business_checkin_history",
                        lambda *a, **k: pytest.fail("Trend stitched history pages"))
    if count > 1000:
        monkeypatch.setattr("src.database.repository.checkin_from_storage",
                            lambda *a: pytest.fail("Over-limit snapshot decoded partial rows"))
        with pytest.raises(domain().BusinessCheckInTrendLimitError, match="[Nn]arrow.*filters"):
            capture(journal, filters=accepted)
    else:
        result = capture(journal, filters=accepted)
        assert len(result.rows) == count
        assert [record.id for record in result.rows] == list(range(1, count + 1))
        assert result.character_name == "Saved runner <b>雪</b>"
        assert result.captured_at.tzinfo == timezone.utc


@pytest.mark.parametrize("changes", [
    {"character_id": True}, {"character_id": 0}, {"character_id": 2**63},
    {"character_id": "1"}, {"business_id": None}, {"business_id": ""},
    {"business_id": "x" * 51}, {"business_id": "bad/business"},
    {"filters": {}}, {"filters": BusinessCheckInHistoryFilters(note_query="x" * 201)},
    {"filters": BusinessCheckInHistoryFilters(note_query="\ud800")},
    {"filters": BusinessCheckInHistoryFilters(note_query="bad\nquery")},
    {"filters": BusinessCheckInHistoryFilters(recorded_from=STAMP)},
    {"filters": BusinessCheckInHistoryFilters(recorded_from=date.max, recorded_until=date.min)},
])
def test_invalid_capture_requests_fail_before_storage(tmp_path, monkeypatch, changes):
    repo = Repository(str(tmp_path / "must-not-create.db"))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("Invalid request accessed storage"))
    assert hasattr(repo, "get_business_checkin_trend"), "Missing trend repository capture"
    values = dict(character_id=1, business_id="bunker")
    values.update(changes)
    with pytest.raises(BusinessCheckInValidationError):
        repo.get_business_checkin_trend(**values)
    assert not (tmp_path / "must-not-create.db").exists()


@pytest.mark.parametrize("query,indices", [
    ("MIXED", [0, 1]), ("é", [0]), ("É", [1]), ("ss", [2]), ("ß", [3]),
    ("%_\\", [4]), ("  ", [5]), ("' OR 1=1 --", [6]), ("absent", []),
])
def test_filters_keep_exact_literal_note_semantics(journal, query, indices):
    notes = ["Mixed café", "mIXED cafÉ", "ss", "ß", "100%_\\ snow 雪",
             "two  spaces", "' OR 1=1 --", ""]
    saved = [save(journal, note=note) for note in notes]
    result = capture(journal, filters=BusinessCheckInHistoryFilters(note_query=query))
    assert result.rows == tuple(saved[index] for index in indices)
    assert result.filters.note_query == query


def test_combined_date_filters_normalize_utc_and_select_all_matching_rows(journal):
    stamps = ["2026-01-02T00:30:00+01:00", "2026-01-01T23:30:00-01:00",
              "2026-01-02T00:00:00.123456Z", "2026-01-03T00:00:00Z"]
    saved = [save(journal, note=" MATCH ") for _ in stamps]
    for record, stamp in zip(saved, stamps):
        set_raw(journal, record.id, "recorded_at=?", (stamp,))
    save(journal, note="nonmatch")
    save(journal, character_id=journal.second, note=" MATCH ")
    save(journal, business_id="nightclub", note=" MATCH ")
    filters = BusinessCheckInHistoryFilters(" MATCH ", date(2026, 1, 2), date(2026, 1, 2))
    result = capture(journal, filters=filters)
    assert [record.id for record in result.rows] == [saved[1].id, saved[2].id]
    assert result.rows[1].recorded_at == datetime(2026, 1, 2, 0, 0, 0, 123456, tzinfo=timezone.utc)
    assert result.filters == filters


def test_filter_cap_is_on_matches_not_scoped_candidates(journal):
    with sqlite3.connect(journal.path) as db:
        db.executemany("INSERT INTO manual_business_checkins "
                       "(character_id,business_id,recorded_at,stock_percent,note) "
                       "VALUES (?,'bunker',?,0,'other')", [(journal.first, STAMP.isoformat())] * 1001)
    kept = save(journal, note="match")
    assert capture(journal, filters=BusinessCheckInHistoryFilters("match")).rows == (kept,)


@pytest.mark.parametrize("assignment,filters", [
    ("note=CAST(x'ff' AS TEXT)", BusinessCheckInHistoryFilters("match")),
    ("note=zeroblob(1000000)", BusinessCheckInHistoryFilters("match")),
    ("note='bad'||char(0)", BusinessCheckInHistoryFilters("match")),
    ("note=replace(hex(zeroblob(1001)), '0', 'a')", BusinessCheckInHistoryFilters("match")),
    ("recorded_at='private invalid date'", BusinessCheckInHistoryFilters("match", date.min)),
    ("recorded_at=zeroblob(1000000)", BusinessCheckInHistoryFilters("match", date.min)),
    ("recorded_at=CAST(x'ff' AS TEXT)", BusinessCheckInHistoryFilters("match", date.min)),
    ("recorded_at='0001-01-01T00:00:00+01:00'", BusinessCheckInHistoryFilters("match", date.min)),
])
def test_corrupt_active_predicates_fail_even_on_nonmatching_candidates(journal, assignment, filters):
    invalid = save(journal, note="other")
    save(journal, note="match")
    set_raw(journal, invalid.id, assignment)
    with pytest.raises(BusinessCheckInDataError) as error:
        capture(journal, filters=filters)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("assignment", [
    "stock_percent=101", "supply_percent=-1", "stock_value=1.5", "stock_value=x'ff'",
    "recorded_at='not a timestamp'", "note=CAST(x'ff' AS TEXT)",
    "stock_percent=NULL,supply_percent=NULL,stock_value=NULL,note='  '",
])
def test_corrupt_selected_payload_fails(journal, assignment):
    invalid = save(journal)
    set_raw(journal, invalid.id, assignment)
    with pytest.raises(BusinessCheckInDataError):
        capture(journal)


def test_unselected_inactive_fields_and_other_scopes_remain_unread(journal):
    kept = save(journal, note="match")
    excluded = save(journal, note="other")
    unrelated = [save(journal, character_id=journal.second), save(journal, business_id="nightclub")]
    set_raw(journal, excluded.id, "recorded_at='invalid',stock_value=1.5")
    for record in unrelated:
        set_raw(journal, record.id, "note=CAST(x'ff' AS TEXT),recorded_at='invalid',stock_value=1.5")
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE characters SET name=CAST(x'ff' AS TEXT) WHERE id=?", (journal.second,))
    assert capture(journal, filters=BusinessCheckInHistoryFilters("match")).rows == (kept,)


def test_large_identifiers_retained_business_and_reverse_duplicate_clocks_preserve_insertion_order(journal):
    with sqlite3.connect(journal.path) as db:
        db.execute("INSERT INTO characters(id,name,is_active) VALUES (?,'Maximum owner',0)", (SQLITE_MAX_INTEGER,))
        db.executemany("INSERT INTO manual_business_checkins "
                       "(id,character_id,business_id,recorded_at,stock_value,note) VALUES (?,?,?,?,?,'exact')", [
                           (2**53 + 1, SQLITE_MAX_INTEGER, "retired-business.v1", "2026-01-02T00:00:00Z", 0),
                           (2**53 + 2, SQLITE_MAX_INTEGER, "retired-business.v1", "2025-01-01T00:00:00Z", 1),
                           (SQLITE_MAX_INTEGER, SQLITE_MAX_INTEGER, "retired-business.v1", "2025-01-01T00:00:00Z", SQLITE_MAX_INTEGER),
                       ])
    result = capture(journal, character_id=SQLITE_MAX_INTEGER, business_id="retired-business.v1")
    assert [record.id for record in result.rows] == [2**53 + 1, 2**53 + 2, SQLITE_MAX_INTEGER]
    assert result.rows[1].recorded_at == result.rows[2].recorded_at < result.rows[0].recorded_at
    assert result.metrics[2].change == SQLITE_MAX_INTEGER


@pytest.mark.parametrize("accepted", [None, BusinessCheckInHistoryFilters("match", date.min)])
def test_capture_is_one_bounded_sql_read_without_legacy_or_accounting_writes(journal, accepted, monkeypatch):
    saved = [save(journal, note="match " + "🚀" * 1994) for _ in range(3)]
    before = contents(journal.path)
    statements = []
    actual_reader = import_module("src.database.repository").checkin_from_storage

    def check_payload(record):
        assert len(record["note"]) <= 8001
        assert len(record["recorded_at"]) <= 65
        assert len(record["business_id"]) <= 51
        return actual_reader(record)

    def record_statement(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters))

    monkeypatch.setattr("src.database.repository.checkin_from_storage", check_payload)
    event.listen(journal.engine, "before_cursor_execute", record_statement)
    try:
        assert capture(journal, filters=accepted).rows == tuple(saved)
    finally:
        event.remove(journal.engine, "before_cursor_execute", record_statement)
    assert len(statements) == 1
    sql, parameters = statements[0]
    assert sql.lstrip().upper().startswith("WITH")
    assert "LIMIT" in sql.upper() and 1001 in parameters
    assert "ORDER BY m.id ASC" in sql
    assert contents(journal.path) == before


@pytest.mark.parametrize("accepted", [None, BusinessCheckInHistoryFilters("match", date.min)])
def test_owner_count_and_rows_are_one_snapshot_under_concurrent_wal_writer(journal, accepted):
    original = [save(journal, note="match", stock_percent=value) for value in (10, 20, 30)]
    with sqlite3.connect(journal.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []

    def write_after_query(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(journal.path) as other:
                other.execute("UPDATE characters SET name='Changed later' WHERE id=?", (journal.first,))
                other.execute("UPDATE manual_business_checkins SET stock_percent=99,note='after' WHERE id=?", (original[-1].id,))
                other.execute("INSERT INTO manual_business_checkins "
                              "(character_id,business_id,recorded_at,stock_percent,note) "
                              "VALUES (?,'bunker',?,0,'match')", (journal.first, STAMP.isoformat()))

    event.listen(journal.engine, "after_cursor_execute", write_after_query)
    try:
        result = capture(journal, filters=accepted)
    finally:
        event.remove(journal.engine, "after_cursor_execute", write_after_query)
    assert len(statements) == 1
    assert result.character_name == "Saved runner <b>雪</b>"
    assert result.rows == tuple(original)
    assert result.to_report()["row_count"] == 3
    fresh = capture(journal, filters=accepted)
    assert fresh.character_name == "Changed later"
    assert fresh.rows != result.rows


def test_absent_owner_and_failed_reads_are_unavailable_not_empty(journal):
    with pytest.raises(BusinessCheckInUnavailable):
        capture(journal, character_id=99999)

    def fail(*args):
        raise OperationalError("private SQL", {}, Exception("private path"))

    event.listen(journal.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(BusinessCheckInUnavailable) as error:
            capture(journal, filters=BusinessCheckInHistoryFilters("match"))
        assert "private" not in str(error.value)
    finally:
        event.remove(journal.engine, "before_cursor_execute", fail)
    assert capture(journal).rows == ()


@pytest.mark.parametrize("assignment", ["name=CAST(x'ff' AS TEXT)", "name=zeroblob(50000)"])
def test_corrupt_owner_is_data_error_even_for_empty_matches(journal, assignment):
    with sqlite3.connect(journal.path) as db:
        db.execute(f"UPDATE characters SET {assignment} WHERE id=?", (journal.first,))
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=BusinessCheckInHistoryFilters("match"))


def test_filter_callback_is_cleared_after_failure_and_success(journal):
    saved = save(journal, note="match")
    set_raw(journal, saved.id, "note=CAST(x'ff' AS TEXT)")
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=BusinessCheckInHistoryFilters("match"))
    set_raw(journal, saved.id, "note='match'")
    assert len(capture(journal, filters=BusinessCheckInHistoryFilters("match")).rows) == 1
    assert capture(journal, filters=BusinessCheckInHistoryFilters("other")).rows == ()
    with journal.engine.connect() as connection, pytest.raises(OperationalError):
        connection.exec_driver_sql("SELECT checkin_history_match(x'', 'text', NULL, NULL)")


@pytest.mark.parametrize("accepted", [None, BusinessCheckInHistoryFilters("match")])
@pytest.mark.parametrize("identifier", [None, -1, 1.5, "bad"])
def test_count_payload_inconsistency_and_invalid_stored_identifiers_fail(journal, accepted, identifier):
    save(journal, note="match")
    with sqlite3.connect(journal.path) as db:
        # Model a damaged legacy table without the normal integer primary key.
        db.execute("ALTER TABLE manual_business_checkins RENAME TO original_manual")
        db.execute("CREATE TABLE manual_business_checkins AS SELECT * FROM original_manual")
        db.execute("UPDATE manual_business_checkins SET id=?", (identifier,))
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=accepted)


@pytest.mark.parametrize("accepted", [None, BusinessCheckInHistoryFilters("match")])
def test_duplicate_stored_identifiers_cannot_create_a_trend(journal, accepted):
    save(journal, note="match")
    with sqlite3.connect(journal.path) as db:
        db.execute("ALTER TABLE manual_business_checkins RENAME TO original_manual")
        db.execute("CREATE TABLE manual_business_checkins AS SELECT * FROM original_manual")
        db.execute("INSERT INTO manual_business_checkins SELECT * FROM original_manual")
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=accepted)


def test_duplicate_owner_context_is_not_an_empty_valid_snapshot(journal):
    with sqlite3.connect(journal.path) as db:
        db.execute("ALTER TABLE characters RENAME TO original_characters")
        db.execute("CREATE TABLE characters AS SELECT * FROM original_characters")
        db.execute("INSERT INTO characters SELECT * FROM original_characters WHERE id=?", (journal.first,))
    with pytest.raises(BusinessCheckInDataError):
        capture(journal)


def test_corrupt_active_predicate_beyond_row_limit_is_not_silently_omitted(journal):
    with sqlite3.connect(journal.path) as db:
        db.executemany("INSERT INTO manual_business_checkins "
                       "(character_id,business_id,recorded_at,stock_percent,note) "
                       "VALUES (?,'bunker',?,0,'match')", [(journal.first, STAMP.isoformat())] * 1001)
    damaged = save(journal, note="other")
    set_raw(journal, damaged.id, "recorded_at='invalid'")
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=BusinessCheckInHistoryFilters("match", date.min))


def test_active_note_is_checked_even_when_date_excludes_row(journal):
    damaged = save(journal)
    set_raw(journal, damaged.id, "note=CAST(x'ff' AS TEXT),recorded_at='2000-01-01T00:00:00Z'")
    with pytest.raises(BusinessCheckInDataError):
        capture(journal, filters=BusinessCheckInHistoryFilters("match", date(2026, 1, 1)))


def test_commit_failure_discards_trend_and_retry_recaptures(journal):
    saved = save(journal, note="match")

    def fail_commit(session):
        raise OperationalError("commit", {}, Exception("private failure"))

    event.listen(journal.repo._session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(BusinessCheckInUnavailable) as error:
            capture(journal, filters=BusinessCheckInHistoryFilters("match"))
        assert "private" not in str(error.value)
    finally:
        event.remove(journal.repo._session_factory, "before_commit", fail_commit)
    assert capture(journal, filters=BusinessCheckInHistoryFilters("match")).rows == (saved,)


@pytest.mark.parametrize("measurements,expected", [
    ([], (0, 0, None, None, None, "no_observations")),
    ([None], (0, 1, None, None, None, "no_observations")),
    ([0], (1, 0, 0, 0, None, "available")),
    ([None, 25, 50], (2, 1, None, 50, None, "available")),
    ([50, 25, None], (2, 1, 50, None, None, "available")),
    ([None, 25, None], (1, 2, None, None, None, "available")),
    ([100, None, 0], (2, 1, 100, 0, -100, "available")),
    ([0, 0], (2, 0, 0, 0, 0, "available")),
])
def test_exact_coverage_endpoints_and_change_never_search_inward(measurements, expected):
    result = snapshot([row(index, stock_percent=value, supply_percent=value, stock_value=value)
                       for index, value in enumerate(measurements, 1)])
    assert isinstance(result.metrics, tuple)
    assert [metric.field for metric in result.metrics] == ["stock_percent", "supply_percent", "stock_value"]
    for metric in result.metrics:
        assert (metric.known_count, metric.unknown_count, metric.first_value, metric.last_value,
                metric.change, metric.plot_status) == expected
        with pytest.raises(FrozenInstanceError):
            metric.change = 1


@pytest.mark.parametrize("value,status", [
    (0, "available"), (2**53, "available"), (2**53 + 1, "precision_limit"),
    (2**53 + 2, "available"), (2**62, "available"), (SQLITE_MAX_INTEGER, "precision_limit"),
])
def test_any_inexact_value_disables_entire_value_plot_but_not_exact_data(value, status):
    result = snapshot([row(1, stock_value=0), row(2, stock_value=value), row(3, stock_value=2)])
    metric = result.metrics[2]
    assert metric.plot_status == status
    assert (metric.known_count, metric.first_value, metric.last_value, metric.change) == (3, 0, 2, 2)
    assert result.rows[1].stock_value == value
    assert result.to_report()["rows"][1]["stock_value"] == value


def test_direct_constructor_detaches_bounds_normalizes_and_freezes():
    rows = [row()]
    stamp = datetime(2026, 10, 4, 14, tzinfo=timezone(timedelta(hours=2)))
    result = snapshot(rows, captured_at=stamp, filters=BusinessCheckInHistoryFilters(""))
    rows.clear()
    assert result.rows == (row(),)
    assert result.captured_at == datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    assert result.filters is None
    with pytest.raises(FrozenInstanceError):
        result.rows = ()
    with pytest.raises(FrozenInstanceError):
        result.rows[0].note = "changed"
    with pytest.raises(domain().BusinessCheckInTrendLimitError):
        snapshot([row()] * 1001)


def test_direct_constructor_rejects_iterables_without_consuming_them():
    def unbounded():
        pytest.fail("Constructor consumed an unbounded iterable")
        yield row()

    class DishonestList(list):
        def __iter__(self):
            pytest.fail("Constructor iterated an untrusted list subclass")

    for rows in (unbounded(), DishonestList([row()]), {1: row()}, "bad", None):
        with pytest.raises(BusinessCheckInValidationError):
            snapshot(rows)


@pytest.mark.parametrize("changes", [
    {"character_id": True}, {"character_id": 0}, {"business_id": "bad/business"},
    {"character_name": None}, {"character_name": "\ud800"}, {"character_name": "🚀" * 1025},
    {"captured_at": "2026-10-04"}, {"filters": "query"},
    {"rows": (object(),)}, {"rows": (row(0),)}, {"rows": (row(1), row(1))},
    {"rows": (row(2), row(1))}, {"rows": (row(character_id=2),)},
    {"rows": (row(business_id="nightclub"),)}, {"rows": (row(stock_percent=101),)},
    {"rows": (row(supply_percent=True),)}, {"rows": (row(stock_value=1.5),)},
    {"rows": (row(stock_value=2**63),)}, {"rows": (row(note=""),)},
    {"rows": (row(note="bad\0"),)}, {"rows": (row(note="x" * 2001),)},
    {"rows": (row(note="\ud800"),)},
])
def test_invalid_direct_snapshots_are_rejected(changes):
    with pytest.raises(BusinessCheckInValidationError):
        snapshot(**changes)


def test_direct_snapshot_validates_timestamp_on_supplied_rows():
    damaged = row()
    object.__setattr__(damaged, "recorded_at", "bad timestamp")
    with pytest.raises(BusinessCheckInValidationError):
        snapshot([damaged])


def test_report_is_complete_fresh_exact_and_independent_of_later_storage(journal, monkeypatch):
    monkeypatch.setattr("src.database.repository.utc_now", lambda: STAMP)
    first = save(journal, stock_percent=None, stock_value=SQLITE_MAX_INTEGER, note="  MATCH <b>雪</b>\n\t ")
    last = save(journal, stock_percent=0, supply_percent=100, stock_value=0, note="match")
    filters = BusinessCheckInHistoryFilters("match", date.min, date.max)
    result = capture(journal, filters=filters)
    report = result.to_report()
    assert report["format_version"] == 1
    assert report["kind"] == "manual_business_checkin_trend"
    assert report["scope"] == "manual_observations"
    assert report["captured_at"] == STAMP.isoformat()
    assert report["timezone"] == "UTC"
    assert report["character"] == {"id": journal.first, "name": "Saved runner <b>雪</b>"}
    assert report["business_id"] == "bunker"
    assert report["row_count"] == 2 and report["row_limit"] == 1000
    assert report["selection"] == "all_matching_observations"
    assert report["ordering"] == "insertion_id_ascending"
    assert report["plot_x"] == "selected_checkin_sequence_1_based"
    assert report["rows"] == [first.to_dict(), last.to_dict()]
    assert report["filters"] == {
        "character_id": journal.first, "business_id": "bunker", "note_query": "match",
        "recorded_from": "0001-01-01", "recorded_until": "9999-12-31",
        "note_match_policy": "literal_substring_ascii_case_insensitive_other_unicode_exact",
        "recorded_date_policy": "inclusive_utc_dates",
    }
    assert report["metrics"][2] == {
        "field": "stock_value", "known_count": 2, "unknown_count": 0,
        "first_value": SQLITE_MAX_INTEGER, "last_value": 0, "change": -SQLITE_MAX_INTEGER,
        "plot_status": "precision_limit",
    }
    notes = " ".join(report["field_notes"].values()).lower()
    for word in ("manual", "null", "zero", "percentage points", "save", "sequence", "precision", "capture"):
        assert word in notes
    assert not {"elapsed_seconds", "duration_seconds", "profit", "rate", "net_per_hour"} & report.keys()
    expected = json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False))
    report["character"]["name"] = "changed"
    report["rows"].clear()
    report["metrics"][0]["change"] = 123
    report["filters"].clear()
    report["field_notes"].clear()
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE characters SET name='changed'")
        db.execute("DELETE FROM manual_business_checkins")
    assert result.to_report() == expected


def test_empty_filters_and_unfiltered_reports_share_same_context():
    plain = snapshot([row()])
    blank = snapshot([row()], filters=BusinessCheckInHistoryFilters(""))
    assert blank.to_report() == plain.to_report()
    assert plain.to_report()["filters"] == {"character_id": 1, "business_id": "bunker"}
