"""Historical activity metrics from real disposable SQLite, including legacy storage."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime, timezone
from fractions import Fraction
import json
import sqlite3
import sys

import pytest
from sqlalchemy import event

from src.database.activity_ledger import ActivityLedgerFilters
from src.database.models import Activity, Character, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture
def repository(tmp_path):
    repo = Repository(str(tmp_path / "insights.sqlite"))
    assert repo.initialize()
    with sqlite3.connect(repo._db_path) as db:
        db.executemany(
            "INSERT INTO characters (id,name,is_active) VALUES (?,?,?)",
            [(1, "First", 1), (2, "Second", 0), (3, "No sessions", 0)],
        )
        db.executemany(
            "INSERT INTO sessions (id,character_id,started_at,ended_at) VALUES (?,?,?,?)",
            [(1, 1, "2026-01-01", "2026-01-03"),
             (2, 2, "2026-01-01", "2026-01-03"),
             (3, 1, "2026-01-01", None),
             (4, 999, "2026-01-01", "2026-01-03"),
             (5, 1, "2026-01-01", "2026-01-03")],
        )
        # Only this disposable legacy fixture permits null/nontext types and
        # preserves numeric-looking text without INTEGER affinity coercion.
        db.execute("DROP TABLE activities")
        db.execute("""CREATE TABLE activities (
            id INTEGER PRIMARY KEY, session_id INTEGER, activity_type, activity_name,
            started_at, ended_at, duration_seconds, earnings, success, business_type, notes
        )""")
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def add_activity(repo, row_id, **overrides):
    values = dict(id=row_id, session_id=1, activity_type="VIP_WORK", activity_name="HeadHunter",
                  started_at="2026-01-02 11:59:00.000000", ended_at="2026-01-02 12:00:00.000000",
                  duration_seconds=60, earnings=100, success=1, business_type=None, notes=None)
    values.update(overrides)
    with sqlite3.connect(repo._db_path) as db:
        db.execute(
            f"INSERT INTO activities ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )


def test_only_completed_existing_sessions_and_characters_contribute(repository):
    for row_id, session_id in enumerate([1, 2, 3, 4, 999, 5], 1):
        add_activity(repository, row_id, session_id=session_id)
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO earnings (session_id,amount) VALUES (?,?)", [(1, 999)] * 5)
        db.execute("UPDATE sessions SET total_earnings=9999999")
    result = repository.get_completed_activity_insights()
    assert result.overall.activities == 3
    assert result.overall.sessions == 3
    assert result.overall.recorded_amount_total == 300
    assert result.groups[0].metrics == result.overall


def test_exact_type_groups_distinct_sessions_outcomes_and_order(repository):
    for row_id, (activity_type, session_id, success) in enumerate([
        (None, 1, 1), ("", 1, 0), ("Custom", 1, 2), ("custom", 1, None),
        ("Custom", 1, 1), ("Custom", 5, 0), ("🛸", 2, "bad"),
    ], 1):
        add_activity(repository, row_id, activity_type=activity_type,
                     session_id=session_id, success=success)
    result = repository.get_completed_activity_insights()
    assert [group.activity_type for group in result.groups] == [None, "", "Custom", "custom", "🛸"]
    assert result.overall.activities == 7 and result.overall.sessions == 3
    assert (result.overall.passed, result.overall.failed, result.overall.unknown) == (2, 2, 3)
    assert result.overall.known_outcomes == 4 and result.overall.pass_rate == 0.5
    custom = result.groups[2].metrics
    assert (custom.activities, custom.sessions, custom.passed, custom.failed, custom.unknown) == (3, 2, 1, 1, 1)


@pytest.mark.parametrize("value,outcome", [
    (1, "passed"), (1.0, "passed"), (0, "failed"), (0.0, "failed"),
    (None, "unknown"), (2, "unknown"), (-1, "unknown"), (0.5, "unknown"),
    ("1", "unknown"), ("0", "unknown"), (b"1", "unknown"),
])
def test_outcomes_use_sql_is_semantics_without_python_truthiness(repository, value, outcome):
    add_activity(repository, 1, success=value)
    for requested in ("passed", "failed", "unknown"):
        result = repository.get_completed_activity_insights(ActivityLedgerFilters(outcome=requested))
        assert result.overall.activities == int(outcome == requested)
    metrics = repository.get_completed_activity_insights().overall
    assert getattr(metrics, outcome) == 1
    assert metrics.pass_rate == (1.0 if outcome == "passed" else 0.0 if outcome == "failed" else None)


@pytest.mark.parametrize("requested", [
    {}, {"character_id": 1}, {"character_id": 2}, {"character_id": 3},
    {"character_id": 2**63 - 1}, {"activity_type": ""}, {"activity_type": "Custom"},
    {"activity_type": "custom"}, {"outcome": "passed"}, {"outcome": "failed"},
    {"outcome": "unknown"}, {"query": "%"}, {"query": "_"}, {"query": "/"},
    {"query": "\\"}, {"query": "caf"}, {"query": "café"}, {"query": "CAFÉ"},
    {"query": "雪"}, {"query": "' OR 1=1 --"},
    {"date_from": date(2026, 1, 2)}, {"date_until": date(2026, 1, 2)},
    {"date_from": date(2026, 1, 2), "date_until": date(2026, 1, 2)},
    {"date_from": date.max, "date_until": date.max},
    {"date_from": date.min, "date_until": date.max},
    {"character_id": 1, "activity_type": "Custom", "query": "CAF", "outcome": "failed"},
])
def test_every_filter_agrees_with_the_existing_ledger(repository, requested):
    samples = [
        (1, "Custom", 0, "café 100%", None, "2026-01-02 00:00:00.000000"),
        (1, "Custom", 0, None, "CAFÉ a_b", "2026-01-02 23:59:59.999999"),
        (2, "custom", 1, "a/b", None, "2026-01-01 23:59:59.999999"),
        (1, "", None, "a\\b", "雪", "2026-01-03 00:00:00.000000"),
        (5, None, 2, "' OR 1=1 --", None, "9999-12-31 23:59:59.999999"),
        (1, "undated", 1, None, None, None),
    ]
    for row_id, (session_id, kind, outcome, name, note, stamp) in enumerate(samples, 1):
        add_activity(repository, row_id, session_id=session_id, activity_type=kind,
                     success=outcome, activity_name=name, notes=note, started_at=stamp, ended_at=None)
    filters = ActivityLedgerFilters(**requested)
    ledger = repository.get_completed_activity_ledger(filters, limit=100)
    result = repository.get_completed_activity_insights(filters)
    assert result.filters is filters
    assert result.overall.activities == ledger.total
    assert result.overall.sessions == len({row.session_id for row in ledger.rows})
    for group in result.groups:
        matches = [row for row in ledger.rows if row.activity_type == group.activity_type]
        assert group.metrics.activities == len(matches)
        assert group.metrics.recorded_amount_total == sum(row.recorded_amount for row in matches)


def test_completion_timestamp_takes_priority_over_start(repository):
    add_activity(repository, 1, started_at="2026-01-10", ended_at="2026-01-02 12:00:00.000000")
    result = repository.get_completed_activity_insights(ActivityLedgerFilters(date_until=date(2026, 1, 2)))
    assert result.overall.activities == 1


def test_coverage_and_rate_use_only_paired_positive_measured_durations(repository):
    values = [(100, 10), (100, 90), (999999, None), (None, 1000), (0, 60),
              (-50, 0), (None, -10), ("75", "60"), (float("inf"), float("inf"))]
    for row_id, (amount, duration) in enumerate(values, 1):
        add_activity(repository, row_id, earnings=amount, duration_seconds=duration, success=0)
    m = repository.get_completed_activity_insights().overall
    assert m.known_amounts == 5 and m.recorded_amount_total == 1000149
    assert m.recorded_amount_mean == 1000149 / 5
    assert (m.positive_durations, m.zero_durations, m.negative_durations, m.unavailable_durations) == (4, 1, 1, 3)
    assert m.duration_seconds_total == 1160 and m.duration_seconds_mean == 290
    assert m.paired_records == 3 and m.recorded_amount_per_hour == 4500
    assert m.failed == 9 and m.issues == ()


@pytest.mark.parametrize("value", [None, "123", "bad", b"123", float("inf"), float("-inf"), float("nan")])
def test_invalid_stored_numbers_are_unavailable_without_coercion(repository, value):
    add_activity(repository, 1, earnings=value, duration_seconds=value)
    m = repository.get_completed_activity_insights().overall
    assert m.known_amounts == 0 and m.unavailable_durations == 1
    assert m.recorded_amount_total is m.recorded_amount_mean is None
    assert m.duration_seconds_total is m.duration_seconds_mean is None
    assert m.recorded_amount_per_hour is None and m.paired_records == 0
    assert m.issues == ()


@pytest.mark.parametrize("amount,expected", [(0, 0.0), (-100, -6000.0)])
def test_zero_and_negative_amounts_have_known_rates(repository, amount, expected):
    add_activity(repository, 1, earnings=amount, duration_seconds=60)
    m = repository.get_completed_activity_insights().overall
    assert m.recorded_amount_total == amount and m.known_amounts == 1
    assert m.recorded_amount_per_hour == expected and m.paired_records == 1


def test_integer_totals_are_exact_beyond_sqlite_and_float_precision(repository):
    for row_id in range(1, 4):
        add_activity(repository, row_id, earnings=2**63 - 1, duration_seconds=2**63 - 1)
    m = repository.get_completed_activity_insights().overall
    assert type(m.recorded_amount_total) is int and m.recorded_amount_total == 3 * (2**63 - 1)
    assert type(m.duration_seconds_total) is int and m.duration_seconds_total == 3 * (2**63 - 1)
    assert m.recorded_amount_per_hour == 3600.0 and m.issues == ()


def test_mixed_finite_values_cancel_exactly_without_intermediate_overflow(repository):
    for row_id, amount in enumerate([1e308, 1e308, -1e308, -1e308, 5], 1):
        add_activity(repository, row_id, earnings=amount, duration_seconds=1)
    m = repository.get_completed_activity_insights().overall
    assert m.recorded_amount_total == 5 and m.recorded_amount_mean == 1
    assert m.recorded_amount_per_hour == 3600 and m.issues == ()


def test_fraction_retains_small_amount_through_large_cancellation(repository):
    for row_id, amount in enumerate([1e300, 0.25, -1e300], 1):
        add_activity(repository, row_id, earnings=amount, duration_seconds=1)
    m = repository.get_completed_activity_insights().overall
    assert m.recorded_amount_total == 0.25
    assert m.recorded_amount_mean == float(Fraction(1, 12))
    assert m.recorded_amount_per_hour == 300


def test_aggregate_overflow_does_not_destroy_finite_mean_or_rate(repository):
    for row_id in (1, 2):
        add_activity(repository, row_id, earnings=sys.float_info.max, duration_seconds=sys.float_info.max)
    result = repository.get_completed_activity_insights()
    m = result.overall
    assert m.recorded_amount_total is None and m.duration_seconds_total is None
    assert m.recorded_amount_mean == sys.float_info.max and m.duration_seconds_mean == sys.float_info.max
    assert m.recorded_amount_per_hour == 3600.0
    assert set(m.issues) == {"recorded_amount_total_out_of_range", "duration_seconds_total_out_of_range"}
    json.dumps(result.to_report(), allow_nan=False)


@pytest.mark.parametrize("amount,duration,unavailable", [
    (sys.float_info.max, 1, "recorded_amount_per_hour_out_of_range"),
    (5e-324, sys.float_info.max, "recorded_amount_per_hour_out_of_range"),
])
def test_unrepresentable_rates_are_unavailable_instead_of_infinite_or_silent_zero(repository, amount, duration, unavailable):
    add_activity(repository, 1, earnings=amount, duration_seconds=duration)
    m = repository.get_completed_activity_insights().overall
    assert m.recorded_amount_per_hour is None and m.issues == (unavailable,)
    assert m.known_amounts == m.positive_durations == m.paired_records == 1


def test_nonzero_mean_underflow_is_reported_without_losing_total(repository):
    add_activity(repository, 1, earnings=5e-324, duration_seconds=None)
    add_activity(repository, 2, earnings=0, duration_seconds=None)
    m = repository.get_completed_activity_insights().overall
    assert m.recorded_amount_total == 5e-324 and m.recorded_amount_mean is None
    assert m.issues == ("recorded_amount_mean_out_of_range",)


def test_all_matching_rows_beyond_live_analytics_sample_are_included(repository):
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO activities (id,session_id,activity_type,earnings,duration_seconds,success) VALUES (?,1,'bulk',2,3,1)", [(i,) for i in range(1, 1502)])
    m = repository.get_completed_activity_insights().overall
    assert m.activities == 1501 and m.recorded_amount_total == 3002 and m.duration_seconds_total == 4503
    assert m.recorded_amount_per_hour == 2400


def test_empty_result_preserves_zero_counts_and_unavailable_metrics(repository):
    result = repository.get_completed_activity_insights()
    assert result.groups == ()
    for key, value in asdict(result.overall).items():
        if key == "issues":
            assert value == ()
        elif key in {"pass_rate", "recorded_amount_total", "recorded_amount_mean", "duration_seconds_total", "duration_seconds_mean", "recorded_amount_per_hour"}:
            assert value is None
        else:
            assert value == 0


def test_source_row_limit_returns_no_partial_report(repository):
    from src.database.activity_insights import ActivityInsightsLimitError, MAX_INSIGHT_RECORDS
    assert MAX_INSIGHT_RECORDS == 100000
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO activities (id,session_id,activity_type) VALUES (?,1,'bulk')", [(i,) for i in range(1, MAX_INSIGHT_RECORDS + 2)])
    with pytest.raises(ActivityInsightsLimitError, match="narrow"):
        repository.get_completed_activity_insights()
    with sqlite3.connect(repository._db_path) as db:
        db.execute("DELETE FROM activities WHERE id=?", (MAX_INSIGHT_RECORDS + 1,))
    assert repository.get_completed_activity_insights().overall.activities == MAX_INSIGHT_RECORDS


def test_group_limit_counts_null_and_blank_and_allows_exact_maximum(repository):
    from src.database.activity_insights import ActivityInsightsLimitError, MAX_INSIGHT_TYPES
    assert MAX_INSIGHT_TYPES == 256
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO activities (id,session_id,activity_type) VALUES (?,1,?)", list(enumerate([None, ""] + [f"type {i}" for i in range(254)], 1)))
    assert len(repository.get_completed_activity_insights().groups) == 256
    add_activity(repository, 257, activity_type="overflow")
    with pytest.raises(ActivityInsightsLimitError, match="narrow"):
        repository.get_completed_activity_insights()


@pytest.mark.parametrize("kind", ["x" * 257, "🛸" * 257, "x\x00" + "y" * 5000, b"binary", 123, 1.5])
def test_unsupported_stored_types_raise_safe_fixed_error(repository, kind):
    from src.database.activity_insights import ActivityInsightsDataError
    add_activity(repository, 1, activity_type=kind)
    with pytest.raises(ActivityInsightsDataError) as error:
        repository.get_completed_activity_insights()
    assert str(error.value) == "Stored activity types cannot be summarized safely. Narrow the filters or repair the source data."


@pytest.mark.parametrize("encoded", ["FF", "ED A0 80", "C0 80", "F4 90 80 80"])
def test_invalid_unicode_stored_types_raise_safe_fixed_error(repository, encoded):
    from src.database.activity_insights import ActivityInsightsDataError
    add_activity(repository, 1)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("UPDATE activities SET activity_type=CAST(? AS TEXT)", (bytes.fromhex(encoded),))
    with pytest.raises(ActivityInsightsDataError, match="Stored activity types cannot"):
        repository.get_completed_activity_insights()


@pytest.mark.parametrize("kind", ["🛸" * 256, "x" * 256, "line\nbreak", "null\x00inside", "line\u2028separator"])
def test_supported_stored_types_preserve_exact_text_including_controls(repository, kind):
    add_activity(repository, 1, activity_type=kind)
    assert repository.get_completed_activity_insights().groups[0].activity_type == kind


def test_one_narrow_select_no_orm_loads_and_bounded_projection(repository):
    add_activity(repository, 1, activity_name="x" * 100000, notes="y" * 100000,
                 earnings="9" * 100000, duration_seconds=b"1" * 100000)
    engine = repository._session_factory.kw["bind"]
    statements, loads = [], []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))
    def capture_load(target, context):
        loads.append(target)
    event.listen(engine, "before_cursor_execute", capture)
    for model in (Activity, Session, Character):
        event.listen(model, "load", capture_load)
    try:
        result = repository.get_completed_activity_insights()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        for model in (Activity, Session, Character):
            event.remove(model, "load", capture_load)
    assert result.overall.activities == 1 and result.overall.known_amounts == 0
    assert loads == [] and len(statements) == 1
    sql, parameters = statements[0]
    sql = sql.lower()
    assert sql.lstrip().startswith("select") and "limit" in sql
    assert 100001 in parameters
    projected = sql.split("from ", 1)[0]
    assert "activity_name" not in projected and "notes" not in projected
    assert "substr" in projected and "typeof" in projected and "blob" in projected
    assert "join earnings" not in sql and "sum(" not in sql
    assert "count(" not in sql and "group by" not in sql


def test_cursor_holds_one_consistent_observation_during_concurrent_write(repository):
    for row_id in range(1, 4):
        add_activity(repository, row_id)
    with sqlite3.connect(repository._db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
    engine = repository._session_factory.kw["bind"]
    def change_after_select(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("select"):
            with sqlite3.connect(repository._db_path) as db:
                db.execute("UPDATE activities SET earnings=200")
    event.listen(engine, "after_cursor_execute", change_after_select)
    try:
        old = repository.get_completed_activity_insights()
    finally:
        event.remove(engine, "after_cursor_execute", change_after_select)
    assert old.overall.recorded_amount_total == 300
    assert repository.get_completed_activity_insights().overall.recorded_amount_total == 600


def test_frozen_detached_report_covers_limits_notes_and_changes_no_database_state(repository):
    add_activity(repository, 1, activity_type="🛸", earnings=0)
    with sqlite3.connect(repository._db_path) as db:
        before_db = list(db.iterdump())
    before = datetime.now(timezone.utc)
    snapshot = repository.get_completed_activity_insights(ActivityLedgerFilters(date_from=date.min, date_until=date.max))
    after = datetime.now(timezone.utc)
    assert before <= snapshot.observed_at <= after
    with sqlite3.connect(repository._db_path) as db:
        assert list(db.iterdump()) == before_db
        db.execute("UPDATE activities SET earnings=500")
    assert snapshot.overall.recorded_amount_total == 0
    for target, key in [(snapshot, "overall"), (snapshot.overall, "activities"), (snapshot.groups[0], "metrics"), (snapshot.filters, "query")]:
        with pytest.raises(FrozenInstanceError):
            setattr(target, key, None)
    source_groups = list(snapshot.groups)
    owned = replace(snapshot, groups=source_groups)
    source_groups.clear()
    assert isinstance(owned.groups, tuple) and len(owned.groups) == 1
    mutable_issues = ["recorded_amount_mean_out_of_range"]
    metrics = replace(snapshot.overall, issues=mutable_issues)
    mutable_issues.clear()
    assert metrics.issues == ("recorded_amount_mean_out_of_range",)
    report = snapshot.to_report()
    assert report["format_version"] == 1 and report["kind"] == "completed_activity_insights"
    assert report["timezone"] == "UTC" and report["scope"] == "completed_sessions"
    assert report["observed_at"] == snapshot.observed_at.isoformat()
    assert report["filters"]["date_from"] == "0001-01-01"
    assert report["filters"]["date_until"] == "9999-12-31"
    assert report["limits"]["max_source_records"] == 100000
    assert report["limits"]["max_activity_types"] == 256
    assert report["limits"]["max_activity_type_characters"] == 256
    assert report["field_notes"] and report["snapshot_notes"]
    assert report["overall"]["issues"] == []
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    report["overall"]["activities"] = 999
    report["groups"][0]["metrics"]["known_amounts"] = 999
    report["limits"].clear()
    report["field_notes"].clear()
    report["snapshot_notes"].clear()
    fresh = snapshot.to_report()
    assert fresh["overall"]["activities"] == fresh["groups"][0]["metrics"]["known_amounts"] == 1
    assert fresh["limits"] and fresh["field_notes"] and fresh["snapshot_notes"]


@pytest.mark.parametrize("field,value", [
    ("character_id", True), ("character_id", 0), ("character_id", 2**63),
    ("date_from", datetime(2026, 1, 2)), ("date_until", "2026-01-02"),
    ("activity_type", 3), ("activity_type", "x" * 257), ("activity_type", "a\nb"),
    ("outcome", "cancelled"), ("query", ""), ("query", "x" * 201),
])
def test_invalid_filters_fail_before_initialization(tmp_path, field, value):
    repo = Repository(str(tmp_path / "missing" / "db.sqlite"))
    with pytest.raises(ValueError, match=field):
        repo.get_completed_activity_insights(ActivityLedgerFilters(**{field: value}))
    assert not repo._initialized and not (tmp_path / "missing").exists()


@pytest.mark.parametrize("filters", [{}, False, "query", ActivityLedgerFilters(date_from=date.max, date_until=date.min)])
def test_invalid_request_shapes_and_reversed_dates_fail_before_storage(tmp_path, filters):
    repo = Repository(str(tmp_path / "missing" / "db.sqlite"))
    with pytest.raises(ValueError):
        repo.get_completed_activity_insights(filters)
    assert not repo._initialized


def test_initialization_and_query_failures_are_not_empty_insights(repository, tmp_path):
    repo = Repository(str(tmp_path / "missing" / "db.sqlite"))
    with pytest.raises(DatabaseError, match="initialization failed"):
        repo.get_completed_activity_insights()
    with sqlite3.connect(repository._db_path) as db:
        db.execute("DROP TABLE activities")
    with pytest.raises(DatabaseError, match="Database operation failed"):
        repository.get_completed_activity_insights()
