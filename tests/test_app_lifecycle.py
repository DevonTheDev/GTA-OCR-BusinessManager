"""Real thread lifecycle with synthetic capture and temporary SQLite only."""

import threading
from types import SimpleNamespace

import pytest

from src.app import AppState, CaptureResult, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.money_parser import MoneyReading


@pytest.fixture
def manager(tmp_path, monkeypatch):
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    repo = Repository(str(tmp_path / "session.db"))
    assert repo.initialize()
    character = repo.get_or_create_character("Synthetic Player")
    captures = []

    def initialize():
        capture = SimpleNamespace(closed=False)

        def close():
            capture.closed = True

        capture.close = close
        captures.append(capture)
        app._capture = capture
        app._repository = repo
        app._data.character_id = character.id
        app._data.db_session_id = repo.start_session(character).id
        app._session_tracker.start_session()

    monkeypatch.setattr(app, "_initialize_components", initialize)
    monkeypatch.setattr(app, "_adjust_capture_rate", lambda *_args: None)
    monkeypatch.setattr(app, "_do_capture_cycle", lambda: CaptureResult())
    yield app, repo, captures
    app._stop_event.set()
    if app._capture_thread and app._capture_thread is not threading.current_thread():
        app._capture_thread.join(2)
    app.stop()
    repo.close()


@pytest.mark.parametrize("opening_write_fails", [False, True])
def test_restart_has_a_fresh_money_baseline_and_database_session(manager, monkeypatch, opening_write_fails):
    app, repo, _ = manager
    if opening_write_fails:
        monkeypatch.setattr(repo, "set_session_start_money", lambda *args: False)
    assert app.start()
    first_id = app._data.db_session_id
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    app.stop()
    assert app.start()
    second_id = app._data.db_session_id
    assert first_id != second_id
    assert app.current_money is None
    assert app.session_start_money is None
    assert app.session_earnings == 0
    assert app._process_money_change(MoneyReading(total=300_000)) == 0
    assert app.session_stats.total_earnings == 0
    assert repo.export_session_data(first_id)["session"]["total_earnings"] == 20_000
    if opening_write_fails:
        assert repo.export_session_data(second_id)["session"]["start_money"] == 0
        app.stop()  # Recovery occurs on normal finalization, not a background retry.
    assert repo.export_session_data(second_id)["session"]["start_money"] == 300_000
    assert repo.export_session_data(second_id)["earnings"] == []


def test_slow_worker_keeps_resources_and_blocks_restart_until_exit(manager, monkeypatch):
    app, repo, captures = manager
    entered, release = threading.Event(), threading.Event()

    def blocked_cycle():
        entered.set()
        release.wait(10)
        assert not captures[-1].closed
        return CaptureResult()

    monkeypatch.setattr(app, "_do_capture_cycle", blocked_cycle)
    assert app.start()
    worker = app._capture_thread
    assert entered.wait(2)
    # Force the join timeout without sleeping five seconds; the worker is real.
    real_join = worker.join
    monkeypatch.setattr(worker, "join", lambda timeout=None: real_join(0.01))
    session_id = app._data.db_session_id
    try:
        app.stop()
        assert app.state == AppState.STOPPING
        assert worker.is_alive()
        assert not captures[0].closed
        assert not app.start()
        assert repo.export_session_data(session_id)["session"]["ended_at"] is None
    finally:
        release.set()
        real_join(2)
    assert not worker.is_alive()
    assert app.state == AppState.STOPPED
    assert captures[0].closed
    assert repo.export_session_data(session_id)["session"]["ended_at"] is not None


def test_stop_from_capture_callback_finishes_without_joining_itself(manager):
    app, _, captures = manager
    stopped = threading.Event()
    errors = []

    def stop_here(_result):
        try:
            app.stop()
        except Exception as error:
            errors.append(error)
        finally:
            stopped.set()

    app._on_capture.append(stop_here)
    assert app.start()
    worker = app._capture_thread
    assert stopped.wait(2)
    worker.join(2)
    assert errors == []
    assert app.state == AppState.STOPPED
    assert captures[0].closed


def test_failed_start_closes_partial_components_and_session(manager, monkeypatch):
    app, repo, captures = manager
    initialize = app._initialize_components

    def broken_init():
        initialize()
        raise RuntimeError("synthetic initialization failure")

    monkeypatch.setattr(app, "_initialize_components", broken_init)
    assert not app.start()
    session_id = app._data.db_session_id
    assert app.state == AppState.STOPPED
    assert not app._session_tracker.is_active
    assert captures[0].closed
    assert repo.export_session_data(session_id)["session"]["ended_at"] is not None


def test_late_old_finalizer_cannot_close_a_restarted_session(manager):
    app, _, captures = manager
    assert app.start()
    old_worker = app._capture_thread
    app.stop()
    assert app.start()
    new_worker = app._capture_thread
    app._finish_stop(old_worker)
    assert app.state == AppState.RUNNING
    assert app._capture_thread is new_worker
    assert not captures[-1].closed


def test_thread_start_failure_releases_initialized_resources(manager, monkeypatch):
    app, repo, captures = manager

    def fail_start(_thread):
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    assert not app.start()
    assert app.state == AppState.STOPPED
    assert captures[0].closed
    assert not app._session_tracker.is_active
    assert repo.export_session_data(app._data.db_session_id)["session"]["ended_at"] is not None


def test_persistence_failure_does_not_prevent_capture_cleanup(manager, monkeypatch):
    app, _, captures = manager
    assert app.start()

    def failed_cleanup():
        raise RuntimeError("synthetic database close failure")

    monkeypatch.setattr(app, "_end_database_session", failed_cleanup)
    app.stop()
    assert app.state == AppState.STOPPED
    assert captures[0].closed


def test_restart_without_database_session_cannot_write_to_old_session(manager, monkeypatch):
    app, repo, _ = manager
    assert app.start()
    first_id = app._data.db_session_id
    app._process_money_change(MoneyReading(total=100_000))
    app.stop()
    original = repo.export_session_data(first_id)

    def no_persistence():
        app._capture = SimpleNamespace(close=lambda: None)
        app._session_tracker.start_session()

    monkeypatch.setattr(app, "_initialize_components", no_persistence)
    assert app.start()
    assert app._data.db_session_id is None
    app._process_money_change(MoneyReading(total=250_000))
    app._process_money_change(MoneyReading(total=270_000))
    app.stop()
    assert repo.export_session_data(first_id) == original


@pytest.mark.parametrize(
    "operation,initial", [("pause", AppState.RUNNING), ("resume", AppState.PAUSED)]
)
def test_pause_and_resume_cannot_overwrite_a_completed_stop(manager, operation, initial):
    app, _, _ = manager
    app._initialize_components()
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    # Suspend the successful state comparison to exercise the real check/write
    # interleaving deterministically, without arbitrary scheduler timing.
    class ObservedState:
        def __eq__(self, value):
            if value == initial:
                entered.set()
                release.wait(2)
                return True
            return False

    app._state = ObservedState()
    transition = threading.Thread(target=getattr(app, operation))
    stopper = threading.Thread(target=lambda: (app.stop(), stopped.set()))
    transition.start()
    assert entered.wait(2)
    stopper.start()
    stopped.wait(0.05)
    release.set()
    transition.join(2)
    stopper.join(2)
    assert not transition.is_alive() and not stopper.is_alive()
    assert app.state == AppState.STOPPED
