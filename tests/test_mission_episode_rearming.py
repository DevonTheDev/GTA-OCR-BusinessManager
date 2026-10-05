"""Result episode handoff through actual capture, trackers and disposable SQLite.

Only capture/OCR IO and the clock are synthetic; these are observation-policy
regressions, not measurements of gameplay or native Windows OCR accuracy.
"""
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult
from src.detection.parsers.mission_parser import MissionParser
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app
from tests.test_mission_result_accounting import clock as clock


def frame(app, mission="", center="", banner="", *, brightness=70, template=None):
    """Run actual capture cycle with one synthetic screenshot and three OCR crops."""
    images = [object(), object(), object()]
    words = dict(zip(map(id, images), (mission, center, banner)))
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(
            text=words[id(image)], confidence=1.0, words=[]),
    )
    templates = SimpleNamespace(
        match_any=lambda _image, names: template if "mission_banner" in names else None)
    if app._state_detector is None:
        app._state_detector = StateDetector(templates, app._ocr)
    else:
        app._state_detector._ocr = app._ocr
        app._state_detector._templates = templates
    app._state_machine = app._state_machine or GameStateMachine()
    app._perf_monitor = app._perf_monitor or PerformanceMonitor()
    app._capture = SimpleNamespace(
        regions=SimpleNamespace(full_screen=0, money_display=1, mission_text=2,
                                center_prompt=3, timer_bottom_right=4, mission_banner=5),
        capture_multiple_regions=lambda _regions: [
            np.full((120, 200, 3), brightness, dtype=np.uint8),
            None, images[0], images[1], None, images[2]],
        close=lambda: None,
    )
    return app._do_capture_cycle()


def rows(app):
    return app._repository.export_session_data(app._data.db_session_id)["activities"]


def finish_first(app, clock, outcome="MISSION PASSED", named=True):
    frame(app, "Headhunter")
    clock.value += timedelta(seconds=120)
    result = frame(app, banner=("Headhunter\n" if named else "") + outcome)
    assert len(rows(app)) == 1
    assert app._activity_tracker.current_activity is None
    return result


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("source", ["mission", "center", "banner"])
def test_title_only_cannot_rearm_consumed_result(app, clock, outcome, named, source):
    finish_first(app, clock, outcome, named)
    clock.value += timedelta(milliseconds=500)
    frame(app, **{source: "Headhunter"})
    clock.value += timedelta(milliseconds=500)
    frame(app, banner=("Headhunter\n" if named else "") + outcome)
    observed = [(r["name"], r["success"], r["duration_seconds"]) for r in rows(app)]
    assert len(observed) == 1, observed
    assert app.session_stats.activities_completed == 1
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize("next_title", ["Sightseer", "Rooftop Rumble", "Cayo Perico finale"])
def test_stale_title_must_not_capture_next_activity(app, clock, next_title):
    finish_first(app, clock)
    frame(app, banner="Headhunter")
    frame(app, banner=next_title)
    current = app._activity_tracker.current_activity
    assert current is not None
    assert current.name == next_title
    assert len(rows(app)) == 1


@pytest.mark.parametrize("boundary", ["blank", "weak_visual", "noise", "ambiguous", "conflicting", "loading"])
def test_unreliable_boundary_does_not_rearm_same_title(app, clock, boundary):
    finish_first(app, clock)
    inputs = {
        "blank": {},
        "weak_visual": {"brightness": 100},
        "noise": {"mission": "unrecognized text"},
        "ambiguous": {"mission": "Headhunter Sightseer"},
        "conflicting": {"banner": "MISSION PASSED\nMISSION FAILED"},
        "loading": {"brightness": 0},
    }
    frame(app, **inputs[boundary])
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity is None


def test_contract_generic_template_can_start_unknown_activity_outside_identity_fence(app, clock):
    finish_first(app, clock)
    match = SimpleNamespace(matched=True, template_name="mission_banner", confidence=0.95)
    frame(app, template=match)
    # Existing behavior is deliberately characterized, not demanded as a change:
    # no explicit identity is available to establish a same-identity replay.
    assert app._activity_tracker.current_activity.name == "Unknown Mission"


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
@pytest.mark.parametrize("named", [False, True])
def test_distinct_identity_starts_without_a_timeout(app, clock, outcome, named):
    finish_first(app, clock, outcome, named)
    frame(app, banner="Sightseer")
    assert app._activity_tracker.current_activity.name == "Sightseer"
    frame(app, banner="Sightseer\nMISSION PASSED")
    assert [r["name"] for r in rows(app)] == ["Headhunter", "Sightseer"]


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("objective_source", ["mission", "center"])
def test_same_name_retry_with_fresh_objective(app, clock, outcome, named, objective_source):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    finish_first(app, clock, outcome, named)
    app._data.current_money = 100
    observed = frame(app, banner="Headhunter", **{objective_source: "Eliminate the targets"})
    assert observed.mission.objective == "Eliminate the targets"
    second = app._activity_tracker.current_activity
    assert second is not None and second.name == "Headhunter"
    started = app._data.mission_start_time
    frame(app, banner="Headhunter", **{objective_source: "Eliminate the targets"})
    assert app._activity_tracker.current_activity is second
    assert app._data.mission_start_time == started
    clock.value += timedelta(seconds=60)
    app._data.current_money = 200
    frame(app, banner="Headhunter\nMISSION PASSED")
    assert [(r["name"], r["duration_seconds"]) for r in rows(app)] == [
        ("Headhunter", 120), ("Headhunter", 60)]
    assert rows(app)[1]["earnings"] == 100
    assert app.session_stats.activities_completed == 2
    assert len(callbacks) == (2 if outcome == "MISSION PASSED" else 1)


def test_unknown_objective_then_same_name_refines_new_activity(app, clock):
    finish_first(app, clock)
    observed = frame(app, mission="Eliminate the targets")
    assert observed.mission.identity_status == "unknown"
    second = app._activity_tracker.current_activity
    assert second is not None
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity is second
    assert second.name == "Headhunter"


@pytest.mark.parametrize("boundary", ["blank", "weak_visual", "noise", "ambiguous", "conflicting"])
def test_uncertain_frames_alone_do_not_start(app, clock, boundary):
    finish_first(app, clock)
    inputs = {
        "blank": {}, "weak_visual": {"brightness": 100},
        "noise": {"mission": "unrecognized text"},
        "ambiguous": {"mission": "Headhunter Sightseer"},
        "conflicting": {"banner": "MISSION PASSED\nMISSION FAILED"},
    }
    frame(app, **inputs[boundary])
    assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == 1


def test_stats_reset_keeps_active_mission_and_db_session(app, clock):
    frame(app, banner="Headhunter")
    current, started = app._activity_tracker.current_activity, app._data.mission_start_time
    session = app._data.db_session_id
    app.reset_session()
    assert app._activity_tracker.current_activity is current
    assert app._data.mission_start_time == started
    assert app._data.db_session_id == session


def test_stats_reset_is_not_a_gameplay_boundary(app, clock):
    finish_first(app, clock)
    session = app._data.db_session_id
    app.reset_session()
    frame(app, banner="Headhunter")
    assert app._data.db_session_id == session
    assert app._activity_tracker.current_activity is None
    assert app.session_stats.activities_completed == 0


def test_start_creates_fresh_tracking_and_db_session(app, clock, monkeypatch):
    finish_first(app, clock)
    previous = app._data.db_session_id
    repo = app._repository
    character = repo.get_or_create_character("Test Player")
    release = Event()

    def initialize():
        app._repository = repo
        app._data.character_id = character.id
        app._data.db_session_id = repo.start_session(character).id
        app._session_tracker.start_session()
        app._state_machine = GameStateMachine()
        app._state_detector = None

    monkeypatch.setattr(app, "_initialize_components", initialize)
    monkeypatch.setattr(app, "_capture_loop", lambda: release.wait(5))
    try:
        assert app.start()
        assert app._data.db_session_id != previous
        frame(app, banner="Headhunter")
        assert app._activity_tracker.current_activity.name == "Headhunter"
        frame(app, banner="MISSION PASSED")
        assert len(rows(app)) == 1
        assert len(repo.export_session_data(previous)["activities"]) == 1
    finally:
        release.set()
        app.stop()


def test_contract_parser_does_not_prove_title_freshness():
    parser = MissionParser()
    title_before = parser.parse_regions(("Headhunter",))
    parser.parse_regions(("Headhunter", "MISSION PASSED"))
    title_after = parser.parse_regions(("Headhunter",))
    assert title_before == title_after
    assert title_after.is_active and not title_after.objective


def test_contract_objective_can_remain_in_result_or_explanatory_text():
    parser = MissionParser()
    result = parser.parse_regions(("Eliminate the targets", "Headhunter\nMISSION PASSED"))
    missed = parser.parse_regions(("Eliminate the targets", "Headhunter"))
    explanation = parser.parse("Headhunter\nIf you eliminate the targets, you earn money")
    assert result.objective == missed.objective == "Eliminate the targets"
    assert result.outcome == "complete" and missed.outcome is None
    assert explanation.objective == "eliminate the targets, you earn money"


@pytest.mark.parametrize("result_layout", ["separate", "same_crop", "two_objectives"])
def test_result_objective_replay_is_not_fresh_start(app, clock, result_layout):
    frame(app, banner="Headhunter")
    clock.value += timedelta(seconds=120)
    inputs = {
        "separate": {"mission": "Eliminate the targets", "banner": "Headhunter\nMISSION PASSED"},
        "same_crop": {"banner": "Headhunter\nEliminate the targets\nMISSION PASSED"},
        "two_objectives": {"mission": "Go to the location", "center": "Eliminate the targets",
                           "banner": "Headhunter\nMISSION PASSED"},
    }
    frame(app, **inputs[result_layout])
    assert len(rows(app)) == 1
    # The second objective may be the only surviving objective after dropout;
    # storing only MissionReading.objective (the first) cannot reject that replay.
    frame(app, mission="Eliminate the targets", banner="Headhunter")
    frame(app, banner="Headhunter\nMISSION PASSED")
    assert len(rows(app)) == 1


def test_contract_objective_string_change_can_be_only_result_text_loss():
    parser = MissionParser()
    before = parser.parse("Headhunter\nEliminate the targets\nMISSION PASSED")
    after = parser.parse("Headhunter\nEliminate the targets")
    assert before.objective == "Eliminate the targets MISSION PASSED"
    assert after.objective == "Eliminate the targets"
    assert before.objective != after.objective


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
def test_initial_named_result_must_not_create_activity_after_dropout(app, clock, outcome):
    frame(app, banner="Headhunter\n" + outcome)
    assert rows(app) == []
    assert app._activity_tracker.current_activity is None
    frame(app, banner="Headhunter")
    frame(app, banner="Headhunter\n" + outcome)
    assert rows(app) == []
    assert app.session_stats.activities_completed == 0


@pytest.mark.parametrize("result", ["MISSION PASSED", "Headhunter Sightseer\nMISSION PASSED"])
def test_initial_unresolved_result_does_not_assign_ownership(app, clock, result):
    observed = frame(app, banner=result)
    assert observed.mission.identity_status in ("unknown", "ambiguous")
    assert rows(app) == []
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


@pytest.mark.parametrize("old,new", [
    ("Headhunter", "Sightseer"),
    ("VIP work", "Security contract"),
    ("Cayo Perico prep", "Cayo Perico finale"),
])
def test_distinct_identity_axes_rearm(app, clock, old, new):
    frame(app, banner=old)
    frame(app, banner=old + "\nMISSION PASSED")
    frame(app, banner=new)
    assert app._activity_tracker.current_activity.name == new


@pytest.mark.parametrize("old,new", [
    ("Headhunter", "VIP work"),
    ("VIP work", "Headhunter"),
    ("Cayo Perico prep", "Cayo Perico"),
    ("Cayo Perico", "Cayo Perico prep"),
])
def test_compatible_identity_refinement_is_not_a_new_episode(app, clock, old, new):
    frame(app, banner=old)
    frame(app, banner=old + "\nMISSION PASSED")
    frame(app, banner=new)
    assert app._activity_tracker.current_activity is None


def test_contract_named_and_unnamed_result_consume_same_owned_identity(app, clock):
    finish_first(app, clock, named=False)
    assert rows(app)[0]["name"] == "Headhunter"
    assert app._data.current_mission is None
    assert app._data.mission_identity_status == "unknown"
    assert app._activity_tracker.completed_activities[-1].name == "Headhunter"


def test_explicit_starting_state_is_only_a_direct_injection(app, clock):
    finish_first(app, clock)
    app._process_state(StateDetectionResult(
        state=GameState.MISSION_STARTING, confidence=1.0, reason="synthetic explicit boundary"),
        CaptureResult())
    assert app._activity_tracker.current_activity is None
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


@pytest.mark.parametrize("old", [
    "Eliminate the targets | MISSION PASSED",
    "Eliminate the targets\nMISSION\nPASSED",
    "Headhunter\nEliminate\nthe targets\nMISSION PASSED",
    "Eliminate the targets | Headhunter",
    "Go to the location\nEliminate the targets",
    "Eliminate the targets\nGo to the location",
])
def test_wrapping_labels_and_other_objective_loss_do_not_rearm(app, old):
    frame(app, banner="Headhunter")
    frame(app, mission=old, banner="Headhunter\nMISSION PASSED")
    observed = frame(app, center="  ELIMINATE   THE TARGETS. ", banner="Headhunter")
    assert observed.game_state == GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert "result" in observed.state_reason.lower()
    assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == 1


@pytest.mark.parametrize("text", [
    "If you eliminate the targets, you earn money",
    "You must eliminate the targets",
    "Headhunter\nIf you eliminate the targets, you earn money",
    "Headhunter", "Hunt the Beast", "Go to", "Eliminate the",
])
def test_explanation_title_or_incomplete_command_cannot_release_fence(app, clock, text):
    finish_first(app, clock)
    frame(app, mission=text, banner="Headhunter")
    assert app._activity_tracker.current_activity is None


def test_command_fragments_from_separate_crops_cannot_release_fence(app, clock):
    finish_first(app, clock)
    frame(app, mission="Go", center="to the location", banner="Headhunter")
    assert app._activity_tracker.current_activity is None


def test_latest_nonempty_active_objectives_survive_title_only_frame_and_result(app):
    frame(app, mission="Go to the location", banner="Headhunter")
    frame(app, mission="Eliminate the targets", center="Collect the package", banner="Headhunter")
    frame(app, banner="Headhunter")
    frame(app, banner="Headhunter\nMISSION PASSED")
    frame(app, mission="Collect the package", center="Eliminate the targets", banner="Headhunter")
    assert app._activity_tracker.current_activity is None
    # The obsolete active objective is intentionally outside the latest baseline.
    frame(app, mission="Go to the location", banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


def test_repeated_results_accumulate_evidence_without_replacing_owned_identity(app):
    frame(app, banner="Headhunter")
    frame(app, mission="Go to the location", banner="MISSION PASSED")
    frame(app, mission="Eliminate the targets", banner="VIP Work\nMISSION PASSED")
    frame(app, mission="Collect the package", banner="MISSION PASSED")
    for text in ("Go to the location", "Eliminate the targets", "Collect the package"):
        frame(app, mission=text, banner="Headhunter")
        assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == 1


def test_result_refinement_is_fenced_even_when_completed_activity_is_mutated(app):
    def mutate(activity):
        activity.name = "Sightseer"
    app.on_mission_complete(mutate)
    frame(app, banner="VIP Work")
    frame(app, banner="Headhunter\nMISSION PASSED")
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity is None
    assert rows(app)[0]["name"] == "Headhunter"
    frame(app, banner="Sightseer")
    assert app._activity_tracker.current_activity.name == "Sightseer"


@pytest.mark.parametrize("state", [GameState.MISSION_ACTIVE, GameState.SELLING,
                                    GameState.HEIST_PREP, GameState.HEIST_FINALE])
def test_direct_same_identity_reactivation_preserves_raw_observation(app, state):
    title = "Cayo Perico" if state in (GameState.HEIST_PREP, GameState.HEIST_FINALE) else "Headhunter"
    app._process_state(StateDetectionResult(state, 1.0, "direct", mission_text=title), CaptureResult())
    app._process_state(StateDetectionResult(GameState.MISSION_COMPLETE, 1.0, "direct"), CaptureResult())
    capture = CaptureResult()
    original = StateDetectionResult(state, 1.0, "direct", mission_text=title, objective_text="old raw", banner_text=title)
    app._process_state(original, capture)
    assert original.state == state and original.confidence == 1.0
    assert capture.game_state == GameState.UNKNOWN and capture.state_confidence == 0.0
    assert capture.mission_text == title and capture.objective_text == "old raw" and capture.banner_text == title
    assert capture.mission.raw_text == title + "\nold raw\n" + title
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize("confidence", [0.0, 0.6, float("nan"), float("inf"), True])
def test_weak_or_invalid_explicit_start_boundary_keeps_fence(app, clock, confidence):
    finish_first(app, clock)
    app._process_state(StateDetectionResult(GameState.MISSION_STARTING, confidence, "direct"), CaptureResult())
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity is None


def test_matching_start_from_completion_listener_is_already_fenced(app):
    observed = []
    def completed(activity):
        observed.append(frame(app, banner="Headhunter"))
    app.on_mission_complete(completed)
    frame(app, banner="Headhunter")
    frame(app, banner="Headhunter\nMISSION PASSED")
    assert observed[0].game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == app.session_stats.activities_completed == 1


def test_nested_new_result_retains_its_newer_fence(app):
    def completed(activity):
        if activity.name == "Headhunter":
            frame(app, banner="Sightseer")
            frame(app, banner="Sightseer\nMISSION PASSED")
    app.on_mission_complete(completed)
    frame(app, banner="Headhunter")
    frame(app, banner="Headhunter\nMISSION PASSED")
    frame(app, banner="Sightseer")
    assert app._activity_tracker.current_activity is None
    assert [row["name"] for row in rows(app)] == ["Headhunter", "Sightseer"]
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


def test_episode_overflow_retains_old_evidence_and_refuses_fresh_objective(app):
    frame(app, banner="Headhunter")
    frame(app, banner="Headhunter\nMISSION PASSED")
    for index in range(129):
        frame(app, mission=f"Collect package {index}", banner="Headhunter\nMISSION PASSED")
    for objective in ("Collect package 0", "Collect package 128", "Eliminate the targets"):
        frame(app, mission=objective, banner="Headhunter")
        assert app._activity_tracker.current_activity is None
    frame(app, banner="Sightseer")
    assert app._activity_tracker.current_activity.name == "Sightseer"
    assert len(rows(app)) == 1


@pytest.mark.parametrize("old", [
    "Eliminate the targets\nMISSION PASSED\n$25000",
    "Eliminate the targets MISSION PASSED +$25,000",
    "Eliminate the targets\nIf you deliver them you earn money",
    "Eliminate the targets If you deliver them you earn money",
    "Go to the location\nEliminate the targets",
])
def test_payout_explanation_or_line_break_loss_does_not_rearm(app, old):
    frame(app, banner="Headhunter")
    frame(app, mission=old, banner="Headhunter\nMISSION PASSED")
    frame(app, mission="Eliminate the targets", banner="Headhunter")
    assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == 1


@pytest.mark.parametrize("title", ["Sell Mission", "Cayo Perico prep", "Cayo Perico finale"])
def test_capture_rejects_reactivation_before_state_listeners_and_money_source(app, title):
    from tests.test_terminal_mission_identity_ownership import capture_frame, stored

    transitions, completions = [], []
    app.on_state_change(lambda old, new: transitions.append((old, new)))
    app.on_mission_complete(completions.append)
    capture_frame(app, banner=title)
    capture_frame(app, banner=title + "\nMISSION PASSED", balance=1100)
    app._state_machine.transition_to(GameState.IDLE, trigger="synthetic neutral observation")
    before = list(transitions)
    observed = capture_frame(app, banner=title, balance=1200)
    assert observed.game_state == GameState.UNKNOWN and observed.state_confidence == 0.0
    assert observed.mission.raw_text == observed.banner_text == title
    assert observed.mission.identity_status == "type_only"
    assert observed.activity_name == ""
    assert app._state_machine.state == GameState.IDLE
    assert transitions == before
    assert observed.money_change == 100 and app.current_money == 1200
    assert stored(app)["earnings"][-1]["source"] == ""
    assert app._activity_tracker.current_activity is None
    assert len(stored(app)["activities"]) == len(completions) == 1
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize("title", ["VIP Work", "Cayo Perico prep", "Heist finale"])
def test_initial_category_result_seeds_no_completion_and_blocks_same_category(app, title):
    frame(app, banner=title + "\nMISSION PASSED")
    frame(app, banner=title)
    assert app._activity_tracker.current_activity is None
    assert rows(app) == [] and app.session_stats.activities_completed == 0


def test_pause_resume_and_elapsed_time_do_not_release_identity(app, clock):
    from src.app import AppState

    finish_first(app, clock)
    app._state = AppState.RUNNING
    app.pause()
    assert app._state == AppState.PAUSED
    clock.value += timedelta(days=7)
    app.resume()
    assert app._state == AppState.RUNNING
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity is None


def test_overflow_keeps_fence_across_stats_reset_but_explicit_start_clears_it(app):
    frame(app, banner="Headhunter")
    frame(app, mission="\n".join(f"Collect package {index}" for index in range(129)),
          banner="Headhunter\nMISSION PASSED")
    assert app._data.terminal_mission_episode.objectives.overflow
    assert len(app._data.terminal_mission_episode.objectives.entries) == 128
    app.reset_session()
    frame(app, mission="Eliminate the targets", banner="Headhunter")
    assert app._activity_tracker.current_activity is None
    app._process_state(StateDetectionResult(GameState.MISSION_STARTING, 1.0, "explicit"), CaptureResult())
    frame(app, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


@pytest.mark.parametrize("title,objective", [
    ("Auto Shop", "Deliver the customer vehicle"),
    ("Auto Shop", "Go to the auto shop"),
    ("Cayo Perico", "Go to Cayo Perico"),
])
def test_known_category_used_as_object_is_valid_fresh_objective(app, title, objective):
    frame(app, banner=title + "\nMISSION PASSED")
    frame(app, mission=objective, banner=title)
    assert app._activity_tracker.current_activity is not None
    assert rows(app) == []
    frame(app, banner=title + "\nMISSION PASSED")
    assert len(rows(app)) == 1


def test_ambiguous_identity_with_strong_generic_template_remains_unresolved_start(app, clock):
    finish_first(app, clock)
    match = SimpleNamespace(matched=True, template_name="mission_banner", confidence=0.95)
    result = frame(app, mission="Headhunter Sightseer", template=match)
    assert result.mission.identity_status == "ambiguous"
    assert app._activity_tracker.current_activity is not None
    assert app._data.mission_identity_status == "ambiguous"
    assert app._data.terminal_mission_episode is None


@pytest.mark.parametrize("fragment,remainder", [("Take out", "the targets"), ("Wait for", "the signal")])
def test_incomplete_phrasal_objectives_do_not_release_from_separate_crops(app, clock, fragment, remainder):
    finish_first(app, clock)
    frame(app, mission=fragment, center=remainder, banner="Headhunter")
    assert app._activity_tracker.current_activity is None
    frame(app, mission=fragment + " " + remainder, banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"


@pytest.mark.parametrize("footer", ["JP +15\nRP +2500", "You earned $25000", "+$25,000 TOTAL"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_result_label_loss_with_footer_preserved_cannot_rearm(app, footer, position):
    command = "Eliminate the targets"
    result = (command + "\nMISSION PASSED\n" + footer if position == "before"
              else "MISSION PASSED\n" + command + "\n" + footer)
    frame(app, banner="Headhunter")
    frame(app, mission=result, banner="Headhunter\nMISSION PASSED")
    for objective in (command + "\n" + footer, command, command + "\n+$99 TOTAL"):
        frame(app, mission=objective, banner="Headhunter")
        assert app._activity_tracker.current_activity is None
    assert len(rows(app)) == 1
    frame(app, mission="Go to the airport", banner="Headhunter")
    assert app._activity_tracker.current_activity.name == "Headhunter"
