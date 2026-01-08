"""Cooldown tracking for GTA Online activities."""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import json
from pathlib import Path

from ..utils.logging import get_logger
from ..game.activities import ActivityType

logger = get_logger("tracking.cooldowns")


# Cooldown definitions in seconds
ACTIVITY_COOLDOWNS: dict[str, int] = {
    # VIP Work
    "headhunter": 300,  # 5 minutes
    "sightseer": 300,  # 5 minutes
    "hostile_takeover": 300,  # 5 minutes
    "executive_search": 300,  # 5 minutes
    "asset_recovery": 300,  # 5 minutes
    "piracy_prevention": 300,  # 5 minutes

    # MC Contracts
    "mc_contract": 300,  # 5 minutes between contracts

    # Client Jobs (Terrorbyte)
    "robbery_in_progress": 300,  # 5 minutes
    "data_sweep": 300,  # 5 minutes
    "targeted_data": 300,  # 5 minutes
    "diamond_shopping": 300,  # 5 minutes

    # Agency
    "payphone_hit": 1200,  # 20 minutes
    "security_contract": 0,  # No cooldown between contracts

    # Other cooldowns
    "cayo_perico": 2880,  # 48 real minutes / varies in-game
    "casino_heist": 0,  # No cooldown

    # Freemode activities
    "business_battle": 900,  # 15 minutes
}


@dataclass
class CooldownInfo:
    """Information about an active cooldown."""

    activity_name: str
    display_name: str
    started_at: datetime
    duration_seconds: int

    @property
    def elapsed_seconds(self) -> float:
        """Get seconds elapsed since cooldown started."""
        now = datetime.now(timezone.utc)
        # Handle timezone-naive started_at
        if self.started_at.tzinfo is None:
            started = self.started_at.replace(tzinfo=timezone.utc)
        else:
            started = self.started_at
        return (now - started).total_seconds()

    @property
    def remaining_seconds(self) -> float:
        """Get seconds remaining on cooldown."""
        remaining = self.duration_seconds - self.elapsed_seconds
        return max(0, remaining)

    @property
    def is_expired(self) -> bool:
        """Check if cooldown has expired."""
        return self.remaining_seconds <= 0

    @property
    def progress(self) -> float:
        """Get cooldown progress as 0.0 to 1.0."""
        if self.duration_seconds <= 0:
            return 1.0
        return min(1.0, self.elapsed_seconds / self.duration_seconds)

    @property
    def remaining_formatted(self) -> str:
        """Get remaining time as formatted string."""
        remaining = int(self.remaining_seconds)
        if remaining <= 0:
            return "Ready"

        minutes, seconds = divmod(remaining, 60)
        if minutes >= 60:
            hours, minutes = divmod(minutes, 60)
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "activity_name": self.activity_name,
            "display_name": self.display_name,
            "started_at": self.started_at.isoformat(),
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CooldownInfo":
        """Create from dictionary."""
        started_at = datetime.fromisoformat(data["started_at"])
        # Ensure timezone awareness
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return cls(
            activity_name=data["activity_name"],
            display_name=data["display_name"],
            started_at=started_at,
            duration_seconds=data["duration_seconds"],
        )


class CooldownTracker:
    """Tracks cooldowns for GTA Online activities."""

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize cooldown tracker.

        Args:
            data_path: Path to save cooldown data. If None, cooldowns won't persist.
        """
        self._data_path = data_path
        self._cooldowns: dict[str, CooldownInfo] = {}
        self._load()
        logger.info("Cooldown tracker initialized")

    def _load(self) -> None:
        """Load cooldowns from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, cd_data in data.get("cooldowns", {}).items():
                cooldown = CooldownInfo.from_dict(cd_data)
                # Only load if not expired
                if not cooldown.is_expired:
                    self._cooldowns[key] = cooldown

            logger.debug(f"Loaded {len(self._cooldowns)} active cooldowns")
        except Exception as e:
            logger.error(f"Failed to load cooldowns: {e}")

    def _save(self) -> None:
        """Save cooldowns to file."""
        if not self._data_path:
            return

        try:
            # Only save non-expired cooldowns
            active = {k: v.to_dict() for k, v in self._cooldowns.items() if not v.is_expired}

            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump({"cooldowns": active}, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cooldowns: {e}")

    def start_cooldown(
        self,
        activity_name: str,
        display_name: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> CooldownInfo:
        """Start a cooldown for an activity.

        Args:
            activity_name: Internal activity name (lowercase, underscored)
            display_name: Human-readable name (optional, derived from activity_name)
            duration_seconds: Cooldown duration (optional, uses default)

        Returns:
            CooldownInfo for the started cooldown
        """
        # Get default duration if not specified
        if duration_seconds is None:
            duration_seconds = ACTIVITY_COOLDOWNS.get(activity_name.lower(), 300)

        # Generate display name if not provided
        if display_name is None:
            display_name = activity_name.replace("_", " ").title()

        cooldown = CooldownInfo(
            activity_name=activity_name.lower(),
            display_name=display_name,
            started_at=datetime.now(timezone.utc),
            duration_seconds=duration_seconds,
        )

        self._cooldowns[activity_name.lower()] = cooldown
        self._save()

        logger.info(f"Started cooldown: {display_name} ({duration_seconds}s)")
        return cooldown

    def get_cooldown(self, activity_name: str) -> Optional[CooldownInfo]:
        """Get cooldown info for an activity.

        Args:
            activity_name: Activity name to check

        Returns:
            CooldownInfo if cooldown is active, None otherwise
        """
        cooldown = self._cooldowns.get(activity_name.lower())
        if cooldown and cooldown.is_expired:
            del self._cooldowns[activity_name.lower()]
            self._save()
            return None
        return cooldown

    def is_on_cooldown(self, activity_name: str) -> bool:
        """Check if an activity is on cooldown.

        Args:
            activity_name: Activity name to check

        Returns:
            True if activity is on cooldown
        """
        return self.get_cooldown(activity_name) is not None

    def get_remaining(self, activity_name: str) -> float:
        """Get remaining cooldown time in seconds.

        Args:
            activity_name: Activity name to check

        Returns:
            Remaining seconds, or 0 if not on cooldown
        """
        cooldown = self.get_cooldown(activity_name)
        return cooldown.remaining_seconds if cooldown else 0

    def clear_cooldown(self, activity_name: str) -> None:
        """Clear a cooldown manually.

        Args:
            activity_name: Activity name to clear
        """
        if activity_name.lower() in self._cooldowns:
            del self._cooldowns[activity_name.lower()]
            self._save()
            logger.info(f"Cleared cooldown: {activity_name}")

    def get_active_cooldowns(self) -> list[CooldownInfo]:
        """Get all active (non-expired) cooldowns.

        Returns:
            List of active CooldownInfo objects, sorted by remaining time
        """
        # Clean up expired cooldowns
        expired = [k for k, v in self._cooldowns.items() if v.is_expired]
        for key in expired:
            del self._cooldowns[key]

        if expired:
            self._save()

        # Return sorted by remaining time
        active = list(self._cooldowns.values())
        active.sort(key=lambda c: c.remaining_seconds)
        return active

    def get_ready_activities(self) -> list[str]:
        """Get activities that are off cooldown and ready.

        Returns:
            List of activity names that are ready
        """
        ready = []
        for activity_name in ACTIVITY_COOLDOWNS.keys():
            if not self.is_on_cooldown(activity_name):
                ready.append(activity_name)
        return ready

    def cleanup_expired(self) -> int:
        """Remove expired cooldowns.

        Returns:
            Number of cooldowns removed
        """
        expired = [k for k, v in self._cooldowns.items() if v.is_expired]
        for key in expired:
            del self._cooldowns[key]

        if expired:
            self._save()
            logger.debug(f"Cleaned up {len(expired)} expired cooldowns")

        return len(expired)


# Singleton instance
_tracker: Optional[CooldownTracker] = None


def get_cooldown_tracker(data_path: Optional[Path] = None) -> CooldownTracker:
    """Get the global cooldown tracker instance.

    Args:
        data_path: Path to save cooldown data (only used on first call)

    Returns:
        CooldownTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = CooldownTracker(data_path)
    return _tracker
