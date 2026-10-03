"""Completion listeners cannot repeat an old result or erase a new mission."""

from datetime import timedelta

import pytest

from src.game.state_machine import GameState
from tests.test_mission_result_accounting import (
    app as app,
    clock as clock,
    activities,
    detect,
)


def start_and_finish(app, clock, name="Headhunter"):
    app._data.current_money = 100
    detect(app, GameState.MISSION_ACTIVE, name)
    clock.value += timedelta(seconds=120)
    app._data.current_money = 350
    detect(app, GameState.MISSION_COMPLETE)


@pytest.mark.parametrize("repeated", [GameState.MISSION_COMPLETE, GameState.MISSION_FAILED])
def test_listener_reentering_result_does_not_duplicate_sqlite_or_session_counts(app, clock, repeated):
    callbacks = []

    def completed(activity):
        callbacks.append(activity)
        detect(app, repeated)

    app.on_mission_complete(completed)
    start_and_finish(app, clock)
    (row,) = activities(app)
    assert row["earnings"] == 250
    assert row["success"] is True
    assert row["name"] == "Headhunter"
    assert app.session_stats.activities_completed == app.session_stats.missions_passed == 1
    assert app.session_stats.missions_failed == 0
    assert callbacks == app._activity_tracker.completed_activities


@pytest.mark.parametrize("next_state", [GameState.MISSION_ACTIVE, GameState.SELLING,
                                       GameState.HEIST_PREP, GameState.HEIST_FINALE])
def test_listener_can_start_new_activity_without_outer_completion_reset(app, clock, next_state):
    started = []

    def completed(activity):
        if started:
            return
        assert activity.name == "Headhunter"
        detect(app, next_state, "Next mission")
        started.append(app._activity_tracker.current_activity)

    app.on_mission_complete(completed)
    start_and_finish(app, clock)
    assert started[0] is not None
    assert app._activity_tracker.current_activity is started[0]
    assert app._data.mission_start_time == clock.value
    assert app._data.mission_start_money == 350
    clock.value += timedelta(seconds=45)
    app._data.current_money = 400
    detect(app, GameState.MISSION_COMPLETE)
    rows = activities(app)
    assert [row["earnings"] for row in rows] == [250, 50]
    assert [row["duration_seconds"] for row in rows] == [120, 45]
    assert app.session_stats.activities_completed == 2


def test_callback_observes_recorded_completion_and_cleared_old_tracking(app, clock):
    observed = []

    def completed(activity):
        observed.append((
            app._data.mission_start_time, app._data.mission_start_money,
            app._data.current_mission, activity.name, activity.earnings,
            len(activities(app)), app.session_stats.activities_completed,
        ))

    app.on_mission_complete(completed)
    start_and_finish(app, clock)
    assert observed == [(None, None, None, "Headhunter", 250, 1, 1)]


def test_new_listener_receives_future_completions_only(app, clock):
    events = []

    def late(activity):
        events.append(("late", activity.name))

    def first(activity):
        events.append(("first", activity.name))
        if activity.name == "Headhunter":
            app.on_mission_complete(late)

    app.on_mission_complete(first)
    start_and_finish(app, clock)
    assert events == [("first", "Headhunter")]
    start_and_finish(app, clock, "Sightseer")
    assert events == [("first", "Headhunter"), ("first", "Sightseer"), ("late", "Sightseer")]


def test_clearing_listener_list_during_notification_preserves_current_snapshot(app, clock):
    events = []

    def first(activity):
        events.append("first")
        app._on_mission_complete.clear()

    app.on_mission_complete(first)
    app.on_mission_complete(lambda activity: events.append("second"))
    start_and_finish(app, clock)
    assert events == ["first", "second"]
    start_and_finish(app, clock, "Sightseer")
    assert events == ["first", "second"]


def test_nested_new_completion_keeps_each_callback_argument_and_payout(app, clock):
    events = []

    def first(activity):
        events.append(("first", activity.name))
        if activity.name == "Headhunter":
            detect(app, GameState.MISSION_ACTIVE, "Sightseer")
            clock.value += timedelta(seconds=30)
            app._data.current_money = 400
            detect(app, GameState.MISSION_COMPLETE)

    app.on_mission_complete(first)
    app.on_mission_complete(lambda activity: events.append(("second", activity.name)))
    start_and_finish(app, clock)
    assert events == [("first", "Headhunter"), ("first", "Sightseer"),
                      ("second", "Sightseer"), ("second", "Headhunter")]
    assert [(row["name"], row["earnings"]) for row in activities(app)] == [
        ("Headhunter", 250), ("Sightseer", 50),
    ]
    assert app.session_stats.activities_completed == 2
    assert app._data.mission_start_time is None


def test_throwing_listener_does_not_skip_later_listener_or_leave_old_baseline(app, clock):
    events = []

    def broken(activity):
        events.append(app._data.mission_start_time)
        raise RuntimeError("synthetic listener failure")

    app.on_mission_complete(broken)
    app.on_mission_complete(lambda activity: events.append(activity.earnings))
    start_and_finish(app, clock)
    assert events == [None, 250]
    detect(app, GameState.MISSION_COMPLETE)
    assert len(activities(app)) == 1
