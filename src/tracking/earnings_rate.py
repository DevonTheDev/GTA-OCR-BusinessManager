"""Real-time earnings rate tracking for GTA Business Manager."""

from collections import deque
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
import statistics

from ..utils.logging import get_logger

logger = get_logger("tracking.earnings_rate")


@dataclass
class EarningEvent:
    """Record of a single earning event."""

    amount: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""


class EarningsRateTracker:
    """Tracks earnings rate in real-time with multiple time windows."""

    def __init__(self, max_events: int = 1000):
        """Initialize earnings rate tracker.

        Args:
            max_events: Maximum number of events to keep in history
        """
        self._events: deque[EarningEvent] = deque(maxlen=max_events)
        self._session_start: Optional[datetime] = None
        self._session_earnings: int = 0

        # Time windows for rate calculation (in seconds)
        self._windows = {
            "5min": 300,
            "15min": 900,
            "30min": 1800,
            "1hour": 3600,
            "session": None,  # Full session
        }

        logger.info("Earnings rate tracker initialized")

    def start_session(self) -> None:
        """Start a new tracking session."""
        self._session_start = datetime.now(timezone.utc)
        self._session_earnings = 0
        self._events.clear()
        logger.info("Earnings rate session started")

    def record_earning(self, amount: int, source: str = "") -> None:
        """Record an earning event.

        Args:
            amount: Amount earned
            source: Source of earning (optional)
        """
        if amount <= 0:
            return

        event = EarningEvent(amount=amount, source=source)
        self._events.append(event)
        self._session_earnings += amount

        logger.debug(f"Recorded earning: ${amount:,} from {source or 'unknown'}")

    def get_rate(self, window: str = "session") -> float:
        """Get earnings rate in $/hour for a time window.

        Args:
            window: Time window name ("5min", "15min", "30min", "1hour", "session")

        Returns:
            Earnings per hour for the window
        """
        if window not in self._windows:
            window = "session"

        window_seconds = self._windows[window]

        if window_seconds is None:
            # Full session rate
            return self._get_session_rate()
        else:
            return self._get_window_rate(window_seconds)

    def _get_session_rate(self) -> float:
        """Get earnings rate for the full session."""
        if not self._session_start:
            return 0.0

        now = datetime.now(timezone.utc)
        duration = (now - self._session_start).total_seconds()

        if duration <= 0:
            return 0.0

        hours = duration / 3600
        return self._session_earnings / hours

    def _get_window_rate(self, window_seconds: int) -> float:
        """Get earnings rate for a specific time window.

        Args:
            window_seconds: Window size in seconds

        Returns:
            Earnings per hour for events within the window
        """
        if not self._events:
            return 0.0

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)

        # Sum earnings within the window
        window_earnings = 0
        for event in self._events:
            if event.timestamp.tzinfo is None:
                event_time = event.timestamp.replace(tzinfo=timezone.utc)
            else:
                event_time = event.timestamp

            if event_time >= cutoff:
                window_earnings += event.amount

        # Calculate rate (extrapolate to per hour)
        hours = window_seconds / 3600
        return window_earnings / hours

    def get_all_rates(self) -> dict[str, float]:
        """Get earnings rates for all time windows.

        Returns:
            Dictionary of window name to $/hour rate
        """
        rates = {}
        for window in self._windows.keys():
            rates[window] = self.get_rate(window)
        return rates

    def get_trend(self) -> str:
        """Get the earnings trend direction.

        Compares recent rate (5min) to session average.

        Returns:
            "up", "down", or "stable"
        """
        recent = self.get_rate("5min")
        session = self.get_rate("session")

        if session == 0:
            return "stable"

        ratio = recent / session

        if ratio > 1.2:
            return "up"
        elif ratio < 0.8:
            return "down"
        else:
            return "stable"

    def get_average_earning(self, window_seconds: Optional[int] = None) -> float:
        """Get average earning amount per event.

        Args:
            window_seconds: Only count events within this window (None = all)

        Returns:
            Average earning amount
        """
        if not self._events:
            return 0.0

        now = datetime.now(timezone.utc)

        amounts = []
        for event in self._events:
            if window_seconds is not None:
                if event.timestamp.tzinfo is None:
                    event_time = event.timestamp.replace(tzinfo=timezone.utc)
                else:
                    event_time = event.timestamp

                cutoff = now - timedelta(seconds=window_seconds)
                if event_time < cutoff:
                    continue

            amounts.append(event.amount)

        if not amounts:
            return 0.0

        return statistics.mean(amounts)

    def get_recent_events(self, limit: int = 10) -> list[EarningEvent]:
        """Get most recent earning events.

        Args:
            limit: Maximum events to return

        Returns:
            List of recent EarningEvent objects
        """
        events = list(self._events)
        events.reverse()  # Most recent first
        return events[:limit]

    def get_time_to_goal(self, target: int) -> Optional[timedelta]:
        """Estimate time to reach an earnings goal.

        Args:
            target: Target earnings amount

        Returns:
            Estimated time, or None if no earnings data
        """
        remaining = target - self._session_earnings
        if remaining <= 0:
            return timedelta(0)

        rate = self.get_rate("session")
        if rate <= 0:
            return None

        hours = remaining / rate
        return timedelta(hours=hours)

    @property
    def session_earnings(self) -> int:
        """Get total session earnings."""
        return self._session_earnings

    @property
    def session_duration(self) -> Optional[timedelta]:
        """Get current session duration."""
        if not self._session_start:
            return None
        return datetime.now(timezone.utc) - self._session_start

    @property
    def event_count(self) -> int:
        """Get number of earning events."""
        return len(self._events)


# Singleton instance
_tracker: Optional[EarningsRateTracker] = None


def get_earnings_rate_tracker() -> EarningsRateTracker:
    """Get the global earnings rate tracker instance.

    Returns:
        EarningsRateTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = EarningsRateTracker()
    return _tracker
