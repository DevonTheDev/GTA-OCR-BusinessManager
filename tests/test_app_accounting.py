"""Offline accounting integration using the real app and temporary SQLite."""

import pytest

from src.app import GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.money_parser import MoneyReading


@pytest.fixture
def app(tmp_path):
    manager = GTABusinessManager(Settings(tmp_path / "config.yaml"))
    manager._session_tracker.start_session()
    repository = Repository(str(tmp_path / "accounting.db"))
    assert repository.initialize()
    character = repository.get_or_create_character("Test Player")
    session = repository.start_session(character)
    manager._repository = repository
    manager._data.character_id = character.id
    manager._data.db_session_id = session.id
    yield manager
    repository.close()


@pytest.mark.parametrize("opening_balance", [0, 1_000_000])
def test_first_balance_is_not_income(app, opening_balance):
    stats = app.session_stats
    original_start_time = stats.started_at
    app._session_tracker.record_activity_complete(success=True)

    assert app._process_money_change(MoneyReading(total=opening_balance)) == 0

    assert app.session_earnings == 0
    assert stats.total_earnings == 0
    assert stats.start_money == opening_balance
    assert stats.current_money == opening_balance
    assert stats.started_at == original_start_time
    assert stats.activities_completed == 1
    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == opening_balance
    assert data["earnings"] == []


def test_spending_updates_tracker_before_later_income(app):
    app._process_money_change(MoneyReading(total=1_000_000))
    assert app._process_money_change(MoneyReading(total=900_000)) == -100_000
    assert app.session_stats.current_money == 900_000
    assert app.session_stats.total_earnings == 0

    assert app._process_money_change(MoneyReading(total=950_000)) == 50_000
    assert app.session_earnings == 50_000
    assert app.session_stats.total_earnings == 50_000
    assert app.session_stats.current_money == 950_000
    data = app._repository.export_session_data(app._data.db_session_id)
    assert [earning["amount"] for earning in data["earnings"]] == [50_000]


def test_persisted_net_change_uses_actual_opening_balance(app):
    for balance in (1_000_000, 900_000, 950_000):
        app._process_money_change(MoneyReading(total=balance))
    app._end_database_session()

    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == 1_000_000
    assert data["session"]["end_money"] == 950_000
    # Keep the repository's existing net-balance semantics; UI earnings are gross.
    assert data["session"]["total_earnings"] == -50_000
    assert app.session_earnings == app.session_stats.total_earnings == 50_000


def test_empty_reading_does_not_create_a_zero_baseline_or_spending(app):
    assert app._process_money_change(MoneyReading()) == 0
    assert app.session_start_money is None
    assert app.current_money is None
    app._process_money_change(MoneyReading(total=100_000))
    assert app._process_money_change(MoneyReading()) == 0
    assert app.current_money == 100_000
    assert app.session_stats.current_money == 100_000


def test_reset_keeps_new_baseline_without_counting_it_as_income(app):
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=150_000))
    app.reset_session()
    app._process_money_change(MoneyReading(total=120_000))
    app._process_money_change(MoneyReading(total=130_000))

    assert app.session_start_money == 150_000
    assert app.session_stats.start_money == 150_000
    assert app.session_earnings == app.session_stats.total_earnings == 10_000
    assert app.session_stats.current_money == 130_000
    # Reset remains an in-memory stats reset, preserving the existing DB session.
    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == 100_000


def test_baseline_update_does_not_rewrite_closed_or_missing_sessions(app):
    repo = app._repository
    session_id = app._data.db_session_id
    assert not repo.set_session_start_money(999999, 10)
    assert repo.set_session_start_money(session_id, 50_000)
    assert repo.end_session(session_id, 70_000)
    assert not repo.set_session_start_money(session_id, 60_000)
    data = repo.export_session_data(session_id)
    assert data["session"]["start_money"] == 50_000
    assert data["session"]["total_earnings"] == 20_000


def test_baseline_persistence_failure_does_not_invent_income(app, monkeypatch):
    def fail_write(*args):
        raise RuntimeError("temporary storage failure")

    monkeypatch.setattr(app._repository, "set_session_start_money", fail_write, raising=False)
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    assert app.session_earnings == app.session_stats.total_earnings == 20_000


def test_unsaved_baseline_is_logged(app, monkeypatch, caplog):
    monkeypatch.setattr(app._repository, "set_session_start_money", lambda *args: False)
    app._process_money_change(MoneyReading(total=100_000))
    assert "opening balance" in caplog.text
    assert app.session_earnings == app.session_stats.total_earnings == 0
