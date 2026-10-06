"""Generated-image diagnostics; no guide images, Windows OCR or accuracy claim."""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for generated-image OCR integration", allow_module_level=True)

from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_heist_success_results import OBSERVED_CAYO_RESULT
from tests.test_inline_mission_result_ocr_images import assert_transcription, money_frame
from tests.test_mission_ocr_images import RESOLUTIONS, run_frames
from tests.test_terminal_mission_identity_ownership import mission_stats, stored


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
def test_generated_heist_result_completes_once_and_keeps_phase_unknown(app, monkeypatch, resolution):
    banners = ["Cayo Perico", OBSERVED_CAYO_RESULT, "The Cayo Perico Heist", OBSERVED_CAYO_RESULT]
    checkpoints, _ = run_frames(app, monkeypatch, [
        money_frame(resolution, banner_text=text, balance=1000 if index == 0 else 1250)
        for index, text in enumerate(banners)
    ])
    assert_transcription(checkpoints, [("", "", text) for text in banners])
    assert [item[0].game_state for item in checkpoints] == [
        GameState.MISSION_ACTIVE, GameState.MISSION_COMPLETE, GameState.UNKNOWN, GameState.MISSION_COMPLETE,
    ]
    assert [len(rows) for _, rows, _ in checkpoints] == [0, 1, 1, 1]
    assert checkpoints[-1][1][0]["type"] == "CAYO_PERICO"
    assert checkpoints[-1][1][0]["earnings"] == 250
    assert mission_stats(app) == (1, 1, 0, 0) and app.session_earnings == 250


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
@pytest.mark.parametrize("first,result", [
    ("Cayo Perico Prep", OBSERVED_CAYO_RESULT),
    ("Casino Heist", OBSERVED_CAYO_RESULT),
    ("Headhunter", OBSERVED_CAYO_RESULT),
    ("Cayo Perico", "HEIST\nPASSED"),
])
def test_generated_scoped_rejections_keep_current_activity(app, monkeypatch, resolution, first, result):
    checkpoints, _ = run_frames(app, monkeypatch, [
        money_frame(resolution, banner_text=first),
        money_frame(resolution, banner_text=result, balance=1250),
    ])
    assert_transcription(checkpoints, [("", "", first), ("", "", result)])
    assert checkpoints[-1][0].game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity.name == first
    assert stored(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    assert app.session_earnings == 250


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=["720p", "1080p", "1440p"])
def test_generated_result_only_and_split_phrase_controls(app, monkeypatch, resolution):
    inputs = [
        {"banner_text": OBSERVED_CAYO_RESULT},
        {"banner_text": "The Cayo Perico Heist"},
        {"mission_text": "heist", "banner_text": "passed Cayo Perico"},
    ]
    checkpoints, _ = run_frames(app, monkeypatch, [money_frame(resolution, **texts) for texts in inputs])
    assert_transcription(checkpoints, [
        ("", "", OBSERVED_CAYO_RESULT), ("", "", "The Cayo Perico Heist"),
        ("heist", "", "passed Cayo Perico"),
    ])
    assert checkpoints[0][0].game_state == GameState.MISSION_COMPLETE
    assert checkpoints[-1][0].mission.outcome is None
    assert checkpoints[-1][0].mission.outcome_scope is None
    assert app._activity_tracker.current_activity is None
    assert stored(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
