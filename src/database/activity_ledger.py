"""Detached completed-activity pages and validation shared by storage and UI."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from unicodedata import category

from .session_comparison import Number


SQLITE_MAX_INTEGER = 2**63 - 1


@dataclass(frozen=True)
class ActivityLedgerFilters:
    """Accepted filters; date bounds are inclusive UTC calendar days."""

    character_id: int | None = None
    date_from: date | None = None
    date_until: date | None = None
    activity_type: str | None = None
    outcome: str | None = None
    query: str | None = None


@dataclass(frozen=True)
class ActivityLedgerRow:
    """Saved activity values, with exact stored 1/0/other outcome semantics."""

    id: int
    session_id: int
    character_id: int
    character_name: str
    activity_type: str | None
    name: str | None
    business_type: str | None
    notes: str | None
    recorded_start: datetime | None
    completed_at: datetime | None
    activity_time: datetime | None
    recorded_amount: Number | None
    duration_seconds: Number | None
    outcome: str


@dataclass(frozen=True)
class ActivityLedgerPage:
    """One bounded, immutable observation; later reads can see different rows."""

    filters: ActivityLedgerFilters
    rows: tuple[ActivityLedgerRow, ...]
    total: int
    offset: int
    limit: int
    observed_at: datetime

    def __post_init__(self):
        # Do not retain a caller-owned mutable list even when manually built.
        object.__setattr__(self, "rows", tuple(self.rows))

    def to_report(self) -> dict:
        """Return fresh JSON-safe containers for this exact accepted page."""
        applied = asdict(self.filters)
        for field in ("date_from", "date_until"):
            value = applied[field]
            applied[field] = value.isoformat() if value is not None else None

        rows = []
        for row in self.rows:
            values = asdict(row)
            for field in ("recorded_start", "completed_at", "activity_time"):
                value = values[field]
                values[field] = value.isoformat() if value is not None else None
            rows.append(values)

        return {
            "format_version": 1,
            "kind": "completed_activity_ledger_page",
            "scope": "completed_sessions",
            "observed_at": self.observed_at.isoformat(),
            "timezone": "UTC",
            "filters": applied,
            "pagination": {
                "offset": self.offset,
                "limit": self.limit,
                "total": self.total,
                "rows_exported": len(self.rows),
            },
            "rows": rows,
            "field_notes": {
                "recorded_amount": (
                    "Stored activity earnings, not verified payout, profit or session net. "
                    "Activity amounts and earnings events are not combined."
                ),
                "duration_seconds": (
                    "Stored measured duration in seconds, not derived from timestamps. "
                    "Zero and negative values are preserved."
                ),
                "unavailable_numbers": (
                    "Missing, nonnumeric or nonfinite amounts and durations are null."
                ),
                "timestamps": (
                    "Stored timestamps use UTC. Recorded start may be a persistence-time "
                    "default, not the gameplay start. Missing completion does not imply "
                    "an active or cancelled activity."
                ),
                "activity_time": "Completion timestamp when present, otherwise recorded start.",
                "date_bounds": (
                    "Inclusive UTC calendar days; undated rows are included only when "
                    "neither date bound is set."
                ),
                "outcome": (
                    "Passed means stored success IS 1; failed means IS 0; all other "
                    "values are unknown. No cancellation status is inferred."
                ),
                "text": "Stored text is preserved, including distinct null and blank values.",
                "query": (
                    "Literal substring of name OR notes. ASCII A-Z ignores case; other "
                    "letters match exactly. Percent, underscore and slash are literal."
                ),
            },
            "snapshot_notes": [
                "Only activities with an existing completed session and character are included.",
                "Order: activity time descending, undated last, then activity ID descending.",
                "Total and rows were read in one SQL statement and detached from storage.",
                "Only this displayed page is exported; total may exceed the exported row count.",
                "Later pages or refreshes may observe later writes; no multi-page snapshot is retained.",
            ],
        }


def validate_ledger_request(
    filters: ActivityLedgerFilters | None = None, *, limit: int = 25, offset: int = 0
) -> ActivityLedgerFilters:
    """Validate all request values without touching storage or changing text.

    Return the supplied frozen filters, or new defaults for ``None``. Raise
    ``ValueError`` for invalid values before either the UI or repository reads.
    """
    if filters is None:
        filters = ActivityLedgerFilters()
    if not isinstance(filters, ActivityLedgerFilters):
        raise ValueError("filters must be ActivityLedgerFilters or None")
    if filters.character_id is not None and (
        isinstance(filters.character_id, bool)
        or not isinstance(filters.character_id, int)
        or not 1 <= filters.character_id <= SQLITE_MAX_INTEGER
    ):
        raise ValueError("character_id must be a positive SQLite signed 64-bit integer or None")
    for field in ("date_from", "date_until"):
        value = getattr(filters, field)
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise ValueError(f"{field} must be a date (not datetime) or None")
    if (
        filters.date_from is not None
        and filters.date_until is not None
        and filters.date_from > filters.date_until
    ):
        raise ValueError("date_from must not be later than date_until")
    for field, maximum in (("activity_type", 256), ("query", 200)):
        value = getattr(filters, field)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{field} must be text of at most {maximum} characters or None")
        if any(category(char) in {"Cc", "Zl", "Zp"} for char in value):
            raise ValueError(f"{field} must not contain control characters or line separators")
        if field == "query" and not value.strip():
            raise ValueError("query must not be blank")
    if filters.outcome is not None and (
        not isinstance(filters.outcome, str)
        or filters.outcome not in ("passed", "failed", "unknown")
    ):
        raise ValueError("outcome must be passed, failed, unknown or None")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= SQLITE_MAX_INTEGER
    ):
        raise ValueError("offset must be a nonnegative SQLite signed 64-bit integer")
    return filters
