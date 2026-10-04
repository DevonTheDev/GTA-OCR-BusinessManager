"""Memory-only recommendation snoozes use one bounded monotonic registry."""

from concurrent.futures import ThreadPoolExecutor
import time

import pytest


class Clock:
    def __init__(self, now=100.0):
        self.now = now
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


def registry(clock):
    from src.optimization.recommendation_snoozes import RecommendationSnoozes

    return RecommendationSnoozes(clock=clock)


def test_snooze_expires_at_exactly_ten_minutes():
    clock = Clock()
    snoozes = registry(clock)
    assert snoozes.snooze("activity:headhunter") is True
    clock.now = 699.999
    assert snoozes.active_ids() == frozenset({"activity:headhunter"})
    clock.now = 700.0
    assert snoozes.active_ids() == frozenset()


def test_wall_clock_rollback_does_not_delay_monotonic_expiry(monkeypatch):
    clock = Clock()
    snoozes = registry(clock)
    monkeypatch.setattr(time, "time", lambda: 100_000.0)
    assert snoozes.snooze("activity:headhunter")
    monkeypatch.setattr(time, "time", lambda: 1.0)
    clock.now = 700.0
    assert snoozes.active_ids() == frozenset()


def test_duplicate_snooze_does_not_extend_the_original_deadline():
    clock = Clock()
    snoozes = registry(clock)
    assert snoozes.snooze("activity:headhunter")
    clock.now = 699.0
    assert snoozes.snooze("activity:headhunter")
    clock.now = 700.0
    assert snoozes.active_ids() == frozenset()


def test_expired_snooze_can_be_started_again_on_write():
    clock = Clock()
    snoozes = registry(clock)
    assert snoozes.snooze("activity:headhunter")
    clock.now = 700.0
    assert snoozes.snooze("activity:headhunter")
    clock.now = 1299.999
    assert snoozes.active_ids() == frozenset({"activity:headhunter"})
    clock.now = 1300.0
    assert snoozes.active_ids() == frozenset()


def test_restore_all_clears_active_and_expired_entries():
    clock = Clock()
    snoozes = registry(clock)
    assert snoozes.snooze("activity:headhunter")
    clock.now = 500.0
    assert snoozes.snooze("activity:sightseer")
    clock.now = 700.0
    assert snoozes.restore_all() is None
    assert snoozes.active_ids() == frozenset()
    assert snoozes.restore_all() is None


def test_full_registry_rejects_new_id_without_evicting_or_extending_any_id():
    clock = Clock()
    snoozes = registry(clock)
    expected = frozenset(f"activity:{index}" for index in range(128))
    assert all(snoozes.snooze(identifier) for identifier in expected)
    clock.now = 699.0
    assert snoozes.snooze("activity:overflow") is False
    assert snoozes.snooze("activity:0") is True
    assert snoozes.active_ids() == expected
    clock.now = 700.0
    assert snoozes.active_ids() == frozenset()


def test_write_prunes_expired_entries_before_enforcing_capacity():
    clock = Clock()
    snoozes = registry(clock)
    assert all(snoozes.snooze(f"activity:{index}") for index in range(128))
    clock.now = 700.0
    assert snoozes.snooze("activity:new") is True
    assert snoozes.active_ids() == frozenset({"activity:new"})


@pytest.mark.parametrize("identifier", [None, "", "   ", [], {}, 4, True])
def test_invalid_identity_does_not_consume_capacity(identifier):
    snoozes = registry(Clock())
    assert snoozes.snooze(identifier) is False
    assert snoozes.active_ids() == frozenset()


def test_registry_instances_and_returned_snapshots_are_isolated():
    clock = Clock()
    first, second = registry(clock), registry(clock)
    assert first.snooze("activity:headhunter")
    observed = first.active_ids()
    assert second.active_ids() == frozenset()
    first.restore_all()
    assert observed == frozenset({"activity:headhunter"})
    assert first.active_ids() == frozenset()


def test_each_registry_read_or_snooze_samples_elapsed_time_once():
    clock = Clock()
    snoozes = registry(clock)
    before = clock.calls
    assert snoozes.snooze("activity:headhunter")
    assert clock.calls - before == 1
    before = clock.calls
    assert snoozes.active_ids() == frozenset({"activity:headhunter"})
    assert clock.calls - before == 1


def test_concurrent_writes_obey_capacity_and_preserve_successful_ids():
    snoozes = registry(Clock())
    identifiers = [f"activity:{index}" for index in range(256)]
    with ThreadPoolExecutor(max_workers=16) as workers:
        accepted = list(workers.map(snoozes.snooze, identifiers))
    expected = frozenset(identifier for identifier, result in zip(identifiers, accepted) if result)
    assert len(expected) == 128
    assert snoozes.active_ids() == expected
