"""Independent OCR crops through actual detection, activity tracking and SQLite.

Text boundaries are synthetic. Optional image tests exercise real Tesseract;
neither set establishes native Windows OCR or real gameplay accuracy.
"""

from datetime import timedelta

import pytest

from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_banner_capture import activity_rows, banner_frame
from tests.test_mission_result_accounting import clock as clock


def spread(first, second, positions):
    regions = ['', '', '']
    regions[positions[0]], regions[positions[1]] = first, second
    return regions


@pytest.mark.parametrize('positions', [(0, 1), (0, 2), (1, 2)])
@pytest.mark.parametrize('first,second', [('Executive', 'Search'), ('Customer', 'vehicle'), ('Casino', 'Heist')])
def test_separate_fragments_do_not_select_a_specific_activity(app, positions, first, second):
    regions = spread(first, second, positions)
    result, _ = banner_frame(app, *regions)
    assert result.mission.identity_status == 'unknown'
    assert result.mission.mission_name == ''
    assert result.mission.mission_type == MissionType.UNKNOWN
    assert (result.mission_text, result.objective_text, result.banner_text) == tuple(regions)
    current = app._activity_tracker.current_activity
    assert current is None or current.activity_type == ActivityType.UNKNOWN
    assert activity_rows(app) == []


@pytest.mark.parametrize('index', range(3))
def test_wrapped_title_inside_each_crop_still_selects_the_catalog_mission(app, index):
    regions = ['', '', '']
    regions[index] = 'Executive\nSearch'
    result, _ = banner_frame(app, *regions)
    assert result.mission.mission_name == 'Executive Search'
    assert result.activity_name == 'Executive Search'
    assert result.activity_type == ActivityType.VIP_WORK


@pytest.mark.parametrize('positions', [(0, 1), (0, 2), (1, 2)])
def test_independently_complete_category_and_title_agree(app, positions):
    result, _ = banner_frame(app, *spread('VIP Work', 'Hostile Takeover', positions))
    assert result.mission.identity_status == 'known_name'
    assert result.activity_name == 'Hostile Takeover'
    assert result.activity_type == ActivityType.VIP_WORK


@pytest.mark.parametrize('positions', [(0, 1), (0, 2), (1, 2)])
def test_independent_heist_family_and_phase_labels_combine(app, positions):
    result, _ = banner_frame(app, *spread('Casino Heist', 'Finale', positions))
    assert result.mission.mission_type == MissionType.CASINO_HEIST
    assert result.mission.heist_phase == MissionType.HEIST_FINALE
    assert result.activity_type == ActivityType.HEIST_FINALE


@pytest.mark.parametrize('positions', [(0, 1), (0, 2), (1, 2)])
def test_different_complete_titles_remain_ambiguous(app, positions):
    result, _ = banner_frame(app, *spread('Headhunter', 'Sightseer', positions))
    assert result.mission.identity_status == 'ambiguous'
    assert result.mission.candidates == ('Headhunter', 'Sightseer')
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize('positions', [(0, 1), (0, 2), (1, 2)])
def test_result_fragments_do_not_finish_an_existing_activity(app, positions):
    banner_frame(app, 'Hostile Takeover')
    current = app._activity_tracker.current_activity
    result, _ = banner_frame(app, *spread('Contract', 'complete', positions))
    assert result.mission.outcome is None
    assert result.game_state != GameState.MISSION_COMPLETE
    assert app._activity_tracker.current_activity is current
    assert activity_rows(app) == []
    assert app.session_stats.activities_completed == 0
    assert app.cooldown_tracker.get_cooldown('hostile_takeover') is None


def test_generic_delivery_phrase_and_objective_do_not_cross_a_crop_boundary(app):
    result, _ = banner_frame(app, 'Deliver the', 'goods')
    assert result.mission.objective == 'Deliver the'
    assert result.game_state == GameState.MISSION_ACTIVE
    assert result.activity_type == ActivityType.UNKNOWN


def test_explicit_results_from_different_crops_still_conflict(app):
    banner_frame(app, 'Hostile Takeover')
    current = app._activity_tracker.current_activity
    result, _ = banner_frame(app, 'MISSION PASSED', '', 'MISSION FAILED')
    assert result.mission.outcome == 'conflicting'
    assert result.game_state == GameState.UNKNOWN
    assert app._activity_tracker.current_activity is current
    assert activity_rows(app) == []


def test_only_complete_later_evidence_refines_and_finishes_the_same_activity(app, clock):
    app._data.current_money = 1000
    banner_frame(app, 'Go to the location')
    current = app._activity_tracker.current_activity
    baseline = app._data.mission_start_time, app._data.mission_start_money, current.started_at
    banner_frame(app, 'Hostile', 'Takeover')
    assert current.activity_type == ActivityType.UNKNOWN
    assert current.name == 'Go to the location'
    banner_frame(app, banner='Hostile Takeover')
    assert app._activity_tracker.current_activity is current
    assert (current.name, current.activity_type) == ('Hostile Takeover', ActivityType.VIP_WORK)
    assert (app._data.mission_start_time, app._data.mission_start_money, current.started_at) == baseline
    clock.value += timedelta(seconds=90)
    app._data.current_money = 2500
    banner_frame(app, 'Contract', 'complete')
    assert app._activity_tracker.current_activity is current
    assert activity_rows(app) == []
    banner_frame(app, banner='Contract complete\nHostile Takeover')
    row, = activity_rows(app)
    assert (row['name'], row['type'], row['earnings'], row['duration_seconds']) == ('Hostile Takeover', 'VIP_WORK', 1500, 90)
    assert app.session_stats.activities_completed == 1
    assert app.cooldown_tracker.get_cooldown('hostile_takeover') is not None
