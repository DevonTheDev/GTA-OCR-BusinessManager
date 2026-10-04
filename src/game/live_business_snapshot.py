"""Detached observed values for reviewing a live reading before a manual check-in."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .businesses import BUSINESSES
from .business_readings import normalize_live_business_reading


@dataclass(frozen=True)
class LiveBusinessReadingSnapshot:
    """One captured live observation, independent of saved character history."""

    business_id: str
    stock_percent: int | None
    supply_percent: int | None
    stock_value: int | None
    updated_at: datetime | None
    identity_source: str | None
    captured_at: datetime
    uses_supplies: bool


def create_live_business_reading_snapshot(
    business_id: str,
    *,
    stock_percent: int | None,
    supply_percent: int | None,
    stock_value: int | None,
    updated_at: datetime | None,
    identity_source: str | None,
    captured_at: datetime,
) -> LiveBusinessReadingSnapshot:
    """Validate observations without estimating, merging or inventing provenance.

    The caller captures the UTC read time. A live update retains its original
    naive local meaning or aware offset; unknown metadata stays unavailable.
    """
    stock_percent, supply_percent, stock_value = normalize_live_business_reading(
        business_id, stock_percent, supply_percent, stock_value, manual=False,
    )
    if not isinstance(captured_at, datetime) or captured_at.utcoffset() != timedelta(0):
        raise ValueError("Live reading capture time must be an aware UTC datetime")
    if not isinstance(updated_at, datetime):
        updated_at = None
    if type(identity_source) is not str or identity_source not in (
        "manual_entry", "ocr_text", "selected_target",
    ):
        identity_source = None
    return LiveBusinessReadingSnapshot(
        business_id=business_id,
        stock_percent=stock_percent,
        supply_percent=supply_percent,
        stock_value=stock_value,
        updated_at=updated_at,
        identity_source=identity_source,
        captured_at=captured_at,
        uses_supplies=BUSINESSES[business_id].uses_supplies,
    )
