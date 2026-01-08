"""Action scheduling for optimal workflow."""

from typing import List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto

from ..game.businesses import Business, BUSINESSES
from ..utils.logging import get_logger


logger = get_logger("optimization.scheduler")


class ActionType(Enum):
    """Types of scheduled actions."""

    SELL = auto()
    RESUPPLY = auto()
    COLLECT_SAFE = auto()
    CHECK_BUSINESS = auto()
    VIP_WORK = auto()
    PAYPHONE_HIT = auto()
    OTHER = auto()


@dataclass
class ScheduledAction:
    """A scheduled action."""

    action_type: ActionType
    business_id: Optional[str] = None
    scheduled_time: datetime = field(default_factory=datetime.now)
    description: str = ""
    estimated_value: int = 0
    priority: int = 5  # 1-10, lower is higher priority

    @property
    def is_due(self) -> bool:
        """Check if this action is due now."""
        return datetime.now() >= self.scheduled_time

    @property
    def time_until(self) -> timedelta:
        """Get time until this action is due."""
        return self.scheduled_time - datetime.now()


class ActionScheduler:
    """Schedules and tracks upcoming actions."""

    def __init__(self):
        """Initialize action scheduler."""
        self._scheduled: List[ScheduledAction] = []
        self._completed: List[ScheduledAction] = []
        self._max_history = 50

    def schedule_sell(
        self,
        business_id: str,
        when: Optional[datetime] = None,
        estimated_value: int = 0,
    ) -> ScheduledAction:
        """Schedule a sell mission.

        Args:
            business_id: Business to sell
            when: When to sell (default: now)
            estimated_value: Expected earnings

        Returns:
            Scheduled action
        """
        business = BUSINESSES.get(business_id)
        name = business.name if business else business_id

        action = ScheduledAction(
            action_type=ActionType.SELL,
            business_id=business_id,
            scheduled_time=when or datetime.now(),
            description=f"Sell {name}",
            estimated_value=estimated_value,
            priority=2,
        )

        self._add_action(action)
        return action

    def schedule_resupply(
        self,
        business_id: str,
        when: Optional[datetime] = None,
    ) -> ScheduledAction:
        """Schedule a resupply.

        Args:
            business_id: Business to resupply
            when: When to resupply

        Returns:
            Scheduled action
        """
        business = BUSINESSES.get(business_id)
        name = business.name if business else business_id

        action = ScheduledAction(
            action_type=ActionType.RESUPPLY,
            business_id=business_id,
            scheduled_time=when or datetime.now(),
            description=f"Resupply {name}",
            priority=3,
        )

        self._add_action(action)
        return action

    def schedule_check(
        self,
        business_id: str,
        when: datetime,
    ) -> ScheduledAction:
        """Schedule a business check (to read stock levels).

        Args:
            business_id: Business to check
            when: When to check

        Returns:
            Scheduled action
        """
        business = BUSINESSES.get(business_id)
        name = business.name if business else business_id

        action = ScheduledAction(
            action_type=ActionType.CHECK_BUSINESS,
            business_id=business_id,
            scheduled_time=when,
            description=f"Check {name}",
            priority=5,
        )

        self._add_action(action)
        return action

    def schedule_vip_work(self, cooldown_ends: datetime) -> ScheduledAction:
        """Schedule VIP work when cooldown ends.

        Args:
            cooldown_ends: When cooldown expires

        Returns:
            Scheduled action
        """
        action = ScheduledAction(
            action_type=ActionType.VIP_WORK,
            scheduled_time=cooldown_ends,
            description="VIP Work available",
            estimated_value=22500,
            priority=4,
        )

        self._add_action(action)
        return action

    def _add_action(self, action: ScheduledAction) -> None:
        """Add an action to the schedule.

        Args:
            action: Action to add
        """
        # Remove any existing action for same business/type
        self._scheduled = [
            a for a in self._scheduled
            if not (
                a.action_type == action.action_type
                and a.business_id == action.business_id
            )
        ]

        self._scheduled.append(action)
        self._scheduled.sort(key=lambda a: (a.scheduled_time, a.priority))

        logger.debug(f"Scheduled: {action.description} at {action.scheduled_time}")

    def complete_action(self, action: ScheduledAction) -> None:
        """Mark an action as complete.

        Args:
            action: Action that was completed
        """
        if action in self._scheduled:
            self._scheduled.remove(action)

        self._completed.append(action)
        if len(self._completed) > self._max_history:
            self._completed.pop(0)

    def cancel_action(self, action: ScheduledAction) -> None:
        """Cancel a scheduled action.

        Args:
            action: Action to cancel
        """
        if action in self._scheduled:
            self._scheduled.remove(action)
            logger.debug(f"Cancelled: {action.description}")

    def get_due_actions(self) -> List[ScheduledAction]:
        """Get all actions that are due now.

        Returns:
            List of due actions, ordered by priority
        """
        due = [a for a in self._scheduled if a.is_due]
        due.sort(key=lambda a: a.priority)
        return due

    def get_upcoming(self, limit: int = 10) -> List[ScheduledAction]:
        """Get upcoming scheduled actions.

        Args:
            limit: Maximum actions to return

        Returns:
            List of upcoming actions
        """
        return self._scheduled[:limit]

    def get_next_action(self) -> Optional[ScheduledAction]:
        """Get the next scheduled action.

        Returns:
            Next action or None
        """
        return self._scheduled[0] if self._scheduled else None

    def clear(self) -> None:
        """Clear all scheduled actions."""
        self._scheduled.clear()
        logger.info("Schedule cleared")

    @property
    def scheduled_count(self) -> int:
        """Get number of scheduled actions."""
        return len(self._scheduled)
