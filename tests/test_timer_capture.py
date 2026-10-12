"""Synthetic timer text through the real capture cycle; no OCR or native capture."""

from types import SimpleNamespace

import numpy as np

from src.capture.regions import ScreenRegions
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


def timer_capture(app):
    regions = ScreenRegions()
    texts = {
        regions.mission_text: "Headhunter",
        regions.money_display: "$100,000",
        regions.timer_bottom_right: "20:00",
    }
    images = {region: object() for region in texts}
    words = {id(images[region]): text for region, text in texts.items()}
    images[regions.full_screen] = np.full((120, 200, 3), 70, dtype=np.uint8)
    app._capture = SimpleNamespace(
        regions=regions,
        capture_multiple_regions=lambda requested: {
            index: images.get(region) for index, region in enumerate(requested)
        },
    )
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=words[id(image)]),
    )
    app._state_detector = StateDetector(
        ocr_engine=app._ocr,
        template_matcher=SimpleNamespace(match_any=lambda *args: None),
    )
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()

    def observe(text):
        words[id(images[regions.timer_bottom_right])] = text
        return app._do_capture_cycle()

    return observe


def test_twenty_minute_timer_survives_real_capture_cycle(app):
    observe = timer_capture(app)

    result = observe("20:00")

    assert result.timer is not None
    assert result.timer.has_value
    assert result.timer.total_seconds == 1200
    assert result.timer.formatted == "20:00"
    assert result.timer.raw_text == "20:00"
    assert app._timer_parser.get_last_valid() is result.timer
    assert result.game_state == GameState.MISSION_ACTIVE
    assert result.activity_name == "Headhunter"
    assert result.activity_type == ActivityType.VIP_WORK
    assert result.activity_identity_status == "known_name"
    assert result.money.display_value == app.current_money == 100_000
    assert result.money_change == app.session_earnings == 0
    assert app._activity_tracker.current_activity.name == "Headhunter"
    assert app._repository.export_session_data(app._data.db_session_id)["activities"] == []


def test_positive_invalid_zero_timer_sequence_preserves_activity_and_money(app):
    observe = timer_capture(app)
    completions = []
    app.on_mission_complete(completions.append)
    positive = observe("20:00")
    owner = app._activity_tracker.current_activity
    owner_started = owner.started_at
    mission_started = app._data.mission_start_time
    baseline_money = app._data.mission_start_money
    assert positive.timer.has_value and positive.timer.total_seconds == 1200
    assert app._timer_parser.get_last_valid() is positive.timer

    for text in ("60:00:60", "0"):
        result = observe(text)
        assert result.game_state is GameState.MISSION_ACTIVE
        assert result.activity_name == owner.name == "Headhunter"
        assert result.activity_type is ActivityType.VIP_WORK
        assert result.activity_identity_status == "known_name"
        assert app._activity_tracker.current_activity is owner
        assert owner.started_at == owner_started
        assert app._data.mission_start_time == mission_started
        assert app._data.mission_start_money == baseline_money
        assert result.money.display_value == app.current_money == 100_000
        assert result.money_change == app.session_earnings == 0
        assert app.session_stats.total_earnings == app.session_stats.activities_completed == 0
        assert result.mission.outcome is None
        assert completions == app._activity_tracker.completed_activities == []
        stored = app._repository.export_session_data(app._data.db_session_id)
        assert stored["activities"] == stored["earnings"] == []
        if text == "60:00:60":
            assert result.timer is None
            assert app._timer_parser.get_last_valid() is positive.timer
        else:
            assert result.timer is not None and result.timer.has_value
            assert result.timer.total_seconds == 0 and result.timer.formatted == "0:00"
            assert result.timer.raw_text == "0"
            assert app._timer_parser.get_last_valid() is result.timer
            assert result.timer is not positive.timer
