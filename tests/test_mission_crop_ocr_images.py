"""Opt-in real Tesseract checks on controlled crop-boundary text.

Images are generated diagnostics, not GTA screenshots. No native Windows OCR,
gameplay incidence, HUD placement or accuracy benchmark is established here.
"""

import os

import pytest

if os.environ.get('GTA_RUN_OCR_TESTS') != '1':
    pytest.skip('Set GTA_RUN_OCR_TESTS=1 for synthetic-image OCR integration', allow_module_level=True)

from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_ocr_images import RESOLUTIONS, run_frames, synthetic_frame


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
@pytest.mark.parametrize('top,center', [('Executive', 'Search'), ('Customer', 'vehicle'), ('Casino', 'Heist')])
def test_rendered_separate_fragments_never_become_a_specific_mission(app, monkeypatch, resolution, top, center):
    checkpoints, _ = run_frames(app, monkeypatch, [synthetic_frame(resolution, top, center)])
    observed, rows, cooldown = checkpoints[0]
    assert observed.mission_text == top
    assert observed.objective_text == center
    assert observed.mission.identity_status == 'unknown'
    assert observed.mission.mission_name == ''
    current = app._activity_tracker.current_activity
    assert current is None or current.activity_type == ActivityType.UNKNOWN
    assert rows == [] and cooldown is None


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_result_fragments_keep_activity_open_until_a_complete_label(app, monkeypatch, resolution):
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text='Hostile Takeover'),
        synthetic_frame(resolution, mission_text='Contract', center_text='complete'),
        synthetic_frame(resolution, banner_text='Contract complete'),
    ])
    first, partial, final = checkpoints
    assert first[0].activity_name == 'Hostile Takeover'
    assert partial[0].mission_text == 'Contract'
    assert partial[0].objective_text == 'complete'
    assert partial[0].mission.outcome is None
    assert partial[0].activity_name == 'Hostile Takeover'
    assert partial[1] == [] and partial[2] is None
    assert final[0].game_state == GameState.MISSION_COMPLETE
    row, = final[1]
    assert (row['name'], row['type'], row['success']) == ('Hostile Takeover', 'VIP_WORK', True)
    assert final[2] is not None
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_wrapped_name_within_one_crop_still_selects_correct_activity(app, monkeypatch, resolution):
    checkpoints, _ = run_frames(app, monkeypatch, [synthetic_frame(resolution, 'Executive\nSearch')])
    result = checkpoints[0][0]
    assert result.mission_text == 'Executive\nSearch'
    assert (result.activity_name, result.activity_type) == ('Executive Search', ActivityType.VIP_WORK)


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
def test_rendered_complete_cross_crop_evidence_still_agrees(app, monkeypatch, resolution):
    checkpoints, _ = run_frames(app, monkeypatch, [synthetic_frame(resolution, 'VIP Work', banner_text='Hostile Takeover')])
    result = checkpoints[0][0]
    assert result.mission_text == 'VIP Work'
    assert result.banner_text == 'Hostile Takeover'
    assert (result.activity_name, result.activity_type) == ('Hostile Takeover', ActivityType.VIP_WORK)


@pytest.mark.parametrize('resolution', RESOLUTIONS, ids=['720p', '1080p', '1440p'])
@pytest.mark.parametrize('outcome,success', [('MISSION PASSED', True), ('MISSION FAILED', False)])
def test_rendered_old_named_result_cannot_finish_a_different_current_mission(app, monkeypatch, resolution, outcome, success):
    checkpoints, _ = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, banner_text='Hostile Takeover'),
        synthetic_frame(resolution, banner_text='MISSION PASSED\nHostile Takeover'),
        synthetic_frame(resolution, banner_text='Sightseer'),
        synthetic_frame(resolution, banner_text=outcome + '\nHostile Takeover'),
        synthetic_frame(resolution, banner_text=outcome + '\nSightseer'),
    ])
    assert len(checkpoints[1][1]) == 1
    assert checkpoints[2][0].activity_name == 'Sightseer'
    stale, rows, _ = checkpoints[3]
    assert stale.mission.mission_name == 'Hostile Takeover'
    assert stale.mission.outcome == ('complete' if success else 'failed')
    assert stale.game_state == GameState.UNKNOWN and stale.state_confidence == 0.0
    assert stale.activity_name == 'Sightseer'
    assert [row['name'] for row in rows] == ['Hostile Takeover']
    final_rows = checkpoints[4][1]
    assert [row['name'] for row in final_rows] == ['Hostile Takeover', 'Sightseer']
    assert final_rows[1]['success'] is success
    assert app.session_stats.activities_completed == 2
    assert (app.cooldown_tracker.get_cooldown('sightseer') is not None) is success
