"""Passive income tracking and predictions for GTA Business Manager."""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
import json
from pathlib import Path

from ..utils.logging import get_logger
from ..game.businesses import NIGHTCLUB, AGENCY

logger = get_logger("tracking.passive_income")


# Nightclub production rates (per hour, with Equipment upgrade + 5 technicians)
# Values represent goods accumulated per real-world hour
NIGHTCLUB_GOODS = {
    "cargo": {
        "name": "Cargo and Shipments",
        "linked_to": "special_cargo",
        "rate_per_hour": 2.0,  # Units per hour
        "value_per_unit": 10_000,
        "max_units": 50,
    },
    "sporting_goods": {
        "name": "Sporting Goods",
        "linked_to": "bunker",
        "rate_per_hour": 2.0,
        "value_per_unit": 5_000,
        "max_units": 100,
    },
    "south_american_imports": {
        "name": "South American Imports",
        "linked_to": "cocaine",
        "rate_per_hour": 2.0,
        "value_per_unit": 20_000,
        "max_units": 10,
    },
    "pharmaceutical_research": {
        "name": "Pharmaceutical Research",
        "linked_to": "meth",
        "rate_per_hour": 1.0,
        "value_per_unit": 8_500,
        "max_units": 20,
    },
    "organic_produce": {
        "name": "Organic Produce",
        "linked_to": "weed",
        "rate_per_hour": 1.5,
        "value_per_unit": 1_500,
        "max_units": 80,
    },
    "printing_and_copying": {
        "name": "Printing and Copying",
        "linked_to": "documents",
        "rate_per_hour": 1.5,
        "value_per_unit": 1_000,
        "max_units": 60,
    },
    "cash_creation": {
        "name": "Cash Creation",
        "linked_to": "cash",
        "rate_per_hour": 1.5,
        "value_per_unit": 3_500,
        "max_units": 40,
    },
}

# Agency safe settings
AGENCY_SAFE_RATE = 500  # Dollars per 48 min (after 201 contracts completed)
AGENCY_SAFE_MAX = 250_000
AGENCY_SAFE_INTERVAL_MINUTES = 48


@dataclass
class PassiveIncomeState:
    """State of a passive income source."""

    source_id: str
    name: str
    current_value: int = 0
    max_value: int = 0
    rate_per_hour: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_collected: Optional[datetime] = None
    is_linked: bool = True  # Whether linked businesses are active (for NC)

    @property
    def fill_percent(self) -> float:
        """Get fill percentage (0-100)."""
        if self.max_value <= 0:
            return 0.0
        return min(100.0, (self.current_value / self.max_value) * 100)

    @property
    def is_full(self) -> bool:
        """Check if at capacity."""
        return self.current_value >= self.max_value

    @property
    def estimated_current_value(self) -> int:
        """Estimate current value based on time elapsed."""
        if not self.is_linked or self.rate_per_hour <= 0:
            return self.current_value

        now = datetime.now(timezone.utc)
        if self.last_updated.tzinfo is None:
            last = self.last_updated.replace(tzinfo=timezone.utc)
        else:
            last = self.last_updated

        hours_elapsed = (now - last).total_seconds() / 3600
        accumulated = int(hours_elapsed * self.rate_per_hour)

        return min(self.max_value, self.current_value + accumulated)

    @property
    def time_until_full(self) -> Optional[timedelta]:
        """Calculate time until full.

        Returns:
            Time until full, or None if already full or not producing
        """
        if self.is_full or not self.is_linked or self.rate_per_hour <= 0:
            return None

        remaining = self.max_value - self.estimated_current_value
        if remaining <= 0:
            return timedelta(0)

        hours = remaining / self.rate_per_hour
        return timedelta(hours=hours)

    @property
    def time_until_full_formatted(self) -> str:
        """Get formatted time until full."""
        remaining = self.time_until_full
        if remaining is None:
            return "N/A"

        if remaining.total_seconds() <= 0:
            return "Full"

        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)

        if hours >= 24:
            days = hours // 24
            hours = hours % 24
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "source_id": self.source_id,
            "name": self.name,
            "current_value": self.current_value,
            "max_value": self.max_value,
            "rate_per_hour": self.rate_per_hour,
            "last_updated": self.last_updated.isoformat(),
            "last_collected": self.last_collected.isoformat() if self.last_collected else None,
            "is_linked": self.is_linked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PassiveIncomeState":
        """Create from dictionary."""
        last_updated = datetime.fromisoformat(data["last_updated"])
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)

        last_collected = None
        if data.get("last_collected"):
            last_collected = datetime.fromisoformat(data["last_collected"])
            if last_collected.tzinfo is None:
                last_collected = last_collected.replace(tzinfo=timezone.utc)

        return cls(
            source_id=data["source_id"],
            name=data["name"],
            current_value=data["current_value"],
            max_value=data["max_value"],
            rate_per_hour=data["rate_per_hour"],
            last_updated=last_updated,
            last_collected=last_collected,
            is_linked=data.get("is_linked", True),
        )


@dataclass
class NightclubGoods:
    """Track individual Nightclub goods."""

    goods_id: str
    name: str
    current_units: int = 0
    max_units: int = 0
    rate_per_hour: float = 0.0
    value_per_unit: int = 0
    is_active: bool = True

    @property
    def current_value(self) -> int:
        """Get current value of goods."""
        return self.current_units * self.value_per_unit

    @property
    def max_value(self) -> int:
        """Get max value when full."""
        return self.max_units * self.value_per_unit

    @property
    def fill_percent(self) -> float:
        """Get fill percentage."""
        if self.max_units <= 0:
            return 0.0
        return min(100.0, (self.current_units / self.max_units) * 100)

    @property
    def is_full(self) -> bool:
        """Check if at capacity."""
        return self.current_units >= self.max_units


class PassiveIncomeTracker:
    """Tracks passive income from Nightclub and Agency."""

    def __init__(self, data_path: Optional[Path] = None):
        """Initialize tracker.

        Args:
            data_path: Path to save state
        """
        self._data_path = data_path
        self._nightclub: Optional[PassiveIncomeState] = None
        self._nightclub_goods: dict[str, NightclubGoods] = {}
        self._agency: Optional[PassiveIncomeState] = None
        self._initialize_defaults()
        self._load()
        logger.info("Passive income tracker initialized")

    def _initialize_defaults(self) -> None:
        """Initialize default states."""
        # Initialize nightclub goods
        for goods_id, info in NIGHTCLUB_GOODS.items():
            self._nightclub_goods[goods_id] = NightclubGoods(
                goods_id=goods_id,
                name=info["name"],
                max_units=info["max_units"],
                rate_per_hour=info["rate_per_hour"],
                value_per_unit=info["value_per_unit"],
            )

        # Calculate total NC production rate
        nc_rate = sum(
            g["rate_per_hour"] * g["value_per_unit"]
            for g in NIGHTCLUB_GOODS.values()
        )
        nc_max = sum(
            g["max_units"] * g["value_per_unit"]
            for g in NIGHTCLUB_GOODS.values()
        )

        self._nightclub = PassiveIncomeState(
            source_id="nightclub",
            name="Nightclub Warehouse",
            max_value=nc_max,
            rate_per_hour=nc_rate,
        )

        # Initialize agency
        # Rate is $500 per 48 min = $625/hour
        self._agency = PassiveIncomeState(
            source_id="agency",
            name="Agency Safe",
            max_value=AGENCY_SAFE_MAX,
            rate_per_hour=(AGENCY_SAFE_RATE / AGENCY_SAFE_INTERVAL_MINUTES) * 60,
        )

    def _load(self) -> None:
        """Load state from file."""
        if not self._data_path or not self._data_path.exists():
            return

        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data.get("nightclub"):
                self._nightclub = PassiveIncomeState.from_dict(data["nightclub"])

            if data.get("agency"):
                self._agency = PassiveIncomeState.from_dict(data["agency"])

            for goods_id, goods_data in data.get("nightclub_goods", {}).items():
                if goods_id in self._nightclub_goods:
                    self._nightclub_goods[goods_id].current_units = goods_data.get("current_units", 0)
                    self._nightclub_goods[goods_id].is_active = goods_data.get("is_active", True)

            logger.debug("Loaded passive income state")
        except Exception as e:
            logger.error(f"Failed to load passive income state: {e}")

    def _save(self) -> None:
        """Save state to file."""
        if not self._data_path:
            return

        try:
            data = {
                "nightclub": self._nightclub.to_dict() if self._nightclub else None,
                "agency": self._agency.to_dict() if self._agency else None,
                "nightclub_goods": {
                    goods_id: {
                        "current_units": g.current_units,
                        "is_active": g.is_active,
                    }
                    for goods_id, g in self._nightclub_goods.items()
                },
            }

            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save passive income state: {e}")

    def update_nightclub(self, current_value: int) -> None:
        """Update nightclub warehouse value from OCR.

        Args:
            current_value: Current total value from game
        """
        if self._nightclub:
            self._nightclub.current_value = current_value
            self._nightclub.last_updated = datetime.now(timezone.utc)
            self._save()
            logger.debug(f"Nightclub updated: ${current_value:,}")

    def update_nightclub_goods(self, goods_id: str, current_units: int) -> None:
        """Update specific nightclub goods.

        Args:
            goods_id: ID of the goods
            current_units: Current units
        """
        if goods_id in self._nightclub_goods:
            self._nightclub_goods[goods_id].current_units = current_units
            self._save()

    def set_nightclub_goods_active(self, goods_id: str, is_active: bool) -> None:
        """Set whether a nightclub goods source is active.

        Args:
            goods_id: ID of the goods
            is_active: Whether the linked business is active
        """
        if goods_id in self._nightclub_goods:
            self._nightclub_goods[goods_id].is_active = is_active
            self._save()

    def update_agency(self, current_value: int) -> None:
        """Update agency safe value from OCR.

        Args:
            current_value: Current safe value from game
        """
        if self._agency:
            self._agency.current_value = current_value
            self._agency.last_updated = datetime.now(timezone.utc)
            self._save()
            logger.debug(f"Agency safe updated: ${current_value:,}")

    def record_nightclub_sale(self, amount: int) -> None:
        """Record a nightclub sale.

        Args:
            amount: Sale amount
        """
        if self._nightclub:
            self._nightclub.current_value = 0
            self._nightclub.last_collected = datetime.now(timezone.utc)
            self._nightclub.last_updated = datetime.now(timezone.utc)

            # Reset all goods
            for goods in self._nightclub_goods.values():
                goods.current_units = 0

            self._save()
            logger.info(f"Nightclub sale recorded: ${amount:,}")

    def record_agency_collection(self, amount: int) -> None:
        """Record agency safe collection.

        Args:
            amount: Collection amount
        """
        if self._agency:
            self._agency.current_value = 0
            self._agency.last_collected = datetime.now(timezone.utc)
            self._agency.last_updated = datetime.now(timezone.utc)
            self._save()
            logger.info(f"Agency collection recorded: ${amount:,}")

    @property
    def nightclub(self) -> Optional[PassiveIncomeState]:
        """Get nightclub state."""
        return self._nightclub

    @property
    def nightclub_goods(self) -> dict[str, NightclubGoods]:
        """Get nightclub goods states."""
        return self._nightclub_goods.copy()

    @property
    def agency(self) -> Optional[PassiveIncomeState]:
        """Get agency state."""
        return self._agency

    @property
    def total_passive_value(self) -> int:
        """Get total estimated passive income available."""
        total = 0
        if self._nightclub:
            total += self._nightclub.estimated_current_value
        if self._agency:
            total += self._agency.estimated_current_value
        return total

    @property
    def total_passive_max(self) -> int:
        """Get total max passive income capacity."""
        total = 0
        if self._nightclub:
            total += self._nightclub.max_value
        if self._agency:
            total += self._agency.max_value
        return total

    def get_predictions(self) -> list[dict]:
        """Get predictions for passive income.

        Returns:
            List of prediction dictionaries with name, value, max, eta
        """
        predictions = []

        if self._nightclub:
            predictions.append({
                "name": "Nightclub",
                "current_value": self._nightclub.estimated_current_value,
                "max_value": self._nightclub.max_value,
                "fill_percent": self._nightclub.fill_percent,
                "time_until_full": self._nightclub.time_until_full_formatted,
                "is_full": self._nightclub.is_full,
            })

        if self._agency:
            predictions.append({
                "name": "Agency Safe",
                "current_value": self._agency.estimated_current_value,
                "max_value": self._agency.max_value,
                "fill_percent": self._agency.fill_percent,
                "time_until_full": self._agency.time_until_full_formatted,
                "is_full": self._agency.is_full,
            })

        return predictions

    def get_recommendations(self) -> list[str]:
        """Get recommendations based on passive income state.

        Returns:
            List of recommendation strings
        """
        recommendations = []

        if self._agency and self._agency.fill_percent >= 90:
            recommendations.append(
                f"Agency safe is {self._agency.fill_percent:.0f}% full - collect soon!"
            )

        if self._nightclub and self._nightclub.fill_percent >= 80:
            recommendations.append(
                f"Nightclub warehouse is {self._nightclub.fill_percent:.0f}% full - consider selling"
            )

        # Check individual NC goods
        for goods in self._nightclub_goods.values():
            if goods.is_full and goods.is_active:
                recommendations.append(
                    f"NC {goods.name} is full - production stopped"
                )

        return recommendations


# Singleton instance
_tracker: Optional[PassiveIncomeTracker] = None


def get_passive_income_tracker(data_path: Optional[Path] = None) -> PassiveIncomeTracker:
    """Get the global passive income tracker instance.

    Args:
        data_path: Path to save state (only used on first call)

    Returns:
        PassiveIncomeTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = PassiveIncomeTracker(data_path)
    return _tracker
