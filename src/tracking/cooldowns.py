"""Cooldown tracking for GTA Online activities."""

from datetime import datetime, timezone
from dataclasses import dataclass, replace
from typing import Optional
import json
import math
from pathlib import Path
from threading import RLock
import unicodedata
from uuid import uuid4

from ..utils.logging import get_logger
from ..utils.persistence import atomic_text_writer

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


def _has_unsafe_characters(value: str) -> bool:
    """Reject control, formatting, line-separator and surrogate characters."""
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)


def validate_reminder_values(display_name: str, duration_seconds: int) -> tuple[str, int]:
    """Validate manual input without changing any timer or saved state."""
    if not isinstance(display_name, str) or _has_unsafe_characters(display_name):
        raise ValueError("Use a plain-text timer name without control characters.")
    display_name = display_name.strip()
    if not 1 <= len(display_name) <= 200:
        raise ValueError("Timer names must contain 1 to 200 characters.")
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 604800:
        raise ValueError("Choose a whole number of seconds from 1 to 604800 (seven days).")
    return display_name, duration_seconds


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
        return self._elapsed_at(datetime.now(timezone.utc))

    def _elapsed_at(self, now: datetime) -> float:
        """Use a caller's clock snapshot for coherent tracker operations."""
        # Handle timezone-naive started_at
        if self.started_at.tzinfo is None:
            started = self.started_at.replace(tzinfo=timezone.utc)
        else:
            started = self.started_at
        return max(0.0, (now - started).total_seconds())

    @property
    def remaining_seconds(self) -> float:
        """Get seconds remaining on cooldown."""
        return self._remaining_at(datetime.now(timezone.utc))

    def _remaining_at(self, now: datetime) -> float:
        return max(0, self.duration_seconds - self._elapsed_at(now))

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
        if not isinstance(data, dict):
            raise ValueError("Invalid cooldown record")
        if not isinstance(data["activity_name"], str) or not isinstance(data["display_name"], str):
            raise ValueError("Invalid cooldown names")
        duration = data["duration_seconds"]
        # Existing files may contain fractional, zero or negative durations.
        # The stricter manual bounds apply only to the new reminder methods.
        if type(duration) not in (int, float) or not math.isfinite(duration):
            raise ValueError("Invalid cooldown duration")
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
        self._lock = RLock()
        self._cooldowns: dict[str, CooldownInfo] = {}
        self._storage_error: str | None = None
        self._needs_save_retry = False
        self._load()
        logger.info("Cooldown tracker initialized")

    def _load(self) -> None:
        """Publish only a complete valid load; observation never repairs its file."""
        with self._lock:
            if not self._data_path:
                return
            try:
                with open(self._data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("cooldowns", {}), dict):
                    raise ValueError("Invalid cooldown file structure")
                staged = {}
                now = datetime.now(timezone.utc)
                for key, cd_data in data.get("cooldowns", {}).items():
                    cooldown = CooldownInfo.from_dict(cd_data)
                    if key != cooldown.activity_name or key != key.lower():
                        raise ValueError("Cooldown key does not match its activity")
                    if cooldown._remaining_at(now) > 0:
                        staged[key] = cooldown
            except FileNotFoundError:
                staged = {}
            except Exception as e:
                self._cooldowns = {}
                self._storage_error = "load_failed"
                self._needs_save_retry = False
                logger.error(f"Failed to load cooldowns: {e}")
                return
            self._cooldowns = staged
            self._storage_error = None
            self._needs_save_retry = False
            logger.debug(f"Loaded {len(self._cooldowns)} active cooldowns")

    @property
    def storage_error(self) -> str | None:
        """A stable UI-safe failure code, without exception details or paths."""
        with self._lock:
            return self._storage_error

    @property
    def needs_save_retry(self) -> bool:
        """Whether an in-memory change still needs to be saved."""
        with self._lock:
            return self._needs_save_retry

    def retry_save(self) -> bool:
        """Retry pending changes without restarting timers or repairing a bad load."""
        with self._lock:
            if not self._needs_save_retry:
                return False
            return self._save()

    def _save(self, now: Optional[datetime] = None) -> bool:
        """Serialize the latest state while retaining the same mutation lock."""
        with self._lock:
            if self._storage_error == "load_failed" and not self._needs_save_retry:
                return False
            if self._data_path:
                try:
                    if now is None:
                        now = datetime.now(timezone.utc)
                    active = {
                        key: info.to_dict() for key, info in self._cooldowns.items()
                        if info._remaining_at(now) > 0
                    }
                    with atomic_text_writer(self._data_path) as f:
                        json.dump({"cooldowns": active}, f, indent=2)
                except Exception as e:
                    self._storage_error = "save_failed"
                    logger.error(f"Failed to save cooldowns: {e}")
                    return False
            self._needs_save_retry = False
            self._storage_error = None
            return True

    def start_custom_timer(self, display_name: str, duration_seconds: int) -> CooldownInfo:
        """Start a distinct named reminder, even when its label matches another."""
        display_name, duration_seconds = validate_reminder_values(display_name, duration_seconds)
        return self.set_timer(f"manual:{uuid4()}", display_name, duration_seconds)

    def set_timer(
        self, activity_name: str, display_name: str, duration_seconds: int,
    ) -> CooldownInfo:
        """Set this stable timer key from now, replacing or recreating it."""
        display_name, duration_seconds = validate_reminder_values(display_name, duration_seconds)
        if (not isinstance(activity_name, str) or not activity_name.strip()
                or len(activity_name) > 256 or _has_unsafe_characters(activity_name)):
            raise ValueError("Use a nonblank timer key of at most 256 plain-text characters.")
        return self.start_cooldown(activity_name, display_name, duration_seconds)

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

        with self._lock:
            now = datetime.now(timezone.utc)
            cooldown = CooldownInfo(
                activity_name=activity_name.lower(),
                display_name=display_name,
                started_at=now,
                duration_seconds=duration_seconds,
            )
            self._cooldowns[activity_name.lower()] = cooldown
            self._needs_save_retry = True
            self._save(now)
            result = replace(cooldown)
        logger.info(f"Started cooldown: {display_name} ({duration_seconds}s)")
        return result

    def get_cooldown(self, activity_name: str) -> Optional[CooldownInfo]:
        """Get cooldown info for an activity.

        Args:
            activity_name: Activity name to check

        Returns:
            CooldownInfo if cooldown is active, None otherwise
        """
        with self._lock:
            cooldown = self._get_cooldown_locked(activity_name.lower(), datetime.now(timezone.utc))
            return replace(cooldown) if cooldown else None

    def _get_cooldown_locked(self, key: str, now: datetime) -> Optional[CooldownInfo]:
        cooldown = self._cooldowns.get(key)
        if cooldown and cooldown._remaining_at(now) <= 0:
            del self._cooldowns[key]
            self._needs_save_retry = True
            self._save(now)
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
        with self._lock:
            now = datetime.now(timezone.utc)
            cooldown = self._get_cooldown_locked(activity_name.lower(), now)
            return cooldown._remaining_at(now) if cooldown else 0

    def clear_cooldown(self, activity_name: str) -> None:
        """Clear a cooldown manually.

        Args:
            activity_name: Activity name to clear
        """
        with self._lock:
            if activity_name.lower() in self._cooldowns:
                del self._cooldowns[activity_name.lower()]
                self._needs_save_retry = True
                self._save()
                logger.info(f"Cleared cooldown: {activity_name}")

    def get_active_cooldowns(self) -> list[CooldownInfo]:
        """Get all active (non-expired) cooldowns.

        Returns:
            List of active CooldownInfo objects, sorted by remaining time
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            self._prune_expired_locked(now)
            active = sorted(
                self._cooldowns.items(), key=lambda item: (item[1]._remaining_at(now), item[0]),
            )
            return [replace(info) for _, info in active]

    def get_ready_activities(self) -> list[str]:
        """Get activities that are off cooldown and ready.

        Returns:
            List of activity names that are ready
        """
        with self._lock:
            self._prune_expired_locked(datetime.now(timezone.utc))
            return [name for name in ACTIVITY_COOLDOWNS if name not in self._cooldowns]

    def cleanup_expired(self) -> int:
        """Remove expired cooldowns.

        Returns:
            Number of cooldowns removed
        """
        with self._lock:
            return self._prune_expired_locked(datetime.now(timezone.utc))

    def _prune_expired_locked(self, now: datetime) -> int:
        expired = [key for key, info in self._cooldowns.items() if info._remaining_at(now) <= 0]
        for key in expired:
            del self._cooldowns[key]

        if expired:
            self._needs_save_retry = True
            self._save(now)
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
