"""Opt-in rendered-image OCR checks for mission-result handoffs.

These are generated diagnostics, not GTA gameplay screenshots. They execute
production crops/preprocessing with a real Tesseract test adapter, then the
actual capture loop, tracker and disposable SQLite. No native Windows OCR or
measured gameplay accuracy is established.
"""

import os

import pytest

if os.environ.get('GTA_RUN_OCR_TESTS') != '1':
    pytest.skip('Set GTA_RUN_OCR_TESTS=1 for synthetic-image OCR integration', allow_module_level=True)

from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_ocr_images import RESOLUTIONS, run_frames, synthetic_frame


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
@pytest.mark.parametrize('outcome', ['MISSION PASSED', 'MISSION FAILED'])
def test_rendered_result_dropout_keeps_the_real_next_identity(app, monkeypatch, resolution, outcome):
    title = 'Hostile Takeover'
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, banner_text=outcome + '\n' + title),
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, banner_text=outcome + '\n' + title),
        synthetic_frame(resolution, banner_text='Sightseer'),
        synthetic_frame(resolution, banner_text='MISSION PASSED\nSightseer'),
    ])
    held = checkpoints[2][0]
    assert held.banner_text == title
    assert held.mission.mission_name == title
    assert held.game_state == GameState.UNKNOWN and held.state_confidence == 0.0
    assert not held.activity_name
    assert held.state_reason
    assert len(checkpoints[3][1]) == 1
    assert checkpoints[4][0].activity_name == 'Sightseer'
    assert [row['name'] for row in checkpoints[-1][1]] == [title, 'Sightseer']
    assert app.session_stats.activities_completed == 2


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
@pytest.mark.parametrize('two_objectives', [False, True])
def test_rendered_result_objectives_do_not_become_fresh_when_other_text_disappears(
    app, monkeypatch, resolution, two_objectives,
):
    title = 'Hostile Takeover'
    old = 'Go to the location'
    surviving = 'Eliminate the targets' if two_objectives else old
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, mission_text=old,
                        center_text=surviving if two_objectives else '',
                        banner_text='MISSION PASSED\n' + title),
        synthetic_frame(resolution, mission_text=surviving, banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
    ])
    held = checkpoints[2][0]
    assert held.mission_text == surviving
    assert held.game_state == GameState.UNKNOWN and held.state_confidence == 0.0
    assert not held.activity_name
    assert len(checkpoints[-1][1]) == 1
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_fresh_objective_can_start_one_same_name_retry(app, monkeypatch, resolution):
    title = 'Hostile Takeover'
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION FAILED\n' + title),
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, mission_text='Go to the location', banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
        synthetic_frame(resolution, mission_text='Go to the location', banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
    ])
    assert checkpoints[2][0].game_state == GameState.UNKNOWN
    assert not checkpoints[2][0].activity_name
    fresh = checkpoints[3][0]
    assert fresh.mission_text == 'Go to the location'
    assert fresh.game_state == GameState.MISSION_ACTIVE
    assert fresh.activity_name == title
    assert checkpoints[5][0].game_state == GameState.UNKNOWN
    assert [row['name'] for row in checkpoints[-1][1]] == [title, title]
    assert [row['success'] for row in checkpoints[-1][1]] == [False, True]
    assert app.session_stats.activities_completed == 2


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_capture_starting_on_named_result_does_not_invent_activity(app, monkeypatch, resolution):
    title = 'Hostile Takeover'
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
    ])
    assert checkpoints[0][1] == []
    assert checkpoints[1][0].banner_text == title
    assert checkpoints[1][0].game_state == GameState.UNKNOWN
    assert not checkpoints[1][0].activity_name
    assert checkpoints[-1][1] == []
    assert app.session_stats.activities_completed == 0


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_result_payout_suffix_loss_does_not_make_old_objective_fresh(app, monkeypatch, resolution):
    title = 'Hostile Takeover'
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text=title),
        synthetic_frame(resolution, mission_text='Eliminate the targets\nMISSION PASSED $25000',
                        banner_text=title),
        synthetic_frame(resolution, mission_text='Eliminate the targets', banner_text=title),
        synthetic_frame(resolution, banner_text='MISSION PASSED\n' + title),
    ])
    result, first_rows, _ = checkpoints[1]
    assert 'MISSION PASSED' in result.mission_text
    assert '$25000' in result.mission_text
    assert result.game_state == GameState.MISSION_COMPLETE
    assert len(first_rows) == 1
    assert checkpoints[2][0].mission_text == 'Eliminate the targets'
    assert checkpoints[2][0].game_state == GameState.UNKNOWN
    assert not checkpoints[2][0].activity_name
    assert len(checkpoints[-1][1]) == 1
