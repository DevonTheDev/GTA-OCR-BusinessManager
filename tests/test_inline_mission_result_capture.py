"""Synthetic OCR text through the actual capture loop, trackers and SQLite."""

from datetime import timedelta

import pytest

from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_inline_mission_results import LABELS
from tests.test_mission_result_accounting import clock as clock
from tests.test_terminal_mission_identity_ownership import capture_frame, mission_stats, stored


@pytest.mark.parametrize("label,outcome", LABELS)
@pytest.mark.parametrize("title_first", [False, True])
@pytest.mark.parametrize("source", ["mission", "center", "banner"])
@pytest.mark.parametrize("separator", [" ", "\n"], ids=["inline", "newline-control"])
def test_starting_on_complete_inline_result_never_invents_activity(app, label, outcome, title_first, source, separator):
    completed = []
    app.on_mission_complete(completed.append)
    text = separator.join(("Headhunter", label) if title_first else (label, "Headhunter"))
    first = capture_frame(app, **{source: text})
    held = capture_frame(app, banner="Headhunter", balance=1250)
    repeated = capture_frame(app, banner=label + "\nHeadhunter", balance=1250)
    assert first.game_state == (GameState.MISSION_COMPLETE if outcome == "complete" else GameState.MISSION_FAILED)
    assert first.mission.outcome == outcome and first.mission.raw_text == text
    assert first.activity_name == repeated.activity_name == held.activity_name == ""
    assert held.game_state == GameState.UNKNOWN and held.state_confidence == 0.0
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == completed == []
    assert stored(app)["activities"] == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert held.money.display_value == app.current_money == 1250
    assert held.money_change == 250
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [(250, "Mission" if outcome == "complete" else "")]


@pytest.mark.parametrize("label,outcome", LABELS)
@pytest.mark.parametrize("title_first", [False, True])
def test_inline_completion_releases_next_mission_and_keeps_result_money_ownership(app, clock, label, outcome, title_first):
    completed, money_changes = [], []
    app.on_mission_complete(completed.append)
    app.on_money_change(lambda reading, change: money_changes.append(change))
    capture_frame(app, banner="Headhunter")
    first = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=120)
    text = f"Headhunter {label}" if title_first else f"{label} Headhunter"
    observed = capture_frame(app, banner=text, balance=1250)
    first_cooldown = app.cooldown_tracker.get_cooldown("headhunter")
    assert observed.game_state == (GameState.MISSION_COMPLETE if outcome == "complete" else GameState.MISSION_FAILED)
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [first]
    assert (first_cooldown is not None) is (outcome == "complete")
    held = capture_frame(app, banner="Headhunter", balance=1250)
    assert held.game_state == GameState.UNKNOWN and not held.activity_name
    capture_frame(app, banner=text, balance=1250)
    assert len(stored(app)["activities"]) == 1
    next_reading = capture_frame(app, banner="Sightseer", balance=1250)
    second = app._activity_tracker.current_activity
    assert next_reading.activity_name == second.name == "Sightseer"
    second_start = second.started_at
    clock.value += timedelta(seconds=10)
    rejected = capture_frame(app, banner=text, balance=1500)
    assert rejected.game_state == GameState.UNKNOWN and rejected.state_confidence == 0.0
    assert "conflict" in rejected.state_reason.lower()
    assert rejected.activity_name == "Sightseer"
    assert app._activity_tracker.current_activity is second and second.started_at == second_start
    assert rejected.money_change == 250 and rejected.money.display_value == app.current_money == 1500
    clock.value += timedelta(seconds=20)
    final = capture_frame(app, banner="Sightseer MISSION PASSED", balance=1750)
    second_cooldown = app.cooldown_tracker.get_cooldown("sightseer")
    assert final.game_state == GameState.MISSION_COMPLETE
    capture_frame(app, banner="MISSION PASSED Sightseer", balance=1750)
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [first, second]
    success = outcome == "complete"
    assert completed == ([first, second] if success else [second])
    assert mission_stats(app) == (2, 1 + int(success), int(not success), 0)
    assert [(row["name"], row["success"], row["duration_seconds"], row["earnings"]) for row in stored(app)["activities"]] == [
        ("Headhunter", success, 120, 250 if success else 0),
        ("Sightseer", True, 30, 500),
    ]
    assert app.cooldown_tracker.get_cooldown("headhunter") == first_cooldown
    assert second_cooldown is not None
    assert app.cooldown_tracker.get_cooldown("sightseer").started_at == second_cooldown.started_at
    assert money_changes == [250, 250, 250]
    assert app.session_earnings == app.session_stats.total_earnings == 750
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [
        (250, "Headhunter" if success else ""), (250, ""), (250, "Sightseer"),
    ]


@pytest.mark.parametrize("crops,expected_outcome", [
    ({"banner": "Headhunter MISSION PASSED if you eliminate the targets"}, None),
    ({"banner": "MISSION PASSED Mystery Job"}, None),
    ({"banner": "MISSION PASSED Headhunter Sightseer"}, None),
    ({"mission": "MISSION", "banner": "PASSED Headhunter"}, None),
    ({"mission": "MISSION PASSED Hostile", "banner": "Takeover"}, None),
    ({"banner": "MISSION PASSED Headhunter MISSION FAILED"}, "conflicting"),
    ({"mission": "JOB COMPLETE Headhunter", "banner": "Headhunter MISSION FAILED"}, "conflicting"),
])
def test_uncertain_result_never_completes_current_activity_or_rewrites_money(app, crops, expected_outcome):
    capture_frame(app, banner="Headhunter")
    current = app._activity_tracker.current_activity
    observed = capture_frame(app, balance=1250, **crops)
    assert observed.mission.outcome == expected_outcome
    assert observed.game_state not in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
    assert app._activity_tracker.current_activity is current
    assert app._activity_tracker.completed_activities == []
    assert stored(app)["activities"] == []
    assert mission_stats(app) == (0, 0, 0, 0)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert observed.money_change == 250 and app.current_money == 1250
    assert app.session_earnings == 250


@pytest.mark.parametrize("label,outcome", LABELS)
@pytest.mark.parametrize("title_first", [False, True])
def test_blow_up_result_retains_identity_to_reject_another_active_mission(app, label, outcome, title_first):
    capture_frame(app, banner="Headhunter")
    current = app._activity_tracker.current_activity
    text = f"Blow Up {label}" if title_first else f"{label} Blow Up"
    observed = capture_frame(app, banner=text, balance=1250)
    assert observed.mission.outcome == outcome
    assert observed.mission.mission_name == "Blow Up"
    assert observed.mission.identity_status == "known_name"
    assert observed.game_state == GameState.UNKNOWN and observed.state_confidence == 0.0
    assert "conflict" in observed.state_reason.lower()
    assert app._activity_tracker.current_activity is current
    assert stored(app)["activities"] == []
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert [(row["amount"], row["source"]) for row in stored(app)["earnings"]] == [(250, "")]
