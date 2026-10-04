"""Explicit, bounded drafts for one manual check-in transaction."""

from dataclasses import dataclass

from .business_checkins import (
    BUSINESS_LABELS, BusinessCheckInValidationError, normalize_business_checkin,
    validate_checkin_business_id,
)


MAX_CHECKIN_BATCH_ROWS = len(BUSINESS_LABELS)


@dataclass(frozen=True)
class BusinessCheckInDraft:
    """Reviewed manual values, without live provenance or persistence state."""

    business_id: str
    stock_percent: int | None = None
    supply_percent: int | None = None
    stock_value: int | None = None
    note: str = ""


class BusinessCheckInBatchUncertain(Exception):
    """Commit or close failed; the caller cannot safely infer whether rows saved."""

    def __init__(self):
        super().__init__(
            "The batch save result is uncertain. Inspect saved history before creating any new check-ins."
        )


def normalize_business_checkin_drafts(
    drafts: list[BusinessCheckInDraft] | tuple[BusinessCheckInDraft, ...],
) -> tuple[BusinessCheckInDraft, ...]:
    """Validate the entire bounded input and detach it before storage is opened."""
    if type(drafts) not in (list, tuple) or not 1 <= len(drafts) <= MAX_CHECKIN_BATCH_ROWS:
        raise BusinessCheckInValidationError("Choose one or more businesses, up to the available catalog size.")
    seen = set()
    normalized = []
    for draft in drafts:
        if type(draft) is not BusinessCheckInDraft:
            raise BusinessCheckInValidationError("Choose valid manual check-in drafts.")
        validate_checkin_business_id(draft.business_id, for_write=True)
        if draft.business_id in seen:
            raise BusinessCheckInValidationError("Choose each business only once per batch.")
        values = normalize_business_checkin(
            draft.stock_percent, draft.supply_percent, draft.stock_value, draft.note,
        )
        normalized.append(BusinessCheckInDraft(draft.business_id, *values))
        seen.add(draft.business_id)
    return tuple(normalized)
