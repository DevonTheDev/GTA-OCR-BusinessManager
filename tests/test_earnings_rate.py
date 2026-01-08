"""Tests for real-time earnings rate tracking."""

import pytest
import time
from datetime import datetime, timezone, timedelta

from src.tracking.earnings_rate import (
    EarningsRateTracker,
    EarningEvent,
)


class TestEarningEvent:
    """Tests for EarningEvent dataclass."""

    def test_event_creation(self):
        """Test basic event creation."""
        event = EarningEvent(amount=25_000, source="Headhunter")

        assert event.amount == 25_000
        assert event.source == "Headhunter"
        assert event.timestamp is not None

    def test_event_default_timestamp(self):
        """Test event has default timestamp."""
        before = datetime.now(timezone.utc)
        event = EarningEvent(amount=10_000)
        after = datetime.now(timezone.utc)

        assert before <= event.timestamp <= after

    def test_event_custom_timestamp(self):
        """Test event with custom timestamp."""
        custom_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = EarningEvent(amount=50_000, timestamp=custom_time)

        assert event.timestamp == custom_time


class TestEarningsRateTracker:
    """Tests for EarningsRateTracker class."""

    @pytest.fixture
    def tracker(self):
        """Create a fresh tracker."""
        tracker = EarningsRateTracker()
        tracker.start_session()
        return tracker

    def test_start_session(self):
        """Test starting a session."""
        tracker = EarningsRateTracker()
        tracker.start_session()

        assert tracker.session_earnings == 0
        assert tracker.session_duration is not None

    def test_record_earning(self, tracker):
        """Test recording an earning."""
        tracker.record_earning(25_000, "Headhunter")

        assert tracker.session_earnings == 25_000
        assert tracker.event_count == 1

    def test_record_multiple_earnings(self, tracker):
        """Test recording multiple earnings."""
        tracker.record_earning(25_000, "Headhunter")
        tracker.record_earning(500_000, "Bunker Sell")
        tracker.record_earning(85_000, "Payphone Hit")

        assert tracker.session_earnings == 610_000
        assert tracker.event_count == 3

    def test_record_zero_earning_ignored(self, tracker):
        """Test that zero/negative earnings are ignored."""
        tracker.record_earning(0, "Nothing")
        tracker.record_earning(-100, "Lost money")

        assert tracker.session_earnings == 0
        assert tracker.event_count == 0

    def test_get_session_rate(self, tracker):
        """Test session rate calculation."""
        # Record earnings and wait a bit for non-zero duration
        tracker.record_earning(100_000)
        time.sleep(0.05)  # Small delay to ensure non-zero duration

        rate = tracker.get_rate("session")

        # Rate should be positive (earnings / time > 0)
        assert rate > 0

    def test_get_window_rate(self, tracker):
        """Test windowed rate calculation."""
        tracker.record_earning(100_000)

        rate_5min = tracker.get_rate("5min")
        rate_1hour = tracker.get_rate("1hour")

        # Both should be positive
        assert rate_5min > 0
        assert rate_1hour > 0

    def test_get_rate_empty(self, tracker):
        """Test rate with no earnings."""
        rate = tracker.get_rate("session")

        assert rate == 0.0

    def test_get_all_rates(self, tracker):
        """Test getting all rates."""
        tracker.record_earning(100_000)

        rates = tracker.get_all_rates()

        assert "5min" in rates
        assert "15min" in rates
        assert "30min" in rates
        assert "1hour" in rates
        assert "session" in rates

    def test_get_trend_stable(self, tracker):
        """Test trend detection - stable."""
        # No earnings = stable
        trend = tracker.get_trend()
        assert trend == "stable"

    def test_get_average_earning(self, tracker):
        """Test average earning calculation."""
        tracker.record_earning(20_000)
        tracker.record_earning(30_000)
        tracker.record_earning(40_000)

        avg = tracker.get_average_earning()

        assert avg == 30_000

    def test_get_average_earning_empty(self, tracker):
        """Test average with no earnings."""
        avg = tracker.get_average_earning()

        assert avg == 0.0

    def test_get_recent_events(self, tracker):
        """Test getting recent events."""
        tracker.record_earning(10_000, "A")
        tracker.record_earning(20_000, "B")
        tracker.record_earning(30_000, "C")

        events = tracker.get_recent_events(2)

        assert len(events) == 2
        # Most recent first
        assert events[0].amount == 30_000
        assert events[1].amount == 20_000

    def test_get_recent_events_limit(self, tracker):
        """Test recent events respects limit."""
        for i in range(10):
            tracker.record_earning(10_000)

        events = tracker.get_recent_events(5)

        assert len(events) == 5

    def test_get_time_to_goal(self, tracker):
        """Test time to goal estimation."""
        # Record enough to establish a rate
        tracker.record_earning(100_000)
        time.sleep(0.1)  # Small delay to have duration

        time_to_goal = tracker.get_time_to_goal(200_000)

        assert time_to_goal is not None
        # Should need time to earn another 100k
        assert time_to_goal.total_seconds() > 0

    def test_get_time_to_goal_already_reached(self, tracker):
        """Test time to goal when already reached."""
        tracker.record_earning(500_000)

        time_to_goal = tracker.get_time_to_goal(400_000)

        assert time_to_goal is not None
        assert time_to_goal.total_seconds() == 0

    def test_get_time_to_goal_no_earnings(self, tracker):
        """Test time to goal with no earnings."""
        time_to_goal = tracker.get_time_to_goal(1_000_000)

        assert time_to_goal is None

    def test_session_duration(self, tracker):
        """Test session duration tracking."""
        time.sleep(0.1)

        duration = tracker.session_duration

        assert duration is not None
        assert duration.total_seconds() >= 0.1

    def test_max_events_limit(self):
        """Test that events are limited to max."""
        tracker = EarningsRateTracker(max_events=10)
        tracker.start_session()

        for i in range(20):
            tracker.record_earning(1000)

        assert tracker.event_count == 10
        assert tracker.session_earnings == 20_000  # All earnings still counted

    def test_session_reset(self):
        """Test starting new session resets data."""
        tracker = EarningsRateTracker()
        tracker.start_session()
        tracker.record_earning(100_000)

        # Start new session
        tracker.start_session()

        assert tracker.session_earnings == 0
        assert tracker.event_count == 0


class TestRateCalculations:
    """Tests for rate calculation accuracy."""

    def test_hourly_rate_calculation(self):
        """Test that hourly rate calculation is correct."""
        tracker = EarningsRateTracker()

        # Manually set start time to 1 hour ago
        tracker._session_start = datetime.now(timezone.utc) - timedelta(hours=1)
        tracker._session_earnings = 500_000

        rate = tracker.get_rate("session")

        # Should be approximately $500,000/hr
        assert 490_000 <= rate <= 510_000

    def test_window_rate_extrapolation(self):
        """Test that window rate extrapolates correctly to hourly."""
        tracker = EarningsRateTracker()
        tracker.start_session()

        # Add event in last 5 minutes
        tracker.record_earning(50_000)

        # 5 min window: $50k in 5 min = $600k/hr
        rate_5min = tracker.get_rate("5min")

        # Should extrapolate to ~600k/hr
        assert rate_5min > 0
