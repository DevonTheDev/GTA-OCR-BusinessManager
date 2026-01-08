"""Nightclub tracking for GTA Online.

The Nightclub has two income sources:
1. Safe income - Based on popularity (up to $10K per in-game day)
2. Warehouse goods - Passive production from linked businesses

This module tracks both and helps players maximize their nightclub earnings.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
import json

from ..utils.logging import get_logger

logger = get_logger("tracking.nightclub")


# Nightclub constants
SAFE_MAX = 250_000  # Maximum safe capacity with upgrades
SAFE_MAX_NO_UPGRADE = 70_000  # Without safe upgrade
SAFE_INCOME_PER_INGAME_DAY = {
    100: 10_000,  # 100% popularity
    90: 9_000,
    80: 8_000,
    70: 7_000,
    60: 6_000,
    50: 5_000,
    40: 4_000,
    30: 3_000,
    20: 2_000,
    10: 1_000,
    0: 500,
}

# In-game day is 48 real minutes
INGAME_DAY_MINUTES = 48

# Popularity decay per in-game day (without promotion)
POPULARITY_DECAY_PER_DAY = 5  # 5% per in-game day

# Warehouse production rates (units per hour, with equipment upgrade)
WAREHOUSE_PRODUCTION_RATES = {
    "cargo": 2.0,  # Crates - Sporting Goods
    "weapons": 1.0,  # Weapons - South American Imports
    "cocaine": 1.0,  # Cocaine - South American Imports
    "meth": 1.0,  # Meth - Pharmaceutical Research
    "weed": 1.5,  # Weed - Organic Produce
    "counterfeit_cash": 1.0,  # Cash - Cash Creation
    "documents": 2.0,  # Documents - Printing & Copying
}

# Warehouse max storage
WAREHOUSE_MAX_STORAGE = {
    "cargo": 50,
    "weapons": 100,
    "cocaine": 10,
    "meth": 20,
    "weed": 80,
    "counterfeit_cash": 40,
    "documents": 60,
}

# Value per unit (with upgrades, Los Santos delivery)
WAREHOUSE_VALUE_PER_UNIT = {
    "cargo": 5_000,
    "weapons": 10_000,
    "cocaine": 20_000,
    "meth": 8_500,
    "weed": 1_500,
    "counterfeit_cash": 3_500,
    "documents": 1_000,
}


@dataclass
class NightclubGoods:
    """Current state of nightclub warehouse goods."""

    cargo: int = 0
    weapons: int = 0
    cocaine: int = 0
    meth: int = 0
    weed: int = 0
    counterfeit_cash: int = 0
    documents: int = 0

    @property
    def total_value(self) -> int:
        """Calculate total value of all goods."""
        value = 0
        for good, amount in self.as_dict().items():
            value += amount * WAREHOUSE_VALUE_PER_UNIT.get(good, 0)
        return value

    @property
    def total_units(self) -> int:
        """Get total units stored."""
        return sum(self.as_dict().values())

    def as_dict(self) -> dict:
        """Get goods as dictionary."""
        return {
            "cargo": self.cargo,
            "weapons": self.weapons,
            "cocaine": self.cocaine,
            "meth": self.meth,
            "weed": self.weed,
            "counterfeit_cash": self.counterfeit_cash,
            "documents": self.documents,
        }

    def get_fill_percentages(self) -> dict:
        """Get fill percentage for each good."""
        result = {}
        for good, amount in self.as_dict().items():
            max_storage = WAREHOUSE_MAX_STORAGE.get(good, 1)
            result[good] = (amount / max_storage) * 100
        return result

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return self.as_dict()

    @classmethod
    def from_dict(cls, data: dict) -> "NightclubGoods":
        """Create from dictionary."""
        return cls(
            cargo=data.get("cargo", 0),
            weapons=data.get("weapons", 0),
            cocaine=data.get("cocaine", 0),
            meth=data.get("meth", 0),
            weed=data.get("weed", 0),
            counterfeit_cash=data.get("counterfeit_cash", 0),
            documents=data.get("documents", 0),
        )


@dataclass
class NightclubState:
    """Complete state of the nightclub."""

    # Safe
    safe_current: int = 0
    safe_max: int = SAFE_MAX
    has_safe_upgrade: bool = True

    # Popularity
    popularity: int = 100  # 0-100%
    last_popularity_update: Optional[datetime] = None

    # Warehouse
    goods: NightclubGoods = field(default_factory=NightclubGoods)
    has_equipment_upgrade: bool = True

    # Linked businesses (which technicians are assigned)
    linked_businesses: list = field(default_factory=list)

    # Last update time
    last_updated: Optional[datetime] = None

    @property
    def safe_fill_percent(self) -> float:
        """Get safe fill percentage."""
        return (self.safe_current / self.safe_max) * 100 if self.safe_max > 0 else 0

    @property
    def is_safe_full(self) -> bool:
        """Check if safe is full."""
        return self.safe_current >= self.safe_max

    @property
    def estimated_safe_income_per_hour(self) -> int:
        """Estimate safe income per real hour based on popularity."""
        # Find income bracket
        income_per_day = 500  # Default minimum
        for pop_threshold, income in sorted(SAFE_INCOME_PER_INGAME_DAY.items(), reverse=True):
            if self.popularity >= pop_threshold:
                income_per_day = income
                break

        # Convert to per real hour (1 in-game day = 48 real minutes)
        days_per_hour = 60 / INGAME_DAY_MINUTES
        return int(income_per_day * days_per_hour)

    @property
    def estimated_time_to_full_safe(self) -> Optional[timedelta]:
        """Estimate time until safe is full."""
        if self.is_safe_full:
            return timedelta(0)

        hourly_income = self.estimated_safe_income_per_hour
        if hourly_income <= 0:
            return None

        remaining = self.safe_max - self.safe_current
        hours = remaining / hourly_income
        return timedelta(hours=hours)

    @property
    def estimated_popularity_now(self) -> int:
        """Estimate current popularity accounting for decay."""
        if not self.last_popularity_update:
            return self.popularity

        now = datetime.now(timezone.utc)
        if self.last_popularity_update.tzinfo is None:
            last_update = self.last_popularity_update.replace(tzinfo=timezone.utc)
        else:
            last_update = self.last_popularity_update

        elapsed_minutes = (now - last_update).total_seconds() / 60
        ingame_days = elapsed_minutes / INGAME_DAY_MINUTES
        decay = int(ingame_days * POPULARITY_DECAY_PER_DAY)

        return max(0, self.popularity - decay)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "safe_current": self.safe_current,
            "safe_max": self.safe_max,
            "has_safe_upgrade": self.has_safe_upgrade,
            "popularity": self.popularity,
            "last_popularity_update": self.last_popularity_update.isoformat() if self.last_popularity_update else None,
            "goods": self.goods.to_dict(),
            "has_equipment_upgrade": self.has_equipment_upgrade,
            "linked_businesses": self.linked_businesses,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NightclubState":
        """Create from dictionary."""
        last_pop_update = None
        if data.get("last_popularity_update"):
            last_pop_update = datetime.fromisoformat(data["last_popularity_update"])

        last_updated = None
        if data.get("last_updated"):
            last_updated = datetime.fromisoformat(data["last_updated"])

        return cls(
            safe_current=data.get("safe_current", 0),
            safe_max=data.get("safe_max", SAFE_MAX),
            has_safe_upgrade=data.get("has_safe_upgrade", True),
            popularity=data.get("popularity", 100),
            last_popularity_update=last_pop_update,
            goods=NightclubGoods.from_dict(data.get("goods", {})),
            has_equipment_upgrade=data.get("has_equipment_upgrade", True),
            linked_businesses=data.get("linked_businesses", []),
            last_updated=last_updated,
        )


class NightclubTracker:
    """Tracks nightclub state and provides insights."""

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize nightclub tracker.

        Args:
            data_path: Path to save nightclub data
        """
        self._data_path = data_path
        self._state = NightclubState()
        self._load()
        logger.info("Nightclub tracker initialized")

    def _load(self) -> None:
        """Load state from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._state = NightclubState.from_dict(data)
            logger.debug("Loaded nightclub state")
        except Exception as e:
            logger.error(f"Failed to load nightclub state: {e}")

    def _save(self) -> None:
        """Save state to file."""
        if not self._data_path:
            return

        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save nightclub state: {e}")

    def update_safe(self, current: int, max_val: Optional[int] = None) -> None:
        """Update safe state.

        Args:
            current: Current safe value
            max_val: Maximum safe capacity (if known)
        """
        self._state.safe_current = current
        if max_val is not None:
            self._state.safe_max = max_val
            self._state.has_safe_upgrade = max_val >= SAFE_MAX
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()
        logger.debug(f"Updated nightclub safe: ${current:,}")

    def update_popularity(self, popularity: int) -> None:
        """Update popularity.

        Args:
            popularity: Current popularity (0-100)
        """
        self._state.popularity = max(0, min(100, popularity))
        self._state.last_popularity_update = datetime.now(timezone.utc)
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()
        logger.debug(f"Updated nightclub popularity: {popularity}%")

    def update_goods(self, goods: NightclubGoods) -> None:
        """Update warehouse goods.

        Args:
            goods: New goods state
        """
        self._state.goods = goods
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()
        logger.debug(f"Updated nightclub goods: ${goods.total_value:,} total")

    def update_single_good(self, good_type: str, amount: int) -> None:
        """Update a single good type.

        Args:
            good_type: Type of good (e.g., "cocaine", "weapons")
            amount: Current amount
        """
        if hasattr(self._state.goods, good_type):
            setattr(self._state.goods, good_type, amount)
            self._state.last_updated = datetime.now(timezone.utc)
            self._save()

    def set_linked_businesses(self, businesses: list[str]) -> None:
        """Set which businesses have technicians assigned.

        Args:
            businesses: List of business types
        """
        self._state.linked_businesses = businesses
        self._save()

    def collect_safe(self) -> int:
        """Record safe collection.

        Returns:
            Amount collected
        """
        collected = self._state.safe_current
        self._state.safe_current = 0
        self._state.last_updated = datetime.now(timezone.utc)
        self._save()
        logger.info(f"Collected ${collected:,} from nightclub safe")
        return collected

    def get_recommendations(self) -> list[str]:
        """Get nightclub-specific recommendations.

        Returns:
            List of recommendation strings
        """
        recommendations = []

        # Safe recommendations
        if self._state.is_safe_full:
            recommendations.append(
                f"Collect nightclub safe NOW - Full at ${self._state.safe_current:,}"
            )
        elif self._state.safe_fill_percent >= 80:
            recommendations.append(
                f"Nightclub safe at {self._state.safe_fill_percent:.0f}% - Consider collecting"
            )

        # Popularity recommendations
        current_pop = self._state.estimated_popularity_now
        if current_pop < 50:
            recommendations.append(
                f"Nightclub popularity low ({current_pop}%) - Do a promotion mission"
            )
        elif current_pop < 80:
            recommendations.append(
                f"Nightclub popularity at {current_pop}% - Promotion recommended"
            )

        # Warehouse recommendations
        fill_pcts = self._state.goods.get_fill_percentages()
        full_goods = [good for good, pct in fill_pcts.items() if pct >= 100]
        if full_goods:
            recommendations.append(
                f"Nightclub goods full: {', '.join(full_goods)} - Sell to avoid waste"
            )

        # High value sell recommendation
        if self._state.goods.total_value >= 500_000:
            recommendations.append(
                f"Nightclub goods worth ${self._state.goods.total_value:,} - Good time to sell"
            )

        return recommendations

    def get_summary(self) -> dict:
        """Get a summary of nightclub state.

        Returns:
            Dictionary with summary data
        """
        return {
            "safe_current": self._state.safe_current,
            "safe_max": self._state.safe_max,
            "safe_fill_percent": self._state.safe_fill_percent,
            "popularity": self._state.popularity,
            "popularity_estimated": self._state.estimated_popularity_now,
            "safe_income_per_hour": self._state.estimated_safe_income_per_hour,
            "goods_value": self._state.goods.total_value,
            "goods_units": self._state.goods.total_units,
            "time_to_full_safe": str(self._state.estimated_time_to_full_safe) if self._state.estimated_time_to_full_safe else None,
        }

    def estimate_warehouse_production(self, hours: float = 1.0) -> dict:
        """Estimate warehouse production over time.

        Args:
            hours: Number of real hours

        Returns:
            Dictionary with production estimates
        """
        production = {}
        value_gained = 0

        for business in self._state.linked_businesses:
            if business in WAREHOUSE_PRODUCTION_RATES:
                rate = WAREHOUSE_PRODUCTION_RATES[business]
                if not self._state.has_equipment_upgrade:
                    rate *= 0.5  # Half rate without upgrade

                units = rate * hours
                current = getattr(self._state.goods, business, 0)
                max_storage = WAREHOUSE_MAX_STORAGE.get(business, 0)

                # Cap at max storage
                actual_units = min(units, max_storage - current)
                value = actual_units * WAREHOUSE_VALUE_PER_UNIT.get(business, 0)

                production[business] = {
                    "units": actual_units,
                    "value": value,
                    "capped": current + units > max_storage,
                }
                value_gained += value

        return {
            "production": production,
            "total_value_gained": value_gained,
            "hours": hours,
        }

    @property
    def state(self) -> NightclubState:
        """Get current nightclub state."""
        return self._state

    @property
    def safe_current(self) -> int:
        """Get current safe value."""
        return self._state.safe_current

    @property
    def popularity(self) -> int:
        """Get current popularity (estimated with decay)."""
        return self._state.estimated_popularity_now

    @property
    def goods_value(self) -> int:
        """Get total goods value."""
        return self._state.goods.total_value


# Singleton instance
_tracker: Optional[NightclubTracker] = None


def get_nightclub_tracker(data_path: Optional[Path] = None) -> NightclubTracker:
    """Get the global nightclub tracker.

    Args:
        data_path: Path to save data (only used on first call)

    Returns:
        NightclubTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = NightclubTracker(data_path)
    return _tracker
