"""Analytics and statistics for GTA Business Manager."""

from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..game.activities import Activity, ActivityType
from ..utils.logging import get_logger


logger = get_logger("tracking.analytics")


@dataclass
class EarningsBreakdown:
    """Breakdown of earnings by source."""

    total: int = 0
    from_missions: int = 0
    from_sells: int = 0
    from_vip_work: int = 0
    from_heists: int = 0
    from_other: int = 0


@dataclass
class TimeBreakdown:
    """Breakdown of time spent."""

    total_seconds: float = 0
    in_missions: float = 0
    in_sells: float = 0
    in_freeroam: float = 0
    idle: float = 0


@dataclass
class EfficiencyMetrics:
    """Efficiency metrics."""

    earnings_per_hour: float = 0
    activities_per_hour: float = 0
    avg_mission_duration: float = 0
    mission_success_rate: float = 0
    best_activity_type: Optional[str] = None
    best_activity_rate: float = 0


class Analytics:
    """Calculates analytics from activity data."""

    def calculate_earnings_breakdown(self, activities: List[Activity]) -> EarningsBreakdown:
        """Calculate earnings breakdown from activities.

        Args:
            activities: List of completed activities

        Returns:
            EarningsBreakdown with categorized earnings
        """
        breakdown = EarningsBreakdown()

        for activity in activities:
            if not activity.success:
                continue

            breakdown.total += activity.earnings

            if activity.activity_type == ActivityType.SELL_MISSION:
                breakdown.from_sells += activity.earnings
            elif activity.activity_type in (ActivityType.VIP_WORK, ActivityType.MC_CONTRACT):
                breakdown.from_vip_work += activity.earnings
            elif activity.activity_type in (
                ActivityType.HEIST_FINALE,
                ActivityType.CAYO_PERICO,
                ActivityType.CASINO_HEIST,
            ):
                breakdown.from_heists += activity.earnings
            elif activity.activity_type == ActivityType.CONTACT_MISSION:
                breakdown.from_missions += activity.earnings
            else:
                breakdown.from_other += activity.earnings

        return breakdown

    def calculate_time_breakdown(
        self, activities: List[Activity], total_session_time: float
    ) -> TimeBreakdown:
        """Calculate time breakdown from activities.

        Args:
            activities: List of completed activities
            total_session_time: Total session duration in seconds

        Returns:
            TimeBreakdown with categorized time
        """
        breakdown = TimeBreakdown(total_seconds=total_session_time)

        for activity in activities:
            duration = activity.duration_seconds

            if activity.activity_type == ActivityType.SELL_MISSION:
                breakdown.in_sells += duration
            else:
                breakdown.in_missions += duration

        # Freeroam is total minus tracked activities
        tracked_time = breakdown.in_missions + breakdown.in_sells
        breakdown.in_freeroam = max(0, total_session_time - tracked_time)

        return breakdown

    def calculate_efficiency(
        self, activities: List[Activity], total_session_time: float
    ) -> EfficiencyMetrics:
        """Calculate efficiency metrics.

        Args:
            activities: List of completed activities
            total_session_time: Total session duration in seconds

        Returns:
            EfficiencyMetrics
        """
        metrics = EfficiencyMetrics()

        if not activities or total_session_time <= 0:
            return metrics

        hours = total_session_time / 3600
        successful = [a for a in activities if a.success]

        # Total earnings per hour
        total_earnings = sum(a.earnings for a in successful)
        metrics.earnings_per_hour = total_earnings / hours if hours > 0 else 0

        # Activities per hour
        metrics.activities_per_hour = len(activities) / hours if hours > 0 else 0

        # Average mission duration
        if activities:
            total_duration = sum(a.duration_seconds for a in activities)
            metrics.avg_mission_duration = total_duration / len(activities)

        # Success rate
        if activities:
            metrics.mission_success_rate = len(successful) / len(activities)

        # Find best activity type
        type_earnings: Dict[ActivityType, int] = {}
        type_time: Dict[ActivityType, float] = {}

        for activity in successful:
            atype = activity.activity_type
            type_earnings[atype] = type_earnings.get(atype, 0) + activity.earnings
            type_time[atype] = type_time.get(atype, 0) + activity.duration_seconds

        best_rate = 0
        best_type = None

        for atype in type_earnings:
            time_hours = type_time[atype] / 3600
            if time_hours > 0:
                rate = type_earnings[atype] / time_hours
                if rate > best_rate:
                    best_rate = rate
                    best_type = atype

        if best_type:
            metrics.best_activity_type = best_type.name
            metrics.best_activity_rate = best_rate

        return metrics

    def get_recommendations(
        self, activities: List[Activity], current_businesses: Dict
    ) -> List[str]:
        """Generate recommendations based on activity history.

        Args:
            activities: List of completed activities
            current_businesses: Current business states

        Returns:
            List of recommendation strings
        """
        recommendations = []

        if not activities:
            recommendations.append("Start completing activities to get personalized recommendations!")
            return recommendations

        # Calculate efficiency for different activity types
        type_rates: Dict[str, float] = {}

        for atype in ActivityType:
            type_activities = [a for a in activities if a.activity_type == atype and a.success]
            if type_activities:
                total_earnings = sum(a.earnings for a in type_activities)
                total_time = sum(a.duration_seconds for a in type_activities)
                if total_time > 0:
                    type_rates[atype.name] = (total_earnings / total_time) * 3600

        # Recommend highest earning activity
        if type_rates:
            best = max(type_rates.items(), key=lambda x: x[1])
            recommendations.append(
                f"Your most efficient activity is {best[0]} at ${best[1]:,.0f}/hour"
            )

        # Check for low success rate activities
        for atype in ActivityType:
            type_activities = [a for a in activities if a.activity_type == atype]
            if len(type_activities) >= 3:
                successes = sum(1 for a in type_activities if a.success)
                rate = successes / len(type_activities)
                if rate < 0.5:
                    recommendations.append(
                        f"Consider practicing {atype.name} - your success rate is only {rate:.0%}"
                    )

        return recommendations[:5]  # Limit to 5 recommendations
