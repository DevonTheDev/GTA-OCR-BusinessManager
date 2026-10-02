"""Completed session statistics with a deterministic clock and no native capture."""

from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.game.activities import ActivityType
from src.tracking import session as session_module
from src.tracking.session import SessionStats, SessionTracker


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=datetime(2026, 1, 1, 12))
    monkeypatch.setattr(session_module, "datetime", SimpleNamespace(now=lambda: clock.now))
    return clock


def test_completed_duration_and_rate_stop_advancing(clock):
    tracker = SessionTracker()
    stats = tracker.start_session(1000)
    clock.now += timedelta(hours=1)
    tracker.update_money(2000)
    assert stats.duration_seconds == 3600
    assert stats.earnings_per_hour == 1000
    assert tracker.end_session() is stats
    ended_at = clock.now
    clock.now += timedelta(hours=2)
    assert stats.duration_seconds == tracker.duration_seconds == 3600
    assert stats.earnings_per_hour == 1000
    assert stats.ended_at == ended_at


def test_repeat_completion_keeps_the_original_end_time(clock):
    tracker = SessionTracker()
    stats = tracker.start_session()
    clock.now += timedelta(minutes=5)
    tracker.end_session()
    clock.now += timedelta(minutes=2)
    assert tracker.end_session() is None
    assert stats.duration_seconds == 300


@pytest.mark.parametrize(
    "update",
    [
        lambda tracker: tracker.set_money_baseline(999),
        lambda tracker: tracker.update_money(5000),
        lambda tracker: tracker.record_activity_complete(True, 200, True),
        lambda tracker: tracker.record_activity_complete(False),
        lambda tracker: tracker.add_mission_time(500),
        lambda tracker: tracker.add_idle_time(600),
    ],
)
def test_late_tracker_updates_do_not_change_completed_stats(clock, update):
    tracker = SessionTracker()
    stats = tracker.start_session(1000)
    tracker.update_money(1200)
    tracker.record_activity_complete(True)
    tracker.add_mission_time(60)
    tracker.add_idle_time(30)
    tracker.end_session()
    before = asdict(stats)
    update(tracker)
    assert asdict(stats) == before


def test_money_update_after_completion_reports_no_change(clock):
    tracker = SessionTracker()
    tracker.start_session(100)
    tracker.end_session()
    assert tracker.update_money(1000) == 0


def test_new_run_has_live_duration_without_reopening_previous_stats(clock):
    tracker = SessionTracker()
    first = tracker.start_session(100)
    clock.now += timedelta(minutes=5)
    tracker.end_session()
    clock.now += timedelta(minutes=2)
    second = tracker.start_session(200)
    clock.now += timedelta(minutes=1)
    assert second is not first
    assert tracker.is_active
    assert first.duration_seconds == 300
    assert second.duration_seconds == 60
    assert second.ended_at is None
    assert tracker.update_money(250) == 50
    assert second.total_earnings == 50


def test_explicit_completed_stats_use_saved_end_time(clock):
    stats = SessionStats(
        started_at=clock.now, ended_at=clock.now + timedelta(minutes=10), total_earnings=1000
    )
    clock.now += timedelta(days=2)
    assert stats.duration_seconds == 600
    assert stats.earnings_per_hour == 6000


def test_application_stop_keeps_refreshed_session_analytics_stable(tmp_path, clock):
    manager = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    manager._state = AppState.RUNNING
    manager._session_tracker.start_session(1000)
    manager._session_tracker.update_money(2000)
    activity = manager._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, "Fixture")
    activity.started_at -= timedelta(minutes=10)
    manager._activity_tracker.complete_activity(success=True, earnings=1000)
    clock.now += timedelta(hours=1)
    manager.stop()
    assert manager.state == AppState.STOPPED
    assert manager.session_stats.duration_seconds == 3600
    manager._recalculate_analytics(force=True)
    first = manager.efficiency_metrics.earnings_per_hour
    clock.now += timedelta(hours=1)
    manager._recalculate_analytics(force=True)
    assert manager.session_stats.duration_seconds == 3600
    assert manager.session_stats.earnings_per_hour == 1000
    assert manager.efficiency_metrics.earnings_per_hour == first == 1000
