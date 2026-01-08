"""Tests for tracking modules (session.py and analytics.py)."""

import pytest
from datetime import datetime, timedelta

from src.tracking.session import SessionStats, SessionTracker
from src.tracking.analytics import Analytics, EarningsBreakdown, TimeBreakdown, EfficiencyMetrics
from src.game.activities import Activity, ActivityType


class TestSessionStats:
    """Tests for SessionStats dataclass."""

    def test_empty_session_stats(self):
        """Test default session stats values."""
        stats = SessionStats()
        assert stats.start_money == 0
        assert stats.current_money == 0
        assert stats.total_earnings == 0
        assert stats.activities_completed == 0

    def test_duration_seconds(self):
        """Test session duration calculation."""
        # Create stats with known start time
        past = datetime.now() - timedelta(minutes=5)
        stats = SessionStats(started_at=past)

        # Duration should be approximately 5 minutes (300 seconds)
        assert 299 <= stats.duration_seconds <= 301

    def test_earnings_per_hour(self):
        """Test earnings per hour calculation."""
        past = datetime.now() - timedelta(hours=1)
        stats = SessionStats(started_at=past, total_earnings=500000)

        # Should be approximately $500,000/hour
        assert 490000 <= stats.earnings_per_hour <= 510000

    def test_earnings_per_hour_zero_duration(self):
        """Test earnings per hour with very short duration."""
        stats = SessionStats(total_earnings=1000000)
        # Very short duration, should not crash
        result = stats.earnings_per_hour
        assert result >= 0

    def test_mission_success_rate(self):
        """Test mission success rate calculation."""
        stats = SessionStats(missions_passed=7, missions_failed=3)
        assert stats.mission_success_rate == 0.7

    def test_mission_success_rate_no_missions(self):
        """Test success rate with no missions."""
        stats = SessionStats()
        assert stats.mission_success_rate == 0


class TestSessionTracker:
    """Tests for SessionTracker class."""

    def test_initial_state(self):
        """Test tracker starts inactive."""
        tracker = SessionTracker()
        assert not tracker.is_active
        assert tracker.stats is None
        assert tracker.duration_seconds == 0
        assert tracker.total_earnings == 0

    def test_start_session(self):
        """Test starting a session."""
        tracker = SessionTracker()
        stats = tracker.start_session(start_money=1000000)

        assert tracker.is_active
        assert stats is not None
        assert stats.start_money == 1000000
        assert stats.current_money == 1000000

    def test_end_session(self):
        """Test ending a session."""
        tracker = SessionTracker()
        tracker.start_session(start_money=100000)
        tracker.update_money(150000)

        stats = tracker.end_session()

        assert not tracker.is_active
        assert stats is not None
        assert stats.total_earnings == 50000

    def test_end_session_no_active(self):
        """Test ending when no session active."""
        tracker = SessionTracker()
        result = tracker.end_session()
        assert result is None

    def test_update_money_positive(self):
        """Test money increase."""
        tracker = SessionTracker()
        tracker.start_session(start_money=100000)

        change = tracker.update_money(150000)

        assert change == 50000
        assert tracker.stats.current_money == 150000
        assert tracker.stats.total_earnings == 50000

    def test_update_money_negative(self):
        """Test money decrease (spending)."""
        tracker = SessionTracker()
        tracker.start_session(start_money=100000)

        change = tracker.update_money(80000)

        assert change == -20000
        assert tracker.stats.current_money == 80000
        # Total earnings should not include negative changes
        assert tracker.stats.total_earnings == 0

    def test_update_money_no_session(self):
        """Test updating money with no session."""
        tracker = SessionTracker()
        change = tracker.update_money(50000)
        assert change == 0

    def test_record_activity_complete_success(self):
        """Test recording successful activity."""
        tracker = SessionTracker()
        tracker.start_session()

        tracker.record_activity_complete(success=True, earnings=15000)

        assert tracker.stats.activities_completed == 1
        assert tracker.stats.missions_passed == 1
        assert tracker.stats.missions_failed == 0

    def test_record_activity_complete_failure(self):
        """Test recording failed activity."""
        tracker = SessionTracker()
        tracker.start_session()

        tracker.record_activity_complete(success=False)

        assert tracker.stats.activities_completed == 1
        assert tracker.stats.missions_passed == 0
        assert tracker.stats.missions_failed == 1

    def test_record_sell_complete(self):
        """Test recording sell mission."""
        tracker = SessionTracker()
        tracker.start_session()

        tracker.record_activity_complete(success=True, is_sell=True)

        assert tracker.stats.sells_completed == 1

    def test_record_activity_no_session(self):
        """Test recording activity with no session."""
        tracker = SessionTracker()
        # Should not crash
        tracker.record_activity_complete(success=True)

    def test_add_mission_time(self):
        """Test adding mission time."""
        tracker = SessionTracker()
        tracker.start_session()

        tracker.add_mission_time(120)
        tracker.add_mission_time(60)

        assert tracker.stats.time_in_missions == 180

    def test_add_idle_time(self):
        """Test adding idle time."""
        tracker = SessionTracker()
        tracker.start_session()

        tracker.add_idle_time(300)

        assert tracker.stats.time_idle == 300


class TestAnalytics:
    """Tests for Analytics class."""

    def _create_activity(
        self,
        activity_type: ActivityType,
        earnings: int = 0,
        success: bool = True,
        duration: int = 300,
    ) -> Activity:
        """Helper to create test activities."""
        activity = Activity(
            activity_type=activity_type,
            name="Test Activity",
            expected_earnings=earnings,
        )
        activity.complete(success=success, earnings=earnings)
        activity._duration_override = duration
        return activity

    def test_empty_earnings_breakdown(self):
        """Test earnings breakdown with no activities."""
        analytics = Analytics()
        breakdown = analytics.calculate_earnings_breakdown([])

        assert breakdown.total == 0
        assert breakdown.from_missions == 0
        assert breakdown.from_sells == 0

    def test_earnings_breakdown_by_type(self):
        """Test earnings categorization."""
        analytics = Analytics()

        activities = [
            self._create_activity(ActivityType.CONTACT_MISSION, earnings=15000),
            self._create_activity(ActivityType.SELL_MISSION, earnings=200000),
            self._create_activity(ActivityType.VIP_WORK, earnings=25000),
            self._create_activity(ActivityType.HEIST_FINALE, earnings=1000000),
        ]

        breakdown = analytics.calculate_earnings_breakdown(activities)

        assert breakdown.total == 1240000
        assert breakdown.from_missions == 15000
        assert breakdown.from_sells == 200000
        assert breakdown.from_vip_work == 25000
        assert breakdown.from_heists == 1000000

    def test_earnings_excludes_failures(self):
        """Test that failed activities don't count in earnings."""
        analytics = Analytics()

        activities = [
            self._create_activity(ActivityType.CONTACT_MISSION, earnings=15000, success=True),
            self._create_activity(ActivityType.CONTACT_MISSION, earnings=20000, success=False),
        ]

        breakdown = analytics.calculate_earnings_breakdown(activities)

        assert breakdown.total == 15000

    def test_time_breakdown(self):
        """Test time breakdown calculation."""
        analytics = Analytics()

        activities = [
            self._create_activity(ActivityType.CONTACT_MISSION, duration=300),
            self._create_activity(ActivityType.SELL_MISSION, duration=600),
        ]

        breakdown = analytics.calculate_time_breakdown(activities, total_session_time=3600)

        assert breakdown.total_seconds == 3600
        assert breakdown.in_missions == 300
        assert breakdown.in_sells == 600
        assert breakdown.in_freeroam == 2700  # 3600 - 300 - 600

    def test_efficiency_metrics_empty(self):
        """Test efficiency with no activities."""
        analytics = Analytics()
        metrics = analytics.calculate_efficiency([], total_session_time=3600)

        assert metrics.earnings_per_hour == 0
        assert metrics.activities_per_hour == 0

    def test_efficiency_metrics(self):
        """Test efficiency calculation."""
        analytics = Analytics()

        # 2 activities in 1 hour, $200K total
        activities = [
            self._create_activity(ActivityType.CONTACT_MISSION, earnings=50000, duration=600),
            self._create_activity(ActivityType.SELL_MISSION, earnings=150000, duration=900),
        ]

        metrics = analytics.calculate_efficiency(activities, total_session_time=3600)

        assert metrics.earnings_per_hour == 200000  # $200K in 1 hour
        assert metrics.activities_per_hour == 2  # 2 activities in 1 hour
        assert metrics.avg_mission_duration == 750  # (600 + 900) / 2
        assert metrics.mission_success_rate == 1.0  # All successful

    def test_efficiency_best_activity(self):
        """Test finding best activity type."""
        analytics = Analytics()

        # VIP work has better $/hour than missions
        activities = [
            self._create_activity(ActivityType.CONTACT_MISSION, earnings=15000, duration=600),
            self._create_activity(ActivityType.VIP_WORK, earnings=30000, duration=300),
        ]

        metrics = analytics.calculate_efficiency(activities, total_session_time=1800)

        assert metrics.best_activity_type == "VIP_WORK"
        # VIP: $30K in 300s = $360K/hr
        assert metrics.best_activity_rate == 360000

    def test_recommendations_empty(self):
        """Test recommendations with no activities."""
        analytics = Analytics()
        recs = analytics.get_recommendations([], {})

        assert len(recs) == 1
        assert "Start completing" in recs[0]

    def test_recommendations_best_activity(self):
        """Test recommendation includes best activity."""
        analytics = Analytics()

        activities = [
            self._create_activity(ActivityType.VIP_WORK, earnings=25000, duration=300),
            self._create_activity(ActivityType.VIP_WORK, earnings=30000, duration=360),
        ]

        recs = analytics.get_recommendations(activities, {})

        assert len(recs) >= 1
        assert "VIP_WORK" in recs[0]
        assert "efficient" in recs[0].lower()

    def test_recommendations_low_success_rate(self):
        """Test recommendation for low success rate."""
        analytics = Analytics()

        activities = [
            self._create_activity(ActivityType.HEIST_FINALE, earnings=0, success=False),
            self._create_activity(ActivityType.HEIST_FINALE, earnings=0, success=False),
            self._create_activity(ActivityType.HEIST_FINALE, earnings=1000000, success=True),
        ]

        recs = analytics.get_recommendations(activities, {})

        # Should recommend practicing heists due to <50% success rate
        has_practice_rec = any("practice" in r.lower() or "success rate" in r.lower() for r in recs)
        assert has_practice_rec

    def test_recommendations_limit(self):
        """Test that recommendations are limited to 5."""
        analytics = Analytics()

        # Create many different activity types with varying success rates
        activities = []
        for atype in ActivityType:
            for i in range(5):
                activities.append(
                    self._create_activity(atype, earnings=10000, success=(i > 2))
                )

        recs = analytics.get_recommendations(activities, {})

        assert len(recs) <= 5


# Patch Activity to support duration override for testing
original_duration = Activity.duration_seconds.fget

@property
def patched_duration(self):
    if hasattr(self, '_duration_override'):
        return self._duration_override
    return original_duration(self)

Activity.duration_seconds = patched_duration
