"""Activity lifecycle clocks retain the start timestamp's timezone convention."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.game import activities as activity_module
from src.game.activities import Activity, ActivityType
from src.tracking import activity_tracker as tracker_module
from src.tracking.activity_tracker import ActivityTracker


ZONES = [None, timezone.utc, timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-7))]


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.instant.astimezone().replace(tzinfo=None)
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(activity_module, "datetime", Clock)
    monkeypatch.setattr(tracker_module, "datetime", Clock)
    return Clock


@pytest.mark.parametrize("zone", ZONES)
def test_active_duration_uses_a_compatible_current_timestamp(clock, zone):
    activity = Activity(ActivityType.VIP_WORK, started_at=clock.now(zone))
    clock.instant += timedelta(seconds=75)
    assert activity.duration_seconds == 75
    assert activity.duration_minutes == 1.25
    assert activity.is_active


@pytest.mark.parametrize("zone", ZONES)
def test_completion_retains_clock_convention_and_serialized_timestamps(clock, zone):
    activity = Activity(ActivityType.VIP_WORK, started_at=clock.now(zone))
    original_start = activity.started_at
    clock.instant += timedelta(seconds=75)
    activity.complete(True, earnings=25000)
    expected_end = clock.now(zone)
    clock.instant += timedelta(hours=1)

    assert activity.duration_seconds == 75
    assert activity.ended_at == expected_end
    assert activity.ended_at.tzinfo is zone
    assert not activity.is_active and activity.success is True
    assert activity.earnings == 25000
    saved = json.loads(json.dumps(activity.to_dict()))
    assert saved["started_at"] == original_start.isoformat()
    assert saved["ended_at"] == expected_end.isoformat()


@pytest.mark.parametrize("zone", ZONES)
def test_tracker_cancellation_retains_compatible_clock_without_adding_history(clock, zone):
    tracker = ActivityTracker()
    activity = tracker.start_activity(ActivityType.SELL_MISSION, "Delivery")
    activity.started_at = clock.now(zone)
    activity.earnings = 50
    clock.instant += timedelta(seconds=75)

    assert tracker.cancel_activity() is activity
    assert activity.duration_seconds == 75
    assert activity.ended_at.tzinfo is zone
    assert activity.notes == "Cancelled" and activity.success is False
    assert activity.earnings == 50
    assert tracker.current_activity is None and not tracker.is_tracking
    assert tracker.completed_activities == []
    assert tracker.cancel_activity() is None


@pytest.mark.parametrize("replace", [False, True])
def test_default_activity_can_be_cancelled_or_replaced_then_measured(clock, replace):
    tracker = ActivityTracker()
    original = tracker.start_activity(ActivityType.CONTACT_MISSION, "Original")
    assert original.started_at.tzinfo is None
    clock.instant = original.started_at.astimezone(timezone.utc) + timedelta(seconds=120)

    if replace:
        replacement = tracker.start_activity(ActivityType.HEIST_PREP, "Replacement")
        assert tracker.current_activity is replacement and replacement.is_active
    else:
        assert tracker.cancel_activity() is original
        assert tracker.current_activity is None

    assert original.duration_seconds == 120
    assert original.duration_minutes == 2
    assert original.ended_at.tzinfo is None
    assert original.notes == "Cancelled" and original.success is False
    assert tracker.completed_activities == []
    saved = original.to_dict()
    assert datetime.fromisoformat(saved["ended_at"]) - datetime.fromisoformat(saved["started_at"]) == timedelta(seconds=120)
