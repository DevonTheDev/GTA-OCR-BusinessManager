"""Workflow optimization and recommendation engine."""

from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..game.businesses import Business, BUSINESSES, calculate_value_per_hour
from ..game.activities import ActivityType
from .priorities import PriorityCalculator, PriorityScore
from .scheduler import ActionScheduler, ScheduledAction
from ..utils.logging import get_logger


logger = get_logger("optimization")


@dataclass
class Recommendation:
    """A workflow recommendation."""

    priority: int  # 1 = highest
    action: str
    reason: str
    estimated_value: int = 0
    estimated_time_minutes: int = 0
    business_type: Optional[str] = None
    activity_type: Optional[ActivityType] = None
    score: float = 0.0  # Raw priority score for sorting


@dataclass
class BusinessState:
    """Current state of a business."""

    business: Business
    stock_percent: int = 0
    supply_percent: int = 0
    last_updated: datetime = None
    estimated_value: int = 0


class Optimizer:
    """Generates workflow recommendations based on current state."""

    def __init__(self, solo_mode: bool = True):
        """Initialize optimizer.

        Args:
            solo_mode: Whether player is playing solo (affects sell recommendations)
        """
        self._solo_mode = solo_mode
        self._business_states: Dict[str, BusinessState] = {}
        self._cooldowns: Dict[str, datetime] = {}

        # Initialize priority calculator and scheduler
        self._priority_calc = PriorityCalculator()
        self._scheduler = ActionScheduler()

        # Activity value estimates
        self._activity_values = {
            "headhunter": 22500,
            "sightseer": 22500,
            "payphone_hit": 85000,
            "security_contract": 50000,
            "auto_shop_contract": 170000,
            "cayo_perico": 1500000,
            "casino_heist": 1800000,
        }

    def update_business_state(
        self,
        business_id: str,
        stock_percent: int,
        supply_percent: int,
        value: int = 0,
    ) -> None:
        """Update the known state of a business.

        Args:
            business_id: Business identifier
            stock_percent: Current stock level (0-100)
            supply_percent: Current supply level (0-100)
            value: Current stock value if known
        """
        business = BUSINESSES.get(business_id)
        if not business:
            return

        estimated_value = value or int(business.max_value * (stock_percent / 100))

        self._business_states[business_id] = BusinessState(
            business=business,
            stock_percent=stock_percent,
            supply_percent=supply_percent,
            last_updated=datetime.now(),
            estimated_value=estimated_value,
        )

        # Update scheduler with business state
        self._scheduler.update_business_stock(business_id, stock_percent)

    def set_cooldown(self, activity: str, duration_minutes: int) -> None:
        """Set a cooldown for an activity.

        Args:
            activity: Activity name
            duration_minutes: Cooldown duration
        """
        self._cooldowns[activity] = datetime.now() + timedelta(minutes=duration_minutes)

    def is_on_cooldown(self, activity: str) -> bool:
        """Check if an activity is on cooldown.

        Args:
            activity: Activity name

        Returns:
            True if on cooldown
        """
        if activity not in self._cooldowns:
            return False
        return datetime.now() < self._cooldowns[activity]

    def get_cooldown_remaining(self, activity: str) -> int:
        """Get remaining cooldown time in minutes.

        Args:
            activity: Activity name

        Returns:
            Minutes remaining, or 0 if not on cooldown
        """
        if activity not in self._cooldowns:
            return 0
        remaining = self._cooldowns[activity] - datetime.now()
        return max(0, int(remaining.total_seconds() / 60))

    def get_recommendations(self, limit: int = 5) -> List[Recommendation]:
        """Generate prioritized recommendations.

        Args:
            limit: Maximum recommendations to return

        Returns:
            List of recommendations, highest priority first
        """
        recommendations = []

        # Check businesses with calculated priorities
        recommendations.extend(self._check_businesses_with_priority())

        # Check for quick activities
        recommendations.extend(self._suggest_quick_activities())

        # Add scheduled actions
        recommendations.extend(self._get_scheduled_recommendations())

        # Remove duplicates (same action)
        seen_actions = set()
        unique_recs = []
        for rec in recommendations:
            if rec.action not in seen_actions:
                seen_actions.add(rec.action)
                unique_recs.append(rec)

        # Sort by priority (lower = higher priority), then by score (higher = better)
        unique_recs.sort(key=lambda r: (r.priority, -r.score))
        return unique_recs[:limit]

    def _check_businesses_with_priority(self) -> List[Recommendation]:
        """Check business states using PriorityCalculator."""
        recommendations = []

        for business_id, state in self._business_states.items():
            business = state.business

            # Calculate sell priority
            time_since_update = 0
            if state.last_updated:
                time_since_update = int((datetime.now() - state.last_updated).total_seconds() / 60)

            sell_score = self._priority_calc.calculate_sell_priority(
                business=business,
                stock_percent=state.stock_percent,
                time_since_check_minutes=time_since_update,
                solo_mode=self._solo_mode,
            )

            # Generate recommendation based on score
            if state.stock_percent >= 95:
                # Full stock - high priority sell
                priority = 1
                action = f"Sell {business.name} NOW"
                reason = f"Stock is full ({state.stock_percent}%). Max value: ${state.estimated_value:,}"
            elif sell_score.total >= 0.6:
                # High priority sell
                priority = 1
                action = f"Sell {business.name}"
                reason = f"High value ready ({state.stock_percent}% stock)"
            elif sell_score.total >= 0.4 and self._solo_mode:
                # Medium priority - good for solo
                priority = 2
                action = f"Consider selling {business.name}"
                reason = f"Good for solo sell ({state.stock_percent}%)"
            elif state.stock_percent >= 50:
                # Has some stock but not urgent
                priority = 3
                action = f"Sell {business.name} when ready"
                reason = f"Stock at {state.stock_percent}%"
            else:
                # Not worth selling yet
                sell_score = None

            if sell_score and sell_score.total > 0.2:
                recommendations.append(
                    Recommendation(
                        priority=priority,
                        action=action,
                        reason=reason,
                        estimated_value=state.estimated_value,
                        estimated_time_minutes=15,
                        business_type=business_id,
                        activity_type=ActivityType.SELL_MISSION,
                        score=sell_score.total,
                    )
                )

            # Calculate resupply priority
            resupply_score = self._priority_calc.calculate_resupply_priority(
                business=business,
                supply_percent=state.supply_percent,
                stock_percent=state.stock_percent,
            )

            if resupply_score.total >= 0.4:
                if state.supply_percent == 0:
                    priority = 1
                    reason = f"Supplies EMPTY - production halted!"
                elif state.supply_percent <= 20:
                    priority = 2
                    reason = f"Supplies critically low ({state.supply_percent}%)"
                else:
                    priority = 3
                    reason = f"Supplies at {state.supply_percent}%"

                recommendations.append(
                    Recommendation(
                        priority=priority,
                        action=f"Resupply {business.name}",
                        reason=reason,
                        estimated_value=0,
                        estimated_time_minutes=5,
                        business_type=business_id,
                        activity_type=ActivityType.RESUPPLY_MISSION,
                        score=resupply_score.total,
                    )
                )

        return recommendations

    def _suggest_quick_activities(self) -> List[Recommendation]:
        """Suggest quick money-making activities based on cooldowns."""
        recommendations = []

        # Payphone Hit - highest payout quick activity
        if not self.is_on_cooldown("payphone_hit"):
            recommendations.append(
                Recommendation(
                    priority=2,
                    action="Do Payphone Hit",
                    reason="$85K with bonus, ~5 min",
                    estimated_value=85000,
                    estimated_time_minutes=5,
                    activity_type=ActivityType.PAYPHONE_HIT,
                    score=0.85,
                )
            )
        else:
            remaining = self.get_cooldown_remaining("payphone_hit")
            if remaining <= 5:
                recommendations.append(
                    Recommendation(
                        priority=4,
                        action=f"Payphone Hit available in {remaining}m",
                        reason="Cooldown almost done",
                        estimated_value=85000,
                        estimated_time_minutes=remaining + 5,
                        activity_type=ActivityType.PAYPHONE_HIT,
                        score=0.3,
                    )
                )

        # VIP Work - Headhunter
        if not self.is_on_cooldown("headhunter"):
            recommendations.append(
                Recommendation(
                    priority=3,
                    action="Run Headhunter",
                    reason="Quick $20-25K, ~4 min",
                    estimated_value=22500,
                    estimated_time_minutes=4,
                    activity_type=ActivityType.VIP_WORK,
                    score=0.6,
                )
            )

        # VIP Work - Sightseer
        if not self.is_on_cooldown("sightseer"):
            recommendations.append(
                Recommendation(
                    priority=3,
                    action="Run Sightseer",
                    reason="Easy $20-25K, ~5 min",
                    estimated_value=22500,
                    estimated_time_minutes=5,
                    activity_type=ActivityType.VIP_WORK,
                    score=0.55,
                )
            )

        # Security Contract
        recommendations.append(
            Recommendation(
                priority=3,
                action="Run Security Contract",
                reason="$30-70K depending on difficulty",
                estimated_value=50000,
                estimated_time_minutes=10,
                activity_type=ActivityType.SECURITY_CONTRACT,
                score=0.5,
            )
        )

        # Auto Shop Contract (if available)
        recommendations.append(
            Recommendation(
                priority=3,
                action="Auto Shop Contract",
                reason="$170K+ for Union Depository",
                estimated_value=170000,
                estimated_time_minutes=15,
                activity_type=ActivityType.AUTO_SHOP,
                score=0.65,
            )
        )

        return recommendations

    def _get_scheduled_recommendations(self) -> List[Recommendation]:
        """Get recommendations from scheduler for upcoming due items."""
        recommendations = []

        due_actions = self._scheduler.get_due_actions()
        for action in due_actions:
            if action.action_type == "sell":
                recommendations.append(
                    Recommendation(
                        priority=1,
                        action=f"Scheduled: Sell {action.business_id}",
                        reason=action.notes or "Scheduled sell mission",
                        estimated_value=0,
                        estimated_time_minutes=15,
                        business_type=action.business_id,
                        activity_type=ActivityType.SELL_MISSION,
                        score=0.9,
                    )
                )
            elif action.action_type == "resupply":
                recommendations.append(
                    Recommendation(
                        priority=2,
                        action=f"Scheduled: Resupply {action.business_id}",
                        reason=action.notes or "Scheduled resupply",
                        estimated_value=0,
                        estimated_time_minutes=5,
                        business_type=action.business_id,
                        activity_type=ActivityType.RESUPPLY_MISSION,
                        score=0.7,
                    )
                )

        return recommendations

    def complete_activity(self, activity_type: str, success: bool = True) -> None:
        """Record completion of an activity for cooldown tracking.

        Args:
            activity_type: Type of activity completed
            success: Whether activity was successful
        """
        cooldown_map = {
            "payphone_hit": 20,  # 20 minute cooldown
            "headhunter": 5,
            "sightseer": 5,
            "hostile_takeover": 5,
            "security_contract": 0,  # No cooldown
        }

        if activity_type.lower() in cooldown_map:
            self.set_cooldown(activity_type.lower(), cooldown_map[activity_type.lower()])

    def estimate_time_to_full(self, business_id: str) -> int:
        """Estimate minutes until a business is full.

        Args:
            business_id: Business identifier

        Returns:
            Estimated minutes, or 0 if unknown
        """
        if business_id not in self._business_states:
            return 0

        state = self._business_states[business_id]
        business = state.business

        if state.stock_percent >= 100:
            return 0

        remaining_percent = 100 - state.stock_percent
        time_for_full = business.full_production_time
        return int(time_for_full * (remaining_percent / 100))

    def get_business_rankings(self) -> List[tuple]:
        """Get businesses ranked by sell priority.

        Returns:
            List of (business_id, score, state) tuples
        """
        business_dict = {
            bid: (state.stock_percent, state.supply_percent)
            for bid, state in self._business_states.items()
        }

        rankings = self._priority_calc.rank_businesses(business_dict, self._solo_mode)

        result = []
        for business_id, score in rankings:
            state = self._business_states.get(business_id)
            if state:
                result.append((business_id, score, state))

        return result

    def get_summary(self) -> Dict:
        """Get a summary of current optimization state.

        Returns:
            Dict with summary information
        """
        total_value = sum(s.estimated_value for s in self._business_states.values())
        businesses_ready = sum(
            1 for s in self._business_states.values() if s.stock_percent >= 50
        )
        businesses_need_supplies = sum(
            1 for s in self._business_states.values() if s.supply_percent <= 20
        )

        # Get top priority business
        rankings = self.get_business_rankings()
        top_business = rankings[0] if rankings else None

        return {
            "total_business_value": total_value,
            "businesses_ready_to_sell": businesses_ready,
            "businesses_need_supplies": businesses_need_supplies,
            "active_cooldowns": len([c for c in self._cooldowns.values() if c > datetime.now()]),
            "top_priority_business": top_business[0] if top_business else None,
            "scheduled_actions": len(self._scheduler.get_upcoming_actions()),
        }

    def schedule_sell(
        self,
        business_id: str,
        when: Optional[datetime] = None,
        notes: str = "",
    ) -> bool:
        """Schedule a sell mission.

        Args:
            business_id: Business to sell
            when: When to sell (default: now)
            notes: Optional notes

        Returns:
            True if scheduled successfully
        """
        return self._scheduler.schedule_sell(business_id, when, notes) is not None

    def schedule_resupply(
        self,
        business_id: str,
        when: Optional[datetime] = None,
        notes: str = "",
    ) -> bool:
        """Schedule a resupply mission.

        Args:
            business_id: Business to resupply
            when: When to resupply (default: now)
            notes: Optional notes

        Returns:
            True if scheduled successfully
        """
        return self._scheduler.schedule_resupply(business_id, when, notes) is not None

    def clear_schedule(self) -> None:
        """Clear all scheduled actions."""
        self._scheduler.clear_schedule()
