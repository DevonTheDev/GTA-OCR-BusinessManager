"""Opt-in real Tesseract checks for generic Take-only mission admission.

These controlled images are generated diagnostics, not gameplay screenshots.
They use production crops and preprocessing, the existing Tesseract test
adapter, the real capture loop and trackers, and disposable SQLite. They do not
measure gameplay accuracy or validate native Windows OCR or actual HUD layout.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for synthetic-image OCR integration", allow_module_level=True)

from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_ocr_images import RESOLUTIONS, run_frames, synthetic_frame


def assert_transcription(observed, backend, mission, center=""):
    """Check actual controlled OCR before making admission assertions."""
    # This fixture renders center text inside the overlapping banner crop too.
    assert tuple(result.text for result in backend.results[:3]) == (mission, center, center)
    assert (observed.mission_text, observed.objective_text, observed.banner_text) == (
        mission, center, center,
    )


def assert_no_activity_side_effects(app, observed, rows, cooldown):
    assert observed.activity_type is None
    assert observed.activity_name == ""
    assert app._data.mission_start_time is None
    assert app._data.mission_start_money is None
    assert app._data.current_mission is None
    assert app._activity_tracker.current_activity is None
    assert app._activity_tracker.completed_activities == []
    assert app.session_stats.activities_completed == 0
    assert rows == [] and cooldown is None
    saved = app._repository.export_session_data(app._data.db_session_id)
    assert saved["activities"] == []
    assert saved["earnings"] == []


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("center", ("", "Bring the briefcase"), ids=("table-only", "unrelated-command"))
def test_rendered_take_table_does_not_start_or_persist_an_activity(app, monkeypatch, resolution, center):
    table = "Player Take\nAlex 25000"
    checkpoints, backend = run_frames(app, monkeypatch, [synthetic_frame(resolution, table, center)])
    observed, rows, cooldown = checkpoints[0]
    assert_transcription(observed, backend, table, center)
    assert observed.game_state == GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_type == MissionType.UNKNOWN
    assert_no_activity_side_effects(app, observed, rows, cooldown)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("command", (
    "Take the briefcase",
    "Take out the targets",
    "Take the\nbriefcase",
), ids=("take", "take-out", "wrapped-take"))
def test_rendered_complete_take_command_starts_an_unresolved_activity(app, monkeypatch, resolution, command):
    checkpoints, backend = run_frames(app, monkeypatch, [synthetic_frame(resolution, command)])
    observed, rows, cooldown = checkpoints[0]
    assert_transcription(observed, backend, command)
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert observed.state_confidence == 0.7
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_type == MissionType.UNKNOWN
    assert observed.activity_type == ActivityType.UNKNOWN
    assert observed.activity_name == command
    current = app._activity_tracker.current_activity
    assert current is not None
    assert (current.activity_type, current.name) == (ActivityType.UNKNOWN, command)
    assert app._data.mission_start_time is not None
    assert app._activity_tracker.completed_activities == []
    assert app.session_stats.activities_completed == 0
    assert rows == [] and cooldown is None


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_incomplete_take_command_cannot_join_another_crop(app, monkeypatch, resolution):
    checkpoints, backend = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, "Take the", "briefcase"),
    ])
    observed, rows, cooldown = checkpoints[0]
    assert_transcription(observed, backend, "Take the", "briefcase")
    assert observed.game_state == GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert observed.mission.identity_status == "unknown"
    assert observed.mission.mission_type == MissionType.UNKNOWN
    assert_no_activity_side_effects(app, observed, rows, cooldown)
