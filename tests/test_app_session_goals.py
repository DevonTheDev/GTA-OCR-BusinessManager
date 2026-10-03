"""Live goal progress through actual app accounting, lifecycle and SQLite."""

from datetime import datetime, timedelta
import json

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.detection.parsers.money_parser import MoneyReading
from src.game.state_machine import GameState
from src.detection.state_detector import StateDetectionResult
from src.tracking.goals import GoalType
from src.tracking import session as session_module
from tests.test_app_accounting import app as _accounting_app
from tests.test_app_lifecycle import manager as _lifecycle_manager

accounting_app = _accounting_app
lifecycle_manager = _lifecycle_manager


def goal(app):
    app.refresh_session_goal()
    return app.goal_tracker.current_goal


def test_opening_cash_spending_and_recovery_use_gross_progress(accounting_app):
    app = accounting_app
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    app._process_money_change(MoneyReading(total=1_000_000))
    assert goal(app).current_value == 0
    app._process_money_change(MoneyReading(total=900_000))
    assert goal(app).current_value == 0
    app._process_money_change(MoneyReading(total=950_000))
    assert goal(app).current_value == 50_000
    assert goal(app).progress_percent == 50
    app._end_database_session()
    record = app._repository.export_session_data(app._data.db_session_id)
    assert record["session"]["total_earnings"] == -50_000
    assert [row["amount"] for row in record["earnings"]] == [50_000]


def test_late_goal_includes_existing_totals_and_change_uses_new_units(accounting_app):
    app = accounting_app
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=140_000))
    app._session_tracker.record_activity_complete(success=True)
    app._session_tracker.record_activity_complete(success=False)
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    assert app.goal_tracker.current_goal.current_value == 40_000
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    assert app.goal_tracker.current_goal.current_value == 2
    assert app.goal_tracker.current_goal.goal_type is GoalType.ACTIVITIES


def test_completed_goal_keeps_first_crossing_until_explicit_new_period(accounting_app):
    app = accounting_app
    app.set_session_goal(GoalType.EARNINGS, 50_000)
    for balance in (100_000, 160_000):
        app._process_money_change(MoneyReading(total=balance))
    completed = goal(app)
    assert completed.is_complete and completed.current_value == 60_000
    app._process_money_change(MoneyReading(total=220_000))
    refreshed = goal(app)
    assert refreshed.current_value == 60_000
    assert refreshed.completed_at == completed.completed_at
    app.reset_session()
    fresh = app.goal_tracker.current_goal
    assert fresh.target_value == 50_000 and fresh.current_value == 0
    assert not fresh.is_complete and fresh.completed_at is None


def test_reset_preserves_target_but_uses_new_statistics_period(accounting_app):
    app = accounting_app
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    app._session_tracker.record_activity_complete(success=False)
    assert goal(app).current_value == 1
    old_stats = app.session_stats
    app.reset_session()
    assert app.session_stats is not old_stats
    assert app.goal_tracker.current_goal.current_value == 0
    assert app.goal_tracker.current_goal.target_value == 5


def test_clear_and_reload_restore_only_explicit_target(accounting_app):
    app = accounting_app
    app.set_session_goal(GoalType.EARNINGS, 100_000, "Fixture target")
    for balance in (100_000, 150_000):
        app._process_money_change(MoneyReading(total=balance))
    assert goal(app).current_value == 50_000
    reopened = GTABusinessManager(app._settings)
    assert reopened.goal_tracker.current_goal.current_value == 0
    assert reopened.goal_tracker.current_goal.display_name == "Fixture target"
    app.clear_session_goal()
    assert app.goal_tracker.current_goal is None
    assert GTABusinessManager(app._settings).goal_tracker.current_goal is None


def test_goal_target_is_available_without_capture_or_database(tmp_path):
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    app.set_session_goal(GoalType.ACTIVITIES, 3)
    assert app.state is AppState.STOPPED
    assert app._capture_thread is None and app._repository is None
    assert app.session_stats is None
    assert goal(app).current_value == 0
    payload = json.loads((tmp_path / "session_goal_target.json").read_text())
    assert payload["target"]["target_value"] == 3
    assert "current_value" not in payload["target"]


def test_new_capture_run_resets_goal_and_rejected_start_does_not(lifecycle_manager):
    app, _, _ = lifecycle_manager
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    assert app.start()
    for balance in (100_000, 175_000):
        app._process_money_change(MoneyReading(total=balance))
    assert goal(app).current_value == 75_000
    assert app.start() is False
    assert goal(app).current_value == 75_000
    app.stop()
    assert app.goal_tracker.current_goal.current_value == 75_000
    assert app.start()
    assert app.goal_tracker.current_goal.current_value == 0


def test_stop_finalizes_goal_even_without_a_last_ui_poll(lifecycle_manager):
    app, _, _ = lifecycle_manager
    app.set_session_goal(GoalType.EARNINGS, 20_000)
    assert app.start()
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    # Capture updates accounting; normal goal display refresh is independent.
    app.stop()
    assert app.goal_tracker.current_goal.current_value == 20_000
    assert app.goal_tracker.current_goal.is_complete


def test_goal_finalization_failure_does_not_retain_capture_resources(lifecycle_manager, monkeypatch):
    app, repo, captures = lifecycle_manager
    app.set_session_goal(GoalType.EARNINGS, 20_000)
    assert app.start()
    session_id = app._data.db_session_id
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic goal failure")
    monkeypatch.setattr(app.goal_tracker, "sync", fail)
    app.stop()
    assert app.state is AppState.STOPPED
    assert app._capture_thread is None and captures[-1].closed
    assert repo.export_session_data(session_id)["session"]["ended_at"] is not None


def test_starting_and_stopping_refresh_cannot_rebase_old_progress(accounting_app):
    app = accounting_app
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=140_000))
    assert goal(app).current_value == 40_000
    for state in (AppState.STARTING, AppState.STOPPING):
        app._state = state
        app._data.session_earnings = 0
        app.refresh_session_goal()
        assert app.goal_tracker.current_goal.current_value == 40_000


def test_elapsed_goal_uses_whole_minutes_including_pause_and_freezes_stop(accounting_app, monkeypatch):
    app = accounting_app
    origin = datetime(2026, 1, 1, 12)
    class Clock(datetime):
        value = origin
        @classmethod
        def now(cls, tz=None):
            return cls.value
    monkeypatch.setattr(session_module, "datetime", Clock)
    app.session_stats.started_at = origin
    app._state = AppState.RUNNING
    app.set_session_goal(GoalType.TIME, 20)
    Clock.value = origin + timedelta(seconds=59, microseconds=999999)
    assert goal(app).current_value == 0
    app.pause()
    Clock.value = origin + timedelta(seconds=125)
    assert goal(app).current_value == 2
    app.stop()
    Clock.value = origin + timedelta(hours=3)
    assert goal(app).current_value == 2


def test_negative_elapsed_reading_never_creates_negative_goal_progress(accounting_app, monkeypatch):
    app = accounting_app
    origin = datetime(2026, 1, 1, 12)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return origin - timedelta(minutes=10)
    monkeypatch.setattr(session_module, "datetime", Clock)
    app.session_stats.started_at = origin
    app.set_session_goal(GoalType.TIME, 10)
    assert goal(app).current_value == 0


@pytest.mark.parametrize("outcome", [GameState.MISSION_COMPLETE, GameState.MISSION_FAILED])
def test_actual_mission_results_count_finished_activities_without_extra_income(accounting_app, outcome):
    app = accounting_app
    app.set_session_goal(GoalType.ACTIVITIES, 2)
    app._process_money_change(MoneyReading(total=100_000))
    start = StateDetectionResult(state=GameState.MISSION_ACTIVE, confidence=1.0, reason="fixture")
    result = StateDetectionResult(state=outcome, confidence=1.0, reason="fixture")
    from src.app import CaptureResult
    app._process_state(start, CaptureResult())
    app._process_money_change(MoneyReading(total=120_000))
    app._process_state(result, CaptureResult())
    app._process_state(result, CaptureResult())
    assert goal(app).current_value == 1
    app.set_session_goal(GoalType.EARNINGS, 50_000)
    assert app.goal_tracker.current_goal.current_value == 20_000
    record = app._repository.export_session_data(app._data.db_session_id)
    assert len(record["activities"]) == 1
    assert [row["amount"] for row in record["earnings"]] == [20_000]
