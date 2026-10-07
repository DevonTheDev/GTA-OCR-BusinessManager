"""Opt-in generated pixels for the complete distinctive objective association.

Controlled rendered text, production crop/preprocessing/detector/app/SQLite and
installed Tesseract. No game images are shipped, and no Windows/live claim is made.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for distinctive-objective images", allow_module_level=True)

from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import BATCH, assert_not_started, persisted
from tests.test_distinctive_objective_pipeline import (
    COMMAND, NORMALIZED, assert_no_fabricated_effects,
)
from tests.test_mission_ocr_images import RESOLUTIONS
from tests.test_result_header_ocr_images import header_frame, image_hud
from tests.test_result_header_workflow import QUALIFIED_RESULT, owner_snapshot


@pytest.mark.parametrize("resolution", RESOLUTIONS[1:], ids=("1080p", "1440p"))
def test_rendered_complete_command_starts_family_with_unknown_phase(app, monkeypatch, resolution):
    hud, backend = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, bottom=COMMAND))
    assert result.bottom_objective_text == COMMAND
    assert any(reading.text == COMMAND for reading in backend.results)
    assert result.game_state is GameState.MISSION_ACTIVE and result.state_confidence == .8
    assert result.mission.mission_type is MissionType.CAYO_PERICO
    assert result.mission.identity_status == "type_only"
    assert result.mission.mission_name == ""
    assert result.mission.heist_phase is MissionType.UNKNOWN
    assert result.mission.outcome is result.mission.outcome_scope is None
    assert app._activity_tracker.current_activity.activity_type is ActivityType.CAYO_PERICO
    assert app._data.mission_objectives.entries == {NORMALIZED}
    assert hud.batches == [BATCH] and len(hud.grabs) == len(hud.waits) == 1
    assert persisted(app)["activities"] == []
    assert_no_fabricated_effects(app)


def test_rendered_720p_transcription_keeps_exact_identity_boundary(app, monkeypatch, record_property):
    # Tesseract versions may read the unchanged 720p font correctly or make the
    # recorded El -> EI] substitution. Only the complete command has authority.
    resolution = RESOLUTIONS[0]
    hud, backend = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, bottom=COMMAND))
    observed_text = result.bottom_objective_text
    record_property("observed_720p_bottom_text", observed_text)
    assert observed_text in (COMMAND, "Go to EI] Rubio's compound."), (
        f"Unmeasured 720p transcription: {observed_text!r}"
    )
    assert any(reading.text == observed_text for reading in backend.results)
    if observed_text == COMMAND:
        assert result.game_state is GameState.MISSION_ACTIVE and result.state_confidence == .8
        assert result.bottom_objective_command == COMMAND.rstrip(".")
        assert result.mission.mission_type is MissionType.CAYO_PERICO
        assert result.mission.identity_status == "type_only"
        assert result.mission.mission_name == ""
        assert result.mission.heist_phase is MissionType.UNKNOWN
        assert result.mission.outcome is result.mission.outcome_scope is None
        assert app._activity_tracker.current_activity.activity_type is ActivityType.CAYO_PERICO
        assert app._data.mission_objectives.entries == {NORMALIZED}
        assert persisted(app)["activities"] == []
    else:
        assert result.bottom_objective_command == ""
        assert result.game_state is GameState.UNKNOWN
        assert_not_started(app)
    assert hud.batches == [BATCH] and len(hud.grabs) == len(hud.waits) == 1
    assert_no_fabricated_effects(app)


def test_rendered_arranged_repeat_keeps_two_exact_owners_and_completes_once_each(app, monkeypatch):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    callbacks = []
    app.on_mission_complete(callbacks.append)
    escape = header_frame(resolution, bottom="Escape Cayo Perico")
    summary = header_frame(resolution, header=QUALIFIED_RESULT)
    hud.observe(frame=escape)
    first = app._activity_tracker.current_activity
    hud.observe(frame=summary)
    assert callbacks == [first]
    hud.observe(frame=header_frame(resolution, bottom=COMMAND))
    second = owner_snapshot(app)
    assert second[0] is not first
    hud.observe(frame=escape)
    assert owner_snapshot(app)[:-1] == second[:-1]
    hud.observe(frame=summary)
    assert callbacks == [first, second[0]] and callbacks[1] is second[0]
    assert second[0].started_at == second[3]
    saved = persisted(app)
    hud.observe(frame=summary)
    assert callbacks == [first, second[0]] and persisted(app) == saved
    assert len(saved["activities"]) == 2
    assert all(row["earnings"] == 0 and row["success"] for row in saved["activities"])
    assert len(hud.grabs) == len(hud.waits) == 6
    assert_no_fabricated_effects(app)


@pytest.mark.parametrize("bottom", [
    "Go to the compound", "El Rubio's compound", "Go to El Rubio",
    "Go to El Rublo's compound", "Go to El Rubio's compound and wait",
    "Go to El Rubio's compound $100", "Go to El Rubio's compound\nMISSION PASSED",
    "Go to El Rubio's compound\nCONTINUE",
])
def test_rendered_nonmatching_or_contaminated_commands_cannot_start(app, monkeypatch, bottom):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, bottom=bottom))
    assert result.bottom_objective_text == bottom
    assert result.bottom_objective_command == ""
    assert result.game_state is GameState.UNKNOWN
    assert_not_started(app)


@pytest.mark.parametrize("source", ["header", "banner"])
def test_rendered_unqualified_result_cannot_borrow_command_family(app, monkeypatch, source):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, bottom=COMMAND, **{source: "HEIST PASSED"}))
    assert (result.result_header_text if source == "header" else result.banner_text) == "HEIST PASSED"
    assert result.game_state is GameState.UNKNOWN and result.state_confidence == 0
    assert result.result_header_evidence == ""
    assert_not_started(app)


def test_rendered_casino_conflict_remains_ambiguous(app, monkeypatch):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, top="Casino Heist", bottom=COMMAND))
    assert result.mission_text == "Casino Heist" and result.bottom_objective_text == COMMAND
    assert result.game_state is GameState.UNKNOWN
    assert result.mission.identity_status == "ambiguous"
    assert_not_started(app)


def test_rendered_fragments_cannot_cross_source_boundaries(app, monkeypatch):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, top="Go to El Rubio's", bottom="compound"))
    assert result.mission_text == "Go to El Rubio's" and result.bottom_objective_text == "compound"
    assert result.mission.mission_type is MissionType.UNKNOWN
    assert result.bottom_objective_command == ""
    assert app._data.mission_identity_type is MissionType.UNKNOWN
    assert_no_fabricated_effects(app)


def test_rendered_primary_business_keeps_fields_and_grab_count(app, monkeypatch):
    resolution = (1600, 900)
    hud, _ = image_hud(app, monkeypatch, resolution)
    result = hud.observe(frame=header_frame(resolution, top="Bunker", bottom=COMMAND, business=True))
    assert result.game_state is GameState.BUSINESS_COMPUTER
    reading = app._data.business_states["bunker"]
    assert (reading["stock"], reading["supply"], reading["value"]) == (40, 60, 7000)
    assert len(hud.grabs) == 4
    assert_not_started(app)
