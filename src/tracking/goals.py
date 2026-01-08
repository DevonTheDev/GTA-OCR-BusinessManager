"""Session goal tracking for GTA Business Manager."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum, auto
import json
from pathlib import Path

from ..utils.logging import get_logger

logger = get_logger("tracking.goals")


class GoalType(Enum):
    """Types of session goals."""

    EARNINGS = auto()  # Target total earnings
    ACTIVITIES = auto()  # Target number of activities completed
    TIME = auto()  # Target session duration


@dataclass
class SessionGoal:
    """A session goal with target and progress tracking."""

    goal_type: GoalType
    target_value: int  # Target amount (dollars, activities, or minutes)
    display_name: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_value: int = 0
    completed_at: Optional[datetime] = None

    def __post_init__(self):
        """Set default display name based on goal type."""
        if not self.display_name:
            if self.goal_type == GoalType.EARNINGS:
                self.display_name = f"Earn ${self.target_value:,}"
            elif self.goal_type == GoalType.ACTIVITIES:
                self.display_name = f"Complete {self.target_value} activities"
            elif self.goal_type == GoalType.TIME:
                hours = self.target_value // 60
                mins = self.target_value % 60
                if hours > 0:
                    self.display_name = f"Play for {hours}h {mins}m"
                else:
                    self.display_name = f"Play for {mins} minutes"

    @property
    def progress(self) -> float:
        """Get progress as 0.0 to 1.0."""
        if self.target_value <= 0:
            return 1.0
        return min(1.0, self.current_value / self.target_value)

    @property
    def progress_percent(self) -> int:
        """Get progress as percentage (0-100)."""
        return int(self.progress * 100)

    @property
    def remaining(self) -> int:
        """Get remaining value to reach goal."""
        return max(0, self.target_value - self.current_value)

    @property
    def is_complete(self) -> bool:
        """Check if goal is complete."""
        return self.current_value >= self.target_value

    @property
    def remaining_formatted(self) -> str:
        """Get remaining value as formatted string."""
        remaining = self.remaining
        if remaining <= 0:
            return "Complete!"

        if self.goal_type == GoalType.EARNINGS:
            if remaining >= 1_000_000:
                return f"${remaining / 1_000_000:.1f}M to go"
            elif remaining >= 1_000:
                return f"${remaining / 1_000:.0f}K to go"
            else:
                return f"${remaining:,} to go"
        elif self.goal_type == GoalType.ACTIVITIES:
            return f"{remaining} more to go"
        elif self.goal_type == GoalType.TIME:
            hours = remaining // 60
            mins = remaining % 60
            if hours > 0:
                return f"{hours}h {mins}m to go"
            else:
                return f"{mins}m to go"

        return f"{remaining} to go"

    @property
    def elapsed_time(self) -> timedelta:
        """Get time elapsed since goal started."""
        now = datetime.now(timezone.utc)
        if self.started_at.tzinfo is None:
            started = self.started_at.replace(tzinfo=timezone.utc)
        else:
            started = self.started_at
        return now - started

    @property
    def estimated_completion_time(self) -> Optional[timedelta]:
        """Estimate time remaining to complete goal.

        Returns:
            Estimated time remaining, or None if no progress yet
        """
        if self.is_complete:
            return timedelta(0)

        elapsed = self.elapsed_time.total_seconds()
        if elapsed <= 0 or self.current_value <= 0:
            return None

        # Calculate rate
        rate = self.current_value / elapsed  # units per second

        if rate <= 0:
            return None

        remaining = self.remaining
        estimated_seconds = remaining / rate

        return timedelta(seconds=estimated_seconds)

    def update(self, new_value: int) -> bool:
        """Update current progress value.

        Args:
            new_value: New current value

        Returns:
            True if goal was just completed
        """
        was_complete = self.is_complete
        self.current_value = new_value

        if self.is_complete and not was_complete:
            self.completed_at = datetime.now(timezone.utc)
            return True

        return False

    def add_progress(self, amount: int) -> bool:
        """Add to current progress.

        Args:
            amount: Amount to add

        Returns:
            True if goal was just completed
        """
        return self.update(self.current_value + amount)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "goal_type": self.goal_type.name,
            "target_value": self.target_value,
            "display_name": self.display_name,
            "started_at": self.started_at.isoformat(),
            "current_value": self.current_value,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionGoal":
        """Create from dictionary."""
        started_at = datetime.fromisoformat(data["started_at"])
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        completed_at = None
        if data.get("completed_at"):
            completed_at = datetime.fromisoformat(data["completed_at"])
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)

        return cls(
            goal_type=GoalType[data["goal_type"]],
            target_value=data["target_value"],
            display_name=data["display_name"],
            started_at=started_at,
            current_value=data["current_value"],
            completed_at=completed_at,
        )


# Preset goals for quick selection
PRESET_GOALS = {
    "quick_500k": SessionGoal(GoalType.EARNINGS, 500_000, "Quick 500K"),
    "million_grind": SessionGoal(GoalType.EARNINGS, 1_000_000, "Million Dollar Grind"),
    "big_session": SessionGoal(GoalType.EARNINGS, 2_500_000, "Big Session (2.5M)"),
    "cayo_run": SessionGoal(GoalType.EARNINGS, 5_000_000, "Cayo Run (5M)"),

    "5_activities": SessionGoal(GoalType.ACTIVITIES, 5, "5 Activities"),
    "10_activities": SessionGoal(GoalType.ACTIVITIES, 10, "10 Activities"),
    "20_activities": SessionGoal(GoalType.ACTIVITIES, 20, "20 Activities"),

    "1_hour": SessionGoal(GoalType.TIME, 60, "1 Hour Session"),
    "2_hours": SessionGoal(GoalType.TIME, 120, "2 Hour Session"),
    "4_hours": SessionGoal(GoalType.TIME, 240, "4 Hour Session"),
}


class GoalTracker:
    """Tracks session goals and progress."""

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize goal tracker.

        Args:
            data_path: Path to save goal data. If None, goals won't persist.
        """
        self._data_path = data_path
        self._current_goal: Optional[SessionGoal] = None
        self._completed_goals: list[SessionGoal] = []
        self._on_goal_complete: list[callable] = []
        self._load()
        logger.info("Goal tracker initialized")

    def _load(self) -> None:
        """Load goals from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("current_goal"):
                self._current_goal = SessionGoal.from_dict(data["current_goal"])

            for goal_data in data.get("completed_goals", []):
                self._completed_goals.append(SessionGoal.from_dict(goal_data))

            logger.debug(f"Loaded goal tracker state")
        except Exception as e:
            logger.error(f"Failed to load goals: {e}")

    def _save(self) -> None:
        """Save goals to file."""
        if not self._data_path:
            return

        try:
            data = {
                "current_goal": self._current_goal.to_dict() if self._current_goal else None,
                "completed_goals": [g.to_dict() for g in self._completed_goals[-10:]],  # Keep last 10
            }

            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save goals: {e}")

    def set_goal(
        self,
        goal_type: GoalType,
        target_value: int,
        display_name: Optional[str] = None,
    ) -> SessionGoal:
        """Set a new session goal.

        Args:
            goal_type: Type of goal
            target_value: Target value to reach
            display_name: Custom display name (optional)

        Returns:
            The created SessionGoal
        """
        if self._current_goal and not self._current_goal.is_complete:
            # Move incomplete goal to completed with current progress
            self._completed_goals.append(self._current_goal)

        self._current_goal = SessionGoal(
            goal_type=goal_type,
            target_value=target_value,
            display_name=display_name or "",
        )
        self._save()

        logger.info(f"New goal set: {self._current_goal.display_name}")
        return self._current_goal

    def set_preset_goal(self, preset_name: str) -> Optional[SessionGoal]:
        """Set a goal from presets.

        Args:
            preset_name: Name of the preset goal

        Returns:
            The created SessionGoal, or None if preset not found
        """
        preset = PRESET_GOALS.get(preset_name)
        if not preset:
            logger.warning(f"Preset goal not found: {preset_name}")
            return None

        return self.set_goal(
            preset.goal_type,
            preset.target_value,
            preset.display_name,
        )

    def update_earnings(self, total_earnings: int) -> bool:
        """Update progress for earnings goal.

        Args:
            total_earnings: Current total session earnings

        Returns:
            True if goal was just completed
        """
        if not self._current_goal or self._current_goal.goal_type != GoalType.EARNINGS:
            return False

        completed = self._current_goal.update(total_earnings)

        if completed:
            self._on_goal_completed()

        self._save()
        return completed

    def update_activities(self, total_activities: int) -> bool:
        """Update progress for activities goal.

        Args:
            total_activities: Current total activities completed

        Returns:
            True if goal was just completed
        """
        if not self._current_goal or self._current_goal.goal_type != GoalType.ACTIVITIES:
            return False

        completed = self._current_goal.update(total_activities)

        if completed:
            self._on_goal_completed()

        self._save()
        return completed

    def update_time(self, session_minutes: int) -> bool:
        """Update progress for time goal.

        Args:
            session_minutes: Current session duration in minutes

        Returns:
            True if goal was just completed
        """
        if not self._current_goal or self._current_goal.goal_type != GoalType.TIME:
            return False

        completed = self._current_goal.update(session_minutes)

        if completed:
            self._on_goal_completed()

        self._save()
        return completed

    def _on_goal_completed(self) -> None:
        """Handle goal completion."""
        if not self._current_goal:
            return

        logger.info(f"Goal completed: {self._current_goal.display_name}")

        # Notify callbacks
        for callback in self._on_goal_complete:
            try:
                callback(self._current_goal)
            except Exception as e:
                logger.error(f"Goal complete callback error: {e}")

        # Move to completed list
        self._completed_goals.append(self._current_goal)

    def clear_goal(self) -> None:
        """Clear the current goal without completing it."""
        if self._current_goal:
            logger.info(f"Goal cleared: {self._current_goal.display_name}")
            self._current_goal = None
            self._save()

    def on_goal_complete(self, callback: callable) -> None:
        """Register a callback for goal completion.

        Args:
            callback: Function to call when goal is completed
        """
        self._on_goal_complete.append(callback)

    @property
    def current_goal(self) -> Optional[SessionGoal]:
        """Get the current goal."""
        return self._current_goal

    @property
    def has_goal(self) -> bool:
        """Check if there's an active goal."""
        return self._current_goal is not None

    @property
    def completed_goals(self) -> list[SessionGoal]:
        """Get list of completed goals."""
        return self._completed_goals.copy()

    @property
    def goals_completed_count(self) -> int:
        """Get total number of goals completed."""
        return len([g for g in self._completed_goals if g.is_complete])


# Singleton instance
_tracker: Optional[GoalTracker] = None


def get_goal_tracker(data_path: Optional[Path] = None) -> GoalTracker:
    """Get the global goal tracker instance.

    Args:
        data_path: Path to save goal data (only used on first call)

    Returns:
        GoalTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = GoalTracker(data_path)
    return _tracker
