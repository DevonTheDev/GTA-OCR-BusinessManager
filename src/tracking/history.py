"""Session history and lifetime statistics tracking.

Players love seeing their progress over time - this module provides
historical session data and lifetime achievement tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from typing import Optional
from pathlib import Path
import json

from ..utils.logging import get_logger

logger = get_logger("tracking.history")


@dataclass
class SessionRecord:
    """Record of a completed play session."""

    session_id: str
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    start_money: int
    end_money: int
    earnings: int
    activities_completed: int
    activities_failed: int
    best_activity: str = ""
    best_activity_earnings: int = 0

    @property
    def earnings_per_hour(self) -> float:
        """Calculate earnings per hour for this session."""
        hours = self.duration_seconds / 3600
        return self.earnings / hours if hours > 0 else 0

    @property
    def duration_formatted(self) -> str:
        """Get formatted duration string."""
        hours = self.duration_seconds // 3600
        minutes = (self.duration_seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def success_rate(self) -> float:
        """Calculate activity success rate."""
        total = self.activities_completed + self.activities_failed
        return self.activities_completed / total if total > 0 else 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": self.duration_seconds,
            "start_money": self.start_money,
            "end_money": self.end_money,
            "earnings": self.earnings,
            "activities_completed": self.activities_completed,
            "activities_failed": self.activities_failed,
            "best_activity": self.best_activity,
            "best_activity_earnings": self.best_activity_earnings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        """Create from dictionary."""
        start_time = datetime.fromisoformat(data["start_time"])
        end_time = datetime.fromisoformat(data["end_time"])

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        return cls(
            session_id=data["session_id"],
            start_time=start_time,
            end_time=end_time,
            duration_seconds=data["duration_seconds"],
            start_money=data["start_money"],
            end_money=data["end_money"],
            earnings=data["earnings"],
            activities_completed=data["activities_completed"],
            activities_failed=data["activities_failed"],
            best_activity=data.get("best_activity", ""),
            best_activity_earnings=data.get("best_activity_earnings", 0),
        )


@dataclass
class LifetimeStats:
    """Lifetime statistics across all sessions."""

    total_earnings: int = 0
    total_play_time_seconds: int = 0
    total_sessions: int = 0
    total_activities_completed: int = 0
    total_activities_failed: int = 0

    # Records
    best_session_earnings: int = 0
    best_session_date: Optional[str] = None
    best_hourly_rate: float = 0
    best_hourly_date: Optional[str] = None
    longest_session_seconds: int = 0
    longest_session_date: Optional[str] = None
    highest_balance_seen: int = 0
    highest_balance_date: Optional[str] = None

    # Streaks
    current_daily_streak: int = 0
    best_daily_streak: int = 0
    last_played_date: Optional[str] = None

    # By day of week (0=Monday, 6=Sunday)
    earnings_by_day: dict = field(default_factory=lambda: {str(i): 0 for i in range(7)})
    sessions_by_day: dict = field(default_factory=lambda: {str(i): 0 for i in range(7)})

    @property
    def total_play_time_formatted(self) -> str:
        """Get formatted total play time."""
        hours = self.total_play_time_seconds // 3600
        if hours >= 24:
            days = hours // 24
            remaining_hours = hours % 24
            return f"{days}d {remaining_hours}h"
        minutes = (self.total_play_time_seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    @property
    def average_session_earnings(self) -> float:
        """Get average earnings per session."""
        return self.total_earnings / self.total_sessions if self.total_sessions > 0 else 0

    @property
    def average_session_duration(self) -> float:
        """Get average session duration in seconds."""
        return self.total_play_time_seconds / self.total_sessions if self.total_sessions > 0 else 0

    @property
    def overall_success_rate(self) -> float:
        """Get overall activity success rate."""
        total = self.total_activities_completed + self.total_activities_failed
        return self.total_activities_completed / total if total > 0 else 0

    @property
    def overall_earnings_per_hour(self) -> float:
        """Get overall earnings per hour."""
        hours = self.total_play_time_seconds / 3600
        return self.total_earnings / hours if hours > 0 else 0

    @property
    def favorite_day(self) -> Optional[str]:
        """Get the day with most sessions."""
        if not self.sessions_by_day:
            return None

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        best_day = max(self.sessions_by_day.items(), key=lambda x: x[1])
        day_idx = int(best_day[0])
        return days[day_idx] if best_day[1] > 0 else None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_earnings": self.total_earnings,
            "total_play_time_seconds": self.total_play_time_seconds,
            "total_sessions": self.total_sessions,
            "total_activities_completed": self.total_activities_completed,
            "total_activities_failed": self.total_activities_failed,
            "best_session_earnings": self.best_session_earnings,
            "best_session_date": self.best_session_date,
            "best_hourly_rate": self.best_hourly_rate,
            "best_hourly_date": self.best_hourly_date,
            "longest_session_seconds": self.longest_session_seconds,
            "longest_session_date": self.longest_session_date,
            "highest_balance_seen": self.highest_balance_seen,
            "highest_balance_date": self.highest_balance_date,
            "current_daily_streak": self.current_daily_streak,
            "best_daily_streak": self.best_daily_streak,
            "last_played_date": self.last_played_date,
            "earnings_by_day": self.earnings_by_day,
            "sessions_by_day": self.sessions_by_day,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LifetimeStats":
        """Create from dictionary."""
        return cls(
            total_earnings=data.get("total_earnings", 0),
            total_play_time_seconds=data.get("total_play_time_seconds", 0),
            total_sessions=data.get("total_sessions", 0),
            total_activities_completed=data.get("total_activities_completed", 0),
            total_activities_failed=data.get("total_activities_failed", 0),
            best_session_earnings=data.get("best_session_earnings", 0),
            best_session_date=data.get("best_session_date"),
            best_hourly_rate=data.get("best_hourly_rate", 0),
            best_hourly_date=data.get("best_hourly_date"),
            longest_session_seconds=data.get("longest_session_seconds", 0),
            longest_session_date=data.get("longest_session_date"),
            highest_balance_seen=data.get("highest_balance_seen", 0),
            highest_balance_date=data.get("highest_balance_date"),
            current_daily_streak=data.get("current_daily_streak", 0),
            best_daily_streak=data.get("best_daily_streak", 0),
            last_played_date=data.get("last_played_date"),
            earnings_by_day=data.get("earnings_by_day", {str(i): 0 for i in range(7)}),
            sessions_by_day=data.get("sessions_by_day", {str(i): 0 for i in range(7)}),
        )


@dataclass
class DailyStats:
    """Statistics for a single day."""

    date: str  # ISO format date
    earnings: int = 0
    play_time_seconds: int = 0
    sessions: int = 0
    activities_completed: int = 0

    @property
    def earnings_per_hour(self) -> float:
        """Calculate earnings per hour."""
        hours = self.play_time_seconds / 3600
        return self.earnings / hours if hours > 0 else 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "date": self.date,
            "earnings": self.earnings,
            "play_time_seconds": self.play_time_seconds,
            "sessions": self.sessions,
            "activities_completed": self.activities_completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DailyStats":
        """Create from dictionary."""
        return cls(
            date=data["date"],
            earnings=data.get("earnings", 0),
            play_time_seconds=data.get("play_time_seconds", 0),
            sessions=data.get("sessions", 0),
            activities_completed=data.get("activities_completed", 0),
        )


class SessionHistory:
    """Manages session history and lifetime statistics."""

    MAX_SESSIONS = 100  # Keep last 100 sessions
    MAX_DAILY_STATS = 90  # Keep 90 days of daily stats

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize session history.

        Args:
            data_path: Path to save history data
        """
        self._data_path = data_path
        self._sessions: list[SessionRecord] = []
        self._lifetime = LifetimeStats()
        self._daily_stats: dict[str, DailyStats] = {}
        self._load()
        logger.info("Session history initialized")

    def _load(self) -> None:
        """Load history from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load sessions
            for session_data in data.get("sessions", []):
                self._sessions.append(SessionRecord.from_dict(session_data))

            # Load lifetime stats
            if "lifetime" in data:
                self._lifetime = LifetimeStats.from_dict(data["lifetime"])

            # Load daily stats
            for day_data in data.get("daily_stats", []):
                daily = DailyStats.from_dict(day_data)
                self._daily_stats[daily.date] = daily

            logger.debug(f"Loaded {len(self._sessions)} session records")
        except Exception as e:
            logger.error(f"Failed to load history: {e}")

    def _save(self) -> None:
        """Save history to file."""
        if not self._data_path:
            return

        try:
            # Trim to max sizes
            self._sessions = self._sessions[-self.MAX_SESSIONS:]

            # Trim daily stats to last N days
            if len(self._daily_stats) > self.MAX_DAILY_STATS:
                sorted_dates = sorted(self._daily_stats.keys(), reverse=True)
                self._daily_stats = {
                    d: self._daily_stats[d]
                    for d in sorted_dates[:self.MAX_DAILY_STATS]
                }

            data = {
                "sessions": [s.to_dict() for s in self._sessions],
                "lifetime": self._lifetime.to_dict(),
                "daily_stats": [d.to_dict() for d in self._daily_stats.values()],
            }

            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def record_session(
        self,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
        start_money: int,
        end_money: int,
        activities_completed: int,
        activities_failed: int,
        best_activity: str = "",
        best_activity_earnings: int = 0,
    ) -> SessionRecord:
        """Record a completed session.

        Args:
            session_id: Unique session identifier
            start_time: When session started
            end_time: When session ended
            start_money: Money at session start
            end_money: Money at session end
            activities_completed: Number of activities completed
            activities_failed: Number of activities failed
            best_activity: Name of highest-earning activity
            best_activity_earnings: Earnings from best activity

        Returns:
            The created SessionRecord
        """
        duration = int((end_time - start_time).total_seconds())
        earnings = max(0, end_money - start_money)

        record = SessionRecord(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration,
            start_money=start_money,
            end_money=end_money,
            earnings=earnings,
            activities_completed=activities_completed,
            activities_failed=activities_failed,
            best_activity=best_activity,
            best_activity_earnings=best_activity_earnings,
        )

        self._sessions.append(record)
        self._update_lifetime_stats(record)
        self._update_daily_stats(record)
        self._save()

        logger.info(
            f"Recorded session: {record.duration_formatted}, "
            f"${earnings:,} earned ({record.earnings_per_hour:,.0f}/hr)"
        )

        return record

    def _update_lifetime_stats(self, record: SessionRecord) -> None:
        """Update lifetime stats with a new session."""
        today = date.today().isoformat()

        # Basic totals
        self._lifetime.total_earnings += record.earnings
        self._lifetime.total_play_time_seconds += record.duration_seconds
        self._lifetime.total_sessions += 1
        self._lifetime.total_activities_completed += record.activities_completed
        self._lifetime.total_activities_failed += record.activities_failed

        # Check records
        if record.earnings > self._lifetime.best_session_earnings:
            self._lifetime.best_session_earnings = record.earnings
            self._lifetime.best_session_date = today

        if record.earnings_per_hour > self._lifetime.best_hourly_rate:
            self._lifetime.best_hourly_rate = record.earnings_per_hour
            self._lifetime.best_hourly_date = today

        if record.duration_seconds > self._lifetime.longest_session_seconds:
            self._lifetime.longest_session_seconds = record.duration_seconds
            self._lifetime.longest_session_date = today

        if record.end_money > self._lifetime.highest_balance_seen:
            self._lifetime.highest_balance_seen = record.end_money
            self._lifetime.highest_balance_date = today

        # Update streaks
        if self._lifetime.last_played_date:
            last_date = date.fromisoformat(self._lifetime.last_played_date)
            today_date = date.today()
            days_diff = (today_date - last_date).days

            if days_diff == 1:
                # Consecutive day
                self._lifetime.current_daily_streak += 1
            elif days_diff > 1:
                # Streak broken
                self._lifetime.current_daily_streak = 1
            # If same day, don't change streak
        else:
            self._lifetime.current_daily_streak = 1

        if self._lifetime.current_daily_streak > self._lifetime.best_daily_streak:
            self._lifetime.best_daily_streak = self._lifetime.current_daily_streak

        self._lifetime.last_played_date = today

        # Update by day of week
        day_of_week = str(record.start_time.weekday())
        self._lifetime.earnings_by_day[day_of_week] = (
            self._lifetime.earnings_by_day.get(day_of_week, 0) + record.earnings
        )
        self._lifetime.sessions_by_day[day_of_week] = (
            self._lifetime.sessions_by_day.get(day_of_week, 0) + 1
        )

    def _update_daily_stats(self, record: SessionRecord) -> None:
        """Update daily stats with a new session."""
        day_str = record.start_time.date().isoformat()

        if day_str not in self._daily_stats:
            self._daily_stats[day_str] = DailyStats(date=day_str)

        daily = self._daily_stats[day_str]
        daily.earnings += record.earnings
        daily.play_time_seconds += record.duration_seconds
        daily.sessions += 1
        daily.activities_completed += record.activities_completed

    def update_balance(self, balance: int) -> None:
        """Update highest balance seen.

        Args:
            balance: Current balance
        """
        if balance > self._lifetime.highest_balance_seen:
            self._lifetime.highest_balance_seen = balance
            self._lifetime.highest_balance_date = date.today().isoformat()
            self._save()

    def get_recent_sessions(self, limit: int = 10) -> list[SessionRecord]:
        """Get recent sessions.

        Args:
            limit: Maximum sessions to return

        Returns:
            List of recent sessions, newest first
        """
        return list(reversed(self._sessions[-limit:]))

    def get_sessions_for_date(self, target_date: date) -> list[SessionRecord]:
        """Get sessions for a specific date.

        Args:
            target_date: Date to get sessions for

        Returns:
            List of sessions on that date
        """
        return [
            s for s in self._sessions
            if s.start_time.date() == target_date
        ]

    def get_daily_stats_range(self, days: int = 7) -> list[DailyStats]:
        """Get daily stats for the last N days.

        Args:
            days: Number of days

        Returns:
            List of DailyStats, sorted by date
        """
        result = []
        today = date.today()

        for i in range(days):
            day = today - timedelta(days=i)
            day_str = day.isoformat()
            if day_str in self._daily_stats:
                result.append(self._daily_stats[day_str])
            else:
                # Empty day
                result.append(DailyStats(date=day_str))

        return list(reversed(result))  # Oldest first

    def get_weekly_summary(self) -> dict:
        """Get summary for the last 7 days.

        Returns:
            Dictionary with weekly summary data
        """
        daily = self.get_daily_stats_range(7)

        total_earnings = sum(d.earnings for d in daily)
        total_time = sum(d.play_time_seconds for d in daily)
        total_sessions = sum(d.sessions for d in daily)
        total_activities = sum(d.activities_completed for d in daily)

        best_day = max(daily, key=lambda d: d.earnings) if daily else None

        return {
            "total_earnings": total_earnings,
            "total_play_time_seconds": total_time,
            "total_sessions": total_sessions,
            "total_activities": total_activities,
            "average_daily_earnings": total_earnings / 7,
            "average_session_earnings": total_earnings / total_sessions if total_sessions > 0 else 0,
            "best_day": best_day.date if best_day and best_day.earnings > 0 else None,
            "best_day_earnings": best_day.earnings if best_day else 0,
            "daily_data": [d.to_dict() for d in daily],
        }

    def get_comparison(self) -> dict:
        """Compare this week to last week.

        Returns:
            Dictionary with comparison data
        """
        this_week = self.get_daily_stats_range(7)
        last_week = []

        today = date.today()
        for i in range(7, 14):
            day = today - timedelta(days=i)
            day_str = day.isoformat()
            if day_str in self._daily_stats:
                last_week.append(self._daily_stats[day_str])
            else:
                last_week.append(DailyStats(date=day_str))

        this_earnings = sum(d.earnings for d in this_week)
        last_earnings = sum(d.earnings for d in last_week)
        this_time = sum(d.play_time_seconds for d in this_week)
        last_time = sum(d.play_time_seconds for d in last_week)

        earnings_change = this_earnings - last_earnings
        earnings_pct = (earnings_change / last_earnings * 100) if last_earnings > 0 else 0
        time_change = this_time - last_time

        return {
            "this_week_earnings": this_earnings,
            "last_week_earnings": last_earnings,
            "earnings_change": earnings_change,
            "earnings_change_percent": earnings_pct,
            "this_week_time": this_time,
            "last_week_time": last_time,
            "time_change": time_change,
            "is_improvement": earnings_change > 0,
        }

    @property
    def lifetime(self) -> LifetimeStats:
        """Get lifetime statistics."""
        return self._lifetime

    @property
    def session_count(self) -> int:
        """Get total number of recorded sessions."""
        return len(self._sessions)

    @property
    def best_session(self) -> Optional[SessionRecord]:
        """Get the best earning session."""
        if not self._sessions:
            return None
        return max(self._sessions, key=lambda s: s.earnings)


# Singleton instance
_history: Optional[SessionHistory] = None


def get_session_history(data_path: Optional[Path] = None) -> SessionHistory:
    """Get the global session history instance.

    Args:
        data_path: Path to save data (only used on first call)

    Returns:
        SessionHistory instance
    """
    global _history
    if _history is None:
        _history = SessionHistory(data_path)
    return _history
