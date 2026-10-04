"""Detached, explicitly ordered comparisons of recorded manual observations."""

from dataclasses import dataclass
from datetime import datetime

from .business_checkins import BusinessCheckIn, BusinessCheckInUnavailable, _utc


class BusinessCheckInComparisonUnavailable(BusinessCheckInUnavailable):
    """Requested IDs are absent from the selected owner and business scope."""

    def __init__(self, checkin_ids: tuple[int, ...]):
        self.checkin_ids = tuple(checkin_ids)
        super().__init__()


@dataclass(frozen=True)
class BusinessCheckInDifferences:
    """Exact B minus A; stock and supply differences use percentage points."""

    stock_percent: int | None
    supply_percent: int | None
    stock_value: int | None


@dataclass(frozen=True)
class BusinessCheckInComparison:
    """One captured pair, preserving the caller's baseline/comparison choices."""

    character_id: int
    character_name: str
    business_id: str
    baseline: BusinessCheckIn
    comparison: BusinessCheckIn
    captured_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "captured_at", _utc(self.captured_at))

    @property
    def differences(self) -> BusinessCheckInDifferences:
        def difference(field):
            baseline = getattr(self.baseline, field)
            comparison = getattr(self.comparison, field)
            return comparison - baseline if baseline is not None and comparison is not None else None

        return BusinessCheckInDifferences(
            difference("stock_percent"), difference("supply_percent"), difference("stock_value"),
        )

    def to_report(self) -> dict:
        """Return fresh report containers for this captured pair without rereading storage."""
        differences = self.differences
        return {
            "format_version": 1,
            "kind": "manual_business_checkin_comparison",
            "scope": "manual_observations",
            "orientation": "comparison_minus_baseline",
            "captured_at": self.captured_at.isoformat(),
            "timezone": "UTC",
            "character": {"id": self.character_id, "name": self.character_name},
            "business_id": self.business_id,
            "baseline": self.baseline.to_dict(),
            "comparison": self.comparison.to_dict(),
            "differences": {
                "stock_percentage_points": differences.stock_percent,
                "supply_percentage_points": differences.supply_percent,
                "stock_value": differences.stock_value,
            },
            "field_notes": {
                "scope": "Explicit manual observations only; no elapsed gameplay, production, sales, profit or rates.",
                "unknown": "Null means unknown; zero is an explicit recorded value. Missing values do not carry forward.",
                "differences": "Comparison B minus baseline A; null when either value is unknown. No improvement is inferred.",
                "units": "Stock and supplies differences are percentage points, not percent growth. Value differences are exact dollars.",
                "stock_value": "User-recorded observed value, not verified sale proceeds or profit.",
                "recorded_at": "UTC time when Save recorded each observation, not a gameplay timestamp.",
                "ordering": "The selected A/B order is preserved, regardless of insertion IDs or recorded timestamps.",
                "captured_at": "Both records and character context were read together at comparison capture; export uses that capture.",
            },
        }
