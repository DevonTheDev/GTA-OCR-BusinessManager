"""Detached values and numeric semantics for completed-session comparisons."""

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite


Number = int | float


class SessionComparisonUnavailable(ValueError):
    """Selected sessions are missing, open, or have no available character."""

    def __init__(self, session_ids: tuple[int, ...]):
        self.session_ids = tuple(session_ids)
        super().__init__(
            "Completed sessions unavailable: " + ", ".join(map(str, self.session_ids))
        )


@dataclass(frozen=True)
class SessionMetrics:
    net_change: Number | None
    duration_seconds: float | None
    net_per_hour: float | None
    recorded_activities: int
    passed: int
    failed: int
    unknown: int


@dataclass(frozen=True)
class SessionSummary:
    session_id: int
    character_id: int
    character_name: str
    started_at: datetime | None
    ended_at: datetime
    start_money: Number | None
    end_money: Number | None
    metrics: SessionMetrics


@dataclass(frozen=True)
class ActivityTypeComparison:
    activity_type: str | None
    baseline: int
    comparison: int
    difference: int


@dataclass(frozen=True)
class SessionComparison:
    baseline: SessionSummary
    comparison: SessionSummary
    differences: SessionMetrics
    activity_types: tuple[ActivityTypeComparison, ...]
    generated_at: datetime

    def to_report(self) -> dict:
        """Return a fresh, versioned report of this exact display snapshot."""
        def summary_report(summary: SessionSummary) -> dict:
            values = asdict(summary)
            values["started_at"] = (
                summary.started_at.isoformat() if summary.started_at is not None else None
            )
            values["ended_at"] = summary.ended_at.isoformat()
            return values

        return {
            "format_version": 1,
            "orientation": "comparison_minus_baseline",
            "generated_at": self.generated_at.isoformat(),
            "timezone": "UTC",
            "metric_notes": {
                "net_change": (
                    "Stored session net balance change (Session.total_earnings), including the "
                    "effect of spending. It is not recalculated from balances, activity amounts "
                    "or earnings events, and is not verified payout or profit."
                ),
                "duration_seconds": (
                    "Stored end time minus stored start time in seconds. Missing starts are "
                    "unavailable; zero and negative durations are preserved."
                ),
                "net_per_hour": (
                    "Net balance change per elapsed hour, available only for a known finite "
                    "net and positive finite duration. Missing or invalid rates are null."
                ),
                "recorded_activities": (
                    "Every recorded activity row is counted, including missing outcomes or "
                    "timestamps. No activity money or earnings-event totals are combined."
                ),
                "outcomes": (
                    "Passed means stored true, failed means stored false, and any other or "
                    "missing outcome is unknown."
                ),
                "activity_types": (
                    "Counts use the actual stored activity type; null and blank are distinct. "
                    "A zero count means that type has no rows in that session."
                ),
                "differences": (
                    "Comparison B minus baseline A. A difference is null when either value "
                    "is unavailable. Differences imply neither causality nor improvement."
                ),
                "timestamps": "Stored timestamps follow the existing UTC convention.",
            },
            "baseline": summary_report(self.baseline),
            "comparison": summary_report(self.comparison),
            "differences": asdict(self.differences),
            "activity_types": [asdict(item) for item in self.activity_types],
        }


def finite_number(value: object) -> Number | None:
    """Keep real stored numbers, including zero/losses, without coercing text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    return None


def build_session_metrics(
    net_change: object,
    duration_seconds: float | None,
    recorded_activities: int,
    passed: int,
    failed: int,
) -> SessionMetrics:
    """Calculate rate only when the stored inputs support one."""
    net = finite_number(net_change)
    duration = finite_number(duration_seconds)
    rate = None
    if net is not None and duration is not None and duration > 0:
        rate = finite_number((net / duration) * 3600)
    return SessionMetrics(
        net_change=net,
        duration_seconds=duration,
        net_per_hour=rate,
        recorded_activities=recorded_activities,
        passed=passed,
        failed=failed,
        unknown=recorded_activities - passed - failed,
    )


def subtract_metrics(baseline: SessionMetrics, comparison: SessionMetrics) -> SessionMetrics:
    """Return B minus A without turning missing operands into zero."""
    def difference(a: Number | None, b: Number | None) -> Number | None:
        if a is None or b is None:
            return None
        return finite_number(b - a)

    return SessionMetrics(
        net_change=difference(baseline.net_change, comparison.net_change),
        duration_seconds=difference(baseline.duration_seconds, comparison.duration_seconds),
        net_per_hour=difference(baseline.net_per_hour, comparison.net_per_hour),
        recorded_activities=comparison.recorded_activities - baseline.recorded_activities,
        passed=comparison.passed - baseline.passed,
        failed=comparison.failed - baseline.failed,
        unknown=comparison.unknown - baseline.unknown,
    )
