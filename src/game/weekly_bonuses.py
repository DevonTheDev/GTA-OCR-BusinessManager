"""Weekly bonus tracking for GTA Online.

GTA Online rotates 2x and 3x money/RP bonuses weekly (Thursday to Thursday).
This module helps players track active bonuses and prioritize activities.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum, auto
import json
from pathlib import Path

from ..utils.logging import get_logger

logger = get_logger("game.weekly_bonuses")


class BonusMultiplier(Enum):
    """Bonus multiplier types."""
    NONE = 1.0
    DOUBLE = 2.0
    TRIPLE = 3.0
    QUAD = 4.0  # Rare but happens


class BonusCategory(Enum):
    """Categories of bonuses."""
    BUSINESS_SELL = auto()  # Business sell missions
    BUSINESS_PRODUCTION = auto()  # Faster production
    HEIST = auto()  # Heist payouts
    ADVERSARY_MODE = auto()  # PvP modes
    RACE = auto()  # Races
    MISSION = auto()  # Contact missions
    VIP_WORK = auto()  # CEO/VIP work
    MC_WORK = auto()  # MC contracts
    FREEMODE = auto()  # Freemode events
    SPECIAL = auto()  # Special events


@dataclass
class WeeklyBonus:
    """Represents an active weekly bonus."""

    name: str  # e.g., "Bunker Sales", "Cayo Perico"
    category: BonusCategory
    multiplier: BonusMultiplier
    description: str = ""
    activity_keys: list = field(default_factory=list)  # Keys to match activities

    @property
    def multiplier_text(self) -> str:
        """Get display text for multiplier."""
        if self.multiplier == BonusMultiplier.DOUBLE:
            return "2X"
        elif self.multiplier == BonusMultiplier.TRIPLE:
            return "3X"
        elif self.multiplier == BonusMultiplier.QUAD:
            return "4X"
        return ""

    @property
    def is_active(self) -> bool:
        """Check if bonus provides extra money."""
        return self.multiplier != BonusMultiplier.NONE

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "category": self.category.name,
            "multiplier": self.multiplier.value,
            "description": self.description,
            "activity_keys": self.activity_keys,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeeklyBonus":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            category=BonusCategory[data["category"]],
            multiplier=BonusMultiplier(data["multiplier"]),
            description=data.get("description", ""),
            activity_keys=data.get("activity_keys", []),
        )


# Common weekly bonus presets for quick entry
BONUS_PRESETS = {
    # Business bonuses
    "bunker_2x": WeeklyBonus(
        "Bunker Sales", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Bunker sell missions",
        ["bunker", "gunrunning"]
    ),
    "mc_2x": WeeklyBonus(
        "MC Business Sales", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on all MC business sales",
        ["cocaine", "meth", "cash", "weed", "documents", "mc"]
    ),
    "ceo_cargo_2x": WeeklyBonus(
        "CEO Cargo", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Special Cargo sales",
        ["cargo", "warehouse", "ceo"]
    ),
    "vehicle_cargo_2x": WeeklyBonus(
        "Vehicle Cargo", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Import/Export",
        ["vehicle", "import", "export"]
    ),
    "nightclub_2x": WeeklyBonus(
        "Nightclub Sales", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Nightclub goods",
        ["nightclub"]
    ),
    "hangar_2x": WeeklyBonus(
        "Hangar Cargo", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Air Freight Cargo",
        ["hangar", "air freight"]
    ),
    "acid_lab_2x": WeeklyBonus(
        "Acid Lab Sales", BonusCategory.BUSINESS_SELL, BonusMultiplier.DOUBLE,
        "Double money on Acid Lab sales",
        ["acid"]
    ),

    # Heist bonuses
    "cayo_perico_2x": WeeklyBonus(
        "Cayo Perico", BonusCategory.HEIST, BonusMultiplier.DOUBLE,
        "Double money on Cayo Perico finale",
        ["cayo", "perico"]
    ),
    "casino_heist_2x": WeeklyBonus(
        "Casino Heist", BonusCategory.HEIST, BonusMultiplier.DOUBLE,
        "Double money on Casino Heist finale",
        ["casino", "heist"]
    ),
    "doomsday_2x": WeeklyBonus(
        "Doomsday Heist", BonusCategory.HEIST, BonusMultiplier.DOUBLE,
        "Double money on Doomsday Heist",
        ["doomsday", "bogdan"]
    ),

    # Work bonuses
    "vip_work_2x": WeeklyBonus(
        "VIP Work", BonusCategory.VIP_WORK, BonusMultiplier.DOUBLE,
        "Double money on VIP Work & Challenges",
        ["vip", "headhunter", "sightseer", "hostile"]
    ),
    "client_jobs_2x": WeeklyBonus(
        "Client Jobs", BonusCategory.VIP_WORK, BonusMultiplier.DOUBLE,
        "Double money on Terrorbyte Client Jobs",
        ["client", "terrorbyte", "robbery", "diamond"]
    ),
    "payphone_2x": WeeklyBonus(
        "Payphone Hits", BonusCategory.VIP_WORK, BonusMultiplier.DOUBLE,
        "Double money on Payphone Hits",
        ["payphone", "hit"]
    ),
    "security_2x": WeeklyBonus(
        "Security Contracts", BonusCategory.VIP_WORK, BonusMultiplier.DOUBLE,
        "Double money on Security Contracts",
        ["security", "contract", "agency"]
    ),
    "auto_shop_2x": WeeklyBonus(
        "Auto Shop Contracts", BonusCategory.VIP_WORK, BonusMultiplier.DOUBLE,
        "Double money on Auto Shop Contracts",
        ["auto shop", "union"]
    ),

    # Mission bonuses
    "contact_2x": WeeklyBonus(
        "Contact Missions", BonusCategory.MISSION, BonusMultiplier.DOUBLE,
        "Double money on Contact Missions",
        ["contact", "mission"]
    ),
    "gerald_2x": WeeklyBonus(
        "Gerald Missions", BonusCategory.MISSION, BonusMultiplier.DOUBLE,
        "Double money on Gerald's Last Play",
        ["gerald"]
    ),
    "lamar_2x": WeeklyBonus(
        "Lamar Missions", BonusCategory.MISSION, BonusMultiplier.DOUBLE,
        "Double money on Lamar Lowrider missions",
        ["lamar", "lowrider"]
    ),

    # Freemode
    "business_battles_2x": WeeklyBonus(
        "Business Battles", BonusCategory.FREEMODE, BonusMultiplier.DOUBLE,
        "Double goods from Business Battles",
        ["business battle"]
    ),
}


@dataclass
class WeeklyBonusState:
    """Current state of weekly bonuses."""

    active_bonuses: list[WeeklyBonus] = field(default_factory=list)
    week_start: Optional[datetime] = None  # Thursday when bonuses started
    week_end: Optional[datetime] = None  # Next Thursday
    last_updated: Optional[datetime] = None
    notes: str = ""  # User notes

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "active_bonuses": [b.to_dict() for b in self.active_bonuses],
            "week_start": self.week_start.isoformat() if self.week_start else None,
            "week_end": self.week_end.isoformat() if self.week_end else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeeklyBonusState":
        """Create from dictionary."""
        week_start = None
        if data.get("week_start"):
            week_start = datetime.fromisoformat(data["week_start"])

        week_end = None
        if data.get("week_end"):
            week_end = datetime.fromisoformat(data["week_end"])

        last_updated = None
        if data.get("last_updated"):
            last_updated = datetime.fromisoformat(data["last_updated"])

        return cls(
            active_bonuses=[WeeklyBonus.from_dict(b) for b in data.get("active_bonuses", [])],
            week_start=week_start,
            week_end=week_end,
            last_updated=last_updated,
            notes=data.get("notes", ""),
        )


class WeeklyBonusTracker:
    """Tracks and manages weekly bonuses."""

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize tracker.

        Args:
            data_path: Path to save bonus data
        """
        self._data_path = data_path
        self._state = WeeklyBonusState()
        self._load()
        logger.info("Weekly bonus tracker initialized")

    def _load(self) -> None:
        """Load bonuses from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._state = WeeklyBonusState.from_dict(data)

            # Check if bonuses are expired
            if self._state.week_end:
                now = datetime.now(timezone.utc)
                if self._state.week_end.tzinfo is None:
                    week_end = self._state.week_end.replace(tzinfo=timezone.utc)
                else:
                    week_end = self._state.week_end

                if now > week_end:
                    logger.info("Weekly bonuses expired, clearing")
                    self._state = WeeklyBonusState()
                    self._save()

            logger.debug(f"Loaded {len(self._state.active_bonuses)} active bonuses")
        except Exception as e:
            logger.error(f"Failed to load bonuses: {e}")

    def _save(self) -> None:
        """Save bonuses to file."""
        if not self._data_path:
            return

        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save bonuses: {e}")

    def add_bonus(self, bonus: WeeklyBonus) -> None:
        """Add an active bonus.

        Args:
            bonus: The bonus to add
        """
        # Remove existing bonus with same name
        self._state.active_bonuses = [
            b for b in self._state.active_bonuses if b.name != bonus.name
        ]
        self._state.active_bonuses.append(bonus)
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()
        logger.info(f"Added bonus: {bonus.name} ({bonus.multiplier_text})")

    def add_preset(self, preset_key: str) -> Optional[WeeklyBonus]:
        """Add a bonus from presets.

        Args:
            preset_key: Key from BONUS_PRESETS

        Returns:
            The added bonus, or None if not found
        """
        if preset_key not in BONUS_PRESETS:
            logger.warning(f"Preset not found: {preset_key}")
            return None

        bonus = BONUS_PRESETS[preset_key]
        self.add_bonus(bonus)
        return bonus

    def remove_bonus(self, name: str) -> bool:
        """Remove a bonus by name.

        Args:
            name: Bonus name to remove

        Returns:
            True if removed
        """
        original_count = len(self._state.active_bonuses)
        self._state.active_bonuses = [
            b for b in self._state.active_bonuses if b.name != name
        ]

        if len(self._state.active_bonuses) < original_count:
            self._save()
            logger.info(f"Removed bonus: {name}")
            return True
        return False

    def clear_all(self) -> None:
        """Clear all bonuses."""
        self._state = WeeklyBonusState()
        self._save()
        logger.info("Cleared all bonuses")

    def set_week_dates(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> None:
        """Set the week start/end dates.

        Args:
            start: Week start (Thursday). If None, uses now.
            end: Week end (next Thursday). If None, calculates from start.
        """
        if start is None:
            start = datetime.now(timezone.utc)

        # Ensure timezone
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        if end is None:
            # Calculate next Thursday
            days_until_thursday = (3 - start.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            end = start + timedelta(days=days_until_thursday)

        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        self._state.week_start = start
        self._state.week_end = end
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()

    def get_bonus_for_activity(self, activity_key: str) -> Optional[WeeklyBonus]:
        """Find a bonus that applies to an activity.

        Args:
            activity_key: Activity identifier to match

        Returns:
            Matching WeeklyBonus or None
        """
        activity_lower = activity_key.lower()

        for bonus in self._state.active_bonuses:
            for key in bonus.activity_keys:
                if key.lower() in activity_lower or activity_lower in key.lower():
                    return bonus

        return None

    def get_multiplier_for_activity(self, activity_key: str) -> float:
        """Get the multiplier for an activity.

        Args:
            activity_key: Activity identifier

        Returns:
            Multiplier value (1.0 if no bonus)
        """
        bonus = self.get_bonus_for_activity(activity_key)
        return bonus.multiplier.value if bonus else 1.0

    def get_boosted_value(self, base_value: int, activity_key: str) -> int:
        """Calculate boosted value with any active bonus.

        Args:
            base_value: Base money value
            activity_key: Activity identifier

        Returns:
            Value with bonus applied
        """
        multiplier = self.get_multiplier_for_activity(activity_key)
        return int(base_value * multiplier)

    @property
    def active_bonuses(self) -> list[WeeklyBonus]:
        """Get all active bonuses."""
        return self._state.active_bonuses.copy()

    @property
    def has_bonuses(self) -> bool:
        """Check if any bonuses are active."""
        return len(self._state.active_bonuses) > 0

    @property
    def time_until_reset(self) -> Optional[timedelta]:
        """Get time until weekly reset (Thursday)."""
        if not self._state.week_end:
            return None

        now = datetime.now(timezone.utc)
        week_end = self._state.week_end
        if week_end.tzinfo is None:
            week_end = week_end.replace(tzinfo=timezone.utc)

        remaining = week_end - now
        return remaining if remaining.total_seconds() > 0 else timedelta(0)

    @property
    def time_until_reset_formatted(self) -> str:
        """Get formatted time until reset."""
        remaining = self.time_until_reset
        if not remaining:
            return "Unknown"

        total_seconds = int(remaining.total_seconds())
        if total_seconds <= 0:
            return "Resetting soon"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600

        if days > 0:
            return f"{days}d {hours}h"
        else:
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"

    def get_summary(self) -> dict:
        """Get a summary of active bonuses."""
        by_category = {}
        for bonus in self._state.active_bonuses:
            cat_name = bonus.category.name
            if cat_name not in by_category:
                by_category[cat_name] = []
            by_category[cat_name].append(f"{bonus.name} ({bonus.multiplier_text})")

        return {
            "total_active": len(self._state.active_bonuses),
            "by_category": by_category,
            "time_until_reset": self.time_until_reset_formatted,
            "last_updated": self._state.last_updated.isoformat() if self._state.last_updated else None,
        }


# Singleton instance
_tracker: Optional[WeeklyBonusTracker] = None


def get_weekly_bonus_tracker(data_path: Optional[Path] = None) -> WeeklyBonusTracker:
    """Get the global weekly bonus tracker.

    Args:
        data_path: Path to save data (only used on first call)

    Returns:
        WeeklyBonusTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = WeeklyBonusTracker(data_path)
    return _tracker
