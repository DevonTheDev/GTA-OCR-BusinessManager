"""Tests for cooldown tracking."""

import pytest
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.tracking.cooldowns import (
    CooldownTracker,
    CooldownInfo,
    ACTIVITY_COOLDOWNS,
)


class TestCooldownInfo:
    """Tests for CooldownInfo dataclass."""

    def test_cooldown_info_creation(self):
        """Test basic CooldownInfo creation."""
        now = datetime.now(timezone.utc)
        info = CooldownInfo(
            activity_name="headhunter",
            display_name="Headhunter",
            started_at=now,
            duration_seconds=300,
        )

        assert info.activity_name == "headhunter"
        assert info.display_name == "Headhunter"
        assert info.duration_seconds == 300

    def test_elapsed_seconds(self):
        """Test elapsed time calculation."""
        # Start 60 seconds ago
        start = datetime.now(timezone.utc) - timedelta(seconds=60)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        elapsed = info.elapsed_seconds
        assert 59 <= elapsed <= 62  # Allow small variance

    def test_remaining_seconds(self):
        """Test remaining time calculation."""
        # Start 60 seconds ago with 300 second duration
        start = datetime.now(timezone.utc) - timedelta(seconds=60)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        remaining = info.remaining_seconds
        assert 238 <= remaining <= 242  # 300 - 60 with variance

    def test_remaining_never_negative(self):
        """Test that remaining time never goes negative."""
        # Start 500 seconds ago with 300 second duration
        start = datetime.now(timezone.utc) - timedelta(seconds=500)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        assert info.remaining_seconds == 0

    def test_is_expired(self):
        """Test expiration detection."""
        # Not expired - started recently
        recent_start = datetime.now(timezone.utc)
        recent_info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=recent_start,
            duration_seconds=300,
        )
        assert not recent_info.is_expired

        # Expired - started long ago
        old_start = datetime.now(timezone.utc) - timedelta(seconds=500)
        old_info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=old_start,
            duration_seconds=300,
        )
        assert old_info.is_expired

    def test_progress(self):
        """Test progress calculation."""
        # 50% through
        start = datetime.now(timezone.utc) - timedelta(seconds=150)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        progress = info.progress
        assert 0.48 <= progress <= 0.52  # ~50% with variance

    def test_progress_caps_at_one(self):
        """Test that progress never exceeds 1.0."""
        old_start = datetime.now(timezone.utc) - timedelta(seconds=500)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=old_start,
            duration_seconds=300,
        )

        assert info.progress == 1.0

    def test_remaining_formatted_seconds(self):
        """Test formatted remaining time for seconds."""
        start = datetime.now(timezone.utc) - timedelta(seconds=270)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        formatted = info.remaining_formatted
        assert "s" in formatted
        assert "m" not in formatted  # Less than a minute

    def test_remaining_formatted_minutes(self):
        """Test formatted remaining time for minutes."""
        start = datetime.now(timezone.utc) - timedelta(seconds=60)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=start,
            duration_seconds=300,
        )

        formatted = info.remaining_formatted
        assert "m" in formatted

    def test_remaining_formatted_ready(self):
        """Test formatted time shows Ready when expired."""
        old_start = datetime.now(timezone.utc) - timedelta(seconds=500)
        info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=old_start,
            duration_seconds=300,
        )

        assert info.remaining_formatted == "Ready"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime.now(timezone.utc)
        info = CooldownInfo(
            activity_name="headhunter",
            display_name="Headhunter",
            started_at=now,
            duration_seconds=300,
        )

        data = info.to_dict()
        assert data["activity_name"] == "headhunter"
        assert data["display_name"] == "Headhunter"
        assert data["duration_seconds"] == 300
        assert "started_at" in data

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "activity_name": "sightseer",
            "display_name": "Sightseer",
            "started_at": now.isoformat(),
            "duration_seconds": 300,
        }

        info = CooldownInfo.from_dict(data)
        assert info.activity_name == "sightseer"
        assert info.display_name == "Sightseer"
        assert info.duration_seconds == 300


class TestCooldownTracker:
    """Tests for CooldownTracker class."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create a CooldownTracker with temp storage."""
        return CooldownTracker(data_path=tmp_path / "cooldowns.json")

    @pytest.fixture
    def memory_tracker(self):
        """Create a CooldownTracker without persistence."""
        return CooldownTracker(data_path=None)

    def test_start_cooldown(self, tracker):
        """Test starting a cooldown."""
        cooldown = tracker.start_cooldown("headhunter", "Headhunter", 300)

        assert cooldown.activity_name == "headhunter"
        assert cooldown.display_name == "Headhunter"
        assert cooldown.duration_seconds == 300
        assert not cooldown.is_expired

    def test_start_cooldown_default_duration(self, tracker):
        """Test starting a cooldown with default duration."""
        cooldown = tracker.start_cooldown("headhunter")

        assert cooldown.duration_seconds == ACTIVITY_COOLDOWNS["headhunter"]

    def test_start_cooldown_default_display_name(self, tracker):
        """Test starting a cooldown with auto-generated display name."""
        cooldown = tracker.start_cooldown("hostile_takeover")

        assert cooldown.display_name == "Hostile Takeover"

    def test_get_cooldown(self, tracker):
        """Test retrieving an active cooldown."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)

        cooldown = tracker.get_cooldown("headhunter")
        assert cooldown is not None
        assert cooldown.activity_name == "headhunter"

    def test_get_cooldown_not_found(self, tracker):
        """Test retrieving a non-existent cooldown."""
        cooldown = tracker.get_cooldown("nonexistent")
        assert cooldown is None

    def test_is_on_cooldown(self, tracker):
        """Test checking if activity is on cooldown."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)

        assert tracker.is_on_cooldown("headhunter")
        assert not tracker.is_on_cooldown("sightseer")

    def test_get_remaining(self, tracker):
        """Test getting remaining cooldown time."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)

        remaining = tracker.get_remaining("headhunter")
        assert 298 <= remaining <= 300  # Just started

    def test_get_remaining_no_cooldown(self, tracker):
        """Test getting remaining time for non-existent cooldown."""
        remaining = tracker.get_remaining("nonexistent")
        assert remaining == 0

    def test_clear_cooldown(self, tracker):
        """Test clearing a specific cooldown."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)
        assert tracker.is_on_cooldown("headhunter")

        tracker.clear_cooldown("headhunter")
        assert not tracker.is_on_cooldown("headhunter")

    def test_get_active_cooldowns(self, tracker):
        """Test getting all active cooldowns."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)
        tracker.start_cooldown("sightseer", "Sightseer", 300)

        active = tracker.get_active_cooldowns()
        assert len(active) == 2

    def test_get_active_cooldowns_sorted(self, tracker):
        """Test that active cooldowns are sorted by remaining time."""
        # Start cooldown that will have more remaining time
        tracker.start_cooldown("payphone_hit", "Payphone Hit", 1200)
        time.sleep(0.1)
        # Start cooldown that will have less remaining time
        tracker.start_cooldown("headhunter", "Headhunter", 300)

        active = tracker.get_active_cooldowns()
        assert active[0].activity_name == "headhunter"  # Less time remaining
        assert active[1].activity_name == "payphone_hit"

    def test_get_ready_activities(self, tracker):
        """Test getting activities that are off cooldown."""
        tracker.start_cooldown("headhunter", "Headhunter", 300)

        ready = tracker.get_ready_activities()
        assert "headhunter" not in ready
        assert "sightseer" in ready  # Not on cooldown

    def test_cleanup_expired(self, tracker):
        """Test cleaning up expired cooldowns."""
        # Create cooldown that's already expired
        now = datetime.now(timezone.utc) - timedelta(seconds=500)
        expired_info = CooldownInfo(
            activity_name="test",
            display_name="Test",
            started_at=now,
            duration_seconds=300,
        )
        tracker._cooldowns["test"] = expired_info

        removed = tracker.cleanup_expired()
        assert removed == 1
        assert "test" not in tracker._cooldowns

    def test_persistence(self, tmp_path):
        """Test that cooldowns persist across tracker instances."""
        path = tmp_path / "cooldowns.json"

        # Create tracker and add cooldown
        tracker1 = CooldownTracker(data_path=path)
        tracker1.start_cooldown("headhunter", "Headhunter", 300)

        # Create new tracker with same path
        tracker2 = CooldownTracker(data_path=path)

        # Should have the cooldown
        assert tracker2.is_on_cooldown("headhunter")

    def test_expired_cooldowns_not_loaded(self, tmp_path):
        """Test that expired cooldowns are not loaded from file."""
        path = tmp_path / "cooldowns.json"

        # Create tracker and add expired cooldown manually
        tracker1 = CooldownTracker(data_path=path)
        old_start = datetime.now(timezone.utc) - timedelta(seconds=500)
        expired = CooldownInfo(
            activity_name="old",
            display_name="Old",
            started_at=old_start,
            duration_seconds=300,
        )
        tracker1._cooldowns["old"] = expired
        tracker1._save()

        # Create new tracker - should not load expired
        tracker2 = CooldownTracker(data_path=path)
        assert not tracker2.is_on_cooldown("old")


class TestActivityCooldowns:
    """Tests for ACTIVITY_COOLDOWNS definitions."""

    def test_payphone_hit_cooldown(self):
        """Test Payphone Hit has 20 minute cooldown."""
        assert ACTIVITY_COOLDOWNS["payphone_hit"] == 1200  # 20 minutes

    def test_vip_work_cooldowns(self):
        """Test VIP work activities have 5 minute cooldowns."""
        vip_activities = ["headhunter", "sightseer", "hostile_takeover"]
        for activity in vip_activities:
            assert ACTIVITY_COOLDOWNS[activity] == 300  # 5 minutes

    def test_client_job_cooldowns(self):
        """Test client jobs have 5 minute cooldowns."""
        client_jobs = ["robbery_in_progress", "data_sweep", "targeted_data", "diamond_shopping"]
        for job in client_jobs:
            assert ACTIVITY_COOLDOWNS[job] == 300  # 5 minutes
