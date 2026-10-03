"""Completed-activity browsing against real, disposable SQLite databases."""

from dataclasses import FrozenInstanceError, asdict
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3

import pytest
from sqlalchemy import event

from src.database.models import Activity, Character, Session
from src.database.repository import DatabaseError, Repository


DAY = datetime(2026, 1, 2, 12)
MAX_SQLITE_INTEGER = 2**63 - 1


@pytest.fixture
def repository(tmp_path):
    repo = Repository(str(tmp_path / "ledger.sqlite"))
    assert repo.initialize()
    with sqlite3.connect(repo._db_path) as db:
        db.executemany(
            "INSERT INTO characters (id, name, is_active) VALUES (?, ?, ?)",
            [(1, "François <A>", 1), (2, "東京 & B", 0), (3, "No sessions", 0)],
        )
        db.executemany(
            "INSERT INTO sessions (id, character_id, started_at, ended_at) VALUES (?, ?, ?, ?)",
            [(1, 1, "2026-01-01 00:00:00", "2026-01-03 00:00:00"),
             (2, 2, "2026-01-01 00:00:00", "2026-01-03 00:00:00"),
             (3, 1, "2026-01-01 00:00:00", None),
             (4, 999, "2026-01-01 00:00:00", "2026-01-03 00:00:00")],
        )
        # Imported legacy databases can have nullable types. Only the disposable
        # fixture is relaxed; the application's schema is never changed.
        db.execute("CREATE TABLE legacy_activities AS SELECT * FROM activities")
        db.execute("DROP TABLE activities")
        db.execute("ALTER TABLE legacy_activities RENAME TO activities")
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def add_activity(repo, row_id, **overrides):
    values = {
        "id": row_id, "session_id": 1, "activity_type": "VIP_WORK",
        "activity_name": "HeadHunter", "started_at": DAY - timedelta(minutes=1),
        "ended_at": DAY, "duration_seconds": 42, "earnings": 123,
        "success": 1, "business_type": None, "notes": None,
    }
    values.update(overrides)
    values = {
        key: value.isoformat(" ", timespec="microseconds") if isinstance(value, datetime)
        else value for key, value in values.items()
    }
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with sqlite3.connect(repo._db_path) as db:
        db.execute(f"INSERT INTO activities ({columns}) VALUES ({placeholders})", tuple(values.values()))


def filters(**kwargs):
    from src.database.activity_ledger import ActivityLedgerFilters
    return ActivityLedgerFilters(**kwargs)


def ids(page):
    return [row.id for row in page.rows]


def test_only_completed_sessions_with_existing_characters_are_included(repository):
    add_activity(repository, 1)
    add_activity(repository, 2, session_id=2)
    add_activity(repository, 3, session_id=3)
    add_activity(repository, 4, session_id=4)
    add_activity(repository, 5, session_id=999)

    page = repository.get_completed_activity_ledger()

    assert page.total == 2
    assert ids(page) == [2, 1]
    assert [row.character_name for row in page.rows] == ["東京 & B", "François <A>"]
    assert (page.offset, page.limit) == (0, 25)


def test_returns_exact_saved_fields_and_does_not_derive_duration(repository):
    add_activity(
        repository, 1, activity_type="custom 🛸", activity_name="<b>Saved</b>",
        started_at=DAY + timedelta(seconds=1), ended_at=DAY, duration_seconds=-3.5,
        earnings=-50, business_type="", notes="line one\nline two & 雪", success=0,
    )

    row = repository.get_completed_activity_ledger().rows[0]

    assert asdict(row) == {
        "id": 1, "session_id": 1, "character_id": 1, "character_name": "François <A>",
        "activity_type": "custom 🛸", "name": "<b>Saved</b>", "business_type": "",
        "notes": "line one\nline two & 雪", "recorded_start": DAY + timedelta(seconds=1),
        "completed_at": DAY, "activity_time": DAY, "recorded_amount": -50,
        "duration_seconds": -3.5, "outcome": "failed",
    }


def test_character_exact_type_outcome_and_phrase_filters_combine_before_paging(repository):
    add_activity(repository, 1, activity_type="Custom", notes="look HERE", success=0)
    add_activity(repository, 2, activity_type="Custom", notes="look here", success=0)
    add_activity(repository, 3, activity_type="custom", notes="look here", success=0)
    add_activity(repository, 4, activity_type="Custom", notes="look here", success=1)
    add_activity(repository, 5, activity_type="Custom", notes="look here", success=0, session_id=2)
    add_activity(repository, 6, activity_type="Custom", notes="unrelated note", success=0)

    page = repository.get_completed_activity_ledger(
        filters(character_id=1, activity_type="Custom", outcome="failed", query="HERE"),
        limit=1, offset=1,
    )

    assert page.total == 2
    assert ids(page) == [1]
    assert repository.get_completed_activity_ledger(filters(character_id=3)).total == 0
    assert repository.get_completed_activity_ledger(filters(character_id=MAX_SQLITE_INTEGER)).rows == ()


@pytest.mark.parametrize("stored,expected", [
    (1, "passed"), (0, "failed"), (None, "unknown"), (2, "unknown"),
    (-1, "unknown"), ("unknown", "unknown"), (0.5, "unknown"),
])
def test_outcome_classification_and_filter_ignore_boolean_coercion(repository, stored, expected):
    add_activity(repository, 1, success=stored)

    assert repository.get_completed_activity_ledger().rows[0].outcome == expected
    for outcome in ("passed", "failed", "unknown"):
        page = repository.get_completed_activity_ledger(filters(outcome=outcome))
        assert ids(page) == ([1] if outcome == expected else [])
        assert page.total == (1 if outcome == expected else 0)


def test_null_blank_custom_type_and_nullable_text_stay_distinct(repository):
    for row_id, value in enumerate([None, "", "custom 🛸"], start=1):
        add_activity(repository, row_id, activity_type=value, activity_name=None,
                     notes=None, business_type=None, started_at=None, ended_at=None)

    rows = repository.get_completed_activity_ledger().rows

    assert [row.activity_type for row in rows] == ["custom 🛸", "", None]
    assert all(row.name is row.notes is row.business_type is None for row in rows)
    assert all(row.recorded_start is row.completed_at is row.activity_time is None for row in rows)
    assert ids(repository.get_completed_activity_ledger(filters(activity_type=""))) == [2]


def test_activity_time_prefers_end_then_start_and_sorts_undated_last(repository):
    add_activity(repository, 1, started_at=DAY + timedelta(days=20), ended_at=DAY)
    add_activity(repository, 2, started_at=DAY, ended_at=None)
    add_activity(repository, 3, started_at=None, ended_at=None)
    add_activity(repository, 4, started_at=None, ended_at=None)
    add_activity(repository, 5, started_at=DAY, ended_at=DAY + timedelta(days=10000))

    page = repository.get_completed_activity_ledger()

    assert ids(page) == [5, 2, 1, 4, 3]
    assert page.rows[1].activity_time == page.rows[2].activity_time == DAY


@pytest.mark.parametrize("bounds,expected", [
    ({}, [6, 5, 4, 3, 2, 1, 7]),
    ({"date_from": date(2026, 1, 2)}, [6, 5, 4, 3, 2]),
    ({"date_until": date(2026, 1, 2)}, [4, 3, 2, 1]),
    ({"date_from": date(2026, 1, 2), "date_until": date(2026, 1, 2)}, [4, 3, 2]),
    ({"date_from": date.max, "date_until": date.max}, [6]),
    ({"date_from": date.min, "date_until": date.max}, [6, 5, 4, 3, 2, 1]),
])
def test_inclusive_utc_days_cover_boundaries_fallback_future_and_date_max(repository, bounds, expected):
    for row_id, value in enumerate([
        datetime(2026, 1, 1, 23, 59, 59, 999999), datetime(2026, 1, 2),
        datetime(2026, 1, 2, 12), datetime(2026, 1, 2, 23, 59, 59, 999999),
        datetime(2026, 1, 3), datetime.max, None,
    ], start=1):
        add_activity(repository, row_id, started_at=value, ended_at=None)

    page = repository.get_completed_activity_ledger(filters(**bounds))

    assert ids(page) == expected
    assert page.total == len(expected)


@pytest.mark.parametrize("query,expected", [
    ("%", [1]), ("_", [2]), ("/", [3]), ("\\", [4]),
    ("CAF", [6, 5]), ("café", [5]), ("CAFÉ", [6]), ("雪", [7]),
    (" Notes ", [8]), ("head note", []), ("type-only", []), ("business-only", []),
    ("' OR 1=1 --", [10]),
])
def test_name_or_notes_search_is_literal_bound_and_ascii_case_insensitive(repository, query, expected):
    names = ["100%", "a_b", "a/b", "a\\b", "café", "CAFÉ", None,
             None, "head", "' OR 1=1 --"]
    for row_id, name in enumerate(names, start=1):
        note = {7: "雪", 8: "other Notes text", 9: "note"}.get(row_id)
        add_activity(repository, row_id, activity_name=name, notes=note,
                     activity_type="type-only", business_type="business-only")

    page = repository.get_completed_activity_ledger(filters(query=query))

    assert ids(page) == expected
    assert page.filters.query == query


@pytest.mark.parametrize("stored,expected", [
    (None, None), (0, 0), (-25, -25), (12.5, 12.5),
    (float("inf"), None), (float("-inf"), None), (float("nan"), None),
    ("invalid numeric text", None),
])
def test_stored_numbers_are_finite_or_unavailable_without_derivation(repository, stored, expected):
    add_activity(repository, 1, earnings=stored, duration_seconds=stored)

    row = repository.get_completed_activity_ledger().rows[0]

    assert row.recorded_amount == expected
    assert row.duration_seconds == expected
    json.dumps(repository.get_completed_activity_ledger().to_report(), allow_nan=False)


def test_pages_are_bounded_deterministic_and_keep_total_beyond_end(repository):
    for row_id in range(1, 132):
        add_activity(repository, row_id)
    first = repository.get_completed_activity_ledger()
    second = repository.get_completed_activity_ledger(limit=100, offset=25)
    last = repository.get_completed_activity_ledger(limit=100, offset=125)
    beyond = repository.get_completed_activity_ledger(limit=100, offset=MAX_SQLITE_INTEGER)

    assert first.total == second.total == last.total == beyond.total == 131
    assert ids(first) + ids(second) + ids(last) == list(range(131, 0, -1))
    assert len(first.rows) == 25
    assert len(second.rows) == 100
    assert beyond.rows == ()
    assert beyond.offset == MAX_SQLITE_INTEGER


@pytest.mark.parametrize("offset", [0, 500])
def test_one_select_count_anchor_has_bounded_page_no_earnings_or_orm_loads(repository, offset):
    add_activity(repository, 1)
    with sqlite3.connect(repository._db_path) as db:
        db.executemany("INSERT INTO earnings (session_id, amount) VALUES (?, ?)", [(1, 999)] * 5)
    engine = repository._session_factory.kw["bind"]
    statements, loaded = [], []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def capture_load(target, context):
        loaded.append(target)

    event.listen(engine, "before_cursor_execute", capture)
    for model in (Activity, Character, Session):
        event.listen(model, "load", capture_load)
    try:
        page = repository.get_completed_activity_ledger(limit=1, offset=offset)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        for model in (Activity, Character, Session):
            event.remove(model, "load", capture_load)

    assert page.total == 1
    assert ids(page) == ([1] if offset == 0 else [])
    assert loaded == []
    assert len(statements) == 1
    sql = statements[0].lower()
    assert sql.lstrip().startswith("select")
    assert "left outer join" in sql and "count(" in sql
    assert "limit" in sql and "offset" in sql and sql.count("order by") == 2
    assert "join earnings" not in sql and "from earnings" not in sql


def test_empty_database_returns_no_placeholder_row_and_zero_total(repository):
    page = repository.get_completed_activity_ledger(offset=100)

    assert page.total == 0 and page.rows == ()
    assert page.to_report()["pagination"] == {
        "offset": 100, "limit": 25, "total": 0, "rows_exported": 0,
    }


def test_frozen_detached_snapshot_fresh_report_and_no_database_mutation(repository):
    add_activity(repository, 1, activity_type=None, earnings=0, notes="a note")
    with sqlite3.connect(repository._db_path) as db:
        before_contents = list(db.iterdump())
    before = datetime.now(timezone.utc)
    page = repository.get_completed_activity_ledger(filters(query="note"))
    after = datetime.now(timezone.utc)
    assert before <= page.observed_at <= after
    with sqlite3.connect(repository._db_path) as db:
        assert list(db.iterdump()) == before_contents
        db.execute("UPDATE activities SET earnings=500, notes='changed'")
        db.execute("UPDATE characters SET name='Changed owner' WHERE id=1")

    assert isinstance(page.rows, tuple)
    for target, field in [(page, "total"), (page.rows[0], "notes"), (page.filters, "query")]:
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, None)
    report = page.to_report()
    assert report["format_version"] == 1
    assert report["kind"] == "completed_activity_ledger_page"
    assert report["scope"] == "completed_sessions"
    assert report["observed_at"] == page.observed_at.isoformat()
    assert report["timezone"] == "UTC"
    assert report["filters"] == {
        "character_id": None, "date_from": None, "date_until": None,
        "activity_type": None, "outcome": None, "query": "note",
    }
    assert report["rows"][0] == {
        **asdict(page.rows[0]), "recorded_start": (DAY - timedelta(minutes=1)).isoformat(),
        "completed_at": DAY.isoformat(), "activity_time": DAY.isoformat(),
    }
    assert report["rows"][0]["recorded_amount"] == 0
    assert report["rows"][0]["notes"] == "a note"
    assert report["rows"][0]["character_name"] == "François <A>"
    assert report["field_notes"] and report["snapshot_notes"]
    assert json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False)) == report
    report["rows"][0]["notes"] = "modified report"
    report["filters"]["query"] = "changed"
    report["pagination"]["total"] = 999
    report["field_notes"].clear()
    report["snapshot_notes"].clear()
    fresh = page.to_report()
    assert fresh["rows"][0]["notes"] == "a note"
    assert fresh["filters"]["query"] == "note"
    assert fresh["pagination"]["total"] == 1
    assert fresh["field_notes"] and fresh["snapshot_notes"]
    assert repository.get_completed_activity_ledger(filters(query="note")).rows == ()


def test_report_dates_and_tuple_ownership_are_independent_of_constructor_list(repository):
    from src.database.activity_ledger import ActivityLedgerPage
    add_activity(repository, 1)
    source = repository.get_completed_activity_ledger()
    rows = list(source.rows)
    page = ActivityLedgerPage(
        filters=filters(date_from=date.min, date_until=date.max), rows=rows,
        total=1, offset=0, limit=25, observed_at=source.observed_at,
    )
    rows.clear()

    assert isinstance(page.rows, tuple) and len(page.rows) == 1
    assert page.to_report()["filters"]["date_from"] == "0001-01-01"
    assert page.to_report()["filters"]["date_until"] == "9999-12-31"


@pytest.mark.parametrize("field,value", [
    ("character_id", 0), ("character_id", -1), ("character_id", True),
    ("character_id", False), ("character_id", "1"), ("character_id", 1.0),
    ("character_id", 2**63), ("date_from", "2026-01-02"),
    ("date_from", datetime(2026, 1, 2)), ("date_until", datetime(2026, 1, 2)),
    ("date_until", False), ("activity_type", 3), ("activity_type", "x" * 257),
    ("activity_type", "line\nbreak"), ("activity_type", "a\u2028b"),
    ("activity_type", "a\u2029b"), ("activity_type", "a\x7fb"),
    ("outcome", "cancelled"), ("outcome", "Passed"), ("outcome", True),
    ("outcome", ["passed"]), ("query", ""), ("query", "  "), ("query", "\u2003"),
    ("query", 1), ("query", "x" * 201), ("query", "a\tb"),
    ("query", "a\x00b"), ("query", "a\u2028b"), ("query", "a\u2029b"),
])
def test_invalid_filters_raise_before_any_database_access(tmp_path, field, value):
    repo = Repository(str(tmp_path / "missing" / "ledger.sqlite"))

    with pytest.raises(ValueError, match=field):
        repo.get_completed_activity_ledger(filters(**{field: value}))

    assert repo._initialized is False and not (tmp_path / "missing").exists()


@pytest.mark.parametrize("bad_filters", [{}, "query", False, object()])
def test_wrong_filter_objects_raise_before_database_access(tmp_path, bad_filters):
    repo = Repository(str(tmp_path / "missing" / "ledger.sqlite"))
    with pytest.raises(ValueError, match="filters"):
        repo.get_completed_activity_ledger(bad_filters)
    assert repo._initialized is False


def test_reversed_dates_raise_before_database_access(tmp_path):
    repo = Repository(str(tmp_path / "missing" / "ledger.sqlite"))
    with pytest.raises(ValueError, match="date"):
        repo.get_completed_activity_ledger(filters(date_from=date.max, date_until=date.min))
    assert repo._initialized is False


@pytest.mark.parametrize("field,value", [
    ("limit", 0), ("limit", -1), ("limit", 101), ("limit", True),
    ("limit", 25.0), ("limit", "25"), ("limit", None),
    ("offset", -1), ("offset", True), ("offset", 0.0), ("offset", "0"),
    ("offset", None), ("offset", 2**63),
])
def test_invalid_page_bounds_raise_before_database_access(tmp_path, field, value):
    repo = Repository(str(tmp_path / "missing" / "ledger.sqlite"))
    with pytest.raises(ValueError, match=field):
        repo.get_completed_activity_ledger(**{field: value})
    assert repo._initialized is False


def test_maximum_text_lengths_and_preserved_spaces_are_valid(repository):
    query = " " + "雪" * 198 + " "
    activity_type = "T" * 256
    add_activity(repository, 1, activity_name=query, activity_type=activity_type)

    page = repository.get_completed_activity_ledger(filters(query=query, activity_type=activity_type))

    assert ids(page) == [1]
    assert page.filters.query == query


def test_pure_validation_returns_the_same_filters_or_fresh_defaults():
    from src.database.activity_ledger import ActivityLedgerFilters, validate_ledger_request
    requested = filters(query="literal")

    assert validate_ledger_request(requested, limit=100, offset=MAX_SQLITE_INTEGER) is requested
    assert validate_ledger_request() == ActivityLedgerFilters()
    assert validate_ledger_request() is not validate_ledger_request()
    with pytest.raises(ValueError, match="query"):
        validate_ledger_request(filters(query=""))
    with pytest.raises(ValueError, match="limit"):
        validate_ledger_request(requested, limit=101)


def test_database_initialization_failure_propagates(tmp_path):
    repo = Repository(str(tmp_path / "missing" / "ledger.sqlite"))
    with pytest.raises(DatabaseError, match="initialization failed"):
        repo.get_completed_activity_ledger()


def test_query_failure_propagates_instead_of_an_empty_page(repository):
    with sqlite3.connect(repository._db_path) as db:
        db.execute("DROP TABLE activities")
    with pytest.raises(DatabaseError, match="Database operation failed"):
        repository.get_completed_activity_ledger()
