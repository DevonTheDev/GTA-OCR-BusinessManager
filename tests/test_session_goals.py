"""Session goal targets persist independently from live attempt progress."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier

import pytest

from src.tracking.goals import GoalType
from src.tracking.session import SessionStats
from src.tracking.session_goals import SessionGoalController
from src.utils import persistence


def target_payload(goal_type="EARNINGS", target_value=100, display_name="My target"):
    return {
        "version": 1,
        "target": {
            "goal_type": goal_type,
            "target_value": target_value,
            "display_name": display_name,
        },
    }


def test_missing_file_means_no_goal_without_creating_directories(tmp_path):
    path = tmp_path / "absent" / "session_goal_target.json"
    controller = SessionGoalController(path)
    assert controller.current_goal is None
    assert controller.has_goal is False
    assert controller.storage_error is None
    assert controller.needs_save_retry is False
    assert controller.retry_save() is False
    assert controller.sync(None, 100, 100, 100) is False
    assert not path.parent.exists()


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_target_round_trip_starts_fresh_and_contains_only_selection(tmp_path, goal_type):
    path = tmp_path / "new" / "session_goal_target.json"
    controller = SessionGoalController(path)
    controller.set_goal(goal_type, 100, "Café <b>literal</b> 🎮")
    assert controller.sync(SessionStats(), 150, 150, 150) is True
    assert controller.current_goal.is_complete
    assert json.loads(path.read_text(encoding="utf-8")) == target_payload(
        goal_type.name, 100, "Café <b>literal</b> 🎮"
    )

    restored = SessionGoalController(path)
    goal = restored.current_goal
    assert restored.has_goal
    assert goal.goal_type == goal_type
    assert goal.display_name == "Café <b>literal</b> 🎮"
    assert goal.target_value == 100
    assert goal.current_value == 0
    assert goal.completed_at is None
    assert not goal.is_complete
    assert restored.storage_error is None
    assert not restored.needs_save_retry


def test_clear_remembers_empty_selection(tmp_path):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)
    controller.set_goal(GoalType.EARNINGS, 100)
    controller.clear_goal()
    assert not controller.has_goal
    assert controller.current_goal is None
    assert json.loads(path.read_text()) == {"version": 1, "target": None}
    assert SessionGoalController(path).current_goal is None


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_empty_name_gets_existing_goal_default(tmp_path, goal_type):
    controller = SessionGoalController(tmp_path / "target.json")
    goal = controller.set_goal(goal_type, 100)
    assert goal.display_name
    restored = SessionGoalController(tmp_path / "target.json")
    assert restored.current_goal.display_name == goal.display_name


@pytest.mark.parametrize(
    "goal_type,target,name",
    [
        ("EARNINGS", 100, "Goal"),
        (None, 100, "Goal"),
        (1, 100, "Goal"),
        (GoalType.EARNINGS, True, "Goal"),
        (GoalType.EARNINGS, False, "Goal"),
        (GoalType.EARNINGS, 1.5, "Goal"),
        (GoalType.EARNINGS, "100", "Goal"),
        (GoalType.EARNINGS, None, "Goal"),
        (GoalType.EARNINGS, 0, "Goal"),
        (GoalType.EARNINGS, -1, "Goal"),
        (GoalType.EARNINGS, 100, None),
        (GoalType.EARNINGS, 100, 7),
        (GoalType.EARNINGS, 100, " \t "),
        (GoalType.EARNINGS, 100, "Line\nbreak"),
        (GoalType.EARNINGS, 100, "Null\x00name"),
        (GoalType.EARNINGS, 100, "Bad\x7fname"),
        (GoalType.EARNINGS, 100, "Bad\ud800name"),
        (GoalType.EARNINGS, 100, "x" * 201),
        (GoalType.EARNINGS, 10 ** 400, "Unrenderable earnings"),
    ],
)
def test_invalid_selection_leaves_active_goal_and_file_unchanged(tmp_path, goal_type, target, name):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)
    controller.set_goal(GoalType.ACTIVITIES, 10, "Keep me")
    controller.sync(SessionStats(), 0, 3, 0)
    before = controller.current_goal
    previous_bytes = path.read_bytes()
    with pytest.raises(ValueError):
        controller.set_goal(goal_type, target, name)
    assert controller.current_goal == before
    assert path.read_bytes() == previous_bytes
    assert not controller.needs_save_retry


INVALID_FILES = [
    b"not json",
    b"\xff",
    b"[]",
    b"null",
    b"{}",
    b'{"version": true, "target": null}',
    b'{"version": 2, "target": null}',
    b'{"version": 1}',
    b'{"version": 1, "target": []}',
    b'{"version": 1, "target": null, "current_value": 90}',
    json.dumps(target_payload(goal_type="UNKNOWN")).encode(),
    json.dumps(target_payload(target_value=True)).encode(),
    json.dumps(target_payload(target_value=1.5)).encode(),
    json.dumps(target_payload(target_value=0)).encode(),
    json.dumps(target_payload(display_name=None)).encode(),
    json.dumps(target_payload(display_name=" ")).encode(),
    json.dumps(target_payload(display_name="Bad\x00name")).encode(),
    json.dumps(target_payload(display_name="x" * 201)).encode(),
    json.dumps(target_payload(target_value=10 ** 400)).encode(),
    b'{"version":1,"version":2,"target":null}',
    b'{"version":1,"target":{"goal_type":"TIME","target_value":2}}',
    b" " * (16 * 1024 + 1),
    json.dumps(target_payload()).encode() + b" " * (16 * 1024),
    b"[" * 2000 + b"]" * 2000,
]


@pytest.mark.parametrize(
    "contents", INVALID_FILES, ids=[f"invalid-{i}" for i in range(len(INVALID_FILES))]
)
def test_invalid_loaded_file_is_visible_and_not_overwritten_by_refresh_or_retry(tmp_path, contents):
    path = tmp_path / "target.json"
    path.write_bytes(contents)
    controller = SessionGoalController(path)
    assert controller.current_goal is None
    assert controller.storage_error
    assert not controller.needs_save_retry
    assert controller.retry_save() is False
    assert controller.sync(SessionStats(), 100, 100, 100) is False
    assert path.read_bytes() == contents
    assert controller.storage_error


def test_unreadable_file_reports_safe_message_without_exception_details(tmp_path, monkeypatch):
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target_payload()))
    original_open = Path.open

    def refuse_target(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("private-device-and-path-details")
        return original_open(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", refuse_target)
        controller = SessionGoalController(path)
    assert controller.current_goal is None
    assert controller.storage_error
    assert "private-device" not in controller.storage_error
    assert not controller.needs_save_retry
    assert controller.retry_save() is False
    assert json.loads(path.read_text()) == target_payload()


@pytest.mark.parametrize("action", ["set", "clear"])
def test_explicit_selection_can_replace_a_bad_file(tmp_path, action):
    path = tmp_path / "target.json"
    path.write_text("broken")
    controller = SessionGoalController(path)
    if action == "set":
        controller.set_goal(GoalType.TIME, 30, "Lunch break")
        expected = target_payload("TIME", 30, "Lunch break")
    else:
        controller.clear_goal()
        expected = {"version": 1, "target": None}
    assert json.loads(path.read_text()) == expected
    assert controller.storage_error is None
    assert not controller.needs_save_retry


def test_progress_period_changes_and_unchanged_selection_never_rewrite_target(
    tmp_path, monkeypatch
):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)
    controller.set_goal(GoalType.EARNINGS, 100, "One hundred")
    previous = path.read_bytes()
    previous_stat = path.stat()

    def refuse_new_file(**kwargs):
        pytest.fail("Refresh or unchanged target must not start a file write")

    monkeypatch.setattr(persistence, "NamedTemporaryFile", refuse_new_file)
    period = SessionStats()
    for total in (0, 20, 20, 100, 150):
        controller.sync(period, total, 0, 0)
    controller.sync(replace(period), 0, 0, 0)
    controller.set_goal(GoalType.EARNINGS, 100, "One hundred")
    assert not controller.needs_save_retry
    assert controller.retry_save() is False
    assert path.read_bytes() == previous
    assert path.stat().st_mtime_ns == previous_stat.st_mtime_ns


@pytest.mark.parametrize(
    "goal_type,expected", [(GoalType.EARNINGS, 13), (GoalType.ACTIVITIES, 7), (GoalType.TIME, 31)]
)
def test_sync_uses_only_the_relevant_absolute_total(tmp_path, goal_type, expected):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(goal_type, 100)
    period = SessionStats()
    for _ in range(3):
        assert controller.sync(period, 13, 7, 31) is False
        assert controller.current_goal.current_value == expected


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_only_first_crossing_completes_and_stays_stable(tmp_path, goal_type):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(goal_type, 100)
    period = SessionStats()
    assert controller.sync(period, 99, 99, 99) is False
    assert controller.sync(period, 120, 120, 120) is True
    completed = controller.current_goal
    assert completed.completed_at is not None
    for total in (0, 90, 100, 150):
        assert controller.sync(period, total, total, total) is False
        assert controller.current_goal == completed


def test_equal_statistics_objects_start_distinct_attempts(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.ACTIVITIES, 2, "Two activities")
    period = SessionStats()
    assert controller.sync(period, 0, 3, 0)
    completed = controller.current_goal
    next_period = replace(period)
    assert next_period == period
    assert next_period is not period
    assert controller.sync(next_period, 0, 0, 0) is False
    fresh = controller.current_goal
    assert fresh.current_value == 0
    assert fresh.completed_at is None
    assert fresh.display_name == completed.display_name
    assert fresh.target_value == completed.target_value
    assert controller.sync(next_period, 0, 2, 0) is True
    assert completed.current_value == 3


def test_same_mutated_period_keeps_current_attempt(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.ACTIVITIES, 10)
    period = SessionStats()
    controller.sync(period, 0, 3, 0)
    started = controller.current_goal.started_at
    period.current_money = 100
    controller.sync(period, 100, 4, 1)
    assert controller.current_goal.started_at == started
    assert controller.current_goal.current_value == 4


def test_none_period_has_zero_progress_even_if_caller_supplies_old_totals(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.EARNINGS, 100)
    assert controller.sync(None, 200, 200, 200) is False
    assert controller.current_goal.current_value == 0
    assert controller.sync(SessionStats(), 200, 0, 0) is True
    assert controller.sync(None, 200, 200, 200) is False
    assert controller.current_goal.current_value == 0
    assert controller.current_goal.completed_at is None


def test_selection_mid_period_immediately_accepts_existing_totals(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    period = SessionStats()
    assert controller.sync(period, 300, 5, 90) is False
    controller.set_goal(GoalType.EARNINGS, 200)
    assert controller.sync(period, 300, 5, 90) is True
    controller.set_goal(GoalType.TIME, 120)
    assert controller.sync(period, 300, 5, 90) is False
    assert controller.current_goal.current_value == 90


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_negative_totals_are_clamped_to_zero(tmp_path, goal_type):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(goal_type, 100)
    assert controller.sync(SessionStats(), -10, -10, -10) is False
    assert controller.current_goal.current_value == 0
    assert controller.current_goal.progress == 0


def test_goal_returns_are_defensive_snapshots(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    selected = controller.set_goal(GoalType.ACTIVITIES, 10, "Original")
    selected.target_value = 1
    selected.display_name = "Altered outside"
    selected.current_value = 100
    assert controller.current_goal.target_value == 10
    assert controller.current_goal.display_name == "Original"
    assert controller.current_goal.current_value == 0
    snapshot = controller.current_goal
    period = SessionStats()
    controller.sync(period, 0, 10, 0)
    snapshot.current_value = -50
    finished = controller.current_goal
    finished.completed_at = None
    assert controller.current_goal.is_complete
    assert controller.current_goal.completed_at is not None


def test_controllers_do_not_use_or_mutate_global_tracker(tmp_path, monkeypatch):
    from src.tracking import goals

    global_tracker = goals.GoalTracker(data_path=None)
    global_tracker.set_goal(GoalType.TIME, 600, "Unrelated")
    monkeypatch.setattr(goals, "_tracker", global_tracker)
    first = SessionGoalController(tmp_path / "one.json")
    second = SessionGoalController(tmp_path / "two.json")
    first.set_goal(GoalType.ACTIVITIES, 2)
    first.sync(SessionStats(), 0, 2, 0)
    assert not second.has_goal
    second.set_goal(GoalType.EARNINGS, 100)
    assert second.current_goal.current_value == 0
    assert first.current_goal.goal_type == GoalType.ACTIVITIES
    assert global_tracker.current_goal.display_name == "Unrelated"
    assert global_tracker.current_goal.current_value == 0


@pytest.mark.parametrize("failure", ["serialize", "write", "close", "replace"])
def test_failed_atomic_save_preserves_file_keeps_target_live_and_can_retry(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)
    controller.set_goal(GoalType.EARNINGS, 100, "Previous")
    previous = path.read_bytes()
    original_factory = persistence.NamedTemporaryFile

    def failed_dump(payload, stream, **kwargs):
        stream.write("partially serialized")
        raise ValueError("private serialization detail")

    def failed_replace(source, destination):
        raise PermissionError("private replacement detail")

    class FailingFile:
        def __init__(self, **kwargs):
            self.handle = original_factory(**kwargs)
            self.name = self.handle.name

        def __enter__(self):
            return self

        def write(self, text):
            if failure == "write":
                self.handle.write("partial")
                raise OSError("private write detail")
            return self.handle.write(text)

        def __exit__(self, *args):
            self.handle.close()
            if failure == "close":
                raise OSError("private close detail")

    with monkeypatch.context() as patch:
        if failure == "serialize":
            patch.setattr(json, "dump", failed_dump)
        elif failure == "replace":
            patch.setattr(persistence.os, "replace", failed_replace)
        else:
            patch.setattr(persistence, "NamedTemporaryFile", FailingFile)
        goal = controller.set_goal(GoalType.ACTIVITIES, 2, "New selection")
        assert controller.current_goal == goal
        assert controller.sync(SessionStats(), 0, 2, 0) is True
        assert controller.needs_save_retry
        assert controller.storage_error
        assert "private" not in controller.storage_error
        assert path.read_bytes() == previous
        assert list(tmp_path.iterdir()) == [path]
        assert controller.retry_save() is False
        assert controller.needs_save_retry
        assert path.read_bytes() == previous

    assert controller.retry_save() is True
    assert not controller.needs_save_retry
    assert controller.storage_error is None
    assert controller.current_goal.is_complete
    assert json.loads(path.read_text()) == target_payload("ACTIVITIES", 2, "New selection")
    assert SessionGoalController(path).current_goal.current_value == 0


def test_failed_first_save_leaves_no_partial_target_and_retry_uses_latest_selection(
    tmp_path, monkeypatch
):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)

    def fail_create(**kwargs):
        raise PermissionError("temporary file denied")

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "NamedTemporaryFile", fail_create)
        controller.set_goal(GoalType.TIME, 60)
        assert not path.exists()
        controller.set_goal(GoalType.ACTIVITIES, 5, "Latest")
        assert controller.needs_save_retry
        assert not path.exists()
    assert controller.retry_save()
    assert json.loads(path.read_text()) == target_payload("ACTIVITIES", 5, "Latest")


def test_failed_clear_is_live_and_retry_remembers_no_goal(tmp_path, monkeypatch):
    path = tmp_path / "target.json"
    controller = SessionGoalController(path)
    controller.set_goal(GoalType.TIME, 60)
    previous = path.read_bytes()

    def fail_replace(source, target):
        raise OSError("replacement unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", fail_replace)
        controller.clear_goal()
        assert controller.current_goal is None
        assert controller.needs_save_retry
        assert path.read_bytes() == previous
        controller.sync(SessionStats(), 0, 0, 60)
        assert controller.current_goal is None
    assert controller.retry_save()
    assert json.loads(path.read_text()) == {"version": 1, "target": None}


def test_concurrent_syncs_report_completion_once_and_return_independent_snapshots(tmp_path):
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.ACTIVITIES, 2)
    period = SessionStats()
    ready = Barrier(8)

    def sync():
        ready.wait(timeout=5)
        completed = controller.sync(period, 0, 2, 0)
        snapshot = controller.current_goal
        snapshot.display_name = "Private copy"
        return completed

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: sync(), range(8)))
    assert results.count(True) == 1
    assert controller.current_goal.is_complete
    assert controller.current_goal.display_name == "Complete 2 activities"
