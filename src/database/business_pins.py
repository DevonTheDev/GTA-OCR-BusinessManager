"""Detached manual board preferences, separate from observations and live state."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from .business_checkins import BUSINESS_LABELS


MAX_BUSINESS_PINS = 256
MAX_PIN_BUSINESS_ID_BYTES = 50
MAX_PIN_CHARACTER_NAME_BYTES = 4096
_SQLITE_MAX_INTEGER = 2**63 - 1
_BUSINESS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,49}\Z")


class BusinessPinError(Exception):
    """A manual pin failure whose message is safe to show directly."""


class BusinessPinValidationError(BusinessPinError, ValueError):
    """The requested identity or desired state is invalid."""


class BusinessPinUnavailable(BusinessPinError):
    """The saved owner or preference storage cannot be accessed."""

    def __init__(self):
        super().__init__("Manual business pins are unavailable. Refresh the saved characters and try again.")


class BusinessPinDataError(BusinessPinError):
    """Stored preferences or their owner contain invalid data."""

    def __init__(self):
        super().__init__("Saved manual business pins contain invalid data and cannot be displayed.")


class BusinessPinLimitError(BusinessPinError):
    """The stored or requested pin set exceeds the bounded collection limit."""

    def __init__(self):
        super().__init__("The manual business pin limit has been reached. Unpin a business before adding another.")


def validate_pin_character_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _SQLITE_MAX_INTEGER:
        raise BusinessPinValidationError("Choose an existing saved character.")


def validate_pin_business_id(value: str, *, for_pin: bool = False) -> None:
    if not isinstance(value, str) or _BUSINESS_ID.fullmatch(value) is None:
        raise BusinessPinValidationError("Choose a valid business identifier.")
    if for_pin and value not in BUSINESS_LABELS:
        raise BusinessPinValidationError("Choose a business from the available catalog.")


def validate_pin_desired_state(pinned: bool) -> None:
    if type(pinned) is not bool:
        raise BusinessPinValidationError("Choose whether to pin or unpin the business.")


@dataclass(frozen=True)
class ManualBusinessPins:
    """Caller-owned snapshot of one saved character's manual pin preferences."""

    character_id: int
    character_name: str
    captured_at: datetime
    business_ids: tuple[str, ...]

    def __post_init__(self):
        stamp = self.captured_at
        if not isinstance(stamp, datetime):
            raise ValueError("A timestamp is required")
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        object.__setattr__(self, "captured_at", stamp)
        object.__setattr__(self, "business_ids", tuple(self.business_ids))


def _stored_text(record, field, max_bytes):
    value = record[field]
    if record[f"{field}_type"] != "text" or not isinstance(value, bytes) or len(value) > max_bytes:
        raise ValueError("Invalid stored text")
    return value.decode("utf-8", errors="strict")


def manual_business_pins_from_storage(records, character_id, captured_at) -> ManualBusinessPins:
    """Admit bounded raw SQLite values without coercion or silent deduplication."""
    if not records:
        raise BusinessPinUnavailable()
    if len(records) > MAX_BUSINESS_PINS:
        raise BusinessPinLimitError()
    try:
        owner = records[0]
        if owner["owner_count"] != 1 or owner["context_character_id_type"] != "integer":
            raise ValueError("Invalid stored owner")
        validate_pin_character_id(owner["context_character_id"])
        if owner["context_character_id"] != character_id:
            raise ValueError("Mismatched stored owner")
        name = _stored_text(owner, "character_name", MAX_PIN_CHARACTER_NAME_BYTES)
        business_ids = []
        for record in records:
            if not record["row_present"]:
                continue
            if record["pin_character_id_type"] != "integer":
                raise ValueError("Invalid stored pin owner")
            validate_pin_character_id(record["pin_character_id"])
            if record["pin_character_id"] != character_id:
                raise ValueError("Mismatched stored pin owner")
            business_id = _stored_text(record, "business_id", MAX_PIN_BUSINESS_ID_BYTES)
            validate_pin_business_id(business_id)
            business_ids.append(business_id)
        if len(set(business_ids)) != len(business_ids):
            raise ValueError("Duplicate stored pins")
        return ManualBusinessPins(character_id, name, captured_at, tuple(sorted(business_ids)))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise BusinessPinDataError() from None
