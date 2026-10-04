"""Daily completed-session snapshots from real, disposable SQLite storage."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from importlib import import_module, util
import json
import sqlite3
import sys

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.repository import Repository


DAY = date(2026, 1, 2)
STAMP = "2026-01-02T12:00:00Z"


def domain():
    assert util.find_spec("src.database.daily_session_overview"), "Daily session domain is missing"
    return import_module("src.database.daily_session_overview")


@pytest.fixture
def repository(tmp_path):
    repo = Repository(str(tmp_path / "daily.sqlite"))
    assert repo.initialize()
    with sqlite3.connect(repo._db_path) as db:
        db.executemany("INSERT INTO characters(id,name,is_active) VALUES(?,?,?)",
                       [(1, "First <b>雪</b>", 1), (2, "Second", 0), (3, "Empty", 0)])
        # A legacy table with no affinity exposes raw numeric-looking text.
        db.execute("DROP TABLE sessions")
        db.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY,character_id,started_at,ended_at,"
                   "start_money,end_money,total_earnings)")
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def add_session(repo, identifier, **overrides):
    values = dict(id=identifier, character_id=1, started_at="2026-01-02T11:00:00Z",
                  ended_at=STAMP, start_money=1_000_000, end_money=2_000_000, total_earnings=100)
    values.update(overrides)
    with sqlite3.connect(repo._db_path) as db:
        db.execute(f"INSERT INTO sessions ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                   tuple(values.values()))


def capture(repo, start=DAY, until=DAY, character_id=None):
    return repo.get_daily_session_overview(domain().DailySessionFilters(start, until, character_id))


def test_public_contract_and_empty_inclusive_days(repository):
    model = domain()
    assert model.MAX_DAILY_SESSION_ROWS == 10_000
    assert model.MAX_DAILY_SESSION_DAYS == 366
    result = capture(repository, DAY, DAY + timedelta(days=2))
    assert [item.day for item in result.days] == [DAY + timedelta(days=i) for i in (2, 1, 0)]
    assert result.rows == () and result.rows_for_day(DAY) == ()
    assert result.overall.sessions == result.overall.known_net == 0
    assert result.overall.net_change_total is result.overall.duration_seconds_total is None
    assert result.overall.net_per_hour is None
    assert all(item.metrics == result.overall for item in result.days)
    assert result.observed_at.tzinfo == timezone.utc
    with pytest.raises(FrozenInstanceError):
        result.rows = ()


@pytest.mark.parametrize("start,until,character", [
    (None, DAY, None), (DAY, None, None), (datetime(2026, 1, 2), DAY, None),
    (DAY, "2026-01-02", None), (DAY, DAY - timedelta(days=1), None),
    (DAY, DAY + timedelta(days=366), None), (DAY, DAY, True),
    (DAY, DAY, 0), (DAY, DAY, -1), (DAY, DAY, 2**63), (DAY, DAY, "1"),
])
def test_invalid_filter_fails_before_storage(tmp_path, monkeypatch, start, until, character):
    model = domain()
    repo = Repository(str(tmp_path / "must-not-create.sqlite"))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("Invalid filter accessed storage"))
    with pytest.raises(model.DailySessionValidationError):
        repo.get_daily_session_overview(model.DailySessionFilters(start, until, character))


def test_date_extrema_and_longest_window_are_safe(repository):
    assert len(capture(repository, date.min, date.min + timedelta(days=365)).days) == 366
    assert capture(repository, date.max, date.max).days[0].day == date.max
    add_session(repository, 1, started_at="9999-12-31T00:00:00Z", ended_at="9999-12-31T23:59:59.999999Z")
    assert capture(repository, date.max, date.max).rows[0].duration_microseconds == 86_399_999_999


def test_whole_sessions_use_normalized_end_day_and_order(repository):
    stamps = ["2026-01-03T00:30:00+01:00", "2026-01-02T23:30:00-01:00",
              "2026-01-02T00:00:00.000001Z", "2026-01-02 23:30:00.000000",
              "2026-01-01T23:59:59.999999Z", "2026-01-03T00:00:00Z"]
    for i, stamp in enumerate(stamps, 1):
        add_session(repository, i, started_at="2026-01-01T23:00:00Z", ended_at=stamp)
    result = capture(repository)
    assert [row.session_id for row in result.rows] == [4, 1, 3]
    assert result.rows_for_day(DAY) == result.rows
    assert all(row.completion_date == DAY and row.ended_at.tzinfo == timezone.utc for row in result.rows)
    assert result.rows[1].duration_seconds == 24.5 * 3600
    assert result.rows[-1].duration_microseconds == 3_600_000_001
    assert result.overall.net_change_total == 300


def test_sql_null_open_sessions_and_orphans_are_excluded(repository):
    add_session(repository, 1)
    add_session(repository, 2, ended_at=None)
    add_session(repository, 3, character_id=999, ended_at="corrupt orphan")
    add_session(repository, 4, character_id=2)
    assert capture(repository).overall.sessions == 2
    assert [row.session_id for row in capture(repository, character_id=1).rows] == [1]


@pytest.mark.parametrize("bad_end", ["invalid", "2026-01-02", "2000-01-01T99:00:00Z", 123,
    b"2026-01-02T12:00:00Z", "x" * 100_000,
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"],
    ids=["word", "date-only", "outside-window", "integer", "blob", "oversize", "underflow", "overflow"])
def test_unassignable_ends_fail_including_apparently_outside_window(repository, bad_end):
    add_session(repository, 1)
    add_session(repository, 2, ended_at=bad_end)
    with pytest.raises(domain().DailySessionDataError) as error:
        capture(repository)
    assert error.value.unassignable_ends == 1
    assert "invalid" not in str(error.value).lower()


def test_invalid_utf8_ends_count_without_driver_decoding_and_respect_scope(repository):
    add_session(repository, 1)
    add_session(repository, 2, character_id=2)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE sessions SET ended_at=CAST(x'ff' AS TEXT) WHERE id=2")
    assert capture(repository, character_id=1).overall.sessions == 1
    with pytest.raises(domain().DailySessionDataError) as error:
        capture(repository)
    assert error.value.unassignable_ends == 1


@pytest.mark.parametrize("start,status", [(None, "missing"), ("bad", "invalid"),
    (123, "invalid"), (b"2026-01-02T11:00:00Z", "invalid"),
    ("0001-01-01T00:00:00+01:00", "invalid")])
def test_bad_starts_retain_net_and_counts(repository, start, status):
    add_session(repository, 1, started_at=start, total_earnings=-25)
    result = capture(repository)
    row = result.rows[0]
    assert row.start_status == status and row.started_at is None
    assert row.duration_microseconds is row.duration_seconds is None
    assert result.overall.net_change_total == -25
    assert result.overall.sessions == result.overall.unavailable_durations == 1
    assert result.overall.paired_sessions == 0 and result.overall.net_per_hour is None


def test_coverage_rate_and_net_use_separate_contributing_sets(repository):
    values = [(100, "2026-01-02T11:00:00Z"), (-25, "2026-01-02T11:00:00Z"),
              (999, None), (None, "2026-01-02T10:00:00Z"), (0, STAMP),
              (None, "2026-01-02T13:00:00Z"), ("75", "2026-01-02T11:00:00Z")]
    for i, (net, start) in enumerate(values, 1):
        add_session(repository, i, total_earnings=net, started_at=start)
    m = capture(repository).overall
    assert (m.sessions, m.known_net, m.net_change_total) == (7, 4, 1074)
    assert (m.positive_durations, m.zero_durations, m.negative_durations, m.unavailable_durations) == (4, 1, 1, 1)
    assert m.duration_seconds_total == 18_000
    assert m.paired_sessions == 2 and m.net_per_hour == 37.5 and m.issues == ()


@pytest.mark.parametrize("net", [None, "123", b"123", "bad", float("inf"), float("-inf"), float("nan")])
def test_raw_numeric_values_are_never_coerced(repository, net):
    add_session(repository, 1, total_earnings=net)
    result = capture(repository)
    assert result.rows[0].net_change is None
    assert result.overall.known_net == result.overall.paired_sessions == 0
    assert result.overall.net_change_total is result.overall.net_per_hour is None


def test_exact_large_integer_sum_and_microsecond_rate(repository):
    for i in range(1, 4):
        add_session(repository, i, total_earnings=2**63 - 1,
                    started_at="2026-01-02T11:59:59.999999Z")
    result = capture(repository)
    assert result.overall.net_change_total == 3 * (2**63 - 1)
    assert type(result.overall.net_change_total) is int
    assert result.overall.duration_seconds_total == 0.000003
    assert result.overall.net_per_hour == float((2**63 - 1) * 3_600_000_000)


def test_float_cancellation_is_exact_and_range_issues_are_explicit(repository):
    for i, net in enumerate((sys.float_info.max, sys.float_info.max), 1):
        add_session(repository, i, total_earnings=net)
    metrics = capture(repository).overall
    assert metrics.net_change_total is None and metrics.net_per_hour == sys.float_info.max
    assert metrics.issues == ("net_change_total_out_of_range",)
    add_session(repository, 3, total_earnings=-sys.float_info.max)
    assert capture(repository).overall.net_change_total == sys.float_info.max


def test_nonzero_rate_underflow_is_unavailable(repository):
    add_session(repository, 1, total_earnings=5e-324, started_at="0001-01-01T00:00:00Z")
    metrics = capture(repository).overall
    assert metrics.net_change_total == 5e-324 and metrics.net_per_hour is None
    assert metrics.issues == ("net_per_hour_out_of_range",)


def test_saved_net_is_independent_of_balances_activities_events_and_annotations(repository):
    add_session(repository, 1, total_earnings=-10)
    baseline = capture(repository)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE sessions SET start_money=-999999,end_money=999999")
        db.execute("INSERT INTO activities(session_id,activity_type,earnings) VALUES(1,'MISSION',10000000)")
        db.execute("INSERT INTO earnings(session_id,amount) VALUES(1,99999999)")
        db.execute("INSERT INTO session_annotations(session_id,label,tags_text,note,revision) VALUES(1,'Note','[]','Other',1)")
    refreshed = capture(repository)
    assert baseline.rows == refreshed.rows and baseline.overall == refreshed.overall


def test_report_keeps_snapshot_sources_and_exact_microseconds(repository):
    add_session(repository, 1, started_at="2026-01-02T11:59:59.999999Z")
    result = capture(repository)
    report = result.to_report()
    assert report["kind"] == "daily_completed_session_overview"
    assert report["timezone"] == "UTC" and report["row_count"] == 1
    assert report["rows"][0]["duration_microseconds"] == 1
    assert report["rows"][0]["start_status"] == "known"
    assert report["filters"] == {"date_from": "2026-01-02", "date_until": "2026-01-02", "character_id": None}
    json.dumps(report, ensure_ascii=False, allow_nan=False)
    report["rows"][0]["net_change"] = 999
    report["days"].clear()
    assert result.to_report()["rows"][0]["net_change"] == 100 and len(result.days) == 1


@pytest.mark.parametrize("count", [10_000, 10_001, 10_100])
def test_source_cap_uses_complete_match_count_before_decoding(repository, monkeypatch, count):
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO sessions(character_id,started_at,ended_at,total_earnings) VALUES(1,?,?,1)",
                       [(STAMP, STAMP)] * count)
        # Unselected valid ends don't consume the cap or expose bad payloads.
        db.executemany("INSERT INTO sessions(character_id,started_at,ended_at,total_earnings) VALUES(1,NULL,?,1)",
                       [("2000-01-01T00:00:00Z",)] * 10)
    if count > 10_000:
        monkeypatch.setattr("src.database.repository.daily_session_row_from_storage",
                            lambda *args: pytest.fail("An over-limit snapshot decoded payload"))
        with pytest.raises(domain().DailySessionLimitError) as error:
            capture(repository)
        assert error.value.matching_sessions == count
    else:
        result = capture(repository)
        assert len(result.rows) == result.overall.sessions == 10_000


def test_unassignable_end_count_is_complete_even_beyond_cap(repository):
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO sessions(character_id,ended_at) VALUES(1,?)", [(STAMP,)] * 10_001)
        db.executemany("INSERT INTO sessions(character_id,ended_at) VALUES(1,?)", [("bad outside window",)] * 7)
    with pytest.raises(domain().DailySessionDataError) as error:
        capture(repository)
    assert error.value.unassignable_ends == 7


def test_explicit_owner_absence_and_disappearance_are_not_empty_success(repository):
    assert capture(repository, character_id=3).rows == ()
    add_session(repository, 1, character_id=3)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("DELETE FROM characters WHERE id=3")
    with pytest.raises(domain().DailySessionUnavailable) as error:
        capture(repository, character_id=3)
    assert error.value.character_id == 3


@pytest.mark.parametrize("assignment", ["name=CAST(x'ff' AS TEXT)", "name=zeroblob(100000)",
    "name='" + "x" * 1001 + "'", "name=NULL"])
def test_corrupt_selected_owner_payload_fails_even_for_empty_scope(repository, assignment):
    with sqlite3.connect(repository._db_path) as db:
        db.execute("ALTER TABLE characters RENAME TO original_characters")
        db.execute("CREATE TABLE characters AS SELECT * FROM original_characters")
        db.execute(f"UPDATE characters SET {assignment} WHERE id=3")
    with pytest.raises(domain().DailySessionDataError):
        capture(repository, character_id=3)


@pytest.mark.parametrize("identifier", [None, -1, 1.5, "1"])
def test_invalid_raw_session_ids_fail_without_coercion(repository, identifier):
    add_session(repository, 1)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("ALTER TABLE sessions RENAME TO original_sessions")
        db.execute("CREATE TABLE sessions(id,character_id,started_at,ended_at,total_earnings)")
        db.execute("INSERT INTO sessions SELECT id,character_id,started_at,ended_at,total_earnings FROM original_sessions")
        db.execute("UPDATE sessions SET id=?", (identifier,))
    with pytest.raises(domain().DailySessionDataError):
        capture(repository)


@pytest.mark.parametrize("table,selected", [("sessions", False), ("characters", False), ("characters", True)])
def test_duplicate_source_ids_or_owners_fail_explicitly(repository, table, selected):
    add_session(repository, 1)
    with sqlite3.connect(repository._db_path) as db:
        db.execute(f"ALTER TABLE {table} RENAME TO original_{table}")
        db.execute(f"CREATE TABLE {table} AS SELECT * FROM original_{table}")
        db.execute(f"INSERT INTO {table} SELECT * FROM original_{table} WHERE id=1")
    with pytest.raises(domain().DailySessionDataError):
        capture(repository, character_id=1 if selected else None)


def test_read_is_one_narrow_bounded_statement_without_writes(repository, monkeypatch):
    add_session(repository, 1)
    statements = []
    engine = repository._session_factory.kw["bind"]
    with sqlite3.connect(repository._db_path) as db:
        before = tuple(db.iterdump())

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        result = capture(repository)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert result.overall.sessions == 1 and len(statements) == 1
    sql, parameters = statements[0]
    assert sql.lstrip().upper().startswith("WITH")
    assert "LIMIT" in sql.upper() and 10_001 in parameters
    assert "start_money" not in sql and "end_money" not in sql
    assert "activities" not in sql and "annotations" not in sql
    with sqlite3.connect(repository._db_path) as db:
        assert tuple(db.iterdump()) == before


def test_snapshot_owner_count_and_payload_remain_consistent_with_wal_writer(repository):
    for i in (1, 2, 3):
        add_session(repository, i, total_earnings=i)
    with sqlite3.connect(repository._db_path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []
    engine = repository._session_factory.kw["bind"]

    def write_after_query(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(repository._db_path) as other:
                other.execute("UPDATE characters SET name='Changed later' WHERE id=1")
                other.execute("UPDATE sessions SET total_earnings=99 WHERE id=3")
                other.execute("INSERT INTO sessions(character_id,ended_at,total_earnings) VALUES(1,?,99)", (STAMP,))

    event.listen(engine, "after_cursor_execute", write_after_query)
    try:
        result = capture(repository, character_id=1)
    finally:
        event.remove(engine, "after_cursor_execute", write_after_query)
    assert len(statements) == 1
    assert result.overall.sessions == 3 and result.overall.net_change_total == 6
    assert all(row.character_name == "First <b>雪</b>" for row in result.rows)
    fresh = capture(repository, character_id=1)
    assert fresh.overall.sessions == 4 and fresh.overall.net_change_total == 201
    assert result.overall.net_change_total == 6


def test_predicate_is_removed_after_success_failure_and_operational_errors(repository):
    add_session(repository, 1, ended_at="bad")
    with pytest.raises(domain().DailySessionDataError):
        capture(repository)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE sessions SET ended_at=?", (STAMP,))
    assert capture(repository).overall.sessions == 1
    engine = repository._session_factory.kw["bind"]
    with engine.connect() as connection, pytest.raises(OperationalError):
        connection.exec_driver_sql("SELECT daily_session_end_match(x'', 'text')")

    def fail(*args):
        raise OperationalError("private SQL", {}, Exception("private path"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(domain().DailySessionUnavailable) as error:
            capture(repository)
        assert "private" not in str(error.value)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert capture(repository).overall.sessions == 1


def test_elapsed_aggregation_retains_microseconds_across_long_spans(repository):
    add_session(repository, 1, started_at="0001-01-01T00:00:00Z", total_earnings=0)
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO sessions(character_id,started_at,ended_at,total_earnings) VALUES(1,?,?,1)",
                       [("2026-01-02T11:59:59.999999Z", STAMP)] * 1000)
    elapsed = datetime(2026, 1, 2, 12) - datetime.min
    exact_us = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + 1000
    result = capture(repository)
    assert result.rows[-1].duration_microseconds == exact_us - 1000
    assert result.overall.duration_seconds_total == float(Fraction(exact_us, 1_000_000))
    assert result.overall.net_per_hour == float(Fraction(1000 * 3_600_000_000, exact_us))


def test_positive_microsecond_duration_can_overflow_rate_without_losing_net(repository):
    add_session(repository, 1, started_at="2026-01-02T11:59:59.999999Z", total_earnings=sys.float_info.max)
    metrics = capture(repository).overall
    assert metrics.net_change_total == sys.float_info.max
    assert metrics.net_per_hour is None and metrics.issues == ("net_per_hour_out_of_range",)


@pytest.mark.parametrize("net,rate", [(0, 0.0), (-100, -100.0)])
def test_zero_and_negative_net_rates_are_known(repository, net, rate):
    add_session(repository, 1, total_earnings=net)
    metrics = capture(repository).overall
    assert metrics.net_per_hour == rate and metrics.paired_sessions == 1


def test_normalized_starts_and_negative_elapsed_remain_exact(repository):
    add_session(repository, 1, started_at="2026-01-02T13:00:00+01:00")
    add_session(repository, 2, started_at="2026-01-02T13:00:00.000001Z")
    rows = capture(repository).rows
    assert rows[0].duration_microseconds == -3_600_000_001
    assert rows[0].duration_seconds == -3600.000001 and rows[0].start_status == "known"
    assert rows[1].duration_microseconds == 0
    assert rows[1].started_at == rows[1].ended_at


def test_maximum_ids_are_preserved_and_timestamp_membership_is_inclusive(repository):
    maximum = 2**63 - 1
    with sqlite3.connect(repository._db_path) as db:
        db.execute("INSERT INTO characters(id,name,is_active) VALUES(?,'Maximum',0)", (maximum,))
    add_session(repository, maximum, character_id=maximum, ended_at="2026-01-02T23:59:59.999999Z")
    add_session(repository, maximum - 1, character_id=maximum, ended_at="2026-01-02T00:00:00Z")
    result = capture(repository, character_id=maximum)
    assert [row.session_id for row in result.rows] == [maximum, maximum - 1]
    assert all(row.character_id == maximum for row in result.rows)


def test_bounded_invalid_utf8_start_retains_net_without_driver_error(repository):
    add_session(repository, 1)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE sessions SET started_at=CAST(x'ff' AS TEXT)")
    result = capture(repository)
    assert result.rows[0].start_status == "invalid"
    assert result.overall.known_net == result.overall.unavailable_durations == 1


def test_sql_callback_and_projection_receive_only_bounded_bytes(repository, monkeypatch):
    add_session(repository, 1, ended_at="x" * 100_000)
    original = import_module("src.database.repository").daily_session_end_match_from_storage
    seen = []

    def bounded(value, storage_type, filters):
        assert type(value) is bytes and len(value) <= 65
        seen.append(len(value))
        return original(value, storage_type, filters)

    monkeypatch.setattr("src.database.repository.daily_session_end_match_from_storage", bounded)
    with pytest.raises(domain().DailySessionDataError):
        capture(repository)
    assert seen and max(seen) == 65

    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE sessions SET ended_at=?,started_at=zeroblob(100000)", (STAMP,))
        db.execute("UPDATE characters SET name=? WHERE id=1", ("🚀" * 1000,))
    original_row = import_module("src.database.repository").daily_session_row_from_storage

    def bounded_row(record):
        assert len(record["started_at"]) <= 65 and len(record["ended_at"]) <= 65
        assert len(record["character_name"]) <= 4001
        return original_row(record)

    monkeypatch.setattr("src.database.repository.daily_session_row_from_storage", bounded_row)
    assert capture(repository).rows[0].character_name == "🚀" * 1000


@pytest.mark.parametrize("character_id", ["1", 1.0, b"1"])
def test_raw_noninteger_character_ids_fail_if_they_match_an_owner(repository, character_id):
    add_session(repository, 1, character_id=character_id)
    # SQLite numeric affinity may join text/real owner keys, but no coercion is accepted.
    if character_id != b"1":
        with pytest.raises(domain().DailySessionDataError):
            capture(repository)
    else:
        assert capture(repository).rows == ()


def test_database_commit_failure_cannot_return_an_accepted_snapshot(repository):
    add_session(repository, 1)
    engine = repository._session_factory.kw["bind"]

    def fail_commit(connection):
        raise OperationalError("private commit", {}, Exception("private storage"))

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(domain().DailySessionUnavailable) as error:
            capture(repository)
        assert "private" not in str(error.value)
    finally:
        event.remove(engine, "commit", fail_commit)
    with engine.connect() as connection, pytest.raises(OperationalError):
        connection.exec_driver_sql("SELECT daily_session_end_match(x'', 'text')")
    assert capture(repository).overall.sessions == 1
