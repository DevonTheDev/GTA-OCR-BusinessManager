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


@pytest.mark.parametrize("failure", ["false", "exception"])
@pytest.mark.parametrize("reset", [False, True])
def test_finalization_recovers_the_original_opening_balance(app, monkeypatch, failure, reset):
    def failed_baseline(*args):
        if failure == "exception":
            raise RuntimeError("temporary opening-balance failure")
        return False

    monkeypatch.setattr(app._repository, "set_session_start_money", failed_baseline)
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    if reset:
        app.reset_session()
    app._process_money_change(MoneyReading(total=110_000))
    app._process_money_change(MoneyReading(total=115_000))
    assert app._repository.export_session_data(app._data.db_session_id)["session"]["start_money"] == 0

    app._end_database_session()

    session = app._repository.export_session_data(app._data.db_session_id)["session"]
    assert session["start_money"] == 100_000
    assert session["end_money"] == 115_000
    assert session["total_earnings"] == 15_000
    assert session["ended_at"] is not None
    assert app.session_earnings == (5_000 if reset else 25_000)


def test_finalization_failure_is_reported_and_rolls_back_both_balances(app, monkeypatch, caplog):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    from src.database.models import Session

    monkeypatch.setattr(app._repository, "set_session_start_money", lambda *args: False)
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    seen = []

    def fail_commit(database_session):
        row = database_session.query(Session).filter_by(id=app._data.db_session_id).one()
        seen.append((row.start_money, row.end_money, row.total_earnings))
        raise SQLAlchemyError("synthetic commit failure")

    target = app._repository._session_factory.class_
    caplog.set_level("INFO")
    caplog.clear()
    event.listen(target, "before_commit", fail_commit)
    try:
        app._end_database_session()
    finally:
        event.remove(target, "before_commit", fail_commit)

    assert seen == [(100_000, 120_000, 20_000)]
    session = app._repository.export_session_data(app._data.db_session_id)["session"]
    assert session["start_money"] == 0
    assert session["end_money"] is None
    assert session["ended_at"] is None
    assert "Failed to finalize database session" in caplog.text
    assert f"Database session {app._data.db_session_id} ended" not in caplog.text
    assert "Ended session" not in caplog.text
    app._end_database_session()
    recovered = app._repository.export_session_data(app._data.db_session_id)["session"]
    assert recovered["start_money"] == 100_000
    assert recovered["total_earnings"] == 20_000


def test_repeated_finalization_cannot_rewrite_closed_history(app):
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    app._end_database_session()
    original = app._repository.export_session_data(app._data.db_session_id)
    app._data.current_money = 999_999

    app._end_database_session()

    assert app._repository.export_session_data(app._data.db_session_id) == original


def test_only_one_concurrent_finalizer_can_close_a_session(app):
    import threading
    app._process_money_change(MoneyReading(total=100_000))
    barrier = threading.Barrier(2)
    results, errors = [], []

    def finalize(end_money):
        try:
            barrier.wait(timeout=2)
            results.append(app._repository.end_session(app._data.db_session_id, end_money))
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=finalize, args=(value,)) for value in (120_000, 130_000)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert sorted(results) == [False, True]
    session = app._repository.export_session_data(app._data.db_session_id)["session"]
    assert session["end_money"] in (120_000, 130_000)
    assert session["total_earnings"] == session["end_money"] - 100_000


@pytest.mark.parametrize("opening, expected", [(None, 10_000), (0, 0), (25_000, 25_000)])
def test_repository_finalization_preserves_or_overrides_opening_balance_explicitly(app, opening, expected):
    repo = app._repository
    session_id = app._data.db_session_id
    assert repo.set_session_start_money(session_id, 10_000)
    assert repo.end_session(session_id, 50_000, start_money=opening)
    session = repo.export_session_data(session_id)["session"]
    assert session["start_money"] == expected
    assert session["total_earnings"] == 50_000 - expected
    assert not repo.end_session(session_id, 60_000, start_money=40_000)
    assert repo.export_session_data(session_id)["session"] == session


def test_zero_observed_opening_balance_is_not_replaced_by_a_later_reading(app, monkeypatch):
    repo = app._repository
    assert repo.set_session_start_money(app._data.db_session_id, 10_000)
    monkeypatch.setattr(repo, "set_session_start_money", lambda *args: False)
    app._process_money_change(MoneyReading(total=0))
    app._process_money_change(MoneyReading(total=20_000))
    app._end_database_session()
    session = repo.export_session_data(app._data.db_session_id)["session"]
    assert session["start_money"] == 0
    assert session["total_earnings"] == 20_000


def test_stop_before_any_valid_reading_keeps_the_default_zero_session(app):
    app._process_money_change(MoneyReading())
    app._end_database_session()
    session = app._repository.export_session_data(app._data.db_session_id)["session"]
    assert session["start_money"] == session["end_money"] == session["total_earnings"] == 0
    assert session["ended_at"] is not None
