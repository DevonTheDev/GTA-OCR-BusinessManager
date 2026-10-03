"""App-owned reminders use disposable files, synthetic capture and real SQLite."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import app as app_module
from src.app import AppState, CaptureResult, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.money_parser import MoneyReading
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState
from tests.test_app_accounting import app as _accounting_app


accounting_app = _accounting_app


def stored_session_data(app):
    record = app._repository.export_session_data(app._data.db_session_id)
    # Open-session duration is computed at read time, not a stored column.
    record["session"].pop("duration_seconds")
    return record


def test_reminders_are_available_before_tracking_without_capture_or_database(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Opening app-owned reminders must not initialize capture, OCR or SQLite")

    for name in ("ScreenCapture", "OCREngine", "get_repository"):
        monkeypatch.setattr(app_module, name, forbidden)
    settings = Settings(tmp_path / "settings.yaml")
    app = GTABusinessManager(settings)
    assert app.state is AppState.STOPPED
    assert app.cooldown_tracker is not None
    timer = app.cooldown_tracker.start_custom_timer("Prepare <literal>", 300)
    assert app.cooldown_tracker.get_cooldown(timer.activity_name).display_name == "Prepare <literal>"
    assert app._capture is app._ocr is app._repository is None
    assert not app._session_tracker.is_active
    assert list(tmp_path.rglob("*.db")) == []
    assert GTABusinessManager(settings).cooldown_tracker.get_cooldown(timer.activity_name).started_at == timer.started_at


def test_actual_component_start_stop_reset_and_character_change_keep_one_tracker(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.yaml")
    app = GTABusinessManager(settings)
    tracker = app.cooldown_tracker
    assert tracker is not None
    timer = tracker.start_custom_timer("Shared reminder", 300)
    repositories = []
    captures = []

    def capture(**_kwargs):
        value = SimpleNamespace(resolution=(1920, 1080), closed=False)
        value.set_capture_rate = lambda _rate: None
        value.close = lambda: setattr(value, "closed", True)
        captures.append(value)
        return value

    def repository():
        value = Repository(str(tmp_path / "lifecycle.db"))
        assert value.initialize()
        repositories.append(value)
        return value

    monkeypatch.setattr(app_module, "ScreenCapture", capture)
    monkeypatch.setattr(app_module, "OCREngine", lambda: SimpleNamespace(is_available=False))
    monkeypatch.setattr(app_module, "StateDetector", lambda **_kwargs: object())
    monkeypatch.setattr(app_module, "PerformanceMonitor", lambda: object())
    monkeypatch.setattr(app_module, "get_repository", repository)

    def cycle():
        app._stop_event.wait(0.01)
        return CaptureResult()

    monkeypatch.setattr(app, "_do_capture_cycle", cycle)
    try:
        for name in ("First character", "Second character"):
            settings.set("general.character_name", name)
            assert app.start()
            assert app.cooldown_tracker is tracker
            app.pause()
            app.reset_session()
            app.resume()
            assert tracker.get_cooldown(timer.activity_name).started_at == timer.started_at
            app.stop()
            assert app.state is AppState.STOPPED
            assert app.cooldown_tracker is tracker
        assert len(captures) == 2 and all(value.closed for value in captures)
        assert len(repositories) == 2
        restored = GTABusinessManager(settings).cooldown_tracker.get_cooldown(timer.activity_name)
        assert restored.started_at == timer.started_at
        assert restored.display_name == "Shared reminder"
    finally:
        app.stop()
        for value in repositories:
            value.close()


def test_failed_capture_start_does_not_replace_or_restart_reminders(tmp_path, monkeypatch):
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    tracker = app.cooldown_tracker
    assert tracker is not None
    timer = tracker.start_custom_timer("Retained on failure", 300)

    def fail(**_kwargs):
        raise RuntimeError("synthetic capture initialization failure")

    monkeypatch.setattr(app_module, "ScreenCapture", fail)
    assert app.start() is False
    assert app.state is AppState.STOPPED
    assert app.cooldown_tracker is tracker
    assert tracker.get_cooldown(timer.activity_name).started_at == timer.started_at


@pytest.mark.parametrize("outcome", [GameState.MISSION_COMPLETE, GameState.MISSION_FAILED])
def test_actual_completion_policy_and_manual_changes_keep_accounting_separate(accounting_app, outcome):
    app = accounting_app
    tracker = app.cooldown_tracker
    assert tracker is not None
    prior = tracker.set_timer("headhunter", "Headhunter", 60)
    app._process_money_change(MoneyReading(total=100_000))
    app._process_state(StateDetectionResult(state=GameState.MISSION_ACTIVE, confidence=1.0,
                                           reason="fixture", mission_text="Headhunter"), CaptureResult())
    app._process_money_change(MoneyReading(total=120_000))
    app._process_state(StateDetectionResult(state=outcome, confidence=1.0, reason="fixture"), CaptureResult())
    observed = tracker.get_cooldown("headhunter")
    if outcome is GameState.MISSION_COMPLETE:
        assert observed.duration_seconds == 300
        assert observed.started_at >= prior.started_at
    else:
        assert observed.duration_seconds == 60
        assert observed.started_at == prior.started_at
    before = stored_session_data(app)
    assert len(before["activities"]) == len(before["earnings"]) == 1
    assert app.session_earnings == 20_000
    app._optimizer._cooldowns["separate-policy"] = datetime.now(timezone.utc)
    optimizer_before = dict(app._optimizer._cooldowns)
    timer = tracker.start_custom_timer("Independent reminder", 300)
    tracker.set_timer(timer.activity_name, "Adjusted reminder", 900)
    tracker.clear_cooldown(timer.activity_name)
    tracker.retry_save()
    assert stored_session_data(app) == before
    assert app._optimizer._cooldowns == optimizer_before
    assert app.session_stats.activities_completed == 1
    assert app.session_earnings == 20_000


@pytest.mark.parametrize("name", ["Security contract", "Heist finale", "Unclassified mission"])
def test_unmapped_success_does_not_invent_an_automatic_timer(accounting_app, name):
    app = accounting_app
    tracker = app.cooldown_tracker
    assert tracker is not None
    app._process_state(StateDetectionResult(state=GameState.MISSION_ACTIVE, confidence=1.0,
                                           reason="fixture", mission_text=name), CaptureResult())
    app._process_state(StateDetectionResult(state=GameState.MISSION_COMPLETE, confidence=1.0,
                                           reason="fixture"), CaptureResult())
    assert tracker.get_active_cooldowns() == []
    assert len(app._repository.export_session_data(app._data.db_session_id)["activities"]) == 1
