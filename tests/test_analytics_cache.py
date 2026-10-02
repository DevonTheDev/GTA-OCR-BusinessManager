"""Analytics cache timing using real calculations and a deterministic clock."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src import app as app_module
from src.app import GTABusinessManager
from src.config.settings import Settings
from src.game.activities import ActivityType


@pytest.fixture
def cached_app(tmp_path, monkeypatch):
    manager = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    clock = SimpleNamespace(elapsed=10.0, wall=1000.0, reads=0)
    monkeypatch.setattr(app_module, "time", SimpleNamespace(
        monotonic=lambda: clock.elapsed, time=lambda: clock.wall,
    ))
    manager._session_tracker = SimpleNamespace(duration_seconds=3600.0, start_session=lambda **kwargs: None)
    activity = manager._activity_tracker.start_activity(ActivityType.CONTACT_MISSION, "Synthetic")
    activity.started_at -= timedelta(minutes=10)
    manager._activity_tracker.complete_activity(success=True, earnings=1000)
    original = manager._activity_tracker.get_recent_activities

    def read_history(count):
        clock.reads += 1
        return original(count)

    monkeypatch.setattr(manager._activity_tracker, "get_recent_activities", read_history)
    return manager, clock


def test_first_calculation_runs_at_zero_clock_origin(cached_app):
    manager, clock = cached_app
    clock.elapsed = clock.wall = 0.0
    metrics = manager.efficiency_metrics
    assert metrics is not None
    assert metrics.earnings_per_hour == pytest.approx(1000)
    assert clock.reads == 1


def test_cached_metrics_refresh_as_session_duration_changes(cached_app):
    manager, clock = cached_app
    assert manager.efficiency_metrics.earnings_per_hour == pytest.approx(1000)
    manager._session_tracker.duration_seconds = 7200
    clock.elapsed += 1
    clock.wall += 1
    assert manager.efficiency_metrics.earnings_per_hour == pytest.approx(500)
    assert clock.reads == 2


@pytest.mark.parametrize("jump", [-3600.0, 3600.0])
def test_wall_clock_adjustments_do_not_change_refresh_budget(cached_app, jump):
    manager, clock = cached_app
    manager._recalculate_analytics()
    before = manager._cached_efficiency
    manager._session_tracker.duration_seconds = 7200
    clock.wall += jump
    clock.elapsed += 0.25
    manager._recalculate_analytics()
    assert manager._cached_efficiency is before
    clock.elapsed += 0.75
    manager._recalculate_analytics()
    assert manager._cached_efficiency.earnings_per_hour == pytest.approx(500)
    assert clock.reads == 2


def test_empty_history_reads_are_throttled(cached_app):
    manager, clock = cached_app
    manager._activity_tracker.clear_history()
    assert manager.efficiency_metrics is None
    assert manager.earnings_breakdown is None
    assert manager.efficiency_metrics is None
    assert clock.reads == 1
    clock.elapsed += 1
    assert manager.efficiency_metrics is None
    assert clock.reads == 2


def test_failed_calculation_preserves_complete_previous_snapshot(cached_app, monkeypatch):
    manager, clock = cached_app
    previous_efficiency = manager.efficiency_metrics
    previous_breakdown = manager.earnings_breakdown
    manager._session_tracker.duration_seconds = 7200
    clock.elapsed += 1
    clock.wall += 1
    original = manager._analytics.calculate_earnings_breakdown

    def fail(*args):
        raise RuntimeError("synthetic breakdown failure")

    with monkeypatch.context() as patch:
        patch.setattr(manager._analytics, "calculate_earnings_breakdown", fail)
        manager._recalculate_analytics()
        assert manager._cached_efficiency is previous_efficiency
        assert manager._cached_breakdown is previous_breakdown
        manager._recalculate_analytics()
        assert clock.reads == 2, "failed attempts must not cause a retry hot loop"
    clock.elapsed += 1
    manager._recalculate_analytics()
    assert manager._cached_efficiency.earnings_per_hour == pytest.approx(500)
    assert manager._cached_breakdown == original(manager._activity_tracker.get_recent_activities(100))


def test_refresh_budget_starts_after_calculation_finishes(cached_app, monkeypatch):
    manager, clock = cached_app
    original = manager._analytics.calculate_efficiency

    def slow_calculation(*args):
        clock.elapsed += 5
        clock.wall += 5
        return original(*args)

    monkeypatch.setattr(manager._analytics, "calculate_efficiency", slow_calculation)
    manager._recalculate_analytics()
    manager._recalculate_analytics()
    assert clock.reads == 1


def test_force_refresh_bypasses_budget_and_reset_invalidates_it(cached_app):
    manager, clock = cached_app
    manager._recalculate_analytics()
    manager._session_tracker.duration_seconds = 7200
    manager._recalculate_analytics(force=True)
    assert manager._cached_efficiency.earnings_per_hour == pytest.approx(500)
    manager.reset_session()
    manager._session_tracker.duration_seconds = 1800
    metrics = manager.efficiency_metrics
    assert metrics is not None
    assert metrics.earnings_per_hour == pytest.approx(2000)
    assert clock.reads == 3


@pytest.mark.parametrize("missing", ["history", "duration"])
def test_missing_current_data_invalidates_old_visible_metrics(cached_app, missing):
    manager, clock = cached_app
    assert manager.earnings_breakdown.total == 1000
    if missing == "history":
        manager._activity_tracker.clear_history()
    else:
        manager._session_tracker.duration_seconds = 0
    clock.elapsed += 1
    clock.wall += 1
    assert manager.earnings_breakdown is None
    assert manager.efficiency_metrics is None
    assert manager.best_activity_type is None
    assert manager.best_activity_rate == 0


def test_concurrent_readers_share_one_refresh(cached_app, monkeypatch):
    import threading
    manager, clock = cached_app
    entered, overlap, release, second_ready = (threading.Event() for _ in range(4))
    original = manager._analytics.calculate_efficiency
    errors, results = [], []

    def blocked_calculation(*args):
        if entered.is_set():
            overlap.set()
        entered.set()
        if not release.wait(2):
            raise RuntimeError("test did not release calculation")
        return original(*args)

    def read(second=False):
        try:
            if second:
                second_ready.set()
            results.append(manager.efficiency_metrics)
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(manager._analytics, "calculate_efficiency", blocked_calculation)
    first = threading.Thread(target=read)
    second = threading.Thread(target=read, args=(True,))
    first.start()
    try:
        assert entered.wait(1)
        second.start()
        assert second_ready.wait(1)
        assert not overlap.wait(0.1), "concurrent reader started a duplicate calculation"
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert results[0] is results[1]
    assert results[0].earnings_per_hour == pytest.approx(1000)
    assert clock.reads == 1


def test_best_activity_getter_refreshes_after_history_changes(cached_app):
    manager, clock = cached_app
    assert manager.best_activity_type == "CONTACT_MISSION"
    manager._activity_tracker.clear_history()
    activity = manager._activity_tracker.start_activity(ActivityType.VIP_WORK, "New activity")
    activity.started_at -= timedelta(minutes=5)
    manager._activity_tracker.complete_activity(success=True, earnings=2000)
    clock.elapsed += 1
    assert manager.best_activity_type == "VIP_WORK"
    assert manager.best_activity_rate > 0
    assert manager.earnings_breakdown.total == 2000
    assert clock.reads == 2
