"""Reusable UI widgets."""

from .dashboard import DashboardWidget
from .session_panel import SessionPanel
from .business_panel import BusinessPanel
from .activity_panel import ActivityPanel
from .recommendations import RecommendationsPanel
from .cooldown_widget import CooldownWidget, CompactCooldownWidget, CooldownItemWidget
from .goal_widget import GoalWidget, GoalProgressWidget, GoalSetterDialog
from .passive_income_widget import PassiveIncomeWidget, CompactPassiveIncomeWidget
from .earnings_rate_widget import EarningsRateWidget, CompactEarningsRateWidget

__all__ = [
    "DashboardWidget",
    "SessionPanel",
    "BusinessPanel",
    "ActivityPanel",
    "RecommendationsPanel",
    "CooldownWidget",
    "CompactCooldownWidget",
    "CooldownItemWidget",
    "GoalWidget",
    "GoalProgressWidget",
    "GoalSetterDialog",
    "PassiveIncomeWidget",
    "CompactPassiveIncomeWidget",
    "EarningsRateWidget",
    "CompactEarningsRateWidget",
]
