"""Remember a goal target while keeping each statistics period's progress local."""

from dataclasses import replace
import json
from pathlib import Path
from threading import RLock
import unicodedata

from .goals import GoalTracker, GoalType, SessionGoal
from .session import SessionStats
from ..utils.logging import get_logger
from ..utils.persistence import atomic_text_writer


logger = get_logger("tracking.session_goals")

MAX_TARGET_FILE_BYTES = 16 * 1024
MAX_DISPLAY_NAME_LENGTH = 200
_NOT_REMEMBERED = object()
_Target = tuple[GoalType, int, str]


def _validated_target(goal_type: GoalType, target_value: int, display_name: str) -> _Target:
    """Validate before changing either the live attempt or its remembered target."""
    if not isinstance(goal_type, GoalType):
        raise ValueError("Choose a supported session goal type.")
    if type(target_value) is not int or target_value <= 0:
        raise ValueError("A session goal target must be a positive integer.")
    if not isinstance(display_name, str):
        raise ValueError("A session goal name must be text.")
    if display_name and not display_name.strip():
        raise ValueError("A session goal name cannot contain only whitespace.")
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(
            f"A session goal name must be at most {MAX_DISPLAY_NAME_LENGTH} characters."
        )
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in display_name):
        raise ValueError("A session goal name cannot contain control characters.")

    # Resolve the standalone goal's default name once, so subsequent attempts
    # and reloads use the same literal display text.
    goal = SessionGoal(goal_type, target_value, display_name)
    if len(goal.display_name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError("The target is too large for a session goal name.")
    try:
        # Earnings formatting uses a float for large dollar amounts. Reject a
        # hand-edited target that would otherwise crash the live goal display.
        _ = goal.remaining_formatted
    except OverflowError as error:
        raise ValueError("The target is too large to display as a session goal.") from error
    return goal_type, target_value, goal.display_name


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous duplicate fields in a saved target."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate session goal field.")
        result[key] = value
    return result


class SessionGoalController:
    """Own a live goal and atomically persist only its selected target.

    The app passes its actual SessionStats object to ``sync``. Object identity,
    rather than a timestamp or database session ID, defines a fresh attempt.
    Callers receive copies and never gain access to the private GoalTracker.
    """

    def __init__(self, target_path: Path):
        self._target_path = Path(target_path)
        self._lock = RLock()
        self._tracker = GoalTracker(data_path=None)
        self._period: SessionStats | None = None
        self._storage_error: str | None = None
        self._needs_save_retry = False
        self._remembered_target: _Target | None | object = _NOT_REMEMBERED
        self._load_target()

    @property
    def current_goal(self) -> SessionGoal | None:
        """Return a defensive snapshot of the current attempt."""
        with self._lock:
            goal = self._tracker.current_goal
            return replace(goal) if goal is not None else None

    @property
    def has_goal(self) -> bool:
        with self._lock:
            return self._tracker.has_goal

    @property
    def storage_error(self) -> str | None:
        """Return a safe display message, without file or exception details."""
        with self._lock:
            return self._storage_error

    @property
    def needs_save_retry(self) -> bool:
        with self._lock:
            return self._needs_save_retry

    def set_goal(
        self, goal_type: GoalType, target_value: int, display_name: str = ""
    ) -> SessionGoal:
        """Start a new live attempt and remember the validated target.

        A save failure leaves the new goal usable and sets ``needs_save_retry``.
        Selecting the already remembered target avoids an unnecessary write.
        """
        with self._lock:
            target = _validated_target(goal_type, target_value, display_name)
            goal = self._tracker.set_goal(*target)
            self._save_target()
            return replace(goal)

    def clear_goal(self) -> None:
        """Clear the live selection and remember that no target is selected."""
        with self._lock:
            self._tracker.clear_goal()
            self._save_target()

    def sync(
        self, period: SessionStats | None, earnings: int, activities: int, minutes: int
    ) -> bool:
        """Apply one absolute total, returning True only for its first crossing.

        A new period reuses the target with fresh progress. None represents no
        statistics period and therefore ignores totals. Progress and period
        changes never write the target file. The private tracker keeps first
        completion stable even if a later total decreases.
        """
        with self._lock:
            if period is not self._period:
                target = self._current_target()
                if target is not None:
                    self._tracker.set_goal(*target)
                # Hold the actual object so equality and recycled object IDs
                # cannot mistake a newly reset period for the previous one.
                self._period = period

            goal = self._tracker.current_goal
            if goal is None or period is None:
                return False
            if goal.goal_type == GoalType.EARNINGS:
                return self._tracker.update_earnings(max(0, earnings))
            if goal.goal_type == GoalType.ACTIVITIES:
                return self._tracker.update_activities(max(0, activities))
            return self._tracker.update_time(max(0, minutes))

    def retry_save(self) -> bool:
        """Retry an explicit failed target change; return False if none is pending.

        Merely retrying a load error must not overwrite the unreadable or invalid
        file. An explicit Set or Clear is required before replacement is allowed.
        """
        with self._lock:
            if not self._needs_save_retry:
                return False
            return self._save_target()

    def _current_target(self) -> _Target | None:
        goal = self._tracker.current_goal
        if goal is None:
            return None
        return goal.goal_type, goal.target_value, goal.display_name

    def _load_target(self) -> None:
        try:
            with self._target_path.open("rb") as stream:
                contents = stream.read(MAX_TARGET_FILE_BYTES + 1)
            if len(contents) > MAX_TARGET_FILE_BYTES:
                raise ValueError("Session goal target file is too large.")
            data = json.loads(contents.decode("utf-8"), object_pairs_hook=_unique_json_object)
            if not isinstance(data, dict) or set(data) != {"version", "target"}:
                raise ValueError("Invalid session goal target document.")
            if type(data["version"]) is not int or data["version"] != 1:
                raise ValueError("Unsupported session goal target version.")
            target = data["target"]
            if target is not None:
                if not isinstance(target, dict) or set(target) != {
                    "goal_type", "target_value", "display_name"
                }:
                    raise ValueError("Invalid session goal target fields.")
                validated = _validated_target(
                    GoalType[target["goal_type"]], target["target_value"], target["display_name"]
                )
                self._tracker.set_goal(*validated)
            self._remembered_target = self._current_target()
        except FileNotFoundError:
            # Reading a missing target must not create directories or a file.
            pass
        except Exception:
            self._storage_error = (
                "The saved session goal could not be loaded. "
                "Set or clear a goal to replace it."
            )
            logger.warning("Unable to load the session goal target", exc_info=True)

    def _save_target(self) -> bool:
        """Write a selection while the controller lock is held, never progress."""
        target = self._current_target()
        if target == self._remembered_target and not self._needs_save_retry:
            return True
        payload = None
        if target is not None:
            goal_type, target_value, display_name = target
            payload = {
                "goal_type": goal_type.name,
                "target_value": target_value,
                "display_name": display_name,
            }
        try:
            with atomic_text_writer(self._target_path) as stream:
                json.dump({"version": 1, "target": payload}, stream, indent=2)
        except Exception:
            self._needs_save_retry = True
            self._storage_error = (
                "The selected session goal could not be remembered. "
                "The current selection still applies in this app. Try saving again."
            )
            logger.warning("Unable to save the session goal target", exc_info=True)
            return False
        self._remembered_target = target
        self._needs_save_retry = False
        self._storage_error = None
        return True
