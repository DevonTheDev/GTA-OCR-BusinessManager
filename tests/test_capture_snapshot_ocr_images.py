"""Opt-in changing-image capture integration; these are not gameplay screenshots.

Run with GTA_RUN_OCR_TESTS=1 and the optional tooling documented by
test_mission_ocr_images. Generated BGR frames replace only the native display,
and that module's Tesseract adapter replaces the Windows-only OCR backend.
The production batch capture, preprocessing, detector, parser, tracker and
disposable SQLite are exercised. This does not establish gameplay incidence,
Windows OCR accuracy, native capture timing, or an OS-level atomic screenshot.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for synthetic-image capture integration", allow_module_level=True)

# The imported diagnostic module checks installed optional dependencies first.
from tests.test_mission_ocr_images import TesseractDiagnostic, synthetic_frame

from types import SimpleNamespace

import numpy as np

from src.capture import screen_capture
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


RESOLUTION = (1920, 1080)
OFFSET = (-1920, 75)


class ChangingScreen:
    """Return pixels from complete images, switching between native grab calls."""

    def __init__(self, before, after, switch_after):
        self.frames = (before, after)
        self.switch_after = switch_after
        self.calls = []

    def grab(self, monitor):
        frame = self.frames[int(len(self.calls) >= self.switch_after)]
        self.calls.append(monitor.copy())
        left, top = monitor["left"] - OFFSET[0], monitor["top"] - OFFSET[1]
        right, bottom = left + monitor["width"], top + monitor["height"]
        assert 0 <= left < right <= RESOLUTION[0]
        assert 0 <= top < bottom <= RESOLUTION[1]
        bgr = frame[top:bottom, left:right]
        alpha = np.full((*bgr.shape[:2], 1), 255, dtype=np.uint8)
        return np.concatenate((bgr, alpha), axis=2)


def configure_capture(app, monkeypatch):
    """Keep the real capture API and application cycle; replace native boundaries."""
    monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _index: SimpleNamespace(
        width=RESOLUTION[0], height=RESOLUTION[1], offset=OFFSET, scale_factor=1.0,
    ))
    app._capture = screen_capture.ScreenCapture()
    app._ocr = TesseractDiagnostic()
    app._state_detector = StateDetector(ocr_engine=app._ocr)
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()


def show_images(app, monkeypatch, before, after, mode, switch_after):
    if mode == "stable-before":
        after = before
    elif mode == "stable-after":
        before = after
    else:
        assert mode == "changing"
    screen = ChangingScreen(before, after, switch_after)
    monkeypatch.setattr(app._capture, "_ensure_mss", lambda: screen)
    return screen


def activity_rows(app):
    return app._repository.export_session_data(app._data.db_session_id)["activities"]


@pytest.mark.parametrize("mode", ("stable-before", "stable-after", "changing"))
def test_frame_switch_cannot_combine_separate_mission_titles(app, monkeypatch, mode):
    configure_capture(app, monkeypatch)
    before = synthetic_frame(RESOLUTION, center_text="Hostile Takeover")
    after = synthetic_frame(RESOLUTION, center_text="Headhunter")
    # The same physical text falls inside center_prompt and mission_banner.
    # Old six-grab batches switch after center_prompt and read two different names.
    show_images(app, monkeypatch, before, after, mode, switch_after=4)

    observed = app._do_capture_cycle()

    expected = "Headhunter" if mode == "stable-after" else "Hostile Takeover"
    assert observed.mission.identity_status == "known_name"
    assert observed.mission.candidates == (expected,)
    assert observed.mission.mission_name == expected
    assert observed.objective_text == observed.banner_text == expected
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert observed.activity_name == expected
    assert observed.activity_type == ActivityType.VIP_WORK
    assert app._activity_tracker.current_activity.name == expected
    assert activity_rows(app) == []


@pytest.mark.parametrize("mode", ("stable-before", "stable-after", "changing"))
def test_frame_switch_cannot_create_name_absent_from_both_images(app, monkeypatch, mode):
    configure_capture(app, monkeypatch)
    before = synthetic_frame(RESOLUTION, mission_text="Executive", center_text="Wait here.")
    after = synthetic_frame(RESOLUTION, mission_text="Go to the location", center_text="Search")
    # Neither frame has Executive Search. Taking the old top crop and the new
    # center/banner makes the real parser select that catalog name incorrectly.
    show_images(app, monkeypatch, before, after, mode, switch_after=3)

    observed = app._do_capture_cycle()

    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_name == ""
    assert observed.mission.candidates == ()
    expected_top, expected_center = (
        ("Go to the location", "Search") if mode == "stable-after"
        else ("Executive", "Wait here.")
    )
    assert observed.mission_text == expected_top
    assert observed.objective_text == observed.banner_text == expected_center
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert observed.activity_type == ActivityType.UNKNOWN
    assert app._activity_tracker.current_activity.name == expected_top
    assert activity_rows(app) == []


@pytest.mark.parametrize("mode", ("stable-before", "stable-after", "changing"))
def test_frame_switch_cannot_hide_result_of_existing_activity(app, monkeypatch, mode):
    configure_capture(app, monkeypatch)
    seed = synthetic_frame(RESOLUTION, center_text="Hostile Takeover")
    show_images(app, monkeypatch, seed, seed, "stable-before", switch_after=4)
    started = app._do_capture_cycle()
    assert started.activity_name == "Hostile Takeover"
    current = app._activity_tracker.current_activity
    before = synthetic_frame(RESOLUTION, center_text="MISSION PASSED")
    after = synthetic_frame(RESOLUTION, center_text="MISSION FAILED")
    show_images(app, monkeypatch, before, after, mode, switch_after=4)

    observed = app._do_capture_cycle()

    success = mode != "stable-after"
    expected_state = GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED
    expected_text = "MISSION PASSED" if success else "MISSION FAILED"
    assert observed.game_state == expected_state
    assert observed.mission.outcome == ("complete" if success else "failed")
    assert observed.objective_text == observed.banner_text == expected_text
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == [current]
    (row,) = activity_rows(app)
    assert (row["name"], row["type"], row["success"]) == ("Hostile Takeover", "VIP_WORK", success)
    cooldown = app.cooldown_tracker.get_cooldown("hostile_takeover")
    assert (cooldown is not None) is success

    # Repeating the same complete observation must not account for it again.
    repeated = before if success else after
    show_images(app, monkeypatch, repeated, repeated, "stable-before", switch_after=4)
    app._do_capture_cycle()
    assert activity_rows(app) == [row]
    assert app._activity_tracker.completed_activities == [current]
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") == cooldown
