"""Recommendation presentation filtering leaves gameplay and persistence alone."""

import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace

import pytest

from src.app import AppState, CaptureResult, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.money_parser import MoneyReading
from src.game.activities import Activity, ActivityType
from src.optimization.optimizer import Recommendation
from tests.test_recommendation_snoozes import Clock


@pytest.fixture
def app(tmp_path):
    return GTABusinessManager(Settings(tmp_path / "settings.yaml"))


def clocked_app(tmp_path, clock):
    return GTABusinessManager(
        Settings(tmp_path / "settings.yaml"), recommendation_clock=clock
    )


def add_history(app, activity_type=ActivityType.VIP_WORK, *, earnings=25_000, success=True):
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    activity = Activity(
        activity_type=activity_type, started_at=started,
        ended_at=started + timedelta(minutes=5), earnings=earnings, success=success,
    )
    app._activity_tracker._completed_activities.append(activity)
    return activity


def add_six_urgent_suggestions(app):
    for business in ("bunker", "cocaine", "meth"):
        app.update_business_state(business, 95, 0)


def identities(recommendations):
    return [getattr(recommendation, "recommendation_id", None) for recommendation in recommendations]


def test_complete_optimizer_pool_ranks_six_urgent_suggestions_ahead_of_insights(app):
    add_six_urgent_suggestions(app)
    add_history(app)
    recommendations = app.recommendations
    assert len(recommendations) == 7
    assert [recommendation.priority for recommendation in recommendations[:6]] == [1] * 6
    assert not any(identifier.startswith("insight:") for identifier in identities(recommendations))


def test_snapshot_counts_all_candidates_and_refills_after_hiding_first_five(app):
    add_six_urgent_suggestions(app)
    add_history(app)
    before = app.recommendation_snapshot
    assert before.total_candidates == 12
    assert before.hidden_count == before.snoozed_count == 0
    hidden = identities(before.visible[:5])
    assert all(app.snooze_recommendation(identifier) for identifier in hidden)
    after = app.recommendation_snapshot
    assert after.total_candidates == 12
    assert after.hidden_count == after.snoozed_count == 5
    assert len(after.visible) == 7
    assert not set(hidden).intersection(identities(after.visible))
    assert app.recommendations == list(after.visible)
    assert "insight:best_activity:VIP_WORK" in identities(after.visible)


def test_snooze_validates_against_the_complete_pool_including_nonvisible_candidates(app):
    add_six_urgent_suggestions(app)
    add_history(app)
    identifier = "insight:best_activity:VIP_WORK"
    assert identifier not in identities(app.recommendations)
    assert app.snooze_recommendation(identifier) is True
    assert app.recommendation_snapshot.hidden_count == 1


@pytest.mark.parametrize("identifier", [None, "", "  ", [], {}, True, 9, "activity:unknown"])
def test_unknown_or_invalid_ids_cannot_be_snoozed(app, identifier):
    assert app.snooze_recommendation(identifier) is False
    assert app.recommendation_snapshot.snoozed_count == 0


def test_no_activity_history_keeps_the_existing_no_insights_behavior(app):
    snapshot = app.recommendation_snapshot
    assert snapshot.total_candidates == 5
    assert not any(identifier.startswith("insight:") for identifier in identities(snapshot.visible))
    assert app.snooze_recommendation("insight:start") is False


def test_hiding_every_candidate_and_restore_all_produce_consistent_counts(app):
    initial = app.recommendation_snapshot
    for identifier in identities(initial.visible):
        assert app.snooze_recommendation(identifier)
    hidden = app.recommendation_snapshot
    assert hidden.visible == ()
    assert hidden.hidden_count == hidden.total_candidates == hidden.snoozed_count == 5
    assert app.recommendations == []
    assert app.restore_snoozed_recommendations() is None
    assert app.recommendation_snapshot == initial


def test_snapshot_and_recommendations_return_detached_containers(app):
    snapshot = app.recommendation_snapshot
    assert isinstance(snapshot.visible, tuple)
    with pytest.raises(FrozenInstanceError):
        snapshot.hidden_count = 1
    recommendations = app.recommendations
    recommendations.clear()
    assert len(app.recommendations) == 5


def test_app_expiry_and_duplicate_idempotence_use_one_clock_sample_per_snapshot(tmp_path):
    clock = Clock()
    app = clocked_app(tmp_path, clock)
    identifier = "activity:headhunter"
    assert app.snooze_recommendation(identifier)
    clock.now = 699.999
    assert app.snooze_recommendation(identifier)
    before_calls = clock.calls
    snapshot = app.recommendation_snapshot
    assert clock.calls - before_calls == 1
    assert snapshot.hidden_count == snapshot.snoozed_count == 1
    assert identifier not in identities(snapshot.visible)
    clock.now = 700.0
    before_calls = clock.calls
    snapshot = app.recommendation_snapshot
    assert clock.calls - before_calls == 1
    assert snapshot.hidden_count == snapshot.snoozed_count == 0
    assert identifier in identities(snapshot.visible)


def test_current_hidden_count_excludes_ineligible_stored_ids_and_rehides_returning_candidate(app):
    app.update_business_state("bunker", 95, 100)
    identifier = "business:bunker:sell"
    assert app.snooze_recommendation(identifier)
    app.clear_business_readings()
    snapshot = app.recommendation_snapshot
    assert snapshot.hidden_count == 0
    assert snapshot.snoozed_count == 1
    assert app.snooze_recommendation(identifier) is False
    app.update_business_state("bunker", 95, 100)
    snapshot = app.recommendation_snapshot
    assert snapshot.hidden_count == snapshot.snoozed_count == 1
    assert identifier not in identities(snapshot.visible)
    app.restore_snoozed_recommendations()
    assert identifier in identities(app.recommendations)


def test_business_snooze_survives_updated_text_priority_and_value(app):
    app.update_business_state("bunker", 50, None, 100_000)
    identifier = "business:bunker:sell"
    assert app.snooze_recommendation(identifier)
    app.update_business_state("bunker", 95, None, 700_000)
    assert identifier not in identities(app.recommendations)
    app.restore_snoozed_recommendations()
    restored = next(rec for rec in app.recommendations if rec.recommendation_id == identifier)
    assert restored.action == "Sell Bunker NOW"
    assert restored.estimated_value == 700_000


def test_insight_snooze_survives_changed_statistics_but_not_a_different_activity_type(app):
    activity = add_history(app)
    identifier = "insight:best_activity:VIP_WORK"
    assert identifier in identities(app.recommendations)
    assert app.snooze_recommendation(identifier)
    activity.earnings = 50_000
    assert identifier not in identities(app.recommendations)
    app.restore_snoozed_recommendations()
    changed = next(rec for rec in app.recommendations if rec.recommendation_id == identifier)
    assert "$600,000/hour" in changed.action
    assert app.snooze_recommendation(identifier)
    add_history(app, ActivityType.CONTACT_MISSION, earnings=100_000)
    assert "insight:best_activity:CONTACT_MISSION" in identities(app.recommendations)
    assert app.recommendation_snapshot.hidden_count == 0
    assert app.recommendation_snapshot.snoozed_count == 1


def test_complete_analytics_pool_refills_beyond_the_legacy_five_insight_limit(app):
    types = list(ActivityType)[:8]
    for activity_type in types:
        for _ in range(3):
            add_history(app, activity_type, earnings=0, success=False)
    for recommendation in app._optimizer.get_recommendations(limit=None):
        assert app.snooze_recommendation(recommendation.recommendation_id)
    snapshot = app.recommendation_snapshot
    assert snapshot.total_candidates == 13
    assert len(snapshot.visible) == 7
    assert identities(snapshot.visible) == [f"insight:practice:{kind.name}" for kind in types[:7]]


def test_stable_id_dedup_keeps_best_ranked_record_and_preserves_legacy_records(app, monkeypatch):
    low = Recommendation(4, "Later wording", "lower", score=0.1, recommendation_id="shared")
    high = Recommendation(1, "Urgent wording", "higher", score=0.9, recommendation_id="shared")
    legacy = Recommendation(2, "Old style", "No ID", score=0.7)
    external = SimpleNamespace(priority=2, action="External style", reason="No field", score=0.5)
    monkeypatch.setattr(app._optimizer, "get_recommendations", lambda limit=None: [low, legacy, external, high])
    snapshot = app.recommendation_snapshot
    assert snapshot.total_candidates == 3
    assert snapshot.visible == (high, legacy, external)
    assert app.snooze_recommendation("Old style") is False
    assert app.snooze_recommendation("External style") is False
    assert app.snooze_recommendation("shared") is True
    assert app.recommendations == [legacy, external]


def test_app_reports_full_registry_and_keeps_existing_snoozes(app, monkeypatch):
    # A larger generated pool models future extension generators without changing
    # the current catalog or bypassing the application's membership validation.
    candidates = [Recommendation(2, f"Task {index}", "", recommendation_id=f"activity:{index}")
                  for index in range(129)]
    monkeypatch.setattr(app._optimizer, "get_recommendations", lambda limit=None: candidates[:limit])
    assert all(app.snooze_recommendation(rec.recommendation_id) for rec in candidates[:128])
    assert app.snooze_recommendation(candidates[-1].recommendation_id) is False
    assert app.snooze_recommendation(candidates[0].recommendation_id) is True
    snapshot = app.recommendation_snapshot
    assert snapshot.snoozed_count == snapshot.hidden_count == 128
    assert snapshot.total_candidates == 129
    assert snapshot.visible == (candidates[-1],)


def test_manager_lifecycle_preserves_snoozes_but_new_manager_starts_clear(tmp_path, monkeypatch):
    clock = Clock()
    app = clocked_app(tmp_path, clock)
    identifier = "activity:headhunter"
    assert app.snooze_recommendation(identifier)

    def initialize():
        app._session_tracker.start_session()

    def cycle():
        app._stop_event.wait(0.01)
        return CaptureResult()

    monkeypatch.setattr(app, "_initialize_components", initialize)
    monkeypatch.setattr(app, "_do_capture_cycle", cycle)
    monkeypatch.setattr(app, "_adjust_capture_rate", lambda _state: None)
    try:
        for _ in range(2):
            assert app.start()
            app.pause()
            app.reset_session()
            app.resume()
            assert identifier not in identities(app.recommendations)
            app.stop()
            assert app.state is AppState.STOPPED
            assert app.recommendation_snapshot.snoozed_count == 1
        new_app = GTABusinessManager(app._settings, recommendation_clock=clock)
        assert new_app.recommendation_snapshot.snoozed_count == 0
        assert identifier in identities(new_app.recommendations)
    finally:
        app.stop()


def test_filter_restore_and_expiry_leave_files_and_gameplay_state_unchanged(tmp_path):
    clock = Clock()
    app = clocked_app(tmp_path, clock)
    repository = Repository(str(tmp_path / "history.db"))
    assert repository.initialize()
    character = repository.get_or_create_character("Snooze test")
    app._repository = repository
    app._data.character_id = character.id
    app._data.db_session_id = repository.start_session(character).id
    app._session_tracker.start_session()
    app._process_money_change(MoneyReading(total=100_000))
    app._process_money_change(MoneyReading(total=120_000))
    app.update_business_state("bunker", 95, 0)
    add_history(app)
    app._activity_tracker.start_activity(ActivityType.VIP_WORK, "In progress")
    app._optimizer.set_cooldown("headhunter", 10)
    app._optimizer._scheduler.schedule_sell("bunker")
    app.cooldown_tracker.start_custom_timer("Independent timer", 300)

    def state():
        return copy.deepcopy((
            app._data, app.session_stats, app._money_parser.__dict__,
            app._timer_parser.__dict__, app._mission_parser.__dict__, app._business_parser.__dict__,
            app._activity_tracker.__dict__, app._optimizer._business_states,
            app._optimizer._cooldowns, app._optimizer._scheduler.__dict__,
            app.cooldown_tracker.get_active_cooldowns(), app._settings._config,
        ))

    def files():
        return {path.relative_to(tmp_path): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()}

    try:
        before_state, before_files = state(), files()
        identifier = "business:bunker:sell"
        assert app.snooze_recommendation(identifier)
        assert identifier not in identities(app.recommendations)
        app.restore_snoozed_recommendations()
        assert app.snooze_recommendation(identifier)
        clock.now = 700.0
        assert identifier in identities(app.recommendation_snapshot.visible)
        assert state() == before_state
        assert files() == before_files
    finally:
        repository.close()


def test_snapshot_releases_data_lock_before_sampling_registry_clock(tmp_path):
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()

    def clock():
        entered.set()
        assert release.wait(2)
        return 100.0

    app = clocked_app(tmp_path, clock)
    results, errors = [], []

    def read_snapshot():
        try:
            results.append(app.recommendation_snapshot)
        except BaseException as error:
            errors.append(error)

    def update_business():
        app.update_business_state("bunker", 95, 0)
        updated.set()

    reader = threading.Thread(target=read_snapshot)
    writer = threading.Thread(target=update_business)
    reader.start()
    try:
        assert entered.wait(1)
        writer.start()
        assert updated.wait(1), "Recommendation registry must not retain the app data lock"
    finally:
        release.set()
        reader.join(2)
        if writer.ident is not None:
            writer.join(2)
    assert not reader.is_alive() and not writer.is_alive()
    assert errors == []
    assert len(results) == 1
