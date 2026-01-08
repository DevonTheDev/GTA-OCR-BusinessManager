"""Tests for session goal tracking."""

import pytest
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.tracking.goals import (
    GoalTracker,
    GoalType,
    SessionGoal,
    PRESET_GOALS,
)


class TestSessionGoal:
    """Tests for SessionGoal dataclass."""

    def test_earnings_goal_creation(self):
        """Test creating an earnings goal."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)

        assert goal.goal_type == GoalType.EARNINGS
        assert goal.target_value == 1_000_000
        assert "1,000,000" in goal.display_name

    def test_activities_goal_creation(self):
        """Test creating an activities goal."""
        goal = SessionGoal(GoalType.ACTIVITIES, 10)

        assert goal.goal_type == GoalType.ACTIVITIES
        assert goal.target_value == 10
        assert "10" in goal.display_name
        assert "activities" in goal.display_name.lower()

    def test_time_goal_creation_minutes(self):
        """Test creating a time goal with minutes."""
        goal = SessionGoal(GoalType.TIME, 30)

        assert goal.goal_type == GoalType.TIME
        assert goal.target_value == 30
        assert "30" in goal.display_name

    def test_time_goal_creation_hours(self):
        """Test creating a time goal with hours."""
        goal = SessionGoal(GoalType.TIME, 120)  # 2 hours

        assert "2h" in goal.display_name

    def test_custom_display_name(self):
        """Test custom display name."""
        goal = SessionGoal(GoalType.EARNINGS, 500_000, "Quick Buck")

        assert goal.display_name == "Quick Buck"

    def test_progress_calculation(self):
        """Test progress calculation."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)
        goal.current_value = 500_000

        assert goal.progress == 0.5
        assert goal.progress_percent == 50

    def test_progress_caps_at_one(self):
        """Test progress doesn't exceed 1.0."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)
        goal.current_value = 2_000_000

        assert goal.progress == 1.0
        assert goal.progress_percent == 100

    def test_remaining_calculation(self):
        """Test remaining value calculation."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)
        goal.current_value = 750_000

        assert goal.remaining == 250_000

    def test_remaining_never_negative(self):
        """Test remaining never goes negative."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)
        goal.current_value = 1_500_000

        assert goal.remaining == 0

    def test_is_complete(self):
        """Test completion detection."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)

        goal.current_value = 999_999
        assert not goal.is_complete

        goal.current_value = 1_000_000
        assert goal.is_complete

        goal.current_value = 1_500_000
        assert goal.is_complete

    def test_remaining_formatted_earnings(self):
        """Test formatted remaining for earnings."""
        goal = SessionGoal(GoalType.EARNINGS, 2_000_000)

        goal.current_value = 500_000
        formatted = goal.remaining_formatted
        assert "1.5M" in formatted or "1,500" in formatted

    def test_remaining_formatted_complete(self):
        """Test formatted remaining when complete."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)
        goal.current_value = 1_000_000

        assert goal.remaining_formatted == "Complete!"

    def test_update_returns_completion(self):
        """Test update returns True when goal is completed."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)

        result1 = goal.update(500_000)
        assert not result1

        result2 = goal.update(1_000_000)
        assert result2  # Just completed

        result3 = goal.update(1_500_000)
        assert not result3  # Already complete

    def test_update_sets_completed_at(self):
        """Test update sets completed_at timestamp."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)

        assert goal.completed_at is None

        goal.update(1_000_000)
        assert goal.completed_at is not None

    def test_add_progress(self):
        """Test adding to progress."""
        goal = SessionGoal(GoalType.ACTIVITIES, 10)

        goal.add_progress(3)
        assert goal.current_value == 3

        goal.add_progress(2)
        assert goal.current_value == 5

    def test_to_dict(self):
        """Test serialization to dictionary."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000, "Test Goal")
        goal.current_value = 500_000

        data = goal.to_dict()
        assert data["goal_type"] == "EARNINGS"
        assert data["target_value"] == 1_000_000
        assert data["display_name"] == "Test Goal"
        assert data["current_value"] == 500_000

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "goal_type": "ACTIVITIES",
            "target_value": 20,
            "display_name": "Activity Goal",
            "started_at": now.isoformat(),
            "current_value": 8,
            "completed_at": None,
        }

        goal = SessionGoal.from_dict(data)
        assert goal.goal_type == GoalType.ACTIVITIES
        assert goal.target_value == 20
        assert goal.display_name == "Activity Goal"
        assert goal.current_value == 8

    def test_estimated_completion_time(self):
        """Test ETA calculation."""
        # Create goal started 10 minutes ago with 50% progress
        start = datetime.now(timezone.utc) - timedelta(minutes=10)
        goal = SessionGoal(
            GoalType.EARNINGS,
            1_000_000,
            started_at=start,
        )
        goal.current_value = 500_000

        eta = goal.estimated_completion_time
        assert eta is not None
        # Should be approximately 10 more minutes
        assert 8 * 60 <= eta.total_seconds() <= 12 * 60

    def test_estimated_completion_time_no_progress(self):
        """Test ETA with no progress returns None."""
        goal = SessionGoal(GoalType.EARNINGS, 1_000_000)

        assert goal.estimated_completion_time is None


class TestGoalTracker:
    """Tests for GoalTracker class."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create a GoalTracker with temp storage."""
        return GoalTracker(data_path=tmp_path / "goals.json")

    @pytest.fixture
    def memory_tracker(self):
        """Create a GoalTracker without persistence."""
        return GoalTracker(data_path=None)

    def test_set_goal(self, tracker):
        """Test setting a goal."""
        goal = tracker.set_goal(GoalType.EARNINGS, 1_000_000, "Test Goal")

        assert goal.goal_type == GoalType.EARNINGS
        assert goal.target_value == 1_000_000
        assert tracker.has_goal
        assert tracker.current_goal == goal

    def test_set_preset_goal(self, tracker):
        """Test setting a preset goal."""
        goal = tracker.set_preset_goal("million_grind")

        assert goal is not None
        assert goal.target_value == 1_000_000
        assert tracker.has_goal

    def test_set_preset_goal_invalid(self, tracker):
        """Test setting invalid preset returns None."""
        goal = tracker.set_preset_goal("nonexistent")

        assert goal is None
        assert not tracker.has_goal

    def test_update_earnings(self, tracker):
        """Test updating earnings progress."""
        tracker.set_goal(GoalType.EARNINGS, 1_000_000)

        completed = tracker.update_earnings(500_000)
        assert not completed
        assert tracker.current_goal.current_value == 500_000

        completed = tracker.update_earnings(1_000_000)
        assert completed

    def test_update_activities(self, tracker):
        """Test updating activities progress."""
        tracker.set_goal(GoalType.ACTIVITIES, 10)

        completed = tracker.update_activities(5)
        assert not completed

        completed = tracker.update_activities(10)
        assert completed

    def test_update_time(self, tracker):
        """Test updating time progress."""
        tracker.set_goal(GoalType.TIME, 60)

        completed = tracker.update_time(30)
        assert not completed

        completed = tracker.update_time(60)
        assert completed

    def test_update_wrong_type(self, tracker):
        """Test updating wrong goal type does nothing."""
        tracker.set_goal(GoalType.EARNINGS, 1_000_000)

        # Try updating activities on earnings goal
        completed = tracker.update_activities(100)
        assert not completed
        assert tracker.current_goal.current_value == 0

    def test_clear_goal(self, tracker):
        """Test clearing goal."""
        tracker.set_goal(GoalType.EARNINGS, 1_000_000)
        assert tracker.has_goal

        tracker.clear_goal()
        assert not tracker.has_goal
        assert tracker.current_goal is None

    def test_goal_complete_callback(self, tracker):
        """Test goal completion callback."""
        completed_goals = []

        def on_complete(goal):
            completed_goals.append(goal)

        tracker.on_goal_complete(on_complete)
        tracker.set_goal(GoalType.EARNINGS, 1_000_000)
        tracker.update_earnings(1_000_000)

        assert len(completed_goals) == 1
        assert completed_goals[0].target_value == 1_000_000

    def test_completed_goals_list(self, tracker):
        """Test completed goals are tracked."""
        tracker.set_goal(GoalType.EARNINGS, 500_000)
        tracker.update_earnings(500_000)

        assert len(tracker.completed_goals) == 1

    def test_persistence(self, tmp_path):
        """Test goals persist across tracker instances."""
        path = tmp_path / "goals.json"

        # Create tracker and set goal
        tracker1 = GoalTracker(data_path=path)
        tracker1.set_goal(GoalType.EARNINGS, 1_000_000)
        tracker1.update_earnings(500_000)

        # Create new tracker with same path
        tracker2 = GoalTracker(data_path=path)

        assert tracker2.has_goal
        assert tracker2.current_goal.target_value == 1_000_000
        assert tracker2.current_goal.current_value == 500_000

    def test_replacing_goal_saves_incomplete(self, tracker):
        """Test replacing goal moves incomplete goal to completed."""
        tracker.set_goal(GoalType.EARNINGS, 1_000_000)
        tracker.update_earnings(300_000)

        # Set new goal
        tracker.set_goal(GoalType.ACTIVITIES, 10)

        # Old incomplete goal should be in completed list
        assert len(tracker.completed_goals) == 1
        assert tracker.completed_goals[0].current_value == 300_000


class TestPresetGoals:
    """Tests for preset goal definitions."""

    def test_earnings_presets_exist(self):
        """Test earnings preset goals exist."""
        earnings_presets = [n for n, g in PRESET_GOALS.items() if g.goal_type == GoalType.EARNINGS]
        assert len(earnings_presets) >= 3

    def test_activities_presets_exist(self):
        """Test activities preset goals exist."""
        activities_presets = [n for n, g in PRESET_GOALS.items() if g.goal_type == GoalType.ACTIVITIES]
        assert len(activities_presets) >= 2

    def test_time_presets_exist(self):
        """Test time preset goals exist."""
        time_presets = [n for n, g in PRESET_GOALS.items() if g.goal_type == GoalType.TIME]
        assert len(time_presets) >= 2

    def test_million_grind_preset(self):
        """Test million dollar grind preset values."""
        preset = PRESET_GOALS["million_grind"]
        assert preset.goal_type == GoalType.EARNINGS
        assert preset.target_value == 1_000_000

    def test_one_hour_preset(self):
        """Test 1 hour session preset."""
        preset = PRESET_GOALS["1_hour"]
        assert preset.goal_type == GoalType.TIME
        assert preset.target_value == 60
