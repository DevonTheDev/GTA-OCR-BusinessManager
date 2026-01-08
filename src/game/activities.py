"""Activity definitions for GTA Online."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional


class ActivityType(Enum):
    """Types of activities in GTA Online."""

    UNKNOWN = auto()
    FREEROAM = auto()

    # Missions
    CONTACT_MISSION = auto()
    VIP_WORK = auto()
    MC_CONTRACT = auto()
    CLIENT_JOB = auto()

    # Business
    SELL_MISSION = auto()
    RESUPPLY_MISSION = auto()
    SOURCE_MISSION = auto()

    # Heists
    HEIST_PREP = auto()
    HEIST_FINALE = auto()
    CAYO_PERICO = auto()
    CASINO_HEIST = auto()
    DOOMSDAY_HEIST = auto()

    # Newer content
    SECURITY_CONTRACT = auto()
    PAYPHONE_HIT = auto()
    AUTO_SHOP_DELIVERY = auto()
    ACID_LAB_SELL = auto()

    # Other
    RACE = auto()
    ADVERSARY_MODE = auto()
    SURVIVAL = auto()
    FREEMODE_EVENT = auto()


@dataclass
class Activity:
    """Represents a tracked activity/mission."""

    activity_type: ActivityType
    name: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    success: Optional[bool] = None
    earnings: int = 0
    expected_earnings: int = 0
    business_type: str = ""  # For business-related activities
    notes: str = ""

    @property
    def is_active(self) -> bool:
        """Check if activity is still in progress."""
        return self.ended_at is None

    @property
    def duration_seconds(self) -> float:
        """Get duration in seconds."""
        end = self.ended_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def duration_minutes(self) -> float:
        """Get duration in minutes."""
        return self.duration_seconds / 60

    def complete(self, success: bool, earnings: int = 0) -> None:
        """Mark activity as complete.

        Args:
            success: Whether activity was successful
            earnings: Money earned (if known)
        """
        self.ended_at = datetime.now()
        self.success = success
        self.earnings = earnings

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "activity_type": self.activity_type.name,
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "success": self.success,
            "earnings": self.earnings,
            "expected_earnings": self.expected_earnings,
            "business_type": self.business_type,
            "notes": self.notes,
        }


# Expected payouts for common activities
ACTIVITY_PAYOUTS = {
    # VIP Work
    "headhunter": {"min": 20_000, "max": 25_000, "cooldown": 240},
    "sightseer": {"min": 20_000, "max": 25_000, "cooldown": 240},
    "hostile takeover": {"min": 15_000, "max": 20_000, "cooldown": 180},

    # Security Contracts
    "recover valuables": {"min": 31_000, "max": 70_000, "cooldown": 0},
    "gang termination": {"min": 31_000, "max": 70_000, "cooldown": 0},
    "asset protection": {"min": 31_000, "max": 70_000, "cooldown": 0},
    "rescue operation": {"min": 31_000, "max": 70_000, "cooldown": 0},
    "vehicle recovery": {"min": 31_000, "max": 70_000, "cooldown": 0},

    # Payphone Hits (with bonus)
    "payphone hit": {"min": 15_000, "max": 85_000, "cooldown": 1200},

    # Auto Shop
    "auto shop delivery": {"min": 20_000, "max": 180_000, "cooldown": 0},
    "exotic export": {"min": 20_000, "max": 20_000, "cooldown": 0},

    # Cayo Perico (solo, primary targets)
    "cayo perico - tequila": {"min": 900_000, "max": 990_000, "cooldown": 0},
    "cayo perico - ruby necklace": {"min": 1_000_000, "max": 1_100_000, "cooldown": 0},
    "cayo perico - bearer bonds": {"min": 1_100_000, "max": 1_210_000, "cooldown": 0},
    "cayo perico - pink diamond": {"min": 1_300_000, "max": 1_430_000, "cooldown": 0},
    "cayo perico - panther": {"min": 1_900_000, "max": 2_090_000, "cooldown": 0},
}


def get_expected_payout(activity_name: str) -> dict:
    """Get expected payout for an activity.

    Args:
        activity_name: Name of the activity (lowercase)

    Returns:
        Dict with min, max, and cooldown, or empty dict if unknown
    """
    return ACTIVITY_PAYOUTS.get(activity_name.lower(), {})


def estimate_hourly_rate(activity_type: ActivityType, avg_duration_minutes: float, avg_payout: int) -> float:
    """Estimate hourly earning rate for an activity.

    Args:
        activity_type: Type of activity
        avg_duration_minutes: Average time to complete
        avg_payout: Average earnings

    Returns:
        Estimated dollars per hour
    """
    if avg_duration_minutes <= 0:
        return 0.0

    activities_per_hour = 60 / avg_duration_minutes
    return activities_per_hour * avg_payout
