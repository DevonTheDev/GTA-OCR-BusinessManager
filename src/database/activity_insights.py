"""Bounded, detached historical activity summaries with explicit numeric coverage."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from math import isfinite
from types import MappingProxyType
from typing import Iterable, Mapping

from .activity_ledger import ActivityLedgerFilters
from .session_comparison import Number, finite_number


MAX_INSIGHT_RECORDS = 100_000
MAX_INSIGHT_TYPES = 256
MAX_ACTIVITY_TYPE_CHARACTERS = 256
# A valid UTF-8 scalar occupies at most four bytes. Fetch one extra byte to
# reject oversized data without transferring an unbounded legacy text value.
MAX_ACTIVITY_TYPE_BYTES = MAX_ACTIVITY_TYPE_CHARACTERS * 4

INSIGHT_ISSUE_MESSAGES = MappingProxyType({
    f"{metric}_out_of_range": (
        f"{label} is unavailable because the exact result exceeds finite numeric "
        "range or is too small to represent without becoming zero."
    )
    for metric, label in (
        ("recorded_amount_total", "Recorded amount total"),
        ("recorded_amount_mean", "Recorded amount mean"),
        ("duration_seconds_total", "Positive duration total"),
        ("duration_seconds_mean", "Positive duration mean"),
        ("recorded_amount_per_hour", "Recorded amount per measured hour"),
    )
})


class ActivityInsightsLimitError(ValueError):
    """The complete matching result exceeds a bounded summary limit."""

    def __init__(self):
        super().__init__(
            "Activity insights exceed the supported record or type limit; "
            "narrow the filters and try again."
        )


class ActivityInsightsDataError(ValueError):
    """A stored type cannot be represented safely in the summary."""

    def __init__(self):
        super().__init__(
            "Stored activity types cannot be summarized safely. "
            "Narrow the filters or repair the source data."
        )


@dataclass(frozen=True)
class ActivityInsightMetrics:
    activities: int
    sessions: int
    passed: int
    failed: int
    unknown: int
    known_outcomes: int
    pass_rate: float | None
    known_amounts: int
    recorded_amount_total: Number | None
    recorded_amount_mean: float | None
    positive_durations: int
    zero_durations: int
    negative_durations: int
    unavailable_durations: int
    duration_seconds_total: Number | None
    duration_seconds_mean: float | None
    paired_records: int
    recorded_amount_per_hour: float | None
    issues: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class ActivityTypeInsight:
    activity_type: str | None
    metrics: ActivityInsightMetrics


@dataclass(frozen=True)
class ActivityInsights:
    """An immutable complete observation; subsequent reads may differ."""

    filters: ActivityLedgerFilters
    groups: tuple[ActivityTypeInsight, ...]
    overall: ActivityInsightMetrics
    observed_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "groups", tuple(self.groups))

    def to_report(self) -> dict:
        """Return independent JSON containers for the accepted observation."""
        applied = asdict(self.filters)
        for name in ("date_from", "date_until"):
            value = applied[name]
            applied[name] = value.isoformat() if value is not None else None

        def metrics_report(metrics):
            values = asdict(metrics)
            values["issues"] = list(metrics.issues)
            return values

        return {
            "format_version": 1,
            "kind": "completed_activity_insights",
            "scope": "completed_sessions",
            "observed_at": self.observed_at.isoformat(),
            "timezone": "UTC",
            "filters": applied,
            "limits": {
                "max_source_records": MAX_INSIGHT_RECORDS,
                "max_activity_types": MAX_INSIGHT_TYPES,
                "max_activity_type_characters": MAX_ACTIVITY_TYPE_CHARACTERS,
                "stored_numeric_types": ["integer", "real"],
                "numeric_aggregation": "exact integers and exact binary64 fractions",
                "numeric_outputs": (
                    "Integer-only totals remain exact integers. Other totals, means and "
                    "rates use finite binary64 floats; overflow and nonzero underflow "
                    "are null with a field-specific issue code."
                ),
                "limit_behavior": "Reject the complete request; never return a partial summary.",
            },
            "overall": metrics_report(self.overall),
            "groups": [
                {"activity_type": group.activity_type, "metrics": metrics_report(group.metrics)}
                for group in self.groups
            ],
            "field_notes": {
                "activities": "Every matching stored activity row, including failed and unknown outcomes.",
                "sessions": "Distinct matching completed session IDs, overall or within each type.",
                "outcomes": (
                    "Passed means stored success IS 1; failed means IS 0; all other "
                    "values are unknown. No cancellation status is inferred."
                ),
                "pass_rate": "Passed divided by known outcomes (passed plus failed), on a 0 to 1 scale.",
                "recorded_amount": (
                    "Stored activity earnings, not verified payout, profit or session net. "
                    "Activity amounts and earnings events are not combined. Finite zero "
                    "and negative amounts are included. No future performance is inferred."
                ),
                "recorded_amount_mean": "Exact total of known amounts divided by known_amounts.",
                "duration_seconds": (
                    "Stored measured seconds, not derived from timestamps. Total and "
                    "mean include only positive finite durations. Zero, negative and "
                    "unavailable durations have separate counts."
                ),
                "recorded_amount_per_hour": (
                    "Sum of amounts from paired records times 3600 divided by sum of "
                    "their measured seconds. A pair requires a known amount and positive "
                    "finite duration on the same row. This is not a mean of row rates."
                ),
                "coverage": (
                    "known_amounts, positive_durations and paired_records describe "
                    "different contributing row sets. Missing, nonnumeric and nonfinite "
                    "numbers are unavailable without coercion. Unknown values are not zero."
                ),
                "empty_metrics": "No contributing values means null total, mean or rate; counts remain zero.",
                "activity_types": (
                    "Exact stored text, including case, whitespace and custom types. "
                    "Null and blank are distinct. Nontext, invalid Unicode and types "
                    "over the character limit reject the complete request."
                ),
                "activity_time": "Completion timestamp when present, otherwise recorded start.",
                "date_bounds": (
                    "Inclusive UTC calendar days; undated rows are included only when "
                    "neither bound is set. Recorded start may be a persistence-time default."
                ),
                "query": (
                    "Literal substring of name OR notes. ASCII A-Z ignores case; other "
                    "letters match exactly. Percent, underscore and slash are literal."
                ),
                "issues": dict(INSIGHT_ISSUE_MESSAGES),
            },
            "snapshot_notes": [
                "Only activities with an existing completed session and character are included.",
                "All matching rows within the limits were read under one SELECT cursor.",
                "Groups are ordered with missing type first, then exact text order, including blank.",
                "The report is detached from storage and exports this captured summary only.",
                "Later refreshes or a matching ledger read may observe later writes.",
            ],
        }


def _finite_float(value: Fraction, metric: str, issues: list[str]) -> float | None:
    """Convert once, preserving exact zero but detecting overflow and underflow."""
    try:
        result = float(value)
    except OverflowError:
        result = None
    if result is None or not isfinite(result) or (result == 0 and value != 0):
        issues.append(f"{metric}_out_of_range")
        return None
    return result


def _exact_value(value: Number) -> int | Fraction:
    return Fraction.from_float(value) if isinstance(value, float) else value


@dataclass
class _Accumulator:
    activities: int = 0
    session_ids: set[int] = field(default_factory=set)
    passed: int = 0
    failed: int = 0
    known_amounts: int = 0
    amount_sum: int | Fraction = 0
    positive_durations: int = 0
    zero_durations: int = 0
    negative_durations: int = 0
    unavailable_durations: int = 0
    duration_sum: int | Fraction = 0
    paired_records: int = 0
    paired_amount_sum: int | Fraction = 0
    paired_duration_sum: int | Fraction = 0

    def add(self, session_id: int, outcome: str, amount: Number | None,
            duration: Number | None) -> None:
        self.activities += 1
        self.session_ids.add(session_id)
        self.passed += outcome == "passed"
        self.failed += outcome == "failed"
        if amount is not None:
            self.known_amounts += 1
            self.amount_sum += _exact_value(amount)
        if duration is None:
            self.unavailable_durations += 1
        elif duration == 0:
            self.zero_durations += 1
        elif duration < 0:
            self.negative_durations += 1
        else:
            self.positive_durations += 1
            self.duration_sum += _exact_value(duration)
            if amount is not None:
                self.paired_records += 1
                self.paired_amount_sum += _exact_value(amount)
                self.paired_duration_sum += _exact_value(duration)

    def finish(self) -> ActivityInsightMetrics:
        issues = []

        def total(value, count, metric):
            if not count:
                return None
            return value if isinstance(value, int) else _finite_float(value, metric, issues)

        amount_total = total(self.amount_sum, self.known_amounts, "recorded_amount_total")
        amount_mean = (
            _finite_float(Fraction(self.amount_sum) / self.known_amounts,
                          "recorded_amount_mean", issues)
            if self.known_amounts else None
        )
        duration_total = total(self.duration_sum, self.positive_durations, "duration_seconds_total")
        duration_mean = (
            _finite_float(Fraction(self.duration_sum) / self.positive_durations,
                          "duration_seconds_mean", issues)
            if self.positive_durations else None
        )
        rate = (
            _finite_float(Fraction(self.paired_amount_sum) * 3600 / self.paired_duration_sum,
                          "recorded_amount_per_hour", issues)
            if self.paired_records else None
        )
        known_outcomes = self.passed + self.failed
        return ActivityInsightMetrics(
            activities=self.activities, sessions=len(self.session_ids),
            passed=self.passed, failed=self.failed,
            unknown=self.activities - known_outcomes, known_outcomes=known_outcomes,
            pass_rate=self.passed / known_outcomes if known_outcomes else None,
            known_amounts=self.known_amounts, recorded_amount_total=amount_total,
            recorded_amount_mean=amount_mean, positive_durations=self.positive_durations,
            zero_durations=self.zero_durations, negative_durations=self.negative_durations,
            unavailable_durations=self.unavailable_durations,
            duration_seconds_total=duration_total, duration_seconds_mean=duration_mean,
            paired_records=self.paired_records, recorded_amount_per_hour=rate,
            issues=tuple(issues),
        )


def _stored_type(value: object) -> str | None:
    """Decode the bounded SQL blob; reject unsupported storage without echoing it."""
    if value is None:
        return None
    if not isinstance(value, bytes):
        raise ActivityInsightsDataError()
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ActivityInsightsDataError() from None
    if len(decoded) > MAX_ACTIVITY_TYPE_CHARACTERS:
        raise ActivityInsightsDataError()
    return decoded


def build_activity_insights(
    records: Iterable[Mapping], filters: ActivityLedgerFilters,
) -> ActivityInsights:
    """Consume bounded projected rows while their single storage cursor is open."""
    groups: dict[str | None, _Accumulator] = {}
    overall = _Accumulator()
    for index, row in enumerate(records):
        if index >= MAX_INSIGHT_RECORDS:
            raise ActivityInsightsLimitError()
        kind = _stored_type(row["activity_type"])
        if kind not in groups:
            if len(groups) >= MAX_INSIGHT_TYPES:
                raise ActivityInsightsLimitError()
            groups[kind] = _Accumulator()
        amount = finite_number(row["recorded_amount"])
        duration = finite_number(row["duration_seconds"])
        values = (row["session_id"], row["outcome"], amount, duration)
        overall.add(*values)
        groups[kind].add(*values)
    return ActivityInsights(
        filters=filters,
        groups=tuple(
            ActivityTypeInsight(kind, groups[kind].finish())
            for kind in sorted(groups, key=lambda kind: (kind is not None, kind or ""))
        ),
        overall=overall.finish(), observed_at=datetime.now(timezone.utc),
    )
