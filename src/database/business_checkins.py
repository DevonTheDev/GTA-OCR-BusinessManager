"""Detached manual observations, with no production or accounting estimates."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from types import MappingProxyType
import unicodedata

from ..game.businesses import BUSINESSES


SQLITE_MAX_INTEGER = 2**63 - 1
MAX_CHECKIN_NOTE_CHARACTERS = 2000
MAX_CHECKIN_NOTE_BYTES = MAX_CHECKIN_NOTE_CHARACTERS * 4
MAX_CHECKIN_NOTE_QUERY_CHARACTERS = 200
MAX_CHECKIN_BUSINESS_ID_LENGTH = 50
MAX_CHECKIN_CHARACTER_NAME_BYTES = 4096
MAX_CHECKIN_CHARACTERS = 1000
MAX_CHECKIN_BUSINESSES = 256
BUSINESS_LABELS = MappingProxyType({key: business.name for key, business in BUSINESSES.items()})
_BUSINESS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class BusinessCheckInValidationError(ValueError):
    """The requested observation or read filter is invalid."""

    def __init__(self, message="Check the manual observation fields and try again."):
        super().__init__(message)


class BusinessCheckInUnavailable(Exception):
    """The saved character or its storage cannot be accessed."""

    def __init__(self):
        super().__init__("Manual check-ins are unavailable. Refresh the saved characters and try again.")


class BusinessCheckInDataError(Exception):
    """Stored data is corrupt; never silently turn it into a blank observation."""

    def __init__(self):
        super().__init__("Saved manual check-ins contain invalid data and cannot be displayed.")


class BusinessCheckInLimitError(Exception):
    """Reading this collection would exceed its bounded resource contract."""

    def __init__(self):
        super().__init__("Saved manual check-ins exceed the display limit. Use a smaller saved dataset.")


def business_label(business_id: str) -> str:
    """Only reuse catalog names; retained unknown IDs stay visible verbatim."""
    return BUSINESS_LABELS.get(business_id, business_id)


def validate_checkin_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SQLITE_MAX_INTEGER:
        raise BusinessCheckInValidationError("Choose an existing saved character or check-in.")


def validate_checkin_business_id(value: str, *, for_write: bool = False) -> None:
    if not isinstance(value, str) or _BUSINESS_ID.fullmatch(value) is None:
        raise BusinessCheckInValidationError("Choose a valid business identifier.")
    if for_write and value not in BUSINESS_LABELS:
        raise BusinessCheckInValidationError("Choose a business from the available catalog.")


def validate_checkin_page(offset: int, limit: int) -> None:
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
        raise BusinessCheckInValidationError("History offset must be a whole number from 0 to 1000000.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise BusinessCheckInValidationError("History page size must be a whole number from 1 to 100.")


@dataclass(frozen=True)
class BusinessCheckInHistoryFilters:
    """Literal personal-note query and inclusive recorded UTC dates."""

    note_query: str | None = None
    recorded_from: date | None = None
    recorded_until: date | None = None


def validate_business_checkin_history_filters(
    filters: BusinessCheckInHistoryFilters | None,
) -> BusinessCheckInHistoryFilters | None:
    """Validate before storage, preserving spaces and normalizing empty filters."""
    if filters is None:
        return None
    if not isinstance(filters, BusinessCheckInHistoryFilters):
        raise BusinessCheckInValidationError("Choose valid manual history filters.")
    query = filters.note_query
    if query is not None:
        if not isinstance(query, str) or len(query) > MAX_CHECKIN_NOTE_QUERY_CHARACTERS:
            raise BusinessCheckInValidationError("Note search must contain at most 200 characters.")
        try:
            query.encode("utf-8", errors="strict")
        except UnicodeError:
            raise BusinessCheckInValidationError("Note search must contain valid Unicode text.") from None
        if any(unicodedata.category(char) in ("Cc", "Zl", "Zp") for char in query):
            raise BusinessCheckInValidationError("Note search must be one line without control characters.")
        query = query or None
    for value in (filters.recorded_from, filters.recorded_until):
        if value is not None and type(value) is not date:
            raise BusinessCheckInValidationError("Recorded date bounds must be dates without a time.")
    if (filters.recorded_from is not None and filters.recorded_until is not None
            and filters.recorded_from > filters.recorded_until):
        raise BusinessCheckInValidationError("From UTC must be on or before Until UTC.")
    if query is None and filters.recorded_from is None and filters.recorded_until is None:
        return None
    return BusinessCheckInHistoryFilters(query, filters.recorded_from, filters.recorded_until)


def _optional_integer(value, maximum, message):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                              or not 0 <= value <= maximum):
        raise BusinessCheckInValidationError(message)
    return value


def normalize_business_checkin(stock_percent, supply_percent, stock_value, note):
    """Validate without coercion, trimming, inference or previous-value carry-forward."""
    stock_percent = _optional_integer(stock_percent, 100, "Stock must be blank or a whole number from 0 to 100.")
    supply_percent = _optional_integer(supply_percent, 100, "Supplies must be blank or a whole number from 0 to 100.")
    stock_value = _optional_integer(stock_value, SQLITE_MAX_INTEGER,
                                   "Observed value must be blank or a whole number from 0 to 9223372036854775807.")
    if not isinstance(note, str) or len(note) > MAX_CHECKIN_NOTE_CHARACTERS:
        raise BusinessCheckInValidationError("Note must contain at most 2000 characters.")
    try:
        note.encode("utf-8", errors="strict")
    except UnicodeError:
        raise BusinessCheckInValidationError("Note must contain valid Unicode text.") from None
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in note):
        raise BusinessCheckInValidationError("Note cannot contain control characters except newlines and tabs.")
    if stock_percent is supply_percent is stock_value is None and not note.strip():
        raise BusinessCheckInValidationError("Enter at least one measurement or a nonblank note.")
    return stock_percent, supply_percent, stock_value, note


def _parse_number(text: str, maximum: int, message: str) -> int | None:
    if not isinstance(text, str):
        raise BusinessCheckInValidationError(message)
    if not text.strip():
        return None
    if not text.isascii() or not all("0" <= char <= "9" for char in text):
        raise BusinessCheckInValidationError(message)
    # Compare canonical decimal strings before int(): avoid float rounding and
    # Python's digit-count exception on very long input (including leading zeroes).
    digits = text.lstrip("0") or "0"
    bound = str(maximum)
    if len(digits) > len(bound) or (len(digits) == len(bound) and digits > bound):
        raise BusinessCheckInValidationError(message)
    return int(digits)


def parse_checkin_percent(text: str) -> int | None:
    return _parse_number(text, 100, "Use digits from 0 to 100, or leave the percentage blank.")


def parse_checkin_value(text: str) -> int | None:
    return _parse_number(text, SQLITE_MAX_INTEGER,
                         "Use digits from 0 to 9223372036854775807, or leave the value blank.")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("A timestamp is required")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CheckInCharacter:
    id: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class BusinessCheckIn:
    id: int
    character_id: int
    business_id: str
    recorded_at: datetime
    stock_percent: int | None
    supply_percent: int | None
    stock_value: int | None
    note: str

    def __post_init__(self):
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))

    def to_dict(self) -> dict:
        return {"id": self.id, "character_id": self.character_id,
                "business_id": self.business_id, "recorded_at": self.recorded_at.isoformat(),
                "stock_percent": self.stock_percent, "supply_percent": self.supply_percent,
                "stock_value": self.stock_value, "note": self.note}


def _report(snapshot, kind):
    return {
        "format_version": 1,
        "kind": kind,
        "scope": "manual_observations",
        "captured_at": snapshot.captured_at.isoformat(),
        "timezone": "UTC",
        "character": {"id": snapshot.character_id, "name": snapshot.character_name},
        "filters": {"character_id": snapshot.character_id},
        "rows": [row.to_dict() for row in snapshot.rows],
        "field_notes": {
            "scope": "Explicit manual observations only; no production, sales or profit estimates.",
            "unknown": "Null means unknown; zero is an explicit recorded value.",
            "independence": "Each check-in stands alone. Missing fields do not carry forward.",
            "recorded_at": "UTC time when Save recorded the observation, not a gameplay timestamp.",
            "ordering": "Insertion ID determines latest and history order, even if the clock moves backwards.",
            "stock_value": "User-recorded observed value, not verified sale proceeds or profit.",
        },
    }


@dataclass(frozen=True)
class BusinessCheckInBoard:
    character_id: int
    character_name: str
    captured_at: datetime
    rows: tuple[BusinessCheckIn, ...]

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "captured_at", _utc(self.captured_at))

    def to_report(self) -> dict:
        report = _report(self, "manual_business_checkin_board")
        report["selection"] = "latest_insertion_per_business"
        return report


@dataclass(frozen=True)
class BusinessCheckInPage:
    character_id: int
    character_name: str
    business_id: str
    captured_at: datetime
    rows: tuple[BusinessCheckIn, ...]
    offset: int
    limit: int
    total: int
    filters: BusinessCheckInHistoryFilters | None = None

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "captured_at", _utc(self.captured_at))
        object.__setattr__(self, "filters", validate_business_checkin_history_filters(self.filters))

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total

    def to_report(self) -> dict:
        report = _report(self, "manual_business_checkin_history_page")
        report["filters"]["business_id"] = self.business_id
        report["pagination"] = {"offset": self.offset, "limit": self.limit, "total": self.total,
                                "has_more": self.has_more, "rows_exported": len(self.rows)}
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
                "note_query": "Literal substring: ASCII letters ignore case; other Unicode is exact. "
                              "Spaces, percent, underscore and backslash are literal.",
                "recorded_dates": "Inclusive UTC calendar dates after parsing and normalizing recorded_at. "
                                  "Null bounds are open-ended.",
            })
        return report


def _stored_text(record, field, max_bytes):
    value = record[field]
    if record[f"{field}_type"] != "text" or not isinstance(value, bytes) or len(value) > max_bytes:
        raise ValueError("Invalid stored text")
    return value.decode("utf-8", errors="strict")


def _stored_checkin_timestamp(record):
    timestamp = _stored_text(record, "recorded_at", 64)
    if _TIMESTAMP.match(timestamp) is None:
        raise ValueError("Invalid stored timestamp")
    return _utc(datetime.fromisoformat(timestamp))


def checkin_history_match_from_storage(note, note_type, recorded_at, recorded_at_type, filters):
    """SQLite predicate over bounded bytes: -1 corrupt, 0 nonmatch, 1 match.

    Validate every active predicate field before testing either predicate. All
    owner/business candidates are checked, including nonmatches and off-page
    rows. Inactive fields and measurements keep their page-only validation.
    No invalid raw data is raised through SQLite or retained by the callback.
    """
    record = {"note": note, "note_type": note_type,
              "recorded_at": recorded_at, "recorded_at_type": recorded_at_type}
    try:
        saved_note = None
        recorded_date = None
        if filters.note_query is not None:
            saved_note = _stored_text(record, "note", MAX_CHECKIN_NOTE_BYTES)
            # Only validate the predicate's note, not unrelated measurements or
            # the requirement for at least one observation in the selected row.
            normalize_business_checkin(0, None, None, saved_note)
        if filters.recorded_from is not None or filters.recorded_until is not None:
            recorded_date = _stored_checkin_timestamp(record).date()
        if (filters.note_query is not None
                and filters.note_query.translate(_ASCII_LOWER) not in saved_note.translate(_ASCII_LOWER)):
            return 0
        if filters.recorded_from is not None and recorded_date < filters.recorded_from:
            return 0
        if filters.recorded_until is not None and recorded_date > filters.recorded_until:
            return 0
        return 1
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return -1


def checkin_character_from_storage(record) -> CheckInCharacter:
    """Validate bounded raw columns before presenting saved character context."""
    try:
        character_id = record["context_character_id"]
        validate_checkin_id(character_id)
        name = _stored_text(record, "character_name", MAX_CHECKIN_CHARACTER_NAME_BYTES)
        # Old schemas allow nullable active flags; only an exact 1 means active.
        return CheckInCharacter(character_id, name, record["active_integer"] == 1)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise BusinessCheckInDataError() from None


def checkin_from_storage(record) -> BusinessCheckIn:
    """Validate SQLite's actual storage types; malformed bytes never reach the UI."""
    try:
        for field in ("id", "character_id"):
            if record[f"{field}_type"] != "integer":
                raise ValueError("Invalid stored identifier")
            validate_checkin_id(record[field])
        business_id = _stored_text(record, "business_id", MAX_CHECKIN_BUSINESS_ID_LENGTH)
        validate_checkin_business_id(business_id)
        note = _stored_text(record, "note", MAX_CHECKIN_NOTE_BYTES)
        for field in ("stock_percent", "supply_percent", "stock_value"):
            if record[f"{field}_type"] not in ("integer", "null"):
                raise ValueError("Invalid stored number")
        values = normalize_business_checkin(record["stock_percent"], record["supply_percent"],
                                           record["stock_value"], note)
        recorded_at = _stored_checkin_timestamp(record)
        return BusinessCheckIn(record["id"], record["character_id"], business_id, recorded_at, *values)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise BusinessCheckInDataError() from None
