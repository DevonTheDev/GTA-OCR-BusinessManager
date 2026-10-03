"""Elapsed-time estimates cannot reverse stored observations after clock rollback."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from src.tracking import cooldowns, nightclub, passive_income


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    for module in (cooldowns, nightclub, passive_income):
        monkeypatch.setattr(module, "datetime", Clock)
    return Clock


def timestamp(style, offset):
    value = NOW + offset
    if style == "legacy-naive":
        return value.replace(tzinfo=None)
    if style == "offset":
        return value.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return value


@pytest.mark.parametrize("style", ["utc", "offset", "legacy-naive"])
def test_future_cooldown_is_bounded_by_its_original_duration(clock, style):
    started = timestamp(style, timedelta(hours=1))
    info = cooldowns.CooldownInfo("fixture", "Fixture", started, 300)
    assert info.elapsed_seconds == 0
    assert info.remaining_seconds == 300
    assert info.progress == 0
    assert info.remaining_formatted == "5m 0s"
    assert not info.is_expired
    assert info.started_at == started


@pytest.mark.parametrize("style", ["utc", "offset", "legacy-naive"])
def test_future_passive_update_cannot_subtract_from_recorded_value(clock, style):
    updated = timestamp(style, timedelta(hours=1))
    state = passive_income.PassiveIncomeState(
        "fixture", "Fixture", current_value=100, max_value=1000,
        rate_per_hour=500, last_updated=updated,
    )
    assert state.estimated_current_value == 100
    assert state.time_until_full == timedelta(hours=1.8)
    assert state.current_value == 100 and state.last_updated == updated


@pytest.mark.parametrize("style", ["utc", "offset", "legacy-naive"])
def test_future_popularity_update_does_not_increase_recorded_popularity(clock, style):
    updated = timestamp(style, timedelta(hours=1))
    state = nightclub.NightclubState(popularity=100, last_popularity_update=updated)
    assert state.estimated_popularity_now == 100
    assert state.popularity == 100 and state.last_popularity_update == updated


def test_persisted_cooldown_waits_for_its_timestamp_then_expires_normally(clock, tmp_path):
    path = tmp_path / "cooldowns.json"
    clock.value = NOW + timedelta(hours=1)
    tracker = cooldowns.CooldownTracker(path)
    tracker.start_cooldown("fixture", duration_seconds=300)
    stored = path.read_bytes()
    clock.value = NOW
    restored = cooldowns.CooldownTracker(path)
    assert restored.get_remaining("fixture") == 300
    assert restored.get_active_cooldowns()[0].progress == 0
    assert path.read_bytes() == stored
    clock.value = NOW + timedelta(hours=1, seconds=150)
    assert restored.get_remaining("fixture") == 150
    assert restored.get_cooldown("fixture").progress == 0.5
    clock.value += timedelta(seconds=150)
    assert restored.get_cooldown("fixture") is None
    assert json.loads(path.read_text())["cooldowns"] == {}


def test_reloaded_nightclub_summary_preserves_observation_until_clock_catches_up(clock, tmp_path):
    path = tmp_path / "nightclub.json"
    clock.value = NOW + timedelta(hours=1)
    tracker = nightclub.NightclubTracker(path)
    tracker.update_popularity(80)
    stored = path.read_bytes()
    clock.value = NOW
    restored = nightclub.NightclubTracker(path)
    assert restored.get_summary()["popularity_estimated"] == 80
    assert restored.popularity == 80
    assert path.read_bytes() == stored
    clock.value = NOW + timedelta(hours=1, minutes=48)
    assert restored.popularity == 75


def test_passive_json_roundtrip_keeps_future_observation_and_later_accrual(clock):
    state = passive_income.PassiveIncomeState(
        "fixture", "Fixture", current_value=100, max_value=1000,
        rate_per_hour=500, last_updated=NOW + timedelta(hours=1),
    )
    restored = passive_income.PassiveIncomeState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored.estimated_current_value == 100
    clock.value = NOW + timedelta(hours=2)
    assert restored.estimated_current_value == 600
    clock.value += timedelta(hours=1)
    assert restored.estimated_current_value == 1000
    assert restored.current_value == 100


@pytest.mark.parametrize("elapsed", [0, 120, 300, 600])
def test_normal_cooldown_progress_and_expiry_are_unchanged(clock, elapsed):
    info = cooldowns.CooldownInfo("fixture", "Fixture", NOW - timedelta(seconds=elapsed), 300)
    assert info.elapsed_seconds == elapsed
    assert info.remaining_seconds == max(0, 300 - elapsed)
    assert info.progress == min(1, elapsed / 300)
    assert info.is_expired is (elapsed >= 300)


def test_inactive_income_and_zero_duration_cooldown_keep_existing_behavior(clock):
    state = passive_income.PassiveIncomeState(
        "fixture", "Fixture", current_value=100, max_value=1000,
        rate_per_hour=500, last_updated=NOW + timedelta(hours=1), is_linked=False,
    )
    assert state.estimated_current_value == 100
    assert state.time_until_full is None
    info = cooldowns.CooldownInfo("fixture", "Fixture", NOW + timedelta(hours=1), 0)
    assert info.progress == 1.0
    assert nightclub.NightclubState(popularity=75).estimated_popularity_now == 75
