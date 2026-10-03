"""Stored session detail and JSON exports preserve normal and nullable history."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.database import models
from src.database.models import Activity, Character, Earnings, Session
from src.database.repository import Repository
from src.utils.exporter import DataExporter


@pytest.fixture
def history(tmp_path):
    repo = Repository(str(tmp_path / "history-export.db"))
    assert repo.initialize()
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    with repo._session_scope() as db:
        character = Character(name="Export fixture", is_active=True)
        db.add(character)
        db.flush()
        session = Session(
            character_id=character.id, started_at=start,
            ended_at=start + timedelta(hours=1),
            start_money=1000, end_money=800, total_earnings=-200,
        )
        db.add(session)
        db.flush()
        db.add_all([
            Activity(
                session_id=session.id, activity_type="VIP_WORK", activity_name="First",
                started_at=start + timedelta(minutes=35),
                ended_at=start + timedelta(minutes=40),
                duration_seconds=300, earnings=200, success=True,
            ),
            Activity(
                session_id=session.id, activity_type="CONTACT_MISSION", activity_name="Second",
                started_at=start + timedelta(minutes=10),
                ended_at=start + timedelta(minutes=20),
                duration_seconds=600, earnings=0, success=False,
            ),
        ])
        first_earning = Earnings(
            session_id=session.id, timestamp=start + timedelta(minutes=40),
            amount=200, source="First", balance_after=1200,
        )
        db.add_all([
            first_earning,
            Earnings(
                session_id=session.id, timestamp=start + timedelta(minutes=20),
                amount=-400, source="Second", balance_after=800,
            ),
        ])
        db.flush()
        fixture = SimpleNamespace(
            repo=repo, session_id=session.id, earning_id=first_earning.id,
            start=start.replace(tzinfo=None), output=tmp_path / "session.json",
        )
    yield fixture
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def database_contents(repo):
    with sqlite3.connect(repo._db_path) as connection:
        return list(connection.iterdump())


def test_normal_session_detail_and_json_preserve_exact_values_and_record_order(history):
    before = database_contents(history.repo)
    expected = {
        "session": {
            "id": history.session_id,
            "started_at": "2026-01-01T12:00:00",
            "ended_at": "2026-01-01T13:00:00",
            "start_money": 1000,
            "end_money": 800,
            "total_earnings": -200,
            "duration_seconds": 3600.0,
        },
        "activities": [
            {
                "type": "VIP_WORK", "name": "First", "earnings": 200,
                "duration_seconds": 300, "success": True,
                "ended_at": "2026-01-01T12:40:00",
            },
            {
                "type": "CONTACT_MISSION", "name": "Second", "earnings": 0,
                "duration_seconds": 600, "success": False,
                "ended_at": "2026-01-01T12:20:00",
            },
        ],
        "earnings": [
            {
                "amount": 200, "source": "First", "balance_after": 1200,
                "timestamp": "2026-01-01T12:40:00",
            },
            {
                "amount": -400, "source": "Second", "balance_after": 800,
                "timestamp": "2026-01-01T12:20:00",
            },
        ],
    }

    assert history.repo.export_session_data(history.session_id) == expected
    result = DataExporter(history.repo).export_to_json(history.session_id, history.output)

    assert result.success
    assert result.file_path == history.output
    assert result.rows_exported == 4
    assert json.loads(history.output.read_text(encoding="utf-8")) == expected
    assert database_contents(history.repo) == before


@pytest.mark.parametrize("missing_start,missing_earning_time", [
    (True, False), (False, True), (True, True),
])
def test_nullable_legacy_timestamps_remain_readable_and_export_as_null(
    history, missing_start, missing_earning_time
):
    with history.repo._session_scope() as db:
        if missing_start:
            db.query(Session).filter_by(id=history.session_id).update({Session.started_at: None})
        if missing_earning_time:
            db.query(Earnings).filter_by(id=history.earning_id).update({Earnings.timestamp: None})
    before = database_contents(history.repo)

    data = history.repo.export_session_data(history.session_id)
    result = DataExporter(history.repo).export_to_json(history.session_id, history.output)

    assert result.success
    assert result.rows_exported == 4
    exported = json.loads(history.output.read_text(encoding="utf-8"))
    assert exported == data
    assert data["session"]["started_at"] == (
        None if missing_start else "2026-01-01T12:00:00"
    )
    assert data["session"]["duration_seconds"] == (None if missing_start else 3600.0)
    assert data["session"]["ended_at"] == "2026-01-01T13:00:00"
    assert data["session"]["total_earnings"] == -200
    assert data["earnings"][0]["timestamp"] == (
        None if missing_earning_time else "2026-01-01T12:40:00"
    )
    assert data["earnings"][1]["timestamp"] == "2026-01-01T12:20:00"
    assert database_contents(history.repo) == before


def test_nullable_legacy_balances_and_activity_fields_remain_null(history):
    with history.repo._session_scope() as db:
        db.query(Session).filter_by(id=history.session_id).update({
            Session.started_at: None, Session.start_money: None,
            Session.end_money: None, Session.total_earnings: None,
        })
        db.query(Activity).filter_by(session_id=history.session_id).update({
            Activity.ended_at: None, Activity.duration_seconds: None,
            Activity.earnings: None, Activity.success: None,
        })
        db.query(Earnings).filter_by(session_id=history.session_id).update({
            Earnings.timestamp: None, Earnings.balance_after: None,
        })
    before = database_contents(history.repo)

    data = history.repo.export_session_data(history.session_id)
    result = DataExporter(history.repo).export_to_json(history.session_id, history.output)

    assert result.success
    assert json.loads(history.output.read_text(encoding="utf-8")) == data
    assert all(data["session"][key] is None for key in [
        "started_at", "duration_seconds", "start_money", "end_money", "total_earnings",
    ])
    for activity in data["activities"]:
        assert all(activity[key] is None for key in [
            "ended_at", "duration_seconds", "earnings", "success",
        ])
    assert all(earning["timestamp"] is None for earning in data["earnings"])
    assert all(earning["balance_after"] is None for earning in data["earnings"])
    assert database_contents(history.repo) == before


def test_open_session_export_retains_live_duration(history, monkeypatch):
    monkeypatch.setattr(models, "utc_now", lambda: history.start + timedelta(minutes=5))
    with history.repo._session_scope() as db:
        db.query(Session).filter_by(id=history.session_id).update({Session.ended_at: None})
    before = database_contents(history.repo)

    data = history.repo.export_session_data(history.session_id)
    result = DataExporter(history.repo).export_to_json(history.session_id, history.output)

    assert result.success
    assert data["session"]["ended_at"] is None
    assert data["session"]["duration_seconds"] == 300.0
    assert json.loads(history.output.read_text(encoding="utf-8")) == data
    assert database_contents(history.repo) == before


def test_missing_session_keeps_existing_none_and_failed_export_contract(history):
    history.output.write_text("previous export", encoding="utf-8")
    before = database_contents(history.repo)

    assert history.repo.export_session_data(99999) is None
    result = DataExporter(history.repo).export_to_json(99999, history.output)

    assert not result.success
    assert result.error_message == "Session 99999 not found"
    assert history.output.read_text(encoding="utf-8") == "previous export"
    assert database_contents(history.repo) == before


def test_database_failure_keeps_existing_none_and_failed_export_contract(tmp_path):
    repo = Repository(str(tmp_path / "missing-directory" / "history.db"))
    output = tmp_path / "session.json"

    assert repo.export_session_data(1) is None
    result = DataExporter(repo).export_to_json(1, output)

    assert not result.success
    assert result.error_message == "Session 1 not found"
    assert not output.exists()
