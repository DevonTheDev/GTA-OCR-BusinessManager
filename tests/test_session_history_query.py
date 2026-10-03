"""Completed-session browsing against disposable SQLite, without native capture."""

from dataclasses import FrozenInstanceError, asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from src.database import repository as repository_module
from src.database.models import Activity, Character, Earnings, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture
def repository(tmp_path):
    repo = Repository(str(tmp_path / "session-history.db"))
    assert repo.initialize()
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


@pytest.fixture
def history(repository):
    """Distinct owners, tied starts, an open session and independent earnings."""
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    with repository._session_scope() as db:
        owner = Character(name="Fixture owner", is_active=True)
        other = Character(name="Fixture other", is_active=False)
        db.add_all([owner, other])
        db.flush()
        old = Session(
            character_id=owner.id,
            started_at=start,
            ended_at=start + timedelta(minutes=30),
            start_money=1000,
            end_money=1250,
            total_earnings=-300,
        )
        tie_first = Session(
            character_id=owner.id,
            started_at=start + timedelta(days=1),
            ended_at=start + timedelta(days=1, minutes=10),
        )
        tie_second = Session(
            character_id=other.id,
            started_at=start + timedelta(days=1),
            ended_at=start + timedelta(days=1, minutes=20),
        )
        newest = Session(
            character_id=owner.id,
            started_at=start + timedelta(days=2),
            ended_at=start + timedelta(days=2, minutes=5),
        )
        opened = Session(character_id=other.id, started_at=start + timedelta(days=3))
        db.add_all([old, tie_first, tie_second, newest, opened])
        db.flush()
        db.add_all([
            Activity(session_id=old.id, activity_type="VIP_WORK", earnings=200)
            for _ in range(3)
        ])
        db.add(Activity(session_id=tie_first.id, activity_type="CONTACT_MISSION"))
        db.add(Activity(session_id=opened.id, activity_type="VIP_WORK"))
        db.add_all([
            Earnings(session_id=old.id, amount=999) for _ in range(4)
        ])
        result = SimpleNamespace(
            repo=repository, owner=owner.id, other=other.id, old=old.id,
            tie_first=tie_first.id, tie_second=tie_second.id,
            newest=newest.id, opened=opened.id, start=start.replace(tzinfo=None),
        )
    return result


def test_history_returns_completed_rows_newest_first_with_stable_ties(history):
    page = history.repo.get_completed_session_history()

    assert page.total == 4
    assert [item.id for item in page.sessions] == [
        history.newest, history.tie_second, history.tie_first, history.old,
    ]
    assert all(item.ended_at is not None for item in page.sessions)
    assert history.opened not in [item.id for item in page.sessions]


def test_history_exposes_stored_money_utc_times_and_unmultiplied_activity_count(history):
    page = history.repo.get_completed_session_history()
    old = next(item for item in page.sessions if item.id == history.old)

    assert asdict(old) == {
        "id": history.old,
        "character_id": history.owner,
        "character_name": "Fixture owner",
        "started_at": history.start,
        "ended_at": history.start + timedelta(minutes=30),
        "start_money": 1000,
        "end_money": 1250,
        "net_change": -300,
        "duration_seconds": 1800.0,
        "activities_count": 3,
    }
    assert [item.activities_count for item in page.sessions] == [0, 0, 1, 3]


def test_character_filter_applies_before_total_and_paging(history):
    page = history.repo.get_completed_session_history(history.owner, limit=1, offset=1)

    assert page.total == 3
    assert [item.id for item in page.sessions] == [history.tie_first]
    other = history.repo.get_completed_session_history(history.other)
    assert other.total == 1
    assert [item.id for item in other.sessions] == [history.tie_second]


def test_pages_do_not_repeat_tied_rows_and_keep_full_total(history):
    first = history.repo.get_completed_session_history(limit=2)
    second = history.repo.get_completed_session_history(limit=2, offset=2)
    beyond = history.repo.get_completed_session_history(limit=2, offset=4)

    assert first.total == second.total == beyond.total == 4
    assert [item.id for item in first.sessions + second.sessions] == [
        history.newest, history.tie_second, history.tie_first, history.old,
    ]
    assert beyond.sessions == []


def test_empty_database_and_unknown_character_return_empty_pages(repository):
    assert repository.get_completed_session_history().sessions == []
    assert repository.get_completed_session_history().total == 0
    assert repository.get_completed_session_history(character_id=999).sessions == []
    assert repository.get_completed_session_history(character_id=999).total == 0


def test_only_open_sessions_return_empty_history(repository):
    character = repository.get_or_create_character("Open fixture")
    assert repository.start_session(character) is not None

    page = repository.get_completed_session_history()

    assert page.total == 0
    assert page.sessions == []


def test_default_page_is_bounded_and_activity_counts_do_not_use_n_plus_one(repository):
    start = datetime(2026, 1, 1, 12)
    with repository._session_scope() as db:
        character = Character(name="Many sessions")
        db.add(character)
        db.flush()
        sessions = [
            Session(
                character_id=character.id, started_at=start + timedelta(hours=i),
                ended_at=start + timedelta(hours=i, minutes=5),
            )
            for i in range(60)
        ]
        db.add_all(sessions)
        db.flush()
        db.add_all([
            Activity(session_id=session.id, activity_type="VIP_WORK") for session in sessions
        ])
    engine = repository._session_factory.kw["bind"]
    statements = []

    def capture_statements(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statements)
    try:
        page = repository.get_completed_session_history()
    finally:
        event.remove(engine, "before_cursor_execute", capture_statements)

    assert page.total == 60
    assert len(page.sessions) == 50
    assert all(item.activities_count == 1 for item in page.sessions)
    assert len(statements) <= 3
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert len(repository.get_completed_session_history(limit=500).sessions) == 60


def test_legacy_missing_start_and_money_are_preserved(history):
    with history.repo._session_scope() as db:
        db.query(Session).filter_by(id=history.old).update({
            Session.started_at: None,
            Session.start_money: None,
            Session.end_money: None,
            Session.total_earnings: None,
        })

    page = history.repo.get_completed_session_history()
    old = page.sessions[-1]

    assert page.total == 4
    assert old.id == history.old
    assert old.started_at is None
    assert old.ended_at == history.start + timedelta(minutes=30)
    assert old.duration_seconds is None
    assert old.start_money is old.end_money is old.net_change is None


def test_browsing_does_not_create_sessions_change_active_character_or_mutate_rows(history):
    def database_contents():
        with sqlite3.connect(history.repo._db_path) as connection:
            return list(connection.iterdump())

    before = database_contents()

    for character in [None, history.owner, history.other, 999]:
        history.repo.get_completed_session_history(character, limit=1, offset=1)

    assert database_contents() == before
    assert history.repo.get_active_character().id == history.owner


def test_history_dtos_are_frozen_detached_snapshots(history):
    page = history.repo.get_completed_session_history()
    item = page.sessions[0]

    assert isinstance(page, repository_module.SessionHistoryPage)
    assert isinstance(item, repository_module.SessionHistoryItem)
    assert is_dataclass(page) and is_dataclass(item)
    with pytest.raises(FrozenInstanceError):
        page.total = 10
    with pytest.raises(FrozenInstanceError):
        item.net_change = 999
    # The public list is caller-owned: changing it cannot alter storage or a later page.
    page.sessions.clear()
    later = history.repo.get_completed_session_history()
    assert len(later.sessions) == later.total == 4
    assert later.sessions[0] == item


@pytest.mark.parametrize("field,value", [
    ("character_id", 0), ("character_id", -1), ("character_id", True),
    ("character_id", False), ("character_id", 1.0), ("character_id", "1"),
    ("limit", 0), ("limit", -1), ("limit", 501), ("limit", True),
    ("limit", False), ("limit", 50.0), ("limit", "50"), ("limit", None),
    ("offset", -1), ("offset", True), ("offset", False), ("offset", 0.0),
    ("offset", "0"), ("offset", None),
])
def test_invalid_query_arguments_raise_before_database_access(tmp_path, field, value):
    repo = Repository(str(tmp_path / "missing-directory" / "history.db"))

    with pytest.raises(ValueError, match=field):
        repo.get_completed_session_history(**{field: value})

    assert repo._initialized is False
    assert not (tmp_path / "missing-directory").exists()


def test_history_propagates_database_initialization_failure(tmp_path):
    repo = Repository(str(tmp_path / "missing-directory" / "history.db"))

    with pytest.raises(DatabaseError, match="initialization failed"):
        repo.get_completed_session_history()


def test_history_propagates_query_failure_instead_of_empty_page(repository):
    engine = repository._session_factory.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE sessions")

    with pytest.raises(DatabaseError, match="Database operation failed"):
        repository.get_completed_session_history()
