"""Opt-in generated-image bottom-objective checks using actual Tesseract.

Production ScreenCapture slices each single grabbed controlled frame, followed
by production preprocessing, detector, app tracking and disposable SQLite. The
test-only backend supplies Tesseract instead of Windows OCR. Generated images
do not validate native Windows, gameplay, YouTube frames or real HUD placement.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for generated bottom-objective OCR", allow_module_level=True)

import numpy as np

from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414 - pytest fixture re-export
from tests.test_bottom_objective_capture import (
    BATCH,
    BOTTOM,
    CAYO_COMMAND,
    CaptureHarness,
    assert_not_started,
    persisted,
)
from tests.test_mission_ocr_images import (
    FONT_PATH,
    RESOLUTIONS,
    Image,
    ImageDraw,
    ImageFont,
    TesseractDiagnostic,
    synthetic_frame,
)


def objective_frame(resolution, bottom, top="", banner=""):
    """Render white objective text wholly inside the configured bottom crop."""
    frame = Image.fromarray(synthetic_frame(resolution, top, banner_text=banner)[:, :, ::-1])
    draw = ImageDraw.Draw(frame)
    width, height = resolution
    scale = height / 1080
    font = ImageFont.truetype(str(FONT_PATH), round(24 * scale))
    left, top, right, lower = BOTTOM.to_absolute(width, height)
    position = (left + round(12 * scale), top + round(3 * scale))
    spacing = round(7 * scale)
    bounds = draw.multiline_textbbox(position, bottom, font=font, spacing=spacing)
    assert left <= bounds[0] < bounds[2] <= right
    assert top <= bounds[1] < bounds[3] <= lower
    draw.multiline_text(position, bottom, font=font, fill="white", spacing=spacing)
    return np.asarray(frame)[:, :, ::-1].copy()


def image_hud(app, monkeypatch, resolution):
    backend = TesseractDiagnostic()
    hud = CaptureHarness(app, monkeypatch, resolution, backend)
    return hud, backend


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("bottom", (CAYO_COMMAND, "Escape\nCayo Perico"),
                         ids=("single-line", "wrapped"))
def test_rendered_bottom_command_starts_cayo_family_without_phase(app, monkeypatch, resolution, bottom):
    hud, backend = image_hud(app, monkeypatch, resolution)
    observed = hud.observe(frame=objective_frame(resolution, bottom))
    assert observed.bottom_objective_text == bottom
    assert backend.results[3].text == bottom
    assert hud.detections[-1].bottom_objective_command == CAYO_COMMAND
    assert hud.batches == [BATCH]
    assert len(hud.grabs) == len(hud.waits) == 1
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.state_confidence == .8
    assert observed.mission.mission_type is MissionType.CAYO_PERICO
    assert observed.mission.heist_phase is MissionType.UNKNOWN
    assert observed.activity_type is ActivityType.CAYO_PERICO
    assert observed.activity_name == CAYO_COMMAND
    assert app._data.mission_objectives.entries == {"escape cayo perico"}
    assert app._activity_tracker.current_activity is not None
    assert persisted(app)["activities"] == persisted(app)["earnings"] == []


IMAGE_REJECTED = (
    ("generic", "Go to the location"),
    ("agency", "Return to the Agency"),
    ("title", "Cayo Perico"),
    ("number", "Escape Cayo Perico 2"),
    ("table-first", "Player Take\nAlex 25000\nEscape Cayo Perico"),
    ("table-last", "Escape Cayo Perico\nPlayer Take\nAlex 25000"),
    ("result", "MISSION PASSED\nEscape Cayo Perico"),
    ("footer", "Escape Cayo Perico\nCONTINUE"),
)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("label,bottom", IMAGE_REJECTED, ids=[case[0] for case in IMAGE_REJECTED])
def test_rendered_untrusted_bottom_text_is_observed_but_cannot_start(app, monkeypatch, resolution,
                                                                  label, bottom):
    hud, backend = image_hud(app, monkeypatch, resolution)
    observed = hud.observe(frame=objective_frame(resolution, bottom))
    assert backend.results[3].text == observed.bottom_objective_text == bottom
    assert hud.detections[-1].bottom_objective_command == ""
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert_not_started(app)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_primary_result_cannot_borrow_bottom_cayo_identity(app, monkeypatch, resolution):
    hud, backend = image_hud(app, monkeypatch, resolution)
    observed = hud.observe(frame=objective_frame(resolution, CAYO_COMMAND, banner="HEIST PASSED"))
    assert backend.results[2].text == observed.banner_text == "HEIST PASSED"
    assert observed.game_state is GameState.UNKNOWN
    assert observed.mission.outcome_scope == "heist"
    assert observed.mission.outcome is None
    assert hud.detections[-1].bottom_objective_command == ""
    assert_not_started(app)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_bottom_conflict_preserves_primary_identity_ambiguity(app, monkeypatch, resolution):
    hud, backend = image_hud(app, monkeypatch, resolution)
    observed = hud.observe(frame=objective_frame(resolution, CAYO_COMMAND, top="Headhunter"))
    assert backend.results[0].text == observed.mission_text == "Headhunter"
    assert backend.results[3].text == observed.bottom_objective_text == CAYO_COMMAND
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0
    assert observed.mission.identity_status == "ambiguous"
    assert_not_started(app)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_result_repeat_and_new_objective_follow_same_terminal_fence(app, monkeypatch,
                                                                          resolution):
    hud, _backend = image_hud(app, monkeypatch, resolution)
    first = hud.observe(frame=objective_frame(resolution, CAYO_COMMAND))
    assert first.bottom_objective_text == CAYO_COMMAND
    assert first.activity_type is ActivityType.CAYO_PERICO
    completed = hud.observe(frame=objective_frame(resolution, CAYO_COMMAND,
                                                  banner="MISSION PASSED\nCayo Perico"))
    assert completed.banner_text == "MISSION PASSED\nCayo Perico"
    assert completed.game_state is GameState.MISSION_COMPLETE
    row, = persisted(app)["activities"]
    assert row["type"] == "CAYO_PERICO"
    assert row["success"] is True
    assert row["earnings"] == 0
    repeated = hud.observe(frame=objective_frame(resolution, "Escape\nCayo Perico"))
    assert repeated.bottom_objective_text == "Escape\nCayo Perico"
    assert repeated.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    fresh = hud.observe(frame=objective_frame(resolution, "Go to Cayo Perico"))
    assert fresh.bottom_objective_text == "Go to Cayo Perico"
    assert fresh.game_state is GameState.MISSION_ACTIVE
    assert app._data.mission_objectives.entries == {"go to cayo perico"}
    assert persisted(app)["activities"] == [row]
    assert app.session_stats.activities_completed == 1
    assert app.session_earnings == 0
    assert hud.batches == [BATCH] * 4
    assert len(hud.grabs) == 4
