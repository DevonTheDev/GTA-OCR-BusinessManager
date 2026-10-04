"""Bounded saved observations with exact coverage and endpoint changes."""

from dataclasses import asdict, dataclass
from datetime import datetime

from .business_checkins import (
    BusinessCheckIn, BusinessCheckInHistoryFilters, BusinessCheckInLimitError, BusinessCheckInValidationError,
    MAX_CHECKIN_CHARACTER_NAME_BYTES, _utc, normalize_business_checkin,
    validate_business_checkin_history_filters, validate_checkin_business_id, validate_checkin_id,
)


MAX_CHECKIN_TREND_ROWS = 1000


class BusinessCheckInTrendLimitError(BusinessCheckInLimitError):
    """An all-match capture cannot fit; never return a truncated trend."""

    def __init__(self):
        super().__init__()
        self.args = (
            "History trends support at most 1000 matching check-ins. Narrow the applied history filters and try again.",
        )


@dataclass(frozen=True)
class BusinessCheckInTrendMetric:
    """Exact coverage and first/last selected observations for one field."""

    field: str
    known_count: int
    unknown_count: int
    first_value: int | None
    last_value: int | None
    change: int | None
    plot_status: str


@dataclass(frozen=True)
class BusinessCheckInTrend:
    """One detached all-match capture in ascending insertion order."""

    character_id: int
    character_name: str
    business_id: str
    captured_at: datetime
    rows: tuple[BusinessCheckIn, ...]
    filters: BusinessCheckInHistoryFilters | None = None

    def __post_init__(self):
        validate_checkin_id(self.character_id)
        validate_checkin_business_id(self.business_id)
        if type(self.rows) not in (tuple, list):
            raise BusinessCheckInValidationError("Choose a bounded list of saved check-ins.")
        if len(self.rows) > MAX_CHECKIN_TREND_ROWS:
            raise BusinessCheckInTrendLimitError()
        try:
            if (not isinstance(self.character_name, str)
                    or len(self.character_name) > MAX_CHECKIN_CHARACTER_NAME_BYTES
                    or len(self.character_name.encode("utf-8", errors="strict")) > MAX_CHECKIN_CHARACTER_NAME_BYTES):
                raise ValueError("Invalid character context")
            captured_at = _utc(self.captured_at)
            filters = validate_business_checkin_history_filters(self.filters)
            previous_id = 0
            rows = []
            for row in self.rows:
                if type(row) is not BusinessCheckIn:
                    raise ValueError("Invalid saved observation")
                validate_checkin_id(row.id)
                validate_checkin_id(row.character_id)
                validate_checkin_business_id(row.business_id)
                if (row.id <= previous_id or row.character_id != self.character_id
                        or row.business_id != self.business_id):
                    raise ValueError("Invalid observation order or scope")
                values = normalize_business_checkin(row.stock_percent, row.supply_percent, row.stock_value, row.note)
                rows.append(BusinessCheckIn(row.id, row.character_id, row.business_id,
                                            _utc(row.recorded_at), *values))
                previous_id = row.id
        except (ValueError, TypeError, UnicodeError, OverflowError):
            raise BusinessCheckInValidationError("Choose valid saved observations and trend context.") from None
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "filters", filters)
        object.__setattr__(self, "rows", tuple(rows))

    @property
    def metrics(self) -> tuple[BusinessCheckInTrendMetric, ...]:
        """Do not fill unknown endpoints or round exact integer differences."""
        result = []
        for field in ("stock_percent", "supply_percent", "stock_value"):
            known = [getattr(row, field) for row in self.rows if getattr(row, field) is not None]
            first = getattr(self.rows[0], field) if self.rows else None
            last = getattr(self.rows[-1], field) if self.rows else None
            change = last - first if len(self.rows) >= 2 and first is not None and last is not None else None
            if not known:
                plot_status = "no_observations"
            elif field == "stock_value" and any(int(float(value)) != value for value in known):
                plot_status = "precision_limit"
            else:
                plot_status = "available"
            result.append(BusinessCheckInTrendMetric(
                field, len(known), len(self.rows) - len(known), first, last, change, plot_status,
            ))
        return tuple(result)

    def to_report(self) -> dict:
        """Build fresh exact report containers without rereading source storage."""
        report = {
            "format_version": 1,
            "kind": "manual_business_checkin_trend",
            "scope": "manual_observations",
            "captured_at": self.captured_at.isoformat(),
            "timezone": "UTC",
            "character": {"id": self.character_id, "name": self.character_name},
            "business_id": self.business_id,
            "filters": {"character_id": self.character_id, "business_id": self.business_id},
            "selection": "all_matching_observations",
            "ordering": "insertion_id_ascending",
            "plot_x": "selected_checkin_sequence_1_based",
            "row_count": len(self.rows),
            "row_limit": MAX_CHECKIN_TREND_ROWS,
            "rows": [row.to_dict() for row in self.rows],
            "metrics": [asdict(metric) for metric in self.metrics],
            "field_notes": {
                "scope": "Explicit manual observations only; no elapsed gameplay, production, sales, profit or rates.",
                "unknown": "Null means unknown; zero is an explicit recorded value. Missing values do not carry forward.",
                "coverage": "Known and unknown counts cover every matching selected row for each field.",
                "endpoints": "First and last values belong to the first and last selected rows, even when unknown.",
                "change": "Last minus first; null unless at least two rows exist and both endpoints are known. No improvement is inferred.",
                "units": "Stock and supplies changes are percentage points, not percent growth. Value changes are exact dollars.",
                "stock_value": "User-recorded observed value, not verified sale proceeds or profit.",
                "recorded_at": "UTC time when Save recorded each observation, not a gameplay timestamp.",
                "ordering": "Ascending insertion ID determines order even for equal or backward recorded timestamps.",
                "plot_x": "Selected check-in sequence 1 through N, not raw IDs or timestamps; every unknown retains its sequence slot.",
                "plot_status": "No observations means no known values. Precision limit disables the entire value plot if any known integer fails int(float(value)) == value. Raw data and summaries remain exact.",
                "captured_at": "All matching records, count and character context were read together at capture; export uses that fixed capture.",
            },
        }
        if self.filters is not None:
            report["filters"].update({
                "note_query": self.filters.note_query,
                "recorded_from": (self.filters.recorded_from.isoformat()
                                  if self.filters.recorded_from is not None else None),
                "recorded_until": (self.filters.recorded_until.isoformat()
                                   if self.filters.recorded_until is not None else None),
                "note_match_policy": "literal_substring_ascii_case_insensitive_other_unicode_exact",
                "recorded_date_policy": "inclusive_utc_dates",
            })
            report["field_notes"].update({
                "note_query": "Literal substring: ASCII letters ignore case; other Unicode is exact. Spaces, percent, underscore and backslash are literal.",
                "recorded_dates": "Inclusive UTC calendar dates after parsing and normalizing recorded_at. Null bounds are open-ended.",
            })
        return report
