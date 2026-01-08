"""Activity tracking for GTA Business Manager."""

from collections import deque
from datetime import datetime, timezone
from typing import Optional, List
from dataclasses import dataclass, field

from ..game.activities import Activity, ActivityType
from ..constants import TRACKING
from ..utils.logging import get_logger


logger = get_logger("tracking.activity")


class ActivityTracker:
    """Tracks individual activities (missions, sells, etc.)."""

    def __init__(self):
        """Initialize activity tracker."""
        self._current_activity: Optional[Activity] = None
        self._completed_activities: deque[Activity] = deque(maxlen=TRACKING.MAX_ACTIVITY_HISTORY)

    def start_activity(
        self,
        activity_type: ActivityType,
        name: str = "",
        expected_earnings: int = 0,
        business_type: str = "",
    ) -> Activity:
        """Start tracking a new activity.

        Args:
            activity_type: Type of activity
            name: Activity name
            expected_earnings: Expected payout
            business_type: Associated business (for sell missions)

        Returns:
            New Activity instance
        """
        # End any current activity first
        if self._current_activity and self._current_activity.is_active:
            self.cancel_activity()

        self._current_activity = Activity(
            activity_type=activity_type,
            name=name,
            expected_earnings=expected_earnings,
            business_type=business_type,
        )

        logger.info(f"Started activity: {activity_type.name} - {name}")
        return self._current_activity

    def complete_activity(self, success: bool, earnings: int = 0) -> Optional[Activity]:
        """Complete the current activity.

        Args:
            success: Whether activity succeeded
            earnings: Actual earnings

        Returns:
            Completed Activity or None
        """
        if not self._current_activity:
            return None

        self._current_activity.complete(success, earnings)
        completed = self._current_activity

        # Add to history (deque auto-removes oldest when full)
        self._completed_activities.append(completed)

        logger.info(
            f"Completed activity: {completed.activity_type.name} - "
            f"{'Success' if success else 'Failed'}, ${earnings:,}"
        )

        self._current_activity = None
        return completed

    def cancel_activity(self) -> Optional[Activity]:
        """Cancel the current activity without completing it.

        Returns:
            Cancelled Activity or None
        """
        if not self._current_activity:
            return None

        cancelled = self._current_activity
        cancelled.ended_at = datetime.now(timezone.utc)
        cancelled.success = False
        cancelled.notes = "Cancelled"

        logger.info(f"Cancelled activity: {cancelled.activity_type.name}")

        self._current_activity = None
        return cancelled

    @property
    def current_activity(self) -> Optional[Activity]:
        """Get the current activity being tracked."""
        return self._current_activity

    @property
    def is_tracking(self) -> bool:
        """Check if currently tracking an activity."""
        return self._current_activity is not None and self._current_activity.is_active

    @property
    def completed_activities(self) -> List[Activity]:
        """Get list of completed activities."""
        return list(self._completed_activities)

    def get_recent_activities(self, count: int = 10) -> List[Activity]:
        """Get recent completed activities.

        Args:
            count: Number of activities to return

        Returns:
            List of recent activities (newest first)
        """
        return list(reversed(self._completed_activities[-count:]))

    def get_stats_by_type(self, activity_type: ActivityType) -> dict:
        """Get statistics for a specific activity type.

        Args:
            activity_type: Type to get stats for

        Returns:
            Dict with count, success_rate, avg_earnings, avg_duration
        """
        activities = [
            a for a in self._completed_activities
            if a.activity_type == activity_type
        ]

        if not activities:
            return {
                "count": 0,
                "success_rate": 0.0,
                "avg_earnings": 0,
                "avg_duration": 0,
            }

        successes = sum(1 for a in activities if a.success)
        total_earnings = sum(a.earnings for a in activities if a.success)
        total_duration = sum(a.duration_seconds for a in activities)

        return {
            "count": len(activities),
            "success_rate": successes / len(activities),
            "avg_earnings": total_earnings // successes if successes else 0,
            "avg_duration": total_duration / len(activities),
        }

    def clear_history(self) -> None:
        """Clear activity history."""
        self._completed_activities.clear()
        logger.info("Activity history cleared")
