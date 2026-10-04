"""Live observations preserve unknown fields and never infer an observed zero."""

from dataclasses import replace
from itertools import product

import pytest

from src.game.activities import ActivityType
from src.game.business_readings import normalize_live_business_reading as normalize
from src.game.businesses import (
    BUSINESSES,
    BusinessStatus,
    estimate_time_to_full,
    estimate_time_to_full_formatted,
)
from src.optimization.optimizer import Optimizer
from src.optimization.priorities import PriorityCalculator

# Unknowns, explicit zeroes, and positive observations must stay distinguishable.
OBSERVATIONS = [
    reading for reading in product((None, 0, 100), repeat=3) if reading != (None, None, None)
]


@pytest.mark.parametrize("business_id", BUSINESSES)
def test_supply_capability_matches_catalog_production_model(business_id):
    business = BUSINESSES[business_id]
    expected = business_id in {"cocaine", "meth", "cash", "weed", "documents", "bunker", "acid_lab"}
    assert business.uses_supplies is expected


@pytest.mark.parametrize("reading", OBSERVATIONS)
def test_normalizer_preserves_every_optional_observation_combination(reading):
    assert normalize("bunker", *reading, manual=True) == reading


@pytest.mark.parametrize(
    "reading",
    [
        (None, None, None),
        (-1, None, None),
        (101, None, None),
        (None, -1, None),
        (None, 101, None),
        (None, None, -1),
        (None, None, 2**63),
        (True, None, None),
        (None, False, None),
        (None, None, True),
        ("50", None, None),
        (None, "0", None),
        (None, None, "10"),
        (1.0, None, None),
        (None, 5.0, None),
        (None, None, 10.0),
    ],
)
def test_normalizer_rejects_invalid_fields(reading):
    with pytest.raises(ValueError):
        normalize("bunker", *reading)


@pytest.mark.parametrize("business_id", ["missing", "", None, [], True])
def test_normalizer_rejects_unknown_business(business_id):
    with pytest.raises(ValueError):
        normalize(business_id, 50, None, None)


def test_normalizer_accepts_largest_native_money_value():
    assert normalize("bunker", None, None, 2**63 - 1) == (None, None, 2**63 - 1)


@pytest.mark.parametrize(
    "business_id", ["nightclub", "agency", "vehicle_warehouse", "special_cargo"]
)
def test_irrelevant_supplies_are_rejected_for_manual_and_ignored_for_ocr(business_id):
    with pytest.raises(ValueError):
        normalize(business_id, 50, 0, None, manual=True)
    assert normalize(business_id, 50, 0, None) == (50, None, None)
    assert normalize(business_id, 50, None, 0, manual=True) == (50, None, 0)
    with pytest.raises(ValueError):
        normalize(business_id, None, 50, None)


@pytest.mark.parametrize("reading", OBSERVATIONS)
def test_optimizer_preserves_raw_observations_and_only_estimates_known_stock(reading):
    stock, supply, value = reading
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", stock, supply, value)
    state = optimizer._business_states["bunker"]
    assert (state.stock_percent, state.supply_percent, state.observed_value) == reading
    expected = (
        value
        if value is not None
        else (int(BUSINESSES["bunker"].max_value * stock / 100) if stock is not None else None)
    )
    assert state.estimated_value == expected
    recommendations = optimizer.get_recommendations(limit=100)
    sells = [rec for rec in recommendations if rec.activity_type == ActivityType.SELL_MISSION]
    resupplies = [
        rec for rec in recommendations if rec.activity_type == ActivityType.RESUPPLY_MISSION
    ]
    if stock is None or expected is None or expected == 0:
        assert sells == []
        assert optimizer.get_business_rankings() == []
    else:
        assert optimizer.get_business_rankings()[0][0] == "bunker"
    if stock is None or supply is None:
        assert resupplies == []
    summary = optimizer.get_summary()
    assert summary["total_business_value"] == (expected if expected is not None else 0)
    assert summary["businesses_with_unknown_value"] == int(expected is None)
    assert summary["businesses_ready_to_sell"] == int(
        stock is not None and stock >= 50 and expected is not None and expected > 0
    )
    assert summary["businesses_need_supplies"] == int(
        stock is not None and supply is not None and supply <= 20
    )


def test_omitted_value_is_estimated_but_zero_remains_zero():
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", 95, 0)
    state = optimizer._business_states["bunker"]
    assert state.observed_value is None
    assert state.estimated_value == 997500
    assert any(
        rec.activity_type == ActivityType.SELL_MISSION
        for rec in optimizer.get_recommendations(limit=100)
    )
    optimizer.update_business_state("bunker", 95, 0, 0)
    state = optimizer._business_states["bunker"]
    assert state.observed_value == state.estimated_value == 0
    assert all(
        rec.activity_type != ActivityType.SELL_MISSION
        for rec in optimizer.get_recommendations(limit=100)
    )


@pytest.mark.parametrize(
    "invalid",
    [
        ("bunker", None, None, None),
        ("bunker", 101, 50, 1),
        ("bunker", 50, -1, 1),
        ("bunker", 50, 50, -1),
        ("bunker", True, 50, 1),
        ("bunker", 50, 50, 2**63),
        ("missing", 50, 50, 1),
    ],
)
def test_invalid_optimizer_update_is_atomic(invalid):
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", 75, 25, 123456)
    before = optimizer._business_states.copy()
    with pytest.raises(ValueError):
        optimizer.update_business_state(*invalid)
    assert optimizer._business_states == before
    assert optimizer._business_states["bunker"] is before["bunker"]


@pytest.mark.parametrize(
    "business_id", ["nightclub", "agency", "vehicle_warehouse", "special_cargo"]
)
def test_unsupported_supply_observations_do_not_trigger_resupply(business_id):
    optimizer = Optimizer()
    optimizer.update_business_state(business_id, 50, 0, 100000)
    assert optimizer._business_states[business_id].supply_percent is None
    assert all(
        rec.activity_type != ActivityType.RESUPPLY_MISSION
        for rec in optimizer.get_recommendations(limit=100)
    )
    assert optimizer.get_summary()["businesses_need_supplies"] == 0
    score = PriorityCalculator().calculate_resupply_priority(BUSINESSES[business_id], 0, 50)
    assert score.total == 0


def test_known_positive_stock_and_empty_supplies_keep_normal_recommendations():
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", 100, 0, 1050000)
    recommendations = optimizer.get_recommendations(limit=100)
    sell = next(rec for rec in recommendations if rec.activity_type == ActivityType.SELL_MISSION)
    resupply = next(
        rec for rec in recommendations if rec.activity_type == ActivityType.RESUPPLY_MISSION
    )
    assert sell.action == "Sell Bunker NOW"
    assert sell.estimated_value == 1050000
    assert sell.score == pytest.approx(0.82)
    assert resupply.action == "Resupply Bunker"
    assert resupply.priority == 1


def test_priority_uses_observed_value_and_keeps_positional_compatibility():
    calculator = PriorityCalculator()
    business = BUSINESSES["bunker"]
    estimated = calculator.calculate_sell_priority(business, 50, 0, True)
    observed = calculator.calculate_sell_priority(business, 50, estimated_value=100000)
    zero = calculator.calculate_sell_priority(business, 50, estimated_value=0)
    assert estimated.value_score == 1.0
    assert observed.value_score == 0.2
    assert zero.value_score == 0.0


def test_rankings_and_recommendations_use_the_same_effective_value():
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", 50, None, 10000)
    optimizer.update_business_state("cocaine", 50, None, 500000)
    rankings = optimizer.get_business_rankings()
    assert [bid for bid, _, _ in rankings] == ["cocaine", "bunker"]
    scores = {bid: score.total for bid, score, _ in rankings}
    sells = [
        rec
        for rec in optimizer.get_recommendations(limit=100)
        if rec.activity_type == ActivityType.SELL_MISSION
    ]
    assert {rec.business_type for rec in sells} == {"bunker", "cocaine"}
    for rec in sells:
        assert rec.score == scores[rec.business_type]


def test_direct_rankings_skip_unknown_or_zero_value_states():
    calculator = PriorityCalculator()
    states = {"bunker": (None, 0), "cocaine": (100, None), "cash": (100, 0), "agency": (0, None)}
    rankings = calculator.rank_businesses(states, estimated_values={"cocaine": 0})
    assert [bid for bid, _ in rankings] == ["cash"]


@pytest.mark.parametrize(
    "business_id, stock, expected, formatted",
    [
        ("bunker", None, None, "Unknown"),
        ("bunker", 100, 0, "Full"),
        ("bunker", 50, 350, "5h 50m"),
        ("agency", 50, None, "Unknown"),
        ("agency", 100, 0, "Full"),
        ("special_cargo", 50, None, "Unknown"),
    ],
)
def test_time_to_full_distinguishes_unknown_from_full(business_id, stock, expected, formatted):
    business = BUSINESSES[business_id]
    assert estimate_time_to_full(business, stock) == expected
    assert estimate_time_to_full_formatted(business, stock) == formatted
    optimizer = Optimizer()
    optimizer.update_business_state(business_id, stock, None, 123)
    assert optimizer.estimate_time_to_full(business_id) == expected
    status = BusinessStatus(business=business, stock_percent=stock, supply_percent=0)
    assert status.time_to_full == expected
    assert status.time_to_full_formatted == formatted


def test_unseen_business_has_unknown_time_to_full():
    assert Optimizer().estimate_time_to_full("bunker") is None


def test_summary_counts_all_scheduled_actions_including_more_than_ten():
    optimizer = Optimizer()
    for business_id in BUSINESSES:
        optimizer._scheduler.schedule_sell(business_id)
    optimizer._scheduler.schedule_resupply("bunker")
    assert optimizer.get_summary()["scheduled_actions"] == 12


def test_partial_update_replaces_previous_known_fields_instead_of_filling_them():
    optimizer = Optimizer()
    optimizer.update_business_state("bunker", 100, 0, 1000000)
    optimizer.update_business_state("bunker", None, 20, None)
    state = optimizer._business_states["bunker"]
    assert state.stock_percent is None
    assert state.supply_percent == 20
    assert state.observed_value is None
    assert state.estimated_value is None
    assert optimizer.get_business_rankings() == []
    assert all(rec.business_type is None for rec in optimizer.get_recommendations(limit=100))


def test_time_estimate_never_rounds_incomplete_stock_to_full():
    business = replace(BUSINESSES["bunker"], full_production_time=30)
    assert estimate_time_to_full(business, 99) == 1
    assert estimate_time_to_full_formatted(business, 99) == "1m"
