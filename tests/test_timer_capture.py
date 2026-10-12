"""Synthetic timer text through the real capture cycle; no OCR or native capture."""

from types import SimpleNamespace

import numpy as np

from src.capture.regions import ScreenRegions
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


def test_twenty_minute_timer_survives_real_capture_cycle(app):
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

    result = app._do_capture_cycle()

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
