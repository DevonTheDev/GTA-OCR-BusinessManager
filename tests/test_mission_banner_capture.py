"""Configured banner crop through real detection, tracking and disposable SQLite.

Only screen/OCR boundaries use synthetic text. These are not native Windows OCR
or gameplay accuracy tests; image-to-text coverage is separately opt-in.
"""

from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult
from src.capture.regions import ScreenRegions
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetector, StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app
from tests.test_capture_timing import capture_clock as capture_clock
from tests.test_mission_result_accounting import clock as clock


REGIONS = ScreenRegions()
BATCH = [REGIONS.full_screen, REGIONS.money_display, REGIONS.mission_text,
         REGIONS.center_prompt, REGIONS.timer_bottom_right, REGIONS.mission_banner,
         REGIONS.bottom_objective]


def banner_frame(app, top="", center="", banner="", template=None):
    """Replace capture/OCR only; keep the actual app's detector and trackers."""
    images, words = {}, {}
    for region, text in ((REGIONS.mission_text, top), (REGIONS.center_prompt, center),
                         (REGIONS.mission_banner, banner)):
        image = None if text is None else object()
        images[region] = image
        if image is not None:
            words[id(image)] = text
    images[REGIONS.full_screen] = np.full((120, 200, 3), 70, dtype=np.uint8)
    requested = []

    def capture(regions):
        requested.append(list(regions))
        return {i: images.get(region) for i, region in enumerate(regions)}

    app._capture = SimpleNamespace(regions=REGIONS, capture_multiple_regions=capture)
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=words[id(image)]),
    )
    app._state_machine = app._state_machine or GameStateMachine()
    app._perf_monitor = app._perf_monitor or PerformanceMonitor()
    if app._state_detector is None:
        app._state_detector = StateDetector(ocr_engine=app._ocr)
    app._state_detector._ocr = app._ocr
    app._state_detector._templates = SimpleNamespace(
        match_any=lambda _image, names: template if "mission_banner" in names else None,
    )
    result = app._do_capture_cycle()
    return result, requested


def activity_rows(app):
    return app._repository.export_session_data(app._data.db_session_id)["activities"]


@pytest.mark.parametrize("top,title,kind,state,phase", [
    ("", "Hostile Takeover", ActivityType.VIP_WORK, GameState.MISSION_ACTIVE, MissionType.UNKNOWN),
    ("VIP work", "Hostile Takeover", ActivityType.VIP_WORK, GameState.MISSION_ACTIVE, MissionType.UNKNOWN),
    ("Contact mission", "Rooftop Rumble", ActivityType.CONTACT_MISSION, GameState.MISSION_ACTIVE, MissionType.UNKNOWN),
    ("Security contract", "Recover Valuables", ActivityType.SECURITY_CONTRACT, GameState.MISSION_ACTIVE, MissionType.UNKNOWN),
    ("Casino Heist", "The Big Con\nFinale", ActivityType.HEIST_FINALE, GameState.HEIST_FINALE, MissionType.HEIST_FINALE),
    ("", "Cayo Perico\nPrep", ActivityType.HEIST_PREP, GameState.HEIST_PREP, MissionType.HEIST_PREP),
])
def test_banner_identity_reaches_real_capture_and_tracker(app, top, title, kind, state, phase):
    result, requested = banner_frame(app, top=top, center="Go to the location", banner=title)
    activity = app._activity_tracker.current_activity
    assert activity is not None
    # Cayo Perico is a catalog family rather than a named approach. Existing
    # objective display precedence remains intact for type-only evidence.
    name = "Go to the location" if title.startswith("Cayo Perico") else title.splitlines()[0]
    assert (activity.name, activity.activity_type) == (name, kind)
    assert result.game_state == state
    assert result.mission.heist_phase == phase
    assert result.mission_text == top
    assert result.objective_text == "Go to the location"
    assert result.banner_text == title
    assert requested == [BATCH]
    assert activity_rows(app) == []


def test_banner_only_detect_accepts_missing_other_crops():
    detector = StateDetector(
        ocr_engine=SimpleNamespace(is_available=True, recognize_preprocessed=lambda *a, **k:
                                  SimpleNamespace(text="Hostile Takeover")),
        template_matcher=SimpleNamespace(match_any=lambda *a: None),
    )
    result = detector.detect(np.full((120, 200, 3), 70, dtype=np.uint8),
                             mission_banner_image=object())
    assert result.state == GameState.MISSION_ACTIVE
    assert result.mission.mission_name == "Hostile Takeover"
    assert result.banner_text == "Hostile Takeover"
    assert result.mission_text == result.objective_text == ""


def test_three_crops_are_parsed_once_and_shared_with_app(app):
    class CountingParser(MissionParser):
        def __init__(self):
            super().__init__()
            self.readings = []

        def parse_regions(self, texts):
            reading = super().parse_regions(texts)
            self.readings.append(reading)
            return reading

    parser = CountingParser()
    app._state_detector = StateDetector(ocr_engine=SimpleNamespace(is_available=False))
    app._state_detector._mission_parser = parser
    # The fallback parser must stay unused when the detector provided evidence.
    app._mission_parser = parser
    result, _ = banner_frame(app, "VIP work", "Collect the briefcase", "Hostile Takeover")
    assert result.mission.mission_name == "Hostile Takeover"
    assert parser.readings == [result.mission]
    assert result.mission.raw_text == "VIP work\nCollect the briefcase\nHostile Takeover"


@pytest.mark.parametrize("top,center,banner", [
    ("Headhunter", "", "Hostile Takeover"),
    ("", "Security contract", "Hostile Takeover"),
    ("Casino Heist\nPrep", "", "The Big Con\nFinale"),
])
def test_conflicting_crop_identities_do_not_choose_a_banner_winner(app, top, center, banner):
    result, _ = banner_frame(app, top, center, banner)
    assert result.mission.identity_status == "ambiguous"
    assert app._activity_tracker.current_activity is None
    assert activity_rows(app) == []


@pytest.mark.parametrize("outcome", ["MISSION PASSED", "MISSION FAILED"])
@pytest.mark.parametrize("title", ["", "\nHostile Takeover"])
def test_result_only_banner_cannot_start_activity(app, outcome, title):
    result, _ = banner_frame(app, banner=outcome + title)
    assert result.game_state in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert activity_rows(app) == []
    assert app.session_stats.activities_completed == 0
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") is None


@pytest.mark.parametrize("outcome,success", [("MISSION PASSED", True), ("MISSION FAILED", False)])
def test_late_result_banner_refines_existing_identity_and_accounts_once(app, clock, outcome, success):
    app._data.current_money = 1000
    banner_frame(app, top="VIP work")
    current = app._activity_tracker.current_activity
    started = current.started_at
    clock.value += timedelta(seconds=90)
    app._data.current_money = 2250
    result, _ = banner_frame(app, banner=outcome + "\nHostile Takeover")
    (row,) = activity_rows(app)
    assert (row["name"], row["type"]) == ("Hostile Takeover", "VIP_WORK")
    assert row["duration_seconds"] == 90
    assert row["success"] is success
    assert row["earnings"] == (1250 if success else 0)
    assert app._activity_tracker.completed_activities == [current]
    assert current.started_at == started
    assert app._activity_tracker.current_activity is None
    assert result.banner_text == outcome + "\nHostile Takeover"
    cooldown = app.cooldown_tracker.get_cooldown("hostile_takeover")
    assert (cooldown is not None) is success
    banner_frame(app, banner=outcome + "\nHostile Takeover")
    assert activity_rows(app) == [row]
    assert app._activity_tracker.completed_activities == [current]
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") == cooldown


def test_cross_crop_conflicting_results_leave_existing_activity_open(app):
    banner_frame(app, banner="Hostile Takeover")
    current = app._activity_tracker.current_activity
    result, _ = banner_frame(app, top="MISSION PASSED", banner="MISSION FAILED")
    assert result.mission.outcome == "conflicting"
    assert result.game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity is current
    assert activity_rows(app) == []


def test_later_banner_does_not_replace_confirmed_incompatible_identity(app):
    banner_frame(app, top="Headhunter")
    current = app._activity_tracker.current_activity
    banner_frame(app, banner="Hostile Takeover")
    assert app._activity_tracker.current_activity is current
    assert current.name == "Headhunter"
    result, _ = banner_frame(app, banner="MISSION PASSED\nHostile Takeover")
    assert result.game_state == GameState.UNKNOWN and result.state_confidence == 0.0
    assert app._activity_tracker.current_activity is current
    assert activity_rows(app) == []
    assert app.cooldown_tracker.get_cooldown("headhunter") is None
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") is None
    banner_frame(app, banner="MISSION PASSED\nHeadhunter")
    (row,) = activity_rows(app)
    assert row["name"] == "Headhunter"
    assert app.cooldown_tracker.get_cooldown("headhunter") is not None
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") is None


def test_strong_template_carries_original_banner_evidence(app):
    template = SimpleNamespace(matched=True, confidence=0.99, template_name="mission_banner")
    result, _ = banner_frame(app, "VIP work", "Collect the briefcase", "Hostile Takeover", template)
    assert result.state_confidence == 0.99
    assert result.banner_text == "Hostile Takeover"
    assert result.mission_text == "VIP work" and result.objective_text == "Collect the briefcase"
    assert result.mission.mission_name == "Hostile Takeover"
    assert result.activity_name == "Hostile Takeover"


@pytest.mark.parametrize("quick,last,expected", [
    (GameState.LOADING, GameState.UNKNOWN, GameState.LOADING),
    (GameState.IDLE, GameState.MISSION_ACTIVE, GameState.MISSION_ACTIVE),
])
def test_visual_and_context_winners_keep_banner_evidence(quick, last, expected):
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=False))
    detector._context.last_state = last
    reading = MissionParser().parse("Unrecognized banner")
    ocr = StateDetectionResult(GameState.UNKNOWN, 0.0, "unrecognized", mission=reading)
    ocr.banner_text = "Unrecognized banner"
    result = detector._combine_results(
        StateDetectionResult(quick, 0.9 if quick == GameState.LOADING else 0.5, "visual"), ocr, None,
    )
    assert result.state == expected
    assert result.banner_text == "Unrecognized banner"
    assert result.mission is reading


@pytest.mark.parametrize("banner", [None, "", " \n "])
def test_missing_or_empty_banner_keeps_top_and_center_behavior(app, banner):
    result, _ = banner_frame(app, "Headhunter", "Eliminate the targets", banner)
    assert result.activity_name == "Headhunter"
    assert result.activity_type == ActivityType.VIP_WORK
    assert result.mission_text == "Headhunter" and result.objective_text == "Eliminate the targets"
    assert result.banner_text == (banner or "")


def test_app_fallback_parser_includes_banner_from_detector_without_reading(app):
    result = SimpleNamespace(state=GameState.MISSION_ACTIVE, confidence=0.9, reason="legacy detector",
                             mission_text="VIP work", objective_text="", banner_text="Hostile Takeover")
    capture = CaptureResult()
    app._process_state(result, capture)
    assert capture.mission.mission_name == "Hostile Takeover"
    assert app._activity_tracker.current_activity.name == "Hostile Takeover"
    assert app._mission_reading(SimpleNamespace(mission_text="Headhunter", objective_text="")).mission_name == "Headhunter"


@pytest.mark.parametrize("banner,kind", [
    ("VIP work", ActivityType.VIP_WORK),
    ("Cayo Perico\nPrep", ActivityType.HEIST_PREP),
])
def test_banner_only_category_keeps_readable_activity_label(app, banner, kind):
    result, _ = banner_frame(app, banner=banner)
    assert result.activity_type == kind
    assert result.activity_name == banner


@pytest.mark.parametrize("banner", ["Deliver the goods", "Deliver goods"])
@pytest.mark.parametrize("active_template", [False, True])
def test_banner_delivery_keeps_generic_category_unresolved_and_refinable(app, banner, active_template):
    template = (SimpleNamespace(matched=True, confidence=0.99, template_name="mission_banner")
                if active_template else None)
    result, _ = banner_frame(app, banner=banner, template=template)
    assert result.activity_type == ActivityType.SELL_MISSION
    assert result.activity_name == banner
    assert result.activity_identity_status == "unknown"
    current = app._activity_tracker.current_activity
    banner_frame(app, banner="Hostile Takeover")
    assert app._activity_tracker.current_activity is current
    assert (current.name, current.activity_type) == ("Hostile Takeover", ActivityType.VIP_WORK)


def test_seven_region_app_batch_preserves_order_and_waits_once(capture_clock, app, monkeypatch):
    capture, clock = capture_clock
    capture._scaler.width, capture._scaler.height = 200, 120
    monitors = []

    def grab(monitor):
        monitors.append(monitor)
        return np.full((monitor["height"], monitor["width"], 4), 70, dtype=np.uint8)

    monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(grab=grab))
    app._capture = capture
    app._ocr = SimpleNamespace(is_available=True, recognize_preprocessed=lambda *a, **k: SimpleNamespace(text=""))
    app._state_detector = StateDetector(ocr_engine=app._ocr)
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()
    app._do_capture_cycle()
    app._do_capture_cycle()
    assert monitors == [REGIONS.full_screen.to_mss_monitor(200, 120)] * 2
    assert clock.waits == [1.0]
    assert app._data.total_captures == 2
