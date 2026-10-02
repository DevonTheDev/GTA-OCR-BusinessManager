"""Recent transition access works with the state machine's retained deque."""

import pytest

from src.constants import TRACKING
from src.game.state_machine import GameState, GameStateMachine


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, ["third"]),
        (2, ["third", "second"]),
        (3, ["third", "second", "first"]),
        (10, ["third", "second", "first"]),
        (0, []),
        (-1, []),
    ],
)
def test_recent_transitions_are_newest_first_without_mutating_context(count, expected):
    machine = GameStateMachine()
    observed = []
    machine.add_listener(observed.append)
    for state, trigger in [
        (GameState.IDLE, "first"),
        (GameState.LOADING, "second"),
        (GameState.MISSION_ACTIVE, "third"),
    ]:
        assert machine.transition_to(state, trigger=trigger)
    context = machine.context

    recent = machine.get_recent_transitions(count)

    assert [transition.trigger for transition in recent] == expected
    assert list(context.transitions) == observed
    assert machine.context is context and machine.state == GameState.MISSION_ACTIVE
    if recent:
        assert recent[0] is observed[-1]
    recent.clear()
    assert list(context.transitions) == observed


@pytest.mark.parametrize("count", [10, 1, 0, -1])
def test_empty_state_history_is_an_empty_list(count):
    machine = GameStateMachine()
    assert machine.get_recent_transitions(count) == []
    assert machine.state == GameState.UNKNOWN


def test_default_count_and_retention_keep_only_latest_transitions():
    machine = GameStateMachine()
    cycle = [
        GameState.IDLE,
        GameState.LOADING,
        GameState.MISSION_ACTIVE,
        GameState.MISSION_COMPLETE,
    ]
    total = TRACKING.MAX_STATE_HISTORY + 7
    for index in range(total):
        machine.transition_to(cycle[index % len(cycle)], trigger=f"step-{index}")

    recent = machine.get_recent_transitions()
    assert [transition.trigger for transition in recent] == [
        f"step-{index}" for index in range(total - 1, total - 11, -1)
    ]
    retained = machine.get_recent_transitions(total)
    assert len(retained) == TRACKING.MAX_STATE_HISTORY
    assert retained[0].trigger == f"step-{total - 1}"
    assert retained[-1].trigger == "step-7"
    assert list(reversed(retained)) == list(machine.context.transitions)
