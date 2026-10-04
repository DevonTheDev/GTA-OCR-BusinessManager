"""Stable recommendation identities and untruncated generator contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from src.game.activities import Activity, ActivityType
from src.optimization.optimizer import Optimizer, Recommendation
from src.tracking.analytics import Analytics


def completed_activity(activity_type, *, earnings=25000, success=True, seconds=300):
    started_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    return Activity(
        activity_type=activity_type,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=seconds),
        earnings=earnings,
        success=success,
    )


def test_legacy_positional_recommendation_defaults_to_no_identity():
    recommendation = Recommendation(
        2, "Sell Bunker", "Ready", 250000, 15, "bunker", ActivityType.SELL_MISSION, 0.5
    )

    assert recommendation.priority == 2
    assert recommendation.action == "Sell Bunker"
    assert recommendation.reason == "Ready"
    assert recommendation.estimated_value == 250000
    assert recommendation.estimated_time_minutes == 15
    assert recommendation.business_type == "bunker"
    assert recommendation.activity_type == ActivityType.SELL_MISSION
    assert recommendation.score == 0.5
    assert recommendation.recommendation_id is None


def test_business_sell_identity_survives_all_urgency_labels_and_values():
    optimizer = Optimizer()
    scenarios = [
        (50, 100000, "Sell Bunker when ready", 3),
        (50, 300000, "Consider selling Bunker", 2),
        (80, 500000, "Sell Bunker", 1),
        (95, 700000, "Sell Bunker NOW", 1),
    ]

    for stock, value, expected_action, expected_priority in scenarios:
        optimizer.update_business_state("bunker", stock, None, value)
        recommendation = next(
            rec for rec in optimizer.get_recommendations(limit=None)
            if rec.activity_type == ActivityType.SELL_MISSION
        )
        assert recommendation.action == expected_action
        assert recommendation.priority == expected_priority
        assert recommendation.estimated_value == value
        assert recommendation.recommendation_id == "business:bunker:sell"


def test_business_and_action_kind_have_distinct_identities():
    optimizer = Optimizer()
    for business_id in ("bunker", "cocaine"):
        optimizer.update_business_state(business_id, 95, 0)

    business_recommendations = [
        rec for rec in optimizer.get_recommendations(limit=None) if rec.business_type
    ]

    assert len(business_recommendations) == 4
    assert {rec.recommendation_id for rec in business_recommendations} == {
        "business:bunker:sell", "business:bunker:resupply",
        "business:cocaine:sell", "business:cocaine:resupply",
    }


def test_business_resupply_identity_survives_urgency_changes():
    optimizer = Optimizer()
    recommendations = []
    for supply in (50, 20, 0):
        optimizer.update_business_state("bunker", 50, supply)
        recommendations.append(next(
            rec for rec in optimizer.get_recommendations(limit=None)
            if rec.activity_type == ActivityType.RESUPPLY_MISSION
        ))

    assert [rec.priority for rec in recommendations] == [3, 2, 1]
    assert len({rec.reason for rec in recommendations}) == 3
    assert {rec.recommendation_id for rec in recommendations} == {"business:bunker:resupply"}


def test_quick_activities_have_explicit_distinct_identities():
    recommendations = Optimizer().get_recommendations(limit=None)

    assert [(rec.action, rec.recommendation_id) for rec in recommendations] == [
        ("Do Payphone Hit", "activity:payphone_hit"),
        ("Auto Shop Contract", "activity:auto_shop_contract"),
        ("Run Headhunter", "activity:headhunter"),
        ("Run Sightseer", "activity:sightseer"),
        ("Run Security Contract", "activity:security_contract"),
    ]
    vip_work = [rec for rec in recommendations if rec.activity_type == ActivityType.VIP_WORK]
    assert len({rec.recommendation_id for rec in vip_work}) == 2


def test_payphone_cooldown_text_and_availability_share_identity():
    optimizer = Optimizer()
    recommendations = []
    for remaining in (5, 2, 0):
        optimizer.set_cooldown("payphone_hit", remaining)
        recommendations.append(next(
            rec for rec in optimizer.get_recommendations(limit=None)
            if rec.activity_type == ActivityType.PAYPHONE_HIT
        ))

    assert len({rec.action for rec in recommendations}) == 3
    assert [rec.priority for rec in recommendations] == [4, 4, 2]
    assert {rec.recommendation_id for rec in recommendations} == {"activity:payphone_hit"}


@pytest.fixture
def busy_optimizer():
    optimizer = Optimizer()
    for business_id in ("bunker", "cocaine", "meth"):
        optimizer.update_business_state(business_id, 95, 0)
    return optimizer


def test_full_optimizer_pool_preserves_all_six_urgent_business_candidates(busy_optimizer):
    recommendations = busy_optimizer.get_recommendations(limit=None)

    assert len(recommendations) == 11
    assert [rec.priority for rec in recommendations[:6]] == [1] * 6
    assert {rec.recommendation_id for rec in recommendations[:6]} == {
        f"business:{business_id}:{kind}"
        for business_id in ("bunker", "cocaine", "meth")
        for kind in ("sell", "resupply")
    }
    assert len({rec.recommendation_id for rec in recommendations}) == 11
    assert recommendations == sorted(recommendations, key=lambda rec: (rec.priority, -rec.score))
    assert busy_optimizer.get_recommendations() == recommendations[:5]


@pytest.mark.parametrize("limit", [0, 1, 3, 8, 100, -1, -3])
def test_optimizer_numeric_limits_keep_existing_slice_semantics(busy_optimizer, limit):
    full_pool = busy_optimizer.get_recommendations(limit=None)

    assert busy_optimizer.get_recommendations(limit=limit) == full_pool[:limit]


def test_empty_analytics_has_stable_start_identity_and_legacy_text():
    analytics = Analytics()
    insights = analytics.get_recommendation_insights([], {})

    assert [(insight.recommendation_id, insight.text) for insight in insights] == [
        ("insight:start", "Start completing activities to get personalized recommendations!"),
    ]
    assert analytics.get_recommendations([], {}) == [insights[0].text]


def test_recommendation_insight_is_immutable():
    insight = Analytics().get_recommendation_insights([], {})[0]

    with pytest.raises(FrozenInstanceError):
        insight.recommendation_id = "changed"
    with pytest.raises(FrozenInstanceError):
        insight.text = "changed"


def test_best_activity_identity_survives_rate_changes_but_tracks_activity_type():
    analytics = Analytics()
    activities = [completed_activity(ActivityType.VIP_WORK, earnings=25000)]
    first = analytics.get_recommendation_insights(activities, {})[0]

    activities.append(completed_activity(ActivityType.VIP_WORK, earnings=35000))
    changed_rate = analytics.get_recommendation_insights(activities, {})[0]

    activities.append(completed_activity(ActivityType.PAYPHONE_HIT, earnings=85000))
    changed_type = analytics.get_recommendation_insights(activities, {})[0]

    assert first.recommendation_id == changed_rate.recommendation_id == "insight:best_activity:VIP_WORK"
    assert first.text == "Your most efficient activity is VIP_WORK at $300,000/hour"
    assert changed_rate.text == "Your most efficient activity is VIP_WORK at $360,000/hour"
    assert changed_type.recommendation_id == "insight:best_activity:PAYPHONE_HIT"
    assert changed_type.text == "Your most efficient activity is PAYPHONE_HIT at $1,020,000/hour"


def test_practice_identity_survives_success_rate_changes_and_differs_from_best():
    analytics = Analytics()
    activities = [
        completed_activity(ActivityType.HEIST_FINALE, success=False),
        completed_activity(ActivityType.HEIST_FINALE, success=False),
        completed_activity(ActivityType.HEIST_FINALE, earnings=1000000),
    ]
    before = analytics.get_recommendation_insights(activities, {})
    activities.append(completed_activity(ActivityType.HEIST_FINALE, success=False))
    after = analytics.get_recommendation_insights(activities, {})

    assert [insight.recommendation_id for insight in before] == [
        "insight:best_activity:HEIST_FINALE", "insight:practice:HEIST_FINALE",
    ]
    assert [insight.recommendation_id for insight in after] == [
        insight.recommendation_id for insight in before
    ]
    assert before[1].text == "Consider practicing HEIST_FINALE - your success rate is only 33%"
    assert after[1].text == "Consider practicing HEIST_FINALE - your success rate is only 25%"


def test_analytics_full_pool_keeps_all_practice_insights_and_legacy_first_five():
    analytics = Analytics()
    activity_types = list(ActivityType)
    activities = [
        completed_activity(activity_type, success=(index == 2))
        for activity_type in reversed(activity_types)
        for index in range(3)
    ]

    insights = analytics.get_recommendation_insights(activities, {})

    assert len(insights) == len(activity_types) + 1
    assert insights[0].recommendation_id == "insight:best_activity:UNKNOWN"
    assert [insight.recommendation_id for insight in insights[1:]] == [
        f"insight:practice:{activity_type.name}" for activity_type in activity_types
    ]
    assert analytics.get_recommendations(activities, {}) == [
        insight.text for insight in insights[:5]
    ]
    assert analytics.get_recommendations(activities, {}) == [
        "Your most efficient activity is UNKNOWN at $300,000/hour",
        *[
            f"Consider practicing {activity_type.name} - your success rate is only 33%"
            for activity_type in activity_types[:4]
        ],
    ]
