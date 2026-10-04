"""Exact, detached comparisons of two explicitly selected manual observations."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from importlib import import_module
from importlib.util import find_spec
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.business_checkins import (
    BusinessCheckIn, BusinessCheckInDataError, BusinessCheckInUnavailable,
    BusinessCheckInValidationError, SQLITE_MAX_INTEGER,
)
from src.database.models import Character
from src.database.repository import Repository


def comparison_domain():
    return import_module("src.database.business_checkin_comparison")


@pytest.fixture
def journal(tmp_path):
    repo = Repository(str(tmp_path / "comparison.db"))
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


def compare(journal, baseline, comparison, **kwargs):
    values = dict(character_id=journal.first, business_id="bunker",
                  baseline_id=baseline.id, comparison_id=comparison.id)
    values.update(kwargs)
    return journal.repo.get_business_checkin_comparison(**values)


def database_contents(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def test_comparison_contract_is_available():
    assert find_spec("src.database.business_checkin_comparison") is not None
    assert hasattr(Repository, "get_business_checkin_comparison")


@pytest.mark.parametrize("field,value", [
    ("character_id", True), ("character_id", 0), ("character_id", -1),
    ("character_id", 2**63), ("character_id", 1.0), ("character_id", "1"),
    ("baseline_id", None), ("baseline_id", False), ("baseline_id", 0),
    ("baseline_id", -1), ("baseline_id", 2**63), ("baseline_id", 1.0),
    ("comparison_id", True), ("comparison_id", 0), ("comparison_id", -1),
    ("comparison_id", 2**63), ("comparison_id", 2.0), ("comparison_id", "2"),
    ("business_id", ""), ("business_id", None), ("business_id", "x" * 51),
    ("business_id", "bunker\0"), ("business_id", "bunker/other"),
    ("comparison_id", 1),
])
def test_invalid_requests_fail_before_storage_initialization(tmp_path, monkeypatch, field, value):
    path = tmp_path / "must-not-create.db"
    repo = Repository(str(path))
    monkeypatch.setattr(repo, "initialize", lambda: pytest.fail("accessed storage"))
    values = dict(character_id=1, business_id="bunker", baseline_id=1, comparison_id=2)
    values[field] = value
    with pytest.raises(BusinessCheckInValidationError):
        repo.get_business_checkin_comparison(**values)
    assert not path.exists()


def test_exact_integer_differences_and_full_read_only_capture(journal):
    baseline = save(journal, stock_percent=100, supply_percent=0, stock_value=SQLITE_MAX_INTEGER,
                    note="  <b>Baseline 雪</b>\nsecond\tline ")
    comparison = save(journal, stock_percent=0, supply_percent=100, stock_value=0, note="B")
    before = database_contents(journal.path)
    snapshot = compare(journal, baseline, comparison)
    assert snapshot.character_id == journal.first
    assert snapshot.character_name == "Saved runner <b>雪</b>"
    assert snapshot.business_id == "bunker"
    assert snapshot.baseline == baseline
    assert snapshot.comparison == comparison
    assert snapshot.differences == comparison_domain().BusinessCheckInDifferences(-100, 100, -SQLITE_MAX_INTEGER)
    assert all(type(value) is int for value in (
        snapshot.differences.stock_percent, snapshot.differences.supply_percent,
        snapshot.differences.stock_value,
    ))
    assert snapshot.captured_at.tzinfo == timezone.utc
    assert database_contents(journal.path) == before
    with pytest.raises(FrozenInstanceError):
        snapshot.character_name = "Changed"
    with pytest.raises(FrozenInstanceError):
        snapshot.baseline.note = "Changed"
    with pytest.raises(FrozenInstanceError):
        snapshot.differences.stock_value = 1


@pytest.mark.parametrize("missing_side", ["baseline", "comparison", "both"])
def test_missing_measurements_are_unknown_and_never_carry_forward(journal, missing_side):
    known = dict(stock_percent=0, supply_percent=100, stock_value=SQLITE_MAX_INTEGER, note="Known")
    unknown = dict(stock_percent=None, supply_percent=None, stock_value=None, note="Only a note")
    baseline = save(journal, **(unknown if missing_side in ("baseline", "both") else known))
    comparison = save(journal, **(unknown if missing_side in ("comparison", "both") else known))
    snapshot = compare(journal, baseline, comparison)
    assert snapshot.differences == comparison_domain().BusinessCheckInDifferences(None, None, None)
    assert snapshot.to_report()["differences"] == {
        "stock_percentage_points": None, "supply_percentage_points": None, "stock_value": None,
    }


def test_differences_have_independent_nulls_and_preserve_zero(journal):
    baseline = save(journal, stock_percent=0, supply_percent=None, stock_value=0)
    comparison = save(journal, stock_percent=0, supply_percent=0, stock_value=SQLITE_MAX_INTEGER)
    assert compare(journal, baseline, comparison).differences == (
        comparison_domain().BusinessCheckInDifferences(0, None, SQLITE_MAX_INTEGER)
    )


@pytest.mark.parametrize("times", [
    ("2026-01-02 12:00:00", "2026-01-02 12:00:00"),
    ("2026-01-02 12:00:00", "2025-01-01 00:00:00"),
    ("2026-01-02 14:00:00+02:00", "2026-01-02 07:00:00-05:00"),
])
def test_explicit_orientation_survives_reverse_ids_equal_clocks_rollbacks_and_offsets(journal, times):
    first = save(journal, stock_percent=25, supply_percent=20, stock_value=0)
    second = save(journal, stock_percent=50, supply_percent=10, stock_value=SQLITE_MAX_INTEGER)
    with sqlite3.connect(journal.path) as db:
        db.executemany("UPDATE manual_business_checkins SET recorded_at=? WHERE id=?",
                       [(times[0], first.id), (times[1], second.id)])
    forward = compare(journal, first, second)
    reverse = compare(journal, second, first)
    assert (forward.baseline.id, forward.comparison.id) == (first.id, second.id)
    assert (reverse.baseline.id, reverse.comparison.id) == (second.id, first.id)
    assert forward.differences == comparison_domain().BusinessCheckInDifferences(25, -10, SQLITE_MAX_INTEGER)
    assert reverse.differences == comparison_domain().BusinessCheckInDifferences(-25, 10, -SQLITE_MAX_INTEGER)
    for row, timestamp in zip((forward.baseline, forward.comparison), times):
        expected = datetime.fromisoformat(timestamp)
        expected = (expected.replace(tzinfo=timezone.utc) if expected.tzinfo is None
                    else expected.astimezone(timezone.utc))
        assert row.recorded_at == expected
    assert forward.baseline.recorded_at.tzinfo == forward.comparison.recorded_at.tzinfo == timezone.utc
    report = reverse.to_report()
    assert not {"elapsed_seconds", "duration_seconds", "profit", "rate", "net_per_hour"} & report.keys()


@pytest.mark.parametrize("target", ["baseline", "comparison", "both", "character"])
def test_missing_records_report_only_unavailable_requested_ids(journal, target):
    first, second = save(journal), save(journal)
    unavailable = (first.id,) if target == "baseline" else (second.id,)
    with sqlite3.connect(journal.path) as db:
        if target == "character":
            db.execute("DELETE FROM characters WHERE id=?", (journal.first,))
            unavailable = (first.id, second.id)
        else:
            if target == "both":
                unavailable = (first.id, second.id)
            db.executemany("DELETE FROM manual_business_checkins WHERE id=?", [(item,) for item in unavailable])
    with pytest.raises(comparison_domain().BusinessCheckInComparisonUnavailable) as error:
        compare(journal, first, second)
    assert error.value.checkin_ids == unavailable
    assert isinstance(error.value, BusinessCheckInUnavailable)
    assert "Private other runner" not in str(error.value)


@pytest.mark.parametrize("change", ["owner", "business", "both"])
def test_records_moved_out_of_scope_are_safe_unavailable(journal, change):
    first, second = save(journal), save(journal)
    with sqlite3.connect(journal.path) as db:
        if change in ("owner", "both"):
            db.execute("UPDATE manual_business_checkins SET character_id=? WHERE id=?", (journal.second, second.id))
        if change in ("business", "both"):
            db.execute("UPDATE manual_business_checkins SET business_id='cocaine' WHERE id=?", (second.id,))
        db.execute("UPDATE manual_business_checkins SET note=CAST(x'ff' AS TEXT) WHERE id=?", (second.id,))
    with pytest.raises(comparison_domain().BusinessCheckInComparisonUnavailable) as error:
        compare(journal, first, second)
    assert error.value.checkin_ids == (second.id,)
    assert "Private" not in str(error.value)


def test_cross_owner_cross_business_and_unknown_pairs_do_not_leak_data(journal):
    first = save(journal)
    other_owner = save(journal, character_id=journal.second, note="private")
    other_business = save(journal, business_id="cocaine", note="different")
    for other in (other_owner, other_business, SimpleNamespace(id=999999)):
        with pytest.raises(comparison_domain().BusinessCheckInComparisonUnavailable) as error:
            compare(journal, first, other)
        assert error.value.checkin_ids == (other.id,)
        assert all(value not in str(error.value) for value in ("private", "different", "runner"))


def test_historical_business_ids_and_maximum_identifiers_are_readable(journal):
    with sqlite3.connect(journal.path) as db:
        db.execute("INSERT INTO characters(id,name,is_active) VALUES (?,'Maximum owner',0)", (SQLITE_MAX_INTEGER,))
    first = save(journal, character_id=SQLITE_MAX_INTEGER, stock_value=SQLITE_MAX_INTEGER)
    second = save(journal, character_id=SQLITE_MAX_INTEGER, stock_value=0)
    historical_id = "retired-business.v1"
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE manual_business_checkins SET business_id=?", (historical_id,))
        db.execute("UPDATE manual_business_checkins SET id=? WHERE id=?", (SQLITE_MAX_INTEGER, second.id))
    result = compare(journal, first, SimpleNamespace(id=SQLITE_MAX_INTEGER),
                     character_id=SQLITE_MAX_INTEGER, business_id=historical_id)
    assert result.character_id == SQLITE_MAX_INTEGER
    assert result.comparison.id == SQLITE_MAX_INTEGER
    assert result.business_id == historical_id
    assert result.differences.stock_value == -SQLITE_MAX_INTEGER


@pytest.mark.parametrize("expression", [
    "note=CAST(x'ff' AS TEXT)", "note=zeroblob(50000)", "note='bad'||char(0)||'note'",
    "note=replace(hex(zeroblob(1001)), '0', 'a')", "stock_percent=101",
    "supply_percent=-1", "stock_value=1.5", "stock_value=x'ff'",
    "recorded_at='not a time'", "recorded_at=zeroblob(50000)",
    "recorded_at='2026-01-01T00:00:00+99:00'",
    "stock_percent=NULL,supply_percent=NULL,stock_value=NULL,note='  '",
])
def test_corrupt_selected_payload_is_data_error(journal, expression):
    first, second = save(journal), save(journal)
    with sqlite3.connect(journal.path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(f"UPDATE manual_business_checkins SET {expression} WHERE id=?", (first.id,))
    with pytest.raises(BusinessCheckInDataError):
        compare(journal, first, second)


@pytest.mark.parametrize("expression", ["name=CAST(x'ff' AS TEXT)", "name=zeroblob(50000)"])
def test_corrupt_selected_character_is_data_error(journal, expression):
    first, second = save(journal), save(journal)
    with sqlite3.connect(journal.path) as db:
        db.execute(f"UPDATE characters SET {expression} WHERE id=?", (journal.first,))
    with pytest.raises(BusinessCheckInDataError):
        compare(journal, first, second)


def test_unrelated_corrupt_payload_and_character_are_not_fetched(journal):
    first = save(journal, stock_percent=1)
    second = save(journal, stock_percent=2)
    unrelated = [save(journal), save(journal, character_id=journal.second),
                 save(journal, business_id="cocaine")]
    with sqlite3.connect(journal.path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.executemany("UPDATE manual_business_checkins SET note=CAST(x'ff' AS TEXT),stock_value=1.5 WHERE id=?",
                       [(row.id,) for row in unrelated])
        db.execute("UPDATE characters SET name=CAST(x'ff' AS TEXT) WHERE id=?", (journal.second,))
    result = compare(journal, first, second)
    assert (result.baseline, result.comparison) == (first, second)


def test_comparison_reads_one_bounded_sql_statement_and_no_writes(journal):
    first, second = save(journal), save(journal)
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters))

    before = database_contents(journal.path)
    event.listen(journal.engine, "before_cursor_execute", record)
    try:
        compare(journal, first, second)
    finally:
        event.remove(journal.engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert statements[0][0].lstrip().upper().startswith("SELECT")
    assert "LIMIT" in statements[0][0].upper()
    assert database_contents(journal.path) == before


def test_character_and_exact_pair_are_one_observation_during_concurrent_wal_writes(journal):
    first = save(journal, stock_percent=10, stock_value=0)
    second = save(journal, stock_percent=20, stock_value=SQLITE_MAX_INTEGER)
    with sqlite3.connect(journal.path) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    statements = []

    def concurrent_write(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
        if len(statements) == 1:
            with sqlite3.connect(journal.path) as other:
                other.execute("UPDATE characters SET name='After observation' WHERE id=?", (journal.first,))
                other.execute("UPDATE manual_business_checkins SET stock_percent=99,note='after' WHERE id=?", (second.id,))

    event.listen(journal.engine, "after_cursor_execute", concurrent_write)
    try:
        snapshot = compare(journal, first, second)
        assert len(statements) == 1
        assert snapshot.character_name == "Saved runner <b>雪</b>"
        assert snapshot.baseline == first
        assert snapshot.comparison == second
    finally:
        event.remove(journal.engine, "after_cursor_execute", concurrent_write)
    fresh = compare(journal, first, second)
    assert fresh.character_name == "After observation"
    assert fresh.comparison.stock_percent == 99
    assert snapshot.comparison.stock_percent == 20


def test_storage_failure_is_generic_and_preserves_retry_context(journal):
    first, second = save(journal), save(journal)

    def failure(*args):
        raise OperationalError("private SQL", {}, Exception("private database path"))

    event.listen(journal.engine, "before_cursor_execute", failure)
    try:
        with pytest.raises(BusinessCheckInUnavailable) as error:
            compare(journal, first, second)
        assert type(error.value) is BusinessCheckInUnavailable
        assert "private" not in str(error.value)
        assert not hasattr(error.value, "checkin_ids")
    finally:
        event.remove(journal.engine, "before_cursor_execute", failure)
    assert compare(journal, first, second).baseline == first


def test_report_is_fresh_exact_and_cannot_retarget_snapshot(journal, monkeypatch):
    first = save(journal, stock_percent=100, supply_percent=0, stock_value=SQLITE_MAX_INTEGER,
                 note=" <p>literal 雪</p>\n\t ")
    second = save(journal, stock_percent=None, supply_percent=100, stock_value=0, note="note only stock")
    stamp = datetime(2026, 1, 3, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr("src.database.repository.utc_now", lambda: stamp)
    snapshot = compare(journal, first, second)
    report = snapshot.to_report()
    assert set(report) == {
        "format_version", "kind", "scope", "orientation", "captured_at", "timezone",
        "character", "business_id", "baseline", "comparison", "differences", "field_notes",
    }
    assert report["format_version"] == 1
    assert report["kind"] == "manual_business_checkin_comparison"
    assert report["scope"] == "manual_observations"
    assert report["orientation"] == "comparison_minus_baseline"
    assert report["captured_at"] == stamp.isoformat()
    assert report["timezone"] == "UTC"
    assert report["character"] == {"id": journal.first, "name": "Saved runner <b>雪</b>"}
    assert report["business_id"] == "bunker"
    assert report["baseline"] == first.to_dict()
    assert report["comparison"] == second.to_dict()
    assert report["differences"] == {"stock_percentage_points": None,
                                      "supply_percentage_points": 100, "stock_value": -SQLITE_MAX_INTEGER}
    notes = " ".join(report["field_notes"].values()).lower()
    for expected in ("manual", "null", "zero", "percentage points", "save", "order", "capture"):
        assert expected in notes
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert str(SQLITE_MAX_INTEGER) in encoded
    assert json.loads(encoded) == report
    original = json.loads(encoded)
    report["character"]["name"] = "Changed"
    report["baseline"]["note"] = "Changed"
    report["comparison"]["stock_value"] = -1
    report["differences"]["stock_value"] = 999
    report["field_notes"].clear()
    with sqlite3.connect(journal.path) as db:
        db.execute("UPDATE characters SET name='Live change' WHERE id=?", (journal.first,))
        db.execute("DELETE FROM manual_business_checkins")
    assert snapshot.to_report() == original


def test_manually_constructed_comparison_normalizes_capture_time():
    stamp = datetime(2026, 1, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    first = BusinessCheckIn(1, 1, "bunker", stamp, 0, None, None, "")
    second = BusinessCheckIn(2, 1, "bunker", stamp, 1, None, None, "")
    snapshot = comparison_domain().BusinessCheckInComparison(1, "Name", "bunker", first, second, stamp)
    assert snapshot.captured_at == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert snapshot.differences.stock_percent == 1
