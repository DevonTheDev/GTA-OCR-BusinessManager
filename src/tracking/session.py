"""Session tracking for GTA Business Manager."""

from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from ..utils.logging import get_logger


logger = get_logger("tracking.session")


@dataclass
class SessionStats:
    """Statistics for a play session."""

    started_at: datetime = field(default_factory=datetime.now)
    start_money: int = 0
    current_money: int = 0
    total_earnings: int = 0
    activities_completed: int = 0
    missions_passed: int = 0
    missions_failed: int = 0
    sells_completed: int = 0
    time_in_missions: float = 0  # seconds
    time_idle: float = 0  # seconds

    @property
    def duration_seconds(self) -> float:
        """Get session duration in seconds."""
        return (datetime.now() - self.started_at).total_seconds()

    @property
    def earnings_per_hour(self) -> float:
        """Calculate earnings per hour."""
        hours = self.duration_seconds / 3600
        return self.total_earnings / hours if hours > 0 else 0

    @property
    def mission_success_rate(self) -> float:
        """Calculate mission success rate."""
        total = self.missions_passed + self.missions_failed
        return self.missions_passed / total if total > 0 else 0


class SessionTracker:
    """Tracks current play session statistics."""

    def __init__(self):
        """Initialize session tracker."""
        self._stats: Optional[SessionStats] = None
        self._is_active = False

    def start_session(self, start_money: int = 0) -> SessionStats:
        """Start a new tracking session.

        Args:
            start_money: Money at session start

        Returns:
            New SessionStats instance
        """
        self._stats = SessionStats(
            started_at=datetime.now(),
            start_money=start_money,
            current_money=start_money,
        )
        self._is_active = True
        logger.info(f"Session started with ${start_money:,}")
        return self._stats

    def end_session(self) -> Optional[SessionStats]:
        """End the current session.

        Returns:
            Final session stats, or None if no session active
        """
        if not self._is_active or not self._stats:
            return None

        self._is_active = False
        logger.info(
            f"Session ended - Duration: {self._stats.duration_seconds / 60:.1f}min, "
            f"Earnings: ${self._stats.total_earnings:,}"
        )
        return self._stats

    def update_money(self, new_money: int) -> int:
        """Update current money and track earnings.

        Args:
            new_money: New money value

        Returns:
            Change amount (can be negative)
        """
        if not self._stats:
            return 0

        change = new_money - self._stats.current_money
        self._stats.current_money = new_money

        if change > 0:
            self._stats.total_earnings += change

        return change

    def record_activity_complete(
        self, success: bool, earnings: int = 0, is_sell: bool = False
    ) -> None:
        """Record a completed activity.

        Args:
            success: Whether the activity succeeded
            earnings: Money earned (if known)
            is_sell: Whether this was a sell mission
        """
        if not self._stats:
            return

        self._stats.activities_completed += 1

        if success:
            self._stats.missions_passed += 1
        else:
            self._stats.missions_failed += 1

        if is_sell and success:
            self._stats.sells_completed += 1

    def add_mission_time(self, seconds: float) -> None:
        """Add time spent in missions.

        Args:
            seconds: Time to add
        """
        if self._stats:
            self._stats.time_in_missions += seconds

    def add_idle_time(self, seconds: float) -> None:
        """Add idle time.

        Args:
            seconds: Time to add
        """
        if self._stats:
            self._stats.time_idle += seconds

    @property
    def is_active(self) -> bool:
        """Check if a session is active."""
        return self._is_active

    @property
    def stats(self) -> Optional[SessionStats]:
        """Get current session stats."""
        return self._stats

    @property
    def duration_seconds(self) -> float:
        """Get current session duration."""
        if self._stats:
            return self._stats.duration_seconds
        return 0

    @property
    def total_earnings(self) -> int:
        """Get total earnings this session."""
        if self._stats:
            return self._stats.total_earnings
        return 0
