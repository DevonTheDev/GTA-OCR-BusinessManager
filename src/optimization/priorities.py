"""Priority calculation for business and activity recommendations."""

from typing import Dict, List, Tuple
from dataclasses import dataclass

from ..game.businesses import Business, BUSINESSES, calculate_value_per_hour


@dataclass
class PriorityScore:
    """Score breakdown for a business or activity."""

    total: float
    value_score: float
    urgency_score: float
    efficiency_score: float
    cooldown_penalty: float


class PriorityCalculator:
    """Calculates priorities for businesses and activities."""

    def __init__(self):
        """Initialize priority calculator."""
        self._weights = {
            "value": 0.4,
            "urgency": 0.3,
            "efficiency": 0.2,
            "cooldown": 0.1,
        }

    def calculate_sell_priority(
        self,
        business: Business,
        stock_percent: int,
        time_since_check_minutes: int = 0,
        solo_mode: bool = True,
    ) -> PriorityScore:
        """Calculate priority for selling a business.

        Args:
            business: Business to evaluate
            stock_percent: Current stock level
            time_since_check_minutes: Time since last check
            solo_mode: Whether playing solo

        Returns:
            PriorityScore with breakdown
        """
        # Value score (based on current value)
        current_value = business.max_value * (stock_percent / 100)
        value_score = min(1.0, current_value / 500000)  # Normalize to ~$500K max

        # Urgency score (higher when full or near full)
        if stock_percent >= 100:
            urgency_score = 1.0
        elif stock_percent >= 80:
            urgency_score = 0.7
        elif stock_percent >= 50:
            urgency_score = 0.4 if solo_mode else 0.2
        else:
            urgency_score = 0.1

        # Efficiency score (based on $/hour of the business)
        rate = calculate_value_per_hour(business)
        efficiency_score = min(1.0, rate / 150000)  # Normalize to ~$150K/hr

        # No cooldown for sells
        cooldown_penalty = 0.0

        # Calculate total
        total = (
            value_score * self._weights["value"]
            + urgency_score * self._weights["urgency"]
            + efficiency_score * self._weights["efficiency"]
            - cooldown_penalty * self._weights["cooldown"]
        )

        return PriorityScore(
            total=total,
            value_score=value_score,
            urgency_score=urgency_score,
            efficiency_score=efficiency_score,
            cooldown_penalty=cooldown_penalty,
        )

    def calculate_resupply_priority(
        self,
        business: Business,
        supply_percent: int,
        stock_percent: int,
    ) -> PriorityScore:
        """Calculate priority for resupplying a business.

        Args:
            business: Business to evaluate
            supply_percent: Current supply level
            stock_percent: Current stock level

        Returns:
            PriorityScore with breakdown
        """
        # Value score (based on potential value when supplies convert)
        potential_value = business.max_value - (business.max_value * stock_percent / 100)
        value_score = min(1.0, potential_value / 500000)

        # Urgency score (higher when supplies are low/empty)
        if supply_percent == 0:
            urgency_score = 1.0
        elif supply_percent <= 20:
            urgency_score = 0.7
        elif supply_percent <= 50:
            urgency_score = 0.3
        else:
            urgency_score = 0.0

        # Efficiency - buying supplies is always more efficient than stealing
        efficiency_score = 0.8  # Fixed for buy supplies

        cooldown_penalty = 0.0

        total = (
            value_score * self._weights["value"]
            + urgency_score * self._weights["urgency"]
            + efficiency_score * self._weights["efficiency"]
        )

        return PriorityScore(
            total=total,
            value_score=value_score,
            urgency_score=urgency_score,
            efficiency_score=efficiency_score,
            cooldown_penalty=cooldown_penalty,
        )

    def rank_businesses(
        self,
        business_states: Dict[str, Tuple[int, int]],  # {id: (stock%, supply%)}
        solo_mode: bool = True,
    ) -> List[Tuple[str, PriorityScore]]:
        """Rank businesses by sell priority.

        Args:
            business_states: Dict of business states
            solo_mode: Whether playing solo

        Returns:
            List of (business_id, score) tuples, highest priority first
        """
        rankings = []

        for business_id, (stock, supply) in business_states.items():
            business = BUSINESSES.get(business_id)
            if business:
                score = self.calculate_sell_priority(business, stock, solo_mode=solo_mode)
                rankings.append((business_id, score))

        # Sort by total score descending
        rankings.sort(key=lambda x: x[1].total, reverse=True)
        return rankings
