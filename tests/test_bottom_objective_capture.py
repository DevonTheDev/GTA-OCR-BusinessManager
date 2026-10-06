"""Bottom objective integration through real capture, detector, app and SQLite.

Only the native screen source and OCR text boundary are substituted. These
checks do not validate native Windows capture/OCR, gameplay or YouTube frames.
"""

from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult
from src.capture import screen_capture
from src.capture.regions import Region, ScreenRegions
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app  # noqa: PLC0414 - pytest fixture re-export
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414

REGIONS = ScreenRegions()
BOTTOM = Region(.25, .90, .50, .10)
BATCH = [REGIONS.full_screen, REGIONS.money_display, REGIONS.mission_text,
         REGIONS.center_prompt, REGIONS.timer_bottom_right, REGIONS.mission_banner, BOTTOM, REGIONS.result_header]
CAYO_COMMAND = "Escape Cayo Perico"


class CaptureHarness:
    """Use ScreenCapture's real same-grab geometry with replaceable OCR IO."""

    def __init__(self, app, monkeypatch, resolution=(400, 240), backend=None):
        self.app = app
        self.resolution = resolution
        self.grabs = []
        self.batches = []
        self.crops = []
        self.waits = []
        self.region_calls = []
        self.ocr_calls = []
        self.detections = []
        self.texts = {}
        self.images = {}
        self.words = {}
        self.frame = np.full((resolution[1], resolution[0], 4), 70, dtype=np.uint8)
        scaler = SimpleNamespace(width=resolution[0], height=resolution[1],
                                 offset=(-200, 30), scale_factor=1.0)
        monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _index: scaler)
        capture = screen_capture.ScreenCapture()
        monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(grab=self.grab))
        monkeypatch.setattr(capture, "_wait_for_rate_limit", lambda: self.waits.append(True))
        capture_batch = capture.capture_multiple_regions
        capture_region = capture.capture_region

        def batch(regions):
            self.batches.append(list(regions))
            images = capture_batch(regions)
            self.crops.append(images)
            for index, region in enumerate(regions):
                self.remember(images[index], region)
            return images

        def region(region, wait_for_rate=True):
            self.region_calls.append((region, wait_for_rate))
            image = capture_region(region, wait_for_rate=wait_for_rate)
            self.remember(image, region)
            return image

        monkeypatch.setattr(capture, "capture_multiple_regions", batch)
        monkeypatch.setattr(capture, "capture_region", region)
        self.capture = capture
        app._capture = capture
        app._ocr = backend or SimpleNamespace(is_available=True,
                                              recognize_preprocessed=self.recognize)
        detector = StateDetector(ocr_engine=app._ocr)
        detector._templates = SimpleNamespace(match_any=lambda *_args: None)
        detect = detector.detect

        def recorded_detect(*args, **kwargs):
            result = detect(*args, **kwargs)
            self.detections.append(result)
            return result

        monkeypatch.setattr(detector, "detect", recorded_detect)
        app._state_detector = detector
        app._state_machine = GameStateMachine()
        app._state_machine.add_listener(app._on_game_state_transition)
        app._perf_monitor = PerformanceMonitor()

    def remember(self, image, region):
        if image is not None:
            self.images[id(image)] = region
            self.words[id(image)] = self.texts.get(region, "")

    def recognize(self, image, **kwargs):
        self.ocr_calls.append((self.images[id(image)], kwargs))
        return SimpleNamespace(text=self.words[id(image)])

    def grab(self, bounds):
        self.grabs.append(bounds.copy())
        x, y = bounds["left"] + 200, bounds["top"] - 30
        return self.frame[y:y + bounds["height"], x:x + bounds["width"]].copy()

    def observe(self, bottom="", top="", center="", banner="", money="", business=None,
                frame=None):
        self.texts = {BOTTOM: bottom, REGIONS.mission_text: top, REGIONS.center_prompt: center,
                      REGIONS.mission_banner: banner, REGIONS.money_display: money}
        if business is not None:
            self.texts.update(zip(REGIONS.get_business_regions().values(), business))
        if frame is not None:
            self.frame = np.dstack((frame, np.full(frame.shape[:2], 255, dtype=np.uint8)))
        return self.app._do_capture_cycle()


@pytest.fixture
def hud(app, monkeypatch):
    return CaptureHarness(app, monkeypatch)


def persisted(app):
    data = app._repository.export_session_data(app._data.db_session_id)
    del data["session"]["duration_seconds"]
    return data


def assert_not_started(app):
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert app._data.mission_start_money is None
    assert app._data.current_mission is None
    assert not app._data.mission_objectives.entries
    assert app._activity_tracker.completed_activities == []
    assert app.session_stats.activities_completed == 0
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app.cooldown_tracker.get_active_cooldowns() == []
    data = persisted(app)
    assert data["activities"] == data["earnings"] == []


@pytest.mark.parametrize("resolution", ((1280, 720), (1920, 1080), (2560, 1440)))
def test_bottom_crop_remains_in_same_grab_with_exact_geometry(app, monkeypatch, resolution):
    assert ScreenRegions().bottom_objective == BOTTOM
    hud = CaptureHarness(app, monkeypatch, resolution)
    height, width = resolution[1], resolution[0]
    hud.frame[:, :, 0] = np.arange(width, dtype=np.uint16) % 256
    hud.frame[:, :, 1] = (np.arange(height, dtype=np.uint16) % 256)[:, None]
    hud.observe()
    assert hud.batches == [BATCH]
    assert hud.grabs == [{"left": -200, "top": 30, "width": width, "height": height}]
    assert hud.waits == [True]
    for index, region in enumerate(BATCH):
        left, top, right, bottom = region.to_absolute(width, height)
        np.testing.assert_array_equal(hud.crops[0][index], hud.frame[top:bottom, left:right, :3])
    assert not np.shares_memory(hud.crops[0][0], hud.crops[0][6])


@pytest.mark.parametrize("text", (CAYO_COMMAND, "Escape\nCayo Perico", "  Escape  Cayo Perico  "))
def test_bottom_only_complete_cayo_command_starts_family_without_guessed_phase(app, hud, text):
    observed = hud.observe(bottom=text)
    detected = hud.detections[-1]
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.state_confidence == .8
    assert observed.bottom_objective_text == detected.bottom_objective_text == text
    assert detected.bottom_objective_command == CAYO_COMMAND
    assert (observed.mission_text, observed.objective_text, observed.banner_text) == ("", "", "")
    assert observed.mission.mission_type is MissionType.CAYO_PERICO
    assert observed.mission.heist_phase is MissionType.UNKNOWN
    assert observed.mission.identity_status == "type_only"
    assert observed.activity_type is ActivityType.CAYO_PERICO
    assert " ".join(observed.activity_name.casefold().split()) == "escape cayo perico"
    assert app._data.mission_objectives.entries == {"escape cayo perico"}
    assert app._activity_tracker.current_activity is not None
    assert persisted(app)["activities"] == []
    assert hud.ocr_calls[:4] == [
        (REGIONS.mission_text, {"invert": True, "scale": 2.0}),
        (REGIONS.center_prompt, {"invert": True, "scale": 2.0}),
        (REGIONS.mission_banner, {"invert": True, "scale": 2.0}),
        (BOTTOM, {"invert": True, "scale": 2.0, "threshold": False}),
    ]


REJECTED_BOTTOM = (
    ("generic", "Go to the location"),
    ("agency", "Return to the Agency"),
    ("title-only", "Cayo Perico"),
    ("generic-prep", "Go to the heist prep"),
    ("generic-finale", "Go to the heist finale"),
    ("numeric", "Escape Cayo Perico 2"),
    ("currency", "Escape Cayo Perico $7000"),
    ("mission-result", "MISSION PASSED"),
    ("heist-result", "HEIST PASSED\nCayo Perico"),
    ("result-command", "MISSION PASSED\nEscape Cayo Perico"),
    ("command-result", "Escape Cayo Perico\nMISSION PASSED"),
    ("table-first", "Player Take\nAlex 25000\nEscape Cayo Perico"),
    ("table-last", "Escape Cayo Perico\nPlayer Take\nAlex 25000"),
    ("footer-rp", "Escape Cayo Perico\nRP"),
    ("footer-medal", "Escape Cayo Perico\nPlatinum"),
    ("footer-continue", "Escape Cayo Perico\nCONTINUE"),
    ("business", "Bunker Stock 40% Supplies 60% Value $7000"),
    ("two-commands", "Escape Cayo Perico\nGo to the Diamond Casino"),
    ("conflicting-families", "Escape Cayo Perico and the Casino Heist"),
    ("incomplete", "Escape the"),
)


@pytest.mark.parametrize("label,text", REJECTED_BOTTOM, ids=[case[0] for case in REJECTED_BOTTOM])
def test_rejected_bottom_text_has_no_activity_business_result_or_cash_authority(app, hud, label, text):
    callbacks = []
    app.on_mission_complete(callbacks.append)
    before = persisted(app)
    observed = hud.observe(bottom=text)
    detected = hud.detections[-1]
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert observed.bottom_objective_text == text
    assert detected.bottom_objective_command == ""
    assert hud.region_calls == []
    assert_not_started(app)
    assert callbacks == []
    assert persisted(app) == before


@pytest.mark.parametrize("top,center,banner", (
    ("Headhunter", "", ""),
    ("", "", "Hostile Takeover"),
    ("", "Casino Heist", ""),
))
def test_bottom_conflicting_family_cannot_select_or_replace_primary_identity(app, hud, top, center, banner):
    observed = hud.observe(bottom=CAYO_COMMAND, top=top, center=center, banner=banner)
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert observed.mission.identity_status == "ambiguous"
    assert_not_started(app)


def test_bottom_cannot_repair_primary_ambiguous_identity(app, hud):
    observed = hud.observe(top="Headhunter", banner="Hostile Takeover", bottom=CAYO_COMMAND)
    assert observed.game_state is GameState.UNKNOWN
    assert observed.mission.identity_status == "ambiguous"
    assert BOTTOM not in [region for region, _ in hud.ocr_calls]
    assert_not_started(app)


def test_primary_business_keeps_original_read_calls_and_values(app, hud):
    observed = hud.observe(top="Bunker Stock Supplies", bottom=CAYO_COMMAND,
                           business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"))
    assert observed.game_state is GameState.BUSINESS_COMPUTER
    assert hud.region_calls == [(region, False) for region in REGIONS.get_business_regions().values()]
    assert BOTTOM not in [region for region, _ in hud.ocr_calls]
    business = app.get_business_state("bunker")
    assert (business["stock"], business["supply"], business["value"]) == (40, 60, 7000)
    assert hud.ocr_calls == [
        (region, {"invert": True, "scale": 2.0})
        for region in (REGIONS.mission_text, REGIONS.center_prompt, REGIONS.mission_banner,
                       REGIONS.money_display, *REGIONS.get_business_regions().values())
    ]
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


@pytest.mark.parametrize("result", ("HEIST PASSED", "HEIST\nPASSED"))
def test_unqualified_primary_heist_result_cannot_borrow_bottom_identity(app, hud, result):
    observed = hud.observe(banner=result, bottom=CAYO_COMMAND)
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert observed.mission.outcome_scope == "heist"
    assert observed.mission.outcome is None
    assert BOTTOM not in [region for region, _ in hud.ocr_calls]
    assert_not_started(app)


@pytest.mark.parametrize("bottom", ("MISSION PASSED\nCayo Perico\n$7000", "$7000",
                                   "Escape Cayo Perico\n$7000"))
def test_bottom_result_or_payout_cannot_finish_an_active_mission_or_credit_cash(app, hud, bottom):
    hud.observe(bottom=CAYO_COMMAND, money="$1000")
    current = app._activity_tracker.current_activity
    before = persisted(app)
    baseline = app._data.mission_start_time, app._data.mission_start_money
    observed = hud.observe(bottom=bottom)
    assert app._activity_tracker.current_activity is current
    assert (app._data.mission_start_time, app._data.mission_start_money) == baseline
    assert observed.game_state not in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
    assert app.current_money == 1000
    assert app.session_earnings == 0
    assert persisted(app) == before


def test_terminal_repeat_is_fenced_until_a_new_accepted_bottom_command(app, hud, clock):
    callbacks = []
    transitions = []
    app.on_mission_complete(callbacks.append)
    app.on_state_change(lambda previous, current: transitions.append((previous, current)))
    hud.observe(bottom=CAYO_COMMAND, money="$1000")
    current = app._activity_tracker.current_activity
    clock.value += timedelta(seconds=45)
    observed = hud.observe(banner="MISSION PASSED\nCayo Perico", money="$1500")
    row, = persisted(app)["activities"]
    assert observed.game_state is GameState.MISSION_COMPLETE
    assert (row["type"], row["duration_seconds"], row["earnings"], row["success"]) == (
        "CAYO_PERICO", 45, 500, True,
    )
    assert app._activity_tracker.completed_activities == [current]
    episode = app._data.terminal_mission_episode
    assert "escape cayo perico" in episode.objectives.entries
    repeated = hud.observe(bottom="Escape\nCayo Perico")
    assert repeated.game_state is GameState.UNKNOWN
    assert repeated.state_confidence == 0
    assert repeated.bottom_objective_text == "Escape\nCayo Perico"
    assert app._activity_tracker.current_activity is None
    direct = CaptureResult()
    app._process_state(hud.detections[-1], direct)
    assert direct.game_state is GameState.UNKNOWN
    assert direct.state_confidence == 0
    assert direct.bottom_objective_text == "Escape\nCayo Perico"
    assert direct.bottom_objective_command == CAYO_COMMAND
    hud.observe(bottom="Escape Cayo Perico\nRP")
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == [row]
    assert len(callbacks) == 1
    assert transitions == [
        (GameState.UNKNOWN, GameState.MISSION_ACTIVE),
        (GameState.MISSION_ACTIVE, GameState.MISSION_COMPLETE),
    ]
    fresh = hud.observe(bottom="Go to Cayo Perico")
    assert fresh.game_state is GameState.MISSION_ACTIVE
    assert fresh.activity_type is ActivityType.CAYO_PERICO
    assert app._activity_tracker.current_activity is not None
    assert app._data.mission_objectives.entries == {"go to cayo perico"}
    assert app._data.mission_start_money == 1500
    assert persisted(app)["activities"] == [row]
    assert transitions[-1] == (GameState.MISSION_COMPLETE, GameState.MISSION_ACTIVE)
    assert len(transitions) == 3


def test_primary_result_owns_completion_despite_conflicting_bottom_command(app, hud):
    hud.observe(banner="Headhunter")
    current = app._activity_tracker.current_activity
    observed = hud.observe(banner="MISSION PASSED\nHostile Takeover", bottom="Go to Headhunter")
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert app._activity_tracker.current_activity is current
    assert persisted(app)["activities"] == []
    observed = hud.observe(banner="MISSION PASSED\nHeadhunter", bottom=CAYO_COMMAND)
    assert observed.game_state is GameState.MISSION_COMPLETE
    row, = persisted(app)["activities"]
    assert (row["name"], row["type"], row["success"]) == ("Headhunter", "VIP_WORK", True)


@pytest.mark.parametrize("top,bottom", (("Escape", "Cayo Perico"),
                                       ("Cayo Perico", "Escape the")))
def test_incomplete_bottom_command_cannot_join_primary_crop(app, hud, top, bottom):
    hud.observe(top=top, bottom=bottom)
    assert hud.detections[-1].bottom_objective_command == ""
    assert not app._data.mission_objectives.entries


@pytest.mark.parametrize("top,state,kind,phase", (
    ("Cayo Perico", GameState.MISSION_ACTIVE, ActivityType.CAYO_PERICO, MissionType.UNKNOWN),
    ("Cayo Perico Prep", GameState.HEIST_PREP, ActivityType.HEIST_PREP, MissionType.HEIST_PREP),
    ("Cayo Perico Finale", GameState.HEIST_FINALE, ActivityType.HEIST_FINALE,
     MissionType.HEIST_FINALE),
))
def test_compatible_primary_family_and_explicit_phase_survive_bottom_command(app, hud, top,
                                                                          state, kind, phase):
    observed = hud.observe(top=top, bottom=CAYO_COMMAND)
    assert observed.game_state is state
    assert observed.activity_type is kind
    assert observed.mission.mission_type is MissionType.CAYO_PERICO
    assert observed.mission.heist_phase is phase
    assert observed.activity_name == CAYO_COMMAND
    assert hud.detections[-1].bottom_objective_command == CAYO_COMMAND
    assert app._data.mission_objectives.entries == {"escape cayo perico"}


@pytest.mark.parametrize("bottom", ("Cayo Perico", "Return to the Agency", "MISSION PASSED"))
def test_refused_bottom_keeps_named_primary_mission_and_objective(app, hud, bottom):
    observed = hud.observe(top="Headhunter", center="Eliminate the targets", bottom=bottom)
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.activity_name == "Headhunter"
    assert observed.activity_type is ActivityType.VIP_WORK
    assert observed.bottom_objective_text == bottom
    assert hud.detections[-1].bottom_objective_command == ""
    assert app._data.mission_objectives.entries == {"eliminate the targets"}
    assert persisted(app)["activities"] == []
