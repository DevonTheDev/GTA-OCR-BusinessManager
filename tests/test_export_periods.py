"""Activity-time export periods and accurate row counts using real SQLite."""

import csv
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from src.database import repository as module
from src.database.models import Activity, Session
from src.database.repository import Repository
from src.utils.exporter import DataExporter


@pytest.fixture
def history(tmp_path, monkeypatch):
    now = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "utc_now", lambda: now)
    repo = Repository(str(tmp_path / "history.db"))
    assert repo.initialize()
    owner = repo.get_or_create_character("Fixture owner")
    other = repo.get_or_create_character("Fixture other")
    with repo._session_scope() as db:
        old = Session(character_id=owner.id, started_at=now - timedelta(days=60))
        recent = Session(character_id=owner.id, started_at=now - timedelta(days=3))
        foreign = Session(character_id=other.id, started_at=now - timedelta(days=3))
        db.add_all([old, recent, foreign])
        db.flush()
        entries = [
            (old.id, "recent in old session", now - timedelta(days=1), 200, 60, "VIP_WORK"),
            (old.id, "boundary", now - timedelta(days=30), 300, 300, "VIP_WORK"),
            (old.id, "expired", now - timedelta(days=30, microseconds=1), 999, 50, "VIP_WORK"),
            (recent.id, "now", now, 500, 180, "SELL_MISSION"),
            (recent.id, "future", now + timedelta(microseconds=1), 888, 50, "VIP_WORK"),
            (foreign.id, "other character", now, 777, 50, "VIP_WORK"),
        ]
        for session_id, name, ended, earnings, duration, kind in entries:
            db.add(
                Activity(
                    session_id=session_id,
                    activity_name=name,
                    activity_type=kind,
                    started_at=ended - timedelta(seconds=duration),
                    ended_at=ended,
                    earnings=earnings,
                    duration_seconds=duration,
                    success=True,
                )
            )
        db.add(
            Activity(
                session_id=old.id,
                activity_name="missing completion",
                activity_type="VIP_WORK",
                started_at=now - timedelta(days=2),
                ended_at=None,
                earnings=100,
                duration_seconds=120,
                success=True,
            )
        )
        undated = Activity(
            session_id=recent.id,
            activity_name="undated",
            activity_type="VIP_WORK",
            started_at=now,
            ended_at=None,
            earnings=111,
        )
        db.add(undated)
        db.flush()
        undated.started_at = None  # Historical rows can lack both timestamps.
        old_id = old.id
    yield repo, owner.id, now, old_id
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_history_export_filters_activity_dates_and_character(history, tmp_path):
    repo, owner, now, _ = history
    output = tmp_path / "history.csv"
    result = DataExporter(repo).export_activity_history(owner, output, days=30)
    assert result.success
    rows = read_rows(output)
    assert [row["Activity Name"] for row in rows] == [
        "now",
        "recent in old session",
        "missing completion",
        "boundary",
    ]
    assert rows[2]["Date"] == (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    assert result.rows_exported == len(rows) == 4


def test_activity_statistics_share_the_activity_time_period(history):
    repo, owner, _, _ = history
    assert repo.get_activity_stats(owner, "VIP_WORK", days=30) == {
        "count": 3,
        "total_earnings": 600,
        "avg_earnings": 200,
        "avg_duration": 160,
    }


def test_zero_day_window_uses_one_instant_with_inclusive_bounds(history, tmp_path):
    repo, owner, _, _ = history
    output = tmp_path / "now.csv"
    result = DataExporter(repo).export_activity_history(owner, output, days=0)
    assert result.success
    assert [row["Activity Name"] for row in read_rows(output)] == ["now"]
    assert result.rows_exported == 1


def test_more_than_1000_sessions_does_not_hide_recent_activity_or_add_n_plus_one_queries(
    history, tmp_path
):
    repo, owner, now, _ = history
    with repo._session_scope() as db:
        db.add_all(
            [
                Session(character_id=owner, started_at=now - timedelta(seconds=i))
                for i in range(1001)
            ]
        )
    engine = repo._session_factory.kw["bind"]
    selects = []

    def count_selects(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        output = tmp_path / "many-sessions.csv"
        result = DataExporter(repo).export_activity_history(owner, output, days=30)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert result.success
    assert "recent in old session" in {row["Activity Name"] for row in read_rows(output)}
    assert len(selects) == 2  # Existence check plus one joined activity query.


@pytest.mark.parametrize("days", [-1, 1.5, True, "30"])
@pytest.mark.parametrize("export_name", ["export_activity_history", "export_earnings_breakdown"])
def test_invalid_period_does_not_replace_existing_export(history, tmp_path, days, export_name):
    repo, owner, _, _ = history
    output = tmp_path / "existing.csv"
    output.write_text("keep previous export")
    result = getattr(DataExporter(repo), export_name)(owner, output, days=days)
    assert not result.success
    assert output.read_text() == "keep previous export"


def test_breakdown_reports_only_rows_actually_written(history, tmp_path):
    repo, owner, _, _ = history
    output = tmp_path / "breakdown.csv"
    result = DataExporter(repo).export_earnings_breakdown(owner, output, days=30)
    assert result.success
    rows = read_rows(output)
    assert result.rows_exported == len(rows) == 2
    vip = next(row for row in rows if row["Activity Type"] == "VIP_WORK")
    assert vip["Count"] == "3"
    assert vip["Total Earnings"] == "$600"


def test_empty_breakdown_reports_zero_data_rows(history, tmp_path):
    repo, _, _, _ = history
    empty = repo.get_or_create_character("No history")
    output = tmp_path / "empty.csv"
    result = DataExporter(repo).export_earnings_breakdown(empty.id, output)
    assert result.success and result.rows_exported == 0
    assert read_rows(output) == []


@pytest.mark.parametrize("days", [-1, 1.5, True, "30"])
def test_repository_rejects_invalid_activity_windows(history, days):
    repo, owner, _, _ = history
    with pytest.raises(ValueError, match="nonnegative integer"):
        repo.get_character_activities(owner, days=days)


def test_activity_query_breaks_timestamp_ties_by_id(history):
    repo, owner, now, _ = history
    recent = repo.get_recent_sessions(owner, limit=1)[0]
    with repo._session_scope() as db:
        db.add(
            Activity(
                session_id=recent.id,
                activity_type="VIP_WORK",
                activity_name="same time later ID",
                started_at=now,
                ended_at=now,
            )
        )
    assert [a.activity_name for a in repo.get_character_activities(owner)][:2] == [
        "same time later ID",
        "now",
    ]


def test_failed_database_initialization_keeps_activity_read_error_contract(tmp_path):
    repo = Repository(str(tmp_path / "missing-parent" / "history.db"))
    assert repo.get_character_activities(1) == []
    assert repo._initialized is False


def test_existing_session_without_matching_activities_writes_header_only(history, tmp_path):
    repo, _, _, _ = history
    empty = repo.get_or_create_character("Empty session")
    repo.start_session(empty)
    output = tmp_path / "empty-activities.csv"
    result = DataExporter(repo).export_activity_history(empty.id, output)
    assert result.success and result.rows_exported == 0
    assert read_rows(output) == []
