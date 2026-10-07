"""Opt-in generated-image result-header integration, with no gameplay assets.

Controlled pixels -> real ScreenCapture batching -> unmodified production OCR
preprocessing -> installed Tesseract -> production detector/app/trackers/SQLite.
This diagnostic backend does not validate Windows OCR or general HUD accuracy.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for generated result-header OCR", allow_module_level=True)

import numpy as np

from src.detection.parsers.mission_parser import MissionType
from src.detection.template_matcher import TemplateMatcher
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import assert_not_started, persisted
from tests.test_mission_ocr_images import (
    FONT_PATH, RESOLUTIONS, Image, ImageDraw, ImageFont, TesseractDiagnostic, synthetic_frame,
)
from tests.test_result_header_workflow import (
    HEADER, QUALIFIED_RESULT, REGIONS, ResultHeaderHarness, assert_no_accounting, owner_snapshot,
)


def header_frame(resolution, header="", top="", banner="", bottom="", business=False):
    """Render controlled strings in distinct production crops, never a real HUD."""
    width, height = resolution
    frame = Image.fromarray(synthetic_frame(resolution, top, banner_text=banner)[:, :, ::-1])
    draw = ImageDraw.Draw(frame)
    scale = height / 1080
    font = ImageFont.truetype(str(FONT_PATH), round(24 * scale))
    strings = [(HEADER, header), (REGIONS.bottom_objective, bottom)]
    if business:
        strings.extend(zip(REGIONS.get_business_regions().values(),
                           ("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000")))
    for region, text in strings:
        if not text:
            continue
        left, top, right, lower = region.to_absolute(width, height)
        point = (left + round(12 * scale), top + round(3 * scale))
        spacing = round(7 * scale)
        bounds = draw.multiline_textbbox(point, text, font=font, spacing=spacing)
        assert left <= bounds[0] < bounds[2] <= right
        assert top <= bounds[1] < bounds[3] <= lower
        draw.multiline_text(point, text, font=font, fill="white", spacing=spacing)
    return np.asarray(frame)[:, :, ::-1].copy()


def image_hud(app, monkeypatch, resolution):
    backend = TesseractDiagnostic()
    hud = ResultHeaderHarness(app, monkeypatch, resolution, backend)
    # The shared text harness disables templates; this image path restores the
    # actual production matcher so only native screen IO and OCR are replaced.
    app._state_detector._templates = TemplateMatcher()
    return hud, backend


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_escape_header_repeat_and_old_escape_complete_exact_owner_once(
        app, monkeypatch, resolution):
    hud, backend = image_hud(app, monkeypatch, resolution)
    completed = []
    app.on_mission_complete(completed.append)
    escape = header_frame(resolution, bottom="Escape Cayo Perico")
    first = hud.observe(frame=escape)
    assert first.bottom_objective_text == "Escape Cayo Perico"
    assert first.game_state is GameState.MISSION_ACTIVE
    owner = app._activity_tracker.current_activity
    started = owner.started_at
    result = hud.observe(frame=header_frame(resolution, QUALIFIED_RESULT))
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.result_header_text == result.result_header_evidence == QUALIFIED_RESULT
    assert any(reading.text == QUALIFIED_RESULT for reading in backend.results)
    assert completed == [owner] and completed[0] is owner and owner.started_at == started
    assert owner.activity_type is ActivityType.CAYO_PERICO
    assert app._data.terminal_mission_episode.identity.phase is MissionType.UNKNOWN
    saved = persisted(app)
    repeat = hud.observe(frame=header_frame(resolution, QUALIFIED_RESULT))
    old = hud.observe(frame=escape)
    assert repeat.game_state is GameState.MISSION_COMPLETE and old.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert completed == [owner] and persisted(app) == saved
    row, = saved["activities"]
    assert row["success"] is True and row["earnings"] == 0
    assert saved["earnings"] == [] and app.session_earnings == 0
    assert len(hud.grabs) == len(hud.waits) == 4
    assert all(batch[7] == HEADER and batch[8] == REGIONS.vip_status and len(batch) == 9
               for batch in hud.batches)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("header", [QUALIFIED_RESULT, "The Cayo Perico Heist", "Agency",
                                    "Escape Cayo Perico", "HEIST PASSED"])
def test_rendered_idle_header_result_and_untrusted_text_cannot_start(app, monkeypatch, resolution, header):
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, header))
    assert result.result_header_text == header
    assert result.game_state is (GameState.MISSION_COMPLETE if header == QUALIFIED_RESULT else GameState.UNKNOWN)
    assert result.result_header_evidence == (header if header == QUALIFIED_RESULT else "")
    assert_not_started(app)


@pytest.mark.parametrize("first,header", [
    ("Casino Heist", QUALIFIED_RESULT), ("Headhunter", QUALIFIED_RESULT),
    ("Cayo Perico Prep", QUALIFIED_RESULT), ("Cayo Perico", "HEIST PASSED"),
    ("Headhunter", "HEIST PASSED"), ("Go to the location", "HEIST PASSED"),
])
def test_rendered_rejected_and_title_dropout_headers_preserve_exact_owner(app, monkeypatch, first, header):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    completed = []
    app.on_mission_complete(completed.append)
    hud.observe(frame=header_frame(resolution, banner=first))
    before = owner_snapshot(app)
    transitions = tuple(app._state_machine.context.transitions)
    result = hud.observe(frame=header_frame(resolution, header))
    assert result.result_header_text == header
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert owner_snapshot(app) == before and app._activity_tracker.current_activity is before[0]
    assert tuple(app._state_machine.context.transitions) == transitions
    assert completed == []
    assert_no_accounting(app)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_unqualified_header_cannot_borrow_primary_title(app, monkeypatch, resolution):
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, "HEIST PASSED", top="The Cayo Perico Heist"))
    assert result.mission_text == "The Cayo Perico Heist"
    assert result.result_header_text == "HEIST PASSED" and result.result_header_evidence == ""
    assert result.game_state is GameState.UNKNOWN
    assert_not_started(app)


def test_rendered_primary_bunker_retains_stock_supplies_and_value(app, monkeypatch):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, QUALIFIED_RESULT, top="Bunker",
                                           bottom="Escape Cayo Perico", business=True))
    assert result.game_state is GameState.BUSINESS_COMPUTER
    reading = app._data.business_states["bunker"]
    assert (reading["stock"], reading["supply"], reading["value"]) == (40, 60, 7000)
    assert len(hud.grabs) == 4
    assert app._activity_tracker.current_activity is None
    assert_no_accounting(app)
