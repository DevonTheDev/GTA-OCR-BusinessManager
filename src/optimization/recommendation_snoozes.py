"""Bounded, in-memory presentation preferences for generated recommendations."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
import time

from .optimizer import Recommendation


SNOOZE_SECONDS = 600
MAX_ACTIVE_SNOOZES = 128


@dataclass(frozen=True)
class RecommendationSnapshot:
    """One candidate view filtered against a single sample of active snoozes."""

    visible: tuple[Recommendation, ...]
    total_candidates: int
    hidden_count: int
    snoozed_count: int


class RecommendationSnoozes:
    """Synchronize fixed-duration snoozes without modifying recommendation sources.

    The owner validates IDs against its current generated candidates. This
    registry owns elapsed-time expiry and never persists or evicts active IDs.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = Lock()
        self._deadlines: dict[str, float] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [identifier for identifier, deadline in self._deadlines.items()
                   if now >= deadline]
        for identifier in expired:
            del self._deadlines[identifier]

    def snooze(self, recommendation_id: str) -> bool:
        """Hide an ID for ten minutes; duplicates retain their original deadline."""
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            if not isinstance(recommendation_id, str) or not recommendation_id.strip():
                return False
            if recommendation_id in self._deadlines:
                return True
            if len(self._deadlines) >= MAX_ACTIVE_SNOOZES:
                return False
            self._deadlines[recommendation_id] = now + SNOOZE_SECONDS
            return True

    def active_ids(self) -> frozenset[str]:
        """Return a detached active-ID set using exactly one elapsed-time sample."""
        with self._lock:
            self._prune_locked(self._clock())
            return frozenset(self._deadlines)

    def restore_all(self) -> None:
        """Restore every stored recommendation, including currently absent ones."""
        with self._lock:
            self._deadlines.clear()
