"""Game state machine for tracking GTA Online activity."""

from collections import deque
from enum import Enum, auto
from typing import Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..constants import TRACKING
from ..utils.logging import get_logger


logger = get_logger("game.state")


class GameState(Enum):
    """Possible game states."""

    UNKNOWN = auto()  # Can't determine state
    IDLE = auto()  # Freeroam, no active mission
    LOADING = auto()  # Loading screen
    MISSION_STARTING = auto()  # Mission lobby/starting
    MISSION_ACTIVE = auto()  # Mission in progress
    MISSION_COMPLETE = auto()  # Mission passed screen
    MISSION_FAILED = auto()  # Mission failed screen
    BUSINESS_COMPUTER = auto()  # Viewing business laptop
    SELLING = auto()  # Sell mission active
    HEIST_PREP = auto()  # Heist preparation
    HEIST_FINALE = auto()  # Heist finale
    MENU = auto()  # Pause menu open
    PHONE = auto()  # Phone open
    CUTSCENE = auto()  # Cutscene playing
    SPECTATING = auto()  # Spectating other players


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


@dataclass
class StateTransition:
    """Record of a state transition."""

    from_state: GameState
    to_state: GameState
    timestamp: datetime = field(default_factory=_utc_now)
    trigger: str = ""  # What caused the transition


@dataclass
class StateContext:
    """Context information about current state."""

    state: GameState = GameState.UNKNOWN
    entered_at: datetime = field(default_factory=_utc_now)
    mission_name: Optional[str] = None
    business_type: Optional[str] = None
    money_at_start: Optional[int] = None
    transitions: deque[StateTransition] = field(
        default_factory=lambda: deque(maxlen=TRACKING.MAX_STATE_HISTORY)
    )

    @property
    def time_in_state(self) -> float:
        """Get seconds spent in current state."""
        now = _utc_now()
        entered = self.entered_at
        # Handle timezone-aware vs naive datetime comparison
        if now.tzinfo is not None and entered.tzinfo is None:
            now = now.replace(tzinfo=None)
        elif entered.tzinfo is not None and now.tzinfo is None:
            entered = entered.replace(tzinfo=None)
        return (now - entered).total_seconds()


class GameStateMachine:
    """Finite state machine for tracking game state."""

    # Valid state transitions
    VALID_TRANSITIONS = {
        GameState.UNKNOWN: {
            GameState.IDLE,
            GameState.LOADING,
            GameState.MENU,
            GameState.MISSION_ACTIVE,
        },
        GameState.IDLE: {
            GameState.LOADING,
            GameState.MISSION_STARTING,
            GameState.MISSION_ACTIVE,
            GameState.BUSINESS_COMPUTER,
            GameState.MENU,
            GameState.PHONE,
            GameState.SELLING,
            GameState.HEIST_PREP,
        },
        GameState.LOADING: {
            GameState.IDLE,
            GameState.MISSION_STARTING,
            GameState.MISSION_ACTIVE,
            GameState.CUTSCENE,
            GameState.HEIST_FINALE,
        },
        GameState.MISSION_STARTING: {
            GameState.MISSION_ACTIVE,
            GameState.LOADING,
            GameState.IDLE,  # Mission cancelled
        },
        GameState.MISSION_ACTIVE: {
            GameState.MISSION_COMPLETE,
            GameState.MISSION_FAILED,
            GameState.LOADING,
            GameState.CUTSCENE,
            GameState.MENU,
        },
        GameState.MISSION_COMPLETE: {
            GameState.IDLE,
            GameState.LOADING,
            GameState.MISSION_STARTING,
        },
        GameState.MISSION_FAILED: {
            GameState.IDLE,
            GameState.LOADING,
            GameState.MISSION_STARTING,  # Retry
        },
        GameState.BUSINESS_COMPUTER: {
            GameState.IDLE,
            GameState.SELLING,
            GameState.MENU,
        },
        GameState.SELLING: {
            GameState.MISSION_COMPLETE,
            GameState.MISSION_FAILED,
            GameState.IDLE,
            GameState.LOADING,
        },
        GameState.HEIST_PREP: {
            GameState.MISSION_ACTIVE,
            GameState.MISSION_COMPLETE,
            GameState.IDLE,
            GameState.LOADING,
        },
        GameState.HEIST_FINALE: {
            GameState.MISSION_ACTIVE,
            GameState.MISSION_COMPLETE,
            GameState.MISSION_FAILED,
            GameState.LOADING,
            GameState.CUTSCENE,
        },
        GameState.MENU: {
            GameState.IDLE,
            GameState.MISSION_ACTIVE,
            GameState.BUSINESS_COMPUTER,
        },
        GameState.PHONE: {
            GameState.IDLE,
            GameState.MISSION_STARTING,
        },
        GameState.CUTSCENE: {
            GameState.IDLE,
            GameState.MISSION_ACTIVE,
            GameState.MISSION_COMPLETE,
            GameState.LOADING,
        },
        GameState.SPECTATING: {
            GameState.IDLE,
            GameState.LOADING,
        },
    }

    def __init__(self):
        """Initialize state machine."""
        self._context = StateContext()
        self._listeners: list[Callable[[StateTransition], None]] = []

    @property
    def state(self) -> GameState:
        """Get current state."""
        return self._context.state

    @property
    def context(self) -> StateContext:
        """Get current state context."""
        return self._context

    def transition_to(self, new_state: GameState, trigger: str = "") -> bool:
        """Attempt to transition to a new state.

        Args:
            new_state: Target state
            trigger: Description of what triggered the transition

        Returns:
            True if transition was valid and executed
        """
        current = self._context.state

        # Allow transition from any state if we can't determine validity
        if current == GameState.UNKNOWN or new_state == GameState.UNKNOWN:
            valid = True
        else:
            valid_targets = self.VALID_TRANSITIONS.get(current, set())
            valid = new_state in valid_targets

        if not valid:
            logger.warning(
                f"Invalid state transition: {current.name} -> {new_state.name}"
            )
            # Allow it anyway but log warning
            # In practice, game state detection might not be perfect

        # Record transition
        transition = StateTransition(
            from_state=current,
            to_state=new_state,
            timestamp=_utc_now(),
            trigger=trigger,
        )

        # Update context - copy transitions deque and add new transition
        new_transitions: deque[StateTransition] = deque(
            self._context.transitions, maxlen=TRACKING.MAX_STATE_HISTORY
        )
        new_transitions.append(transition)

        old_context = self._context
        self._context = StateContext(
            state=new_state,
            entered_at=_utc_now(),
            mission_name=old_context.mission_name if self._should_keep_mission(new_state) else None,
            business_type=old_context.business_type,
            money_at_start=old_context.money_at_start,
            transitions=new_transitions,
        )

        logger.info(f"State transition: {current.name} -> {new_state.name} ({trigger})")

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(transition)
            except Exception as e:
                logger.error(f"State listener error: {e}")

        return True

    def _should_keep_mission(self, new_state: GameState) -> bool:
        """Check if mission context should be preserved."""
        return new_state in {
            GameState.MISSION_ACTIVE,
            GameState.MISSION_COMPLETE,
            GameState.MISSION_FAILED,
            GameState.LOADING,
            GameState.CUTSCENE,
            GameState.SELLING,
        }

    def set_mission_name(self, name: str) -> None:
        """Set the current mission name."""
        self._context.mission_name = name
        logger.debug(f"Mission name set: {name}")

    def set_business_type(self, business: str) -> None:
        """Set the current business type being viewed."""
        self._context.business_type = business
        logger.debug(f"Business type set: {business}")

    def set_money_at_start(self, amount: int) -> None:
        """Set money at start of current activity."""
        self._context.money_at_start = amount

    def add_listener(self, callback: Callable[[StateTransition], None]) -> None:
        """Add a state transition listener.

        Args:
            callback: Function called with StateTransition on each transition
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[StateTransition], None]) -> None:
        """Remove a state transition listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def get_recent_transitions(self, count: int = 10) -> list[StateTransition]:
        """Get recent state transitions.

        Args:
            count: Number of transitions to return

        Returns:
            List of recent transitions (newest first)
        """
        return list(reversed(self._context.transitions[-count:]))

    def is_in_activity(self) -> bool:
        """Check if currently in a tracked activity."""
        return self._context.state in {
            GameState.MISSION_ACTIVE,
            GameState.SELLING,
            GameState.HEIST_PREP,
            GameState.HEIST_FINALE,
        }

    def is_available_for_activity(self) -> bool:
        """Check if player could start a new activity."""
        return self._context.state in {
            GameState.IDLE,
            GameState.BUSINESS_COMPUTER,
        }

    def reset(self) -> None:
        """Reset state machine to initial state."""
        self._context = StateContext()
        logger.info("State machine reset")
