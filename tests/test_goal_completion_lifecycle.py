"""Completed goal progress and callback ownership through real persisted state."""

import json

import pytest

from src.tracking.goals import GoalTracker, GoalType


@pytest.fixture(
    params=[
        (GoalType.EARNINGS, "update_earnings"),
        (GoalType.ACTIVITIES, "update_activities"),
        (GoalType.TIME, "update_time"),
    ]
)
def completed(request, tmp_path):
    goal_type, method = request.param
    tracker = GoalTracker(tmp_path / "goals.json")
    goal = tracker.set_goal(goal_type, 100, "Original")
    return tracker, goal, getattr(tracker, method), tmp_path / "goals.json"


def test_completed_progress_survives_later_reset_and_growth(completed):
    tracker, goal, update, path = completed
    calls = []
    tracker.on_goal_complete(calls.append)
    assert update(120) is True
    ended = goal.completed_at
    for amount in (0, 50, 100, 200):
        assert update(amount) is False
    assert goal.current_value == 120
    assert goal.completed_at == ended
    assert calls == [goal]
    assert tracker.goals_completed_count == 1
    assert len(tracker.completed_goals) == 1
    restored = GoalTracker(path)
    assert restored.current_goal.current_value == 120
    assert restored.goals_completed_count == 1


@pytest.mark.parametrize("change", ["clear", "replace"])
def test_callback_changes_current_goal_without_changing_completed_event(completed, change):
    tracker, goal, update, path = completed
    observed = []
    replacement = []
    history_during_callback = []

    def mutate_current(completed_goal):
        history_during_callback.append(tracker.completed_goals)
        assert completed_goal is goal
        if change == "clear":
            tracker.clear_goal()
        else:
            replacement.append(tracker.set_goal(goal.goal_type, 200, "Next"))

    tracker.on_goal_complete(mutate_current)
    tracker.on_goal_complete(observed.append)
    assert update(100) is True
    assert observed == [goal]
    assert history_during_callback == [[goal]]
    assert tracker.completed_goals == [goal]
    assert tracker.current_goal is (replacement[0] if change == "replace" else None)
    restored = GoalTracker(path)
    assert restored.goals_completed_count == 1
    assert restored.completed_goals[0].display_name == "Original"
    assert (restored.current_goal.display_name if restored.current_goal else None) == (
        "Next" if change == "replace" else None
    )


def test_nested_completion_keeps_each_distinct_goal_in_history(tmp_path):
    path = tmp_path / "goals.json"
    tracker = GoalTracker(path)
    first = tracker.set_goal(GoalType.ACTIVITIES, 1, "First")
    replacement = []
    events = []

    def finish_next(goal):
        events.append(goal)
        if goal is first:
            replacement.append(tracker.set_goal(GoalType.ACTIVITIES, 2, "Second"))
            assert tracker.update_activities(2) is True

    tracker.on_goal_complete(finish_next)
    assert tracker.update_activities(1) is True
    assert events == [first, replacement[0]]
    assert tracker.completed_goals == [first, replacement[0]]
    assert tracker.current_goal is replacement[0]
    assert [g.display_name for g in GoalTracker(path).completed_goals] == ["First", "Second"]


def test_callbacks_added_during_notification_start_with_next_completion(tmp_path):
    tracker = GoalTracker(tmp_path / "goals.json")
    first = tracker.set_goal(GoalType.ACTIVITIES, 1)
    calls = []

    def later(goal):
        calls.append(goal)

    def register(goal):
        if goal is first:
            tracker.on_goal_complete(later)

    tracker.on_goal_complete(register)
    tracker.update_activities(1)
    assert calls == []
    second = tracker.set_goal(GoalType.ACTIVITIES, 2)
    tracker.update_activities(2)
    assert calls == [second]


def test_callback_failure_does_not_prevent_history_save_or_later_listener(tmp_path, caplog):
    path = tmp_path / "goals.json"
    tracker = GoalTracker(path)
    goal = tracker.set_goal(GoalType.EARNINGS, 100)
    observed = []

    def broken(completed_goal):
        tracker.clear_goal()
        raise RuntimeError("synthetic listener failure")

    tracker.on_goal_complete(broken)
    tracker.on_goal_complete(observed.append)
    assert tracker.update_earnings(100) is True
    assert observed == [goal]
    assert tracker.completed_goals == [goal]
    assert tracker.current_goal is None
    assert "synthetic listener failure" in caplog.text
    assert GoalTracker(path).goals_completed_count == 1


def test_legacy_completed_goal_without_completion_timestamp_stays_completed(tmp_path):
    path = tmp_path / "goals.json"
    tracker = GoalTracker(path)
    goal = tracker.set_goal(GoalType.EARNINGS, 100)
    tracker.update_earnings(100)
    data = json.loads(path.read_text())
    data["current_goal"]["completed_at"] = None
    path.write_text(json.dumps(data))
    restored = GoalTracker(path)
    assert restored.update_earnings(0) is False
    assert restored.current_goal.current_value == 100
    assert restored.current_goal.completed_at is None
    assert restored.update_earnings(100) is False
    assert restored.goals_completed_count == 1
    assert goal.is_complete
