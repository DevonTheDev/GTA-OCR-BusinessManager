"""Multi-business sell planner for GTA Business Manager."""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum, auto

from ..game.businesses import BUSINESSES, Business, BusinessCategory
from ..utils.logging import get_logger

logger = get_logger("optimization.sell_planner")


class SellPriority(Enum):
    """Priority levels for sell missions."""

    CRITICAL = auto()  # Must sell now (raid risk, at capacity)
    HIGH = auto()  # Should sell soon (>80% full)
    MEDIUM = auto()  # Can sell (>50% full)
    LOW = auto()  # Optional sell (<50% full)
    SKIP = auto()  # Don't sell (too low value)


@dataclass
class BusinessSellInfo:
    """Information about a business ready to sell."""

    business: Business
    stock_percent: int
    supply_percent: int
    estimated_value: int
    priority: SellPriority
    vehicles_needed: int = 1
    solo_friendly: bool = True
    time_to_full: Optional[timedelta] = None
    notes: str = ""

    @property
    def value_per_vehicle(self) -> int:
        """Get value per delivery vehicle."""
        if self.vehicles_needed <= 0:
            return self.estimated_value
        return self.estimated_value // self.vehicles_needed

    @property
    def priority_score(self) -> float:
        """Get numeric priority score for sorting."""
        base_scores = {
            SellPriority.CRITICAL: 100,
            SellPriority.HIGH: 75,
            SellPriority.MEDIUM: 50,
            SellPriority.LOW: 25,
            SellPriority.SKIP: 0,
        }
        # Add value factor
        value_factor = min(self.estimated_value / 500_000, 1.0) * 20
        # Solo bonus
        solo_bonus = 10 if self.solo_friendly else 0

        return base_scores[self.priority] + value_factor + solo_bonus


@dataclass
class SellPlan:
    """A coordinated plan for selling multiple businesses."""

    businesses: list[BusinessSellInfo] = field(default_factory=list)
    total_value: int = 0
    total_vehicles: int = 0
    estimated_time_minutes: int = 0
    solo_viable: bool = True

    def add_business(self, info: BusinessSellInfo) -> None:
        """Add a business to the sell plan."""
        self.businesses.append(info)
        self.total_value += info.estimated_value
        self.total_vehicles += info.vehicles_needed
        if not info.solo_friendly:
            self.solo_viable = False
        # Estimate ~15 min per sell mission
        self.estimated_time_minutes += 15

    @property
    def value_per_hour(self) -> float:
        """Estimate $/hour for this sell plan."""
        if self.estimated_time_minutes <= 0:
            return 0.0
        hours = self.estimated_time_minutes / 60
        return self.total_value / hours


class SellPlanner:
    """Plans optimal sell order for multiple businesses."""

    # Vehicle estimates based on stock level
    VEHICLE_ESTIMATES = {
        "cocaine": [(25, 1), (50, 1), (75, 2), (100, 2)],
        "meth": [(25, 1), (50, 1), (75, 2), (100, 2)],
        "cash": [(25, 1), (50, 1), (75, 2), (100, 2)],
        "weed": [(25, 1), (50, 1), (75, 2), (100, 3)],
        "documents": [(25, 1), (50, 1), (75, 2), (100, 2)],
        "bunker": [(25, 1), (50, 1), (75, 2), (100, 3)],
        "nightclub": [(100, 1)],  # Always 1 vehicle
        "acid_lab": [(50, 1), (100, 2)],
    }

    # Solo-friendly thresholds (max vehicles for solo)
    SOLO_MAX_VEHICLES = {
        "cocaine": 1,
        "meth": 1,
        "cash": 2,  # Post Ops can be done solo if careful
        "weed": 1,
        "documents": 2,
        "bunker": 1,
        "nightclub": 1,
        "acid_lab": 1,
    }

    def __init__(self, solo_mode: bool = True):
        """Initialize sell planner.

        Args:
            solo_mode: Whether to prioritize solo-friendly sells
        """
        self._solo_mode = solo_mode
        self._business_states: dict[str, dict] = {}
        logger.info(f"Sell planner initialized (solo_mode={solo_mode})")

    def update_business_state(
        self,
        business_id: str,
        stock_percent: int,
        supply_percent: int,
        value: int = 0,
    ) -> None:
        """Update tracked state for a business.

        Args:
            business_id: Business identifier
            stock_percent: Current stock level (0-100)
            supply_percent: Current supply level (0-100)
            value: Current stock value in dollars
        """
        self._business_states[business_id] = {
            "stock": stock_percent,
            "supply": supply_percent,
            "value": value,
            "updated": datetime.now(timezone.utc),
        }

    def _estimate_vehicles(self, business_id: str, stock_percent: int) -> int:
        """Estimate delivery vehicles needed.

        Args:
            business_id: Business identifier
            stock_percent: Stock level percentage

        Returns:
            Estimated number of delivery vehicles
        """
        estimates = self.VEHICLE_ESTIMATES.get(business_id, [(100, 1)])

        for threshold, vehicles in estimates:
            if stock_percent <= threshold:
                return vehicles

        return estimates[-1][1]

    def _is_solo_friendly(self, business_id: str, vehicles: int) -> bool:
        """Check if a sell is solo-friendly.

        Args:
            business_id: Business identifier
            vehicles: Number of delivery vehicles

        Returns:
            True if can be done solo
        """
        max_solo = self.SOLO_MAX_VEHICLES.get(business_id, 1)
        return vehicles <= max_solo

    def _calculate_priority(
        self,
        business: Business,
        stock_percent: int,
        supply_percent: int,
    ) -> SellPriority:
        """Calculate sell priority for a business.

        Args:
            business: Business definition
            stock_percent: Current stock level
            supply_percent: Current supply level

        Returns:
            SellPriority level
        """
        # Critical: At or near capacity
        if stock_percent >= 95:
            return SellPriority.CRITICAL

        # Critical: MC business with no supplies and high stock (raid risk)
        if business.category == BusinessCategory.MC:
            if supply_percent == 0 and stock_percent >= 50:
                return SellPriority.CRITICAL

        # High: Good stock level
        if stock_percent >= 80:
            return SellPriority.HIGH

        # Medium: Decent stock
        if stock_percent >= 50:
            return SellPriority.MEDIUM

        # Low: Some stock
        if stock_percent >= 25:
            return SellPriority.LOW

        # Skip: Not worth selling
        return SellPriority.SKIP

    def _estimate_value(self, business: Business, stock_percent: int) -> int:
        """Estimate current stock value.

        Args:
            business: Business definition
            stock_percent: Stock level percentage

        Returns:
            Estimated value in dollars
        """
        return int(business.max_value * (stock_percent / 100))

    def _calculate_time_to_full(
        self,
        business: Business,
        stock_percent: int,
        supply_percent: int,
    ) -> Optional[timedelta]:
        """Calculate time until business is full.

        Args:
            business: Business definition
            stock_percent: Current stock level
            supply_percent: Current supply level

        Returns:
            Time until full, or None if no supplies
        """
        if supply_percent <= 0 or stock_percent >= 100:
            return None

        remaining_percent = 100 - stock_percent
        # Estimate based on full production time
        if business.full_production_time > 0:
            minutes_per_percent = business.full_production_time / 100
            minutes_remaining = remaining_percent * minutes_per_percent
            return timedelta(minutes=minutes_remaining)

        return None

    def analyze_business(self, business_id: str) -> Optional[BusinessSellInfo]:
        """Analyze a single business for selling.

        Args:
            business_id: Business identifier

        Returns:
            BusinessSellInfo or None if business not tracked
        """
        if business_id not in self._business_states:
            return None

        business = BUSINESSES.get(business_id)
        if not business:
            return None

        state = self._business_states[business_id]
        stock = state["stock"]
        supply = state["supply"]
        value = state.get("value", 0) or self._estimate_value(business, stock)

        vehicles = self._estimate_vehicles(business_id, stock)
        solo_friendly = self._is_solo_friendly(business_id, vehicles)
        priority = self._calculate_priority(business, stock, supply)
        time_to_full = self._calculate_time_to_full(business, stock, supply)

        # Generate notes
        notes = []
        if priority == SellPriority.CRITICAL and supply == 0:
            notes.append("Raid risk - sell ASAP!")
        if not solo_friendly:
            notes.append(f"Needs {vehicles} vehicles - get help")
        if time_to_full and time_to_full.total_seconds() < 3600:
            notes.append("Almost full")

        return BusinessSellInfo(
            business=business,
            stock_percent=stock,
            supply_percent=supply,
            estimated_value=value,
            priority=priority,
            vehicles_needed=vehicles,
            solo_friendly=solo_friendly,
            time_to_full=time_to_full,
            notes=" | ".join(notes) if notes else "",
        )

    def get_sell_recommendations(self, limit: int = 5) -> list[BusinessSellInfo]:
        """Get prioritized list of businesses to sell.

        Args:
            limit: Maximum recommendations to return

        Returns:
            List of BusinessSellInfo sorted by priority
        """
        recommendations = []

        for business_id in self._business_states:
            info = self.analyze_business(business_id)
            if info and info.priority != SellPriority.SKIP:
                # In solo mode, deprioritize non-solo-friendly sells
                if self._solo_mode and not info.solo_friendly:
                    # Only include if critical
                    if info.priority != SellPriority.CRITICAL:
                        continue

                recommendations.append(info)

        # Sort by priority score (highest first)
        recommendations.sort(key=lambda x: x.priority_score, reverse=True)

        return recommendations[:limit]

    def create_sell_plan(
        self,
        max_time_minutes: int = 60,
        min_value: int = 100_000,
    ) -> SellPlan:
        """Create an optimized sell plan.

        Args:
            max_time_minutes: Maximum time to spend selling
            min_value: Minimum value threshold per sell

        Returns:
            SellPlan with recommended sells
        """
        plan = SellPlan()
        recommendations = self.get_sell_recommendations(limit=10)

        for info in recommendations:
            # Skip if below minimum value
            if info.estimated_value < min_value:
                continue

            # Check if we have time
            if plan.estimated_time_minutes + 15 > max_time_minutes:
                break

            # In solo mode, skip non-solo-friendly unless critical
            if self._solo_mode and not info.solo_friendly:
                if info.priority != SellPriority.CRITICAL:
                    continue

            plan.add_business(info)

        return plan

    def get_quick_sell(self) -> Optional[BusinessSellInfo]:
        """Get the single best business to sell right now.

        Returns:
            Best BusinessSellInfo or None
        """
        recommendations = self.get_sell_recommendations(limit=1)
        return recommendations[0] if recommendations else None

    def get_total_sellable_value(self) -> int:
        """Get total value of all sellable stock.

        Returns:
            Total estimated value in dollars
        """
        total = 0
        for business_id in self._business_states:
            info = self.analyze_business(business_id)
            if info:
                total += info.estimated_value
        return total

    def get_businesses_at_risk(self) -> list[BusinessSellInfo]:
        """Get businesses at raid risk.

        Returns:
            List of businesses that should be sold to avoid raids
        """
        at_risk = []
        for business_id in self._business_states:
            info = self.analyze_business(business_id)
            if info and info.priority == SellPriority.CRITICAL:
                if "raid" in info.notes.lower():
                    at_risk.append(info)
        return at_risk


def format_sell_plan(plan: SellPlan) -> str:
    """Format a sell plan for display.

    Args:
        plan: SellPlan to format

    Returns:
        Formatted string representation
    """
    if not plan.businesses:
        return "No sells recommended at this time."

    lines = [
        f"=== Sell Plan ({len(plan.businesses)} businesses) ===",
        f"Total Value: ${plan.total_value:,}",
        f"Est. Time: {plan.estimated_time_minutes} minutes",
        f"$/Hour: ${plan.value_per_hour:,.0f}",
        f"Solo Viable: {'Yes' if plan.solo_viable else 'No'}",
        "",
        "Sell Order:",
    ]

    for i, info in enumerate(plan.businesses, 1):
        priority_str = info.priority.name
        lines.append(
            f"  {i}. {info.business.name}: ${info.estimated_value:,} "
            f"({info.stock_percent}% stock, {info.vehicles_needed}v) [{priority_str}]"
        )
        if info.notes:
            lines.append(f"     Note: {info.notes}")

    return "\n".join(lines)
