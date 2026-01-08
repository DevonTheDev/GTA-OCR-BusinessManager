"""Tests for passive income tracking."""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.tracking.passive_income import (
    PassiveIncomeTracker,
    PassiveIncomeState,
    NightclubGoods,
    NIGHTCLUB_GOODS,
    AGENCY_SAFE_MAX,
)


class TestPassiveIncomeState:
    """Tests for PassiveIncomeState dataclass."""

    def test_state_creation(self):
        """Test basic state creation."""
        state = PassiveIncomeState(
            source_id="nightclub",
            name="Nightclub",
            max_value=1_000_000,
            rate_per_hour=50_000,
        )

        assert state.source_id == "nightclub"
        assert state.max_value == 1_000_000
        assert state.rate_per_hour == 50_000

    def test_fill_percent(self):
        """Test fill percentage calculation."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=500_000,
            max_value=1_000_000,
            rate_per_hour=0,
        )

        assert state.fill_percent == 50.0

    def test_fill_percent_caps_at_100(self):
        """Test fill percentage caps at 100."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=1_500_000,
            max_value=1_000_000,
            rate_per_hour=0,
        )

        assert state.fill_percent == 100.0

    def test_is_full(self):
        """Test full detection."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            max_value=1_000_000,
            rate_per_hour=0,
        )

        state.current_value = 999_999
        assert not state.is_full

        state.current_value = 1_000_000
        assert state.is_full

    def test_estimated_current_value_no_rate(self):
        """Test estimated value with no production rate."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=500_000,
            max_value=1_000_000,
            rate_per_hour=0,
        )

        assert state.estimated_current_value == 500_000

    def test_estimated_current_value_with_rate(self):
        """Test estimated value with production rate."""
        # Start 1 hour ago
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=500_000,
            max_value=2_000_000,
            rate_per_hour=100_000,
            last_updated=start,
        )

        estimated = state.estimated_current_value
        # Should be approximately 600,000 (500,000 + 100,000)
        assert 590_000 <= estimated <= 610_000

    def test_estimated_value_capped_at_max(self):
        """Test estimated value doesn't exceed max."""
        start = datetime.now(timezone.utc) - timedelta(hours=10)
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=500_000,
            max_value=1_000_000,
            rate_per_hour=100_000,
            last_updated=start,
        )

        assert state.estimated_current_value == 1_000_000

    def test_time_until_full(self):
        """Test time until full calculation."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=0,
            max_value=1_000_000,
            rate_per_hour=100_000,
        )

        time_left = state.time_until_full
        assert time_left is not None
        # Should be approximately 10 hours
        assert 9.5 * 3600 <= time_left.total_seconds() <= 10.5 * 3600

    def test_time_until_full_already_full(self):
        """Test time until full when already full."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=1_000_000,
            max_value=1_000_000,
            rate_per_hour=100_000,
        )

        assert state.time_until_full is None

    def test_time_until_full_no_production(self):
        """Test time until full with no production."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=500_000,
            max_value=1_000_000,
            rate_per_hour=0,
        )

        assert state.time_until_full is None

    def test_time_until_full_formatted(self):
        """Test formatted time until full."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=0,
            max_value=100_000,
            rate_per_hour=100_000,  # 1 hour to fill
        )

        formatted = state.time_until_full_formatted
        assert "h" in formatted or "m" in formatted

    def test_time_until_full_formatted_full(self):
        """Test formatted time when full."""
        state = PassiveIncomeState(
            source_id="test",
            name="Test",
            current_value=1_000_000,
            max_value=1_000_000,
            rate_per_hour=100_000,
        )

        assert state.time_until_full_formatted == "N/A"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime.now(timezone.utc)
        state = PassiveIncomeState(
            source_id="nightclub",
            name="Nightclub",
            current_value=500_000,
            max_value=1_000_000,
            rate_per_hour=50_000,
            last_updated=now,
        )

        data = state.to_dict()
        assert data["source_id"] == "nightclub"
        assert data["current_value"] == 500_000
        assert data["max_value"] == 1_000_000

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "source_id": "agency",
            "name": "Agency Safe",
            "current_value": 100_000,
            "max_value": 250_000,
            "rate_per_hour": 625,
            "last_updated": now.isoformat(),
            "last_collected": None,
            "is_linked": True,
        }

        state = PassiveIncomeState.from_dict(data)
        assert state.source_id == "agency"
        assert state.current_value == 100_000
        assert state.max_value == 250_000


class TestNightclubGoods:
    """Tests for NightclubGoods dataclass."""

    def test_goods_creation(self):
        """Test basic goods creation."""
        goods = NightclubGoods(
            goods_id="cargo",
            name="Cargo",
            current_units=25,
            max_units=50,
            rate_per_hour=2.0,
            value_per_unit=10_000,
        )

        assert goods.goods_id == "cargo"
        assert goods.current_units == 25

    def test_current_value(self):
        """Test current value calculation."""
        goods = NightclubGoods(
            goods_id="test",
            name="Test",
            current_units=10,
            max_units=50,
            rate_per_hour=2.0,
            value_per_unit=10_000,
        )

        assert goods.current_value == 100_000

    def test_max_value(self):
        """Test max value calculation."""
        goods = NightclubGoods(
            goods_id="test",
            name="Test",
            current_units=0,
            max_units=50,
            rate_per_hour=2.0,
            value_per_unit=10_000,
        )

        assert goods.max_value == 500_000

    def test_fill_percent(self):
        """Test fill percentage."""
        goods = NightclubGoods(
            goods_id="test",
            name="Test",
            current_units=25,
            max_units=50,
            rate_per_hour=2.0,
            value_per_unit=10_000,
        )

        assert goods.fill_percent == 50.0

    def test_is_full(self):
        """Test full detection."""
        goods = NightclubGoods(
            goods_id="test",
            name="Test",
            current_units=50,
            max_units=50,
            rate_per_hour=2.0,
            value_per_unit=10_000,
        )

        assert goods.is_full


class TestPassiveIncomeTracker:
    """Tests for PassiveIncomeTracker class."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create a PassiveIncomeTracker with temp storage."""
        return PassiveIncomeTracker(data_path=tmp_path / "passive.json")

    @pytest.fixture
    def memory_tracker(self):
        """Create a PassiveIncomeTracker without persistence."""
        return PassiveIncomeTracker(data_path=None)

    def test_initialization(self, tracker):
        """Test tracker initialization."""
        assert tracker.nightclub is not None
        assert tracker.agency is not None
        assert len(tracker.nightclub_goods) > 0

    def test_update_nightclub(self, tracker):
        """Test updating nightclub value."""
        tracker.update_nightclub(500_000)

        assert tracker.nightclub.current_value == 500_000

    def test_update_agency(self, tracker):
        """Test updating agency value."""
        tracker.update_agency(150_000)

        assert tracker.agency.current_value == 150_000

    def test_record_nightclub_sale(self, tracker):
        """Test recording nightclub sale."""
        tracker.update_nightclub(500_000)
        tracker.record_nightclub_sale(500_000)

        assert tracker.nightclub.current_value == 0
        assert tracker.nightclub.last_collected is not None

    def test_record_agency_collection(self, tracker):
        """Test recording agency collection."""
        tracker.update_agency(200_000)
        tracker.record_agency_collection(200_000)

        assert tracker.agency.current_value == 0
        assert tracker.agency.last_collected is not None

    def test_total_passive_value(self, tracker):
        """Test total passive value calculation."""
        tracker.update_nightclub(500_000)
        tracker.update_agency(100_000)

        total = tracker.total_passive_value
        assert total >= 600_000  # At least what we set

    def test_total_passive_max(self, tracker):
        """Test total max capacity."""
        max_val = tracker.total_passive_max
        assert max_val > 0
        assert max_val >= AGENCY_SAFE_MAX  # At least agency max

    def test_get_predictions(self, tracker):
        """Test getting predictions."""
        tracker.update_nightclub(500_000)
        tracker.update_agency(100_000)

        predictions = tracker.get_predictions()
        assert len(predictions) == 2

        nc_pred = next(p for p in predictions if "Nightclub" in p["name"])
        assert nc_pred["current_value"] >= 500_000

        agency_pred = next(p for p in predictions if "Agency" in p["name"])
        assert agency_pred["current_value"] >= 100_000

    def test_get_recommendations_full_agency(self, tracker):
        """Test recommendations when agency is nearly full."""
        tracker.update_agency(240_000)  # 96% full

        recs = tracker.get_recommendations()
        assert len(recs) > 0
        assert any("agency" in r.lower() or "safe" in r.lower() for r in recs)

    def test_persistence(self, tmp_path):
        """Test that state persists across tracker instances."""
        path = tmp_path / "passive.json"

        # Create tracker and set values
        tracker1 = PassiveIncomeTracker(data_path=path)
        tracker1.update_nightclub(500_000)
        tracker1.update_agency(100_000)

        # Create new tracker with same path
        tracker2 = PassiveIncomeTracker(data_path=path)

        assert tracker2.nightclub.current_value == 500_000
        assert tracker2.agency.current_value == 100_000

    def test_nightclub_goods_update(self, tracker):
        """Test updating specific nightclub goods."""
        tracker.update_nightclub_goods("cargo", 25)

        goods = tracker.nightclub_goods["cargo"]
        assert goods.current_units == 25

    def test_nightclub_goods_active(self, tracker):
        """Test setting goods active status."""
        tracker.set_nightclub_goods_active("cargo", False)

        goods = tracker.nightclub_goods["cargo"]
        assert not goods.is_active


class TestNightclubGoodsDefinitions:
    """Tests for NIGHTCLUB_GOODS definitions."""

    def test_all_goods_have_required_fields(self):
        """Test all goods have required fields."""
        for goods_id, info in NIGHTCLUB_GOODS.items():
            assert "name" in info
            assert "rate_per_hour" in info
            assert "value_per_unit" in info
            assert "max_units" in info

    def test_south_american_imports_highest_value(self):
        """Test South American Imports has highest per-unit value."""
        sa_value = NIGHTCLUB_GOODS["south_american_imports"]["value_per_unit"]

        for goods_id, info in NIGHTCLUB_GOODS.items():
            if goods_id != "south_american_imports":
                assert info["value_per_unit"] <= sa_value

    def test_cargo_goods_exist(self):
        """Test cargo goods configuration."""
        assert "cargo" in NIGHTCLUB_GOODS
        assert NIGHTCLUB_GOODS["cargo"]["linked_to"] == "special_cargo"
