"""Bounded, immutable daily observations of saved completed-session net changes."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from math import isfinite
import re
from types import MappingProxyType
from typing import Iterable, Mapping

from .session_comparison import Number, finite_number


MAX_DAILY_SESSION_ROWS = 10_000
MAX_DAILY_SESSION_DAYS = 366
MAX_DAILY_CHARACTER_NAME_CHARACTERS = 1000
MAX_DAILY_CHARACTER_NAME_BYTES = MAX_DAILY_CHARACTER_NAME_CHARACTERS * 4
MAX_DAILY_TIMESTAMP_BYTES = 64
SQLITE_MAX_INTEGER = 2**63 - 1
_MICROSECONDS_PER_SECOND = 1_000_000
_MICROSECONDS_PER_HOUR = 3_600_000_000
# Match the existing stored full-ISO timestamp convention before fromisoformat.
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}")

DAILY_SESSION_ISSUE_MESSAGES = MappingProxyType({
    f"{metric}_out_of_range": (
        f"{label} is unavailable because the exact result exceeds finite numeric "
        "range or is too small to represent without becoming zero."
    )
    for metric, label in (
        ("net_change_total", "Saved net change total"),
        ("duration_seconds_total", "Positive elapsed duration total"),
        ("net_per_hour", "Paired net change per elapsed hour"),
    )
})


class DailySessionValidationError(ValueError):
    """The requested mandatory UTC window or saved character is invalid."""


class DailySessionDataError(ValueError):
    """Unassignable ends or corrupt selected sources prevent a complete report."""

    def __init__(self, unassignable_ends: int = 0):
        self.unassignable_ends = unassignable_ends
        super().__init__(
            f"{unassignable_ends} completed session end timestamp(s) cannot be assigned to a UTC day. "
            "Repair the saved timestamps or select another character."
            if unassignable_ends else
            "Saved daily session sources cannot be read safely. Repair the source data and try again."
        )


class DailySessionLimitError(ValueError):
    """The full matching source count exceeds the accepted snapshot bound."""

    def __init__(self, matching_sessions: int = MAX_DAILY_SESSION_ROWS + 1):
        self.matching_sessions = matching_sessions
        super().__init__(
            f"{matching_sessions} sessions match; the daily overview supports at most "
            f"{MAX_DAILY_SESSION_ROWS}. Narrow the date window or character filter."
        )


class DailySessionUnavailable(Exception):
    """The explicit character has disappeared or the storage read failed."""

    def __init__(self, character_id: int | None = None):
        self.character_id = character_id
        super().__init__("The saved character or daily session storage is unavailable. Refresh and try again.")


@dataclass(frozen=True)
class DailySessionFilters:
    date_from: date
    date_until: date
    character_id: int | None = None


def _valid_id(value) -> bool:
    return type(value) is int and 1 <= value <= SQLITE_MAX_INTEGER


def validate_daily_session_filters(filters: DailySessionFilters) -> DailySessionFilters:
    """Validate before any storage access, including automatic initialization."""
    if not isinstance(filters, DailySessionFilters):
        raise DailySessionValidationError("Choose a start date and end date for the daily overview.")
    if type(filters.date_from) is not date or type(filters.date_until) is not date:
        raise DailySessionValidationError("A start date and end date are both required, as UTC calendar dates.")
    if not 0 <= (filters.date_until - filters.date_from).days < MAX_DAILY_SESSION_DAYS:
        raise DailySessionValidationError(
            f"Choose an ordered, inclusive UTC date window of at most {MAX_DAILY_SESSION_DAYS} days."
        )
    if filters.character_id is not None and not _valid_id(filters.character_id):
        raise DailySessionValidationError("Choose a saved character with a positive SQLite integer ID.")
    return filters


@dataclass(frozen=True)
class DailySessionRow:
    session_id: int
    character_id: int
    character_name: str
    started_at: datetime | None
    ended_at: datetime
    completion_date: date
    net_change: Number | None
    duration_seconds: float | None
    start_status: str
    duration_microseconds: int | None


@dataclass(frozen=True)
class DailySessionMetrics:
    sessions: int
    known_net: int
    net_change_total: Number | None
    positive_durations: int
    zero_durations: int
    negative_durations: int
    unavailable_durations: int
    duration_seconds_total: Number | None
    paired_sessions: int
    net_per_hour: float | None
    issues: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class DailySessionDay:
    day: date
    metrics: DailySessionMetrics


@dataclass(frozen=True)
class DailySessionOverview:
    """One accepted observation for display, selected-day inspection and export."""

    filters: DailySessionFilters
    days: tuple[DailySessionDay, ...]
    overall: DailySessionMetrics
    rows: tuple[DailySessionRow, ...]
    observed_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "days", tuple(self.days))
        object.__setattr__(self, "rows", tuple(self.rows))

    def rows_for_day(self, day: date) -> tuple[DailySessionRow, ...]:
        return tuple(row for row in self.rows if row.completion_date == day)

    def to_report(self) -> dict:
        """Create independent JSON containers without observing storage again."""
        def metrics_report(metrics):
            values = asdict(metrics)
            values["issues"] = list(metrics.issues)
            return values

        def row_report(row):
            values = asdict(row)
            values["started_at"] = row.started_at.isoformat() if row.started_at is not None else None
            values["ended_at"] = row.ended_at.isoformat()
            values["completion_date"] = row.completion_date.isoformat()
            return values

        return {
            "format_version": 1,
            "kind": "daily_completed_session_overview",
            "scope": "completed_sessions",
            "timezone": "UTC",
            "observed_at": self.observed_at.isoformat(),
            "filters": {
                "date_from": self.filters.date_from.isoformat(),
                "date_until": self.filters.date_until.isoformat(),
                "character_id": self.filters.character_id,
            },
            "row_count": len(self.rows),
            "overall": metrics_report(self.overall),
            "days": [{"day": entry.day.isoformat(), "metrics": metrics_report(entry.metrics)} for entry in self.days],
            "rows": [row_report(row) for row in self.rows],
            "limits": {
                "max_source_records": MAX_DAILY_SESSION_ROWS,
                "max_inclusive_days": MAX_DAILY_SESSION_DAYS,
                "max_character_name_characters": MAX_DAILY_CHARACTER_NAME_CHARACTERS,
                "max_timestamp_bytes": MAX_DAILY_TIMESTAMP_BYTES,
                "stored_numeric_types": ["integer", "real"],
                "numeric_aggregation": "exact integers, exact binary64 fractions and integer elapsed microseconds",
                "numeric_outputs": "Integer-only net totals and whole-second duration totals remain exact "
                                   "integers. Fractional duration totals, net totals with real-number "
                                   "contributions, and rates use finite binary64 floats; overflow and "
                                   "nonzero underflow are null with issue codes.",
                "limit_behavior": "Reject the complete request; never return a partial overview.",
            },
            "field_notes": {
                "completion_date": "The whole session belongs to its normalized UTC end date. Dates are inclusive.",
                "net_change": "Saved Session.total_earnings, including spending; not verified profit or payout. "
                              "Never recomputed from balances, activities, earnings events or annotations.",
                "start_status": "known: normalized full timestamp; missing: SQL NULL; invalid: unreadable "
                                "start retained as unavailable while session and saved net still count.",
                "duration_seconds": "Normalized end minus normalized start, including pauses and midnight "
                                    "crossings. Overlapping sessions are summed. This is not daily active-play time.",
                "duration_microseconds": "Exact signed integer elapsed microseconds before conversion to seconds.",
                "duration_seconds_total": "Sum of positive elapsed durations only. Zero, negative and unavailable "
                                          "durations are counted separately.",
                "net_per_hour": "Sum of saved net on paired sessions times 3600 divided by their total elapsed "
                                "seconds. Only known net plus positive duration on the same session forms a pair.",
                "coverage": "known_net, positive_durations and paired_sessions can count different source sets. "
                            "Missing, nonnumeric and nonfinite net values are unavailable without coercion.",
                "empty_metrics": "No contributing values means null totals and rates, including empty days.",
                "issues": dict(DAILY_SESSION_ISSUE_MESSAGES),
            },
            "snapshot_notes": [
                "Explicit owner existence, complete counts and bounded sources were read in one SQL statement.",
                "SQL NULL end timestamps and orphan sessions are excluded. Unreadable non-NULL ends reject "
                "the selected character scope, even if the apparent end text is outside the date window.",
                "Naive stored timestamps use UTC; aware timestamps normalize to UTC.",
                "Days are newest first; rows are ordered by normalized end descending then session ID descending.",
                "Selected-day inspection and export use these same detached source rows. Later refreshes may differ.",
            ],
        }


def _stored_text(value, storage_type, maximum):
    if storage_type != "text" or not isinstance(value, bytes) or len(value) > maximum:
        raise ValueError("Unsupported stored text")
    return value.decode("utf-8", errors="strict")


def daily_character_name_from_storage(value, storage_type) -> str:
    try:
        name = _stored_text(value, storage_type, MAX_DAILY_CHARACTER_NAME_BYTES)
        if len(name) > MAX_DAILY_CHARACTER_NAME_CHARACTERS:
            raise ValueError("Oversized name")
        return name
    except (ValueError, UnicodeError):
        raise DailySessionDataError() from None


def _stored_timestamp(value, storage_type) -> datetime:
    timestamp = _stored_text(value, storage_type, MAX_DAILY_TIMESTAMP_BYTES)
    if _TIMESTAMP.match(timestamp) is None:
        raise ValueError("A full timestamp is required")
    parsed = datetime.fromisoformat(timestamp)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def daily_session_end_match_from_storage(value, storage_type, filters: DailySessionFilters) -> int:
    """SQLite callback over at most 65 bytes: -1 unreadable, 0 outside, 1 in window."""
    try:
        day = _stored_timestamp(value, storage_type).date()
    except (ValueError, UnicodeError, OverflowError):
        return -1
    return int(filters.date_from <= day <= filters.date_until)


def daily_session_row_from_storage(record: Mapping) -> DailySessionRow:
    """Validate one narrow source projection; bad starts alone remain unavailable."""
    for field in ("session_id", "character_id", "owner_id"):
        if record[f"{field}_type"] != "integer" or not _valid_id(record[field]):
            raise DailySessionDataError()
    if record["owner_id"] != record["character_id"]:
        raise DailySessionDataError()
    name = daily_character_name_from_storage(record["character_name"], record["character_name_type"])
    try:
        ended_at = _stored_timestamp(record["ended_at"], record["ended_at_type"])
    except (ValueError, UnicodeError, OverflowError):
        raise DailySessionDataError() from None
    started_at = None
    start_status = "missing" if record["started_at_type"] == "null" else "invalid"
    if start_status != "missing":
        try:
            started_at = _stored_timestamp(record["started_at"], record["started_at_type"])
            start_status = "known"
        except (ValueError, UnicodeError, OverflowError):
            pass
    duration_microseconds = None
    if started_at is not None:
        elapsed = ended_at - started_at
        duration_microseconds = (elapsed.days * 86400 + elapsed.seconds) * _MICROSECONDS_PER_SECOND + elapsed.microseconds
    return DailySessionRow(
        session_id=record["session_id"], character_id=record["character_id"], character_name=name,
        started_at=started_at, ended_at=ended_at, completion_date=ended_at.date(),
        net_change=finite_number(record["net_change"]),
        duration_seconds=(duration_microseconds / _MICROSECONDS_PER_SECOND if duration_microseconds is not None else None),
        start_status=start_status, duration_microseconds=duration_microseconds,
    )


def _finite_float(value: Fraction, metric: str, issues: list[str]) -> float | None:
    try:
        result = float(value)
    except OverflowError:
        result = None
    if result is None or not isfinite(result) or (result == 0 and value != 0):
        issues.append(f"{metric}_out_of_range")
        return None
    return result


@dataclass
class _Accumulator:
    sessions: int = 0
    known_net: int = 0
    net_sum: int | Fraction = 0
    positive_durations: int = 0
    zero_durations: int = 0
    negative_durations: int = 0
    unavailable_durations: int = 0
    duration_microseconds: int = 0
    paired_sessions: int = 0
    paired_net_sum: int | Fraction = 0
    paired_duration_microseconds: int = 0

    def add(self, row: DailySessionRow):
        self.sessions += 1
        amount = row.net_change
        if amount is not None:
            amount = Fraction.from_float(amount) if isinstance(amount, float) else amount
            self.known_net += 1
            self.net_sum += amount
        elapsed = row.duration_microseconds
        if elapsed is None:
            self.unavailable_durations += 1
        elif elapsed == 0:
            self.zero_durations += 1
        elif elapsed < 0:
            self.negative_durations += 1
        else:
            self.positive_durations += 1
            self.duration_microseconds += elapsed
            if amount is not None:
                self.paired_sessions += 1
                self.paired_net_sum += amount
                self.paired_duration_microseconds += elapsed

    def finish(self) -> DailySessionMetrics:
        issues = []
        net_total = None
        if self.known_net:
            net_total = (self.net_sum if isinstance(self.net_sum, int)
                         else _finite_float(self.net_sum, "net_change_total", issues))
        duration_total = None
        if self.positive_durations:
            exact_seconds = Fraction(self.duration_microseconds, _MICROSECONDS_PER_SECOND)
            duration_total = (exact_seconds.numerator if exact_seconds.denominator == 1 else
                              _finite_float(exact_seconds, "duration_seconds_total", issues))
        net_per_hour = (
            _finite_float(Fraction(self.paired_net_sum) * _MICROSECONDS_PER_HOUR /
                          self.paired_duration_microseconds, "net_per_hour", issues)
            if self.paired_sessions else None
        )
        return DailySessionMetrics(
            self.sessions, self.known_net, net_total, self.positive_durations, self.zero_durations,
            self.negative_durations, self.unavailable_durations, duration_total,
            self.paired_sessions, net_per_hour, tuple(issues),
        )


def build_daily_session_overview(
    rows: Iterable[DailySessionRow], filters: DailySessionFilters, observed_at: datetime,
) -> DailySessionOverview:
    """Summarize a complete validated source set with exact elapsed arithmetic."""
    validate_daily_session_filters(filters)
    by_day = {}
    overall = _Accumulator()
    accepted = []
    identifiers = set()
    for row in rows:
        if row.session_id in identifiers or not filters.date_from <= row.completion_date <= filters.date_until:
            raise DailySessionDataError()
        if filters.character_id is not None and row.character_id != filters.character_id:
            raise DailySessionDataError()
        identifiers.add(row.session_id)
        accepted.append(row)
        if len(accepted) > MAX_DAILY_SESSION_ROWS:
            raise DailySessionLimitError(len(accepted))
        overall.add(row)
        by_day.setdefault(row.completion_date, _Accumulator()).add(row)
    days = tuple(
        DailySessionDay(day, by_day.get(day, _Accumulator()).finish())
        for offset in range((filters.date_until - filters.date_from).days + 1)
        for day in (filters.date_until - timedelta(days=offset),)
    )
    return DailySessionOverview(
        filters=filters, days=days, overall=overall.finish(),
        rows=tuple(sorted(accepted, key=lambda row: (row.ended_at, row.session_id), reverse=True)),
        observed_at=observed_at,
    )
