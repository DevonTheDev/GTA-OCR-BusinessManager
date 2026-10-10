"""One observed named start banner; injected text is not native OCR proof.

The fixture records the unchanged production diagnostic on a real publisher
JPEG. Only its OCR text/provenance ships; original pixels remain local.
"""

from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from src.app import CaptureResult, GTABusinessManager
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app  # noqa: PLC0414
from tests.test_bottom_objective_capture import assert_not_started, persisted
from tests.test_detection_sample_core import make_collector
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414
from tests.test_result_header_workflow import owner_snapshot, assert_no_accounting
from tests.test_screenshot_diagnostic import diagnostic, image_path, native  # noqa: PLC0414
from tests.test_vip_status_admission import detect
from tests.test_vip_status_capture import hud as hud, REGIONS  # noqa: PLC0414


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/ocr_sightseer_start_banner.json').read_text())
TEXT = {name: item['text'] for name, item in FIXTURE['sources'].items()}
HEADER = TEXT['result_header']
STATUS = TEXT['vip_status']
ACTUAL = dict(top=TEXT['mission_text'], center=TEXT['center_prompt'],
              banner=TEXT['mission_banner'], bottom=TEXT['bottom_objective'], header=HEADER)
NEGATIVES = (
    'Sightseer', 'Collect the packages hidden around the map',
    'Sightseer app\nCollect the packages hidden around the map',
    HEADER + '\nMISSION PASSED', HEADER + '\nMISSION FAILED', HEADER + '\n$25000',
    HEADER + '\nHeadhunter', HEADER + '\nVIP WORK', HEADER + '.',
    HEADER.replace('\n', ' '), HEADER.replace('\n', '\v'),
    HEADER.replace('\n', '\u2028'), HEADER.replace('SIGHTSEER', 'SİGHTSEER'),
    HEADER.replace('the packages', 'the\u00a0packages'),
    HEADER.replace('packages', 'package'), HEADER.replace('hidden', 'located'),
    HEADER.replace('around the map', 'around\nthe map'),
    'Collect the packages hidden around the map\nSIGHTSEER',
)


def direct(*, header=HEADER, status=STATUS, evidence=None, cached=None,
           state=GameState.MISSION_ACTIVE, marker='VIP WORK', **kwargs):
    result = StateDetectionResult(state, .8, 'direct upper title',
        result_header_text=header, vip_status_text=status, vip_status_evidence=marker,
        mission=cached, **kwargs)
    # Dynamic assignment lets the baseline fail a behavioral assertion instead
    # of erroring because the new optional dataclass field does not exist yet.
    result.active_title_evidence = header if evidence is None else evidence
    return result


def test_optional_title_evidence_defaults_empty():
    for result in (CaptureResult(), StateDetectionResult(GameState.UNKNOWN, 0, 'empty')):
        assert getattr(result, 'active_title_evidence', None) == ''


def test_captured_complete_title_is_already_a_parser_name_but_app_reference_is_not():
    parser = MissionParser()
    title = parser.parse(HEADER)
    assert title.identity_status == 'known_name' and title.mission_name == 'Sightseer'
    assert title.outcome is title.outcome_scope is None
    for text in (TEXT['bottom_objective'], 'Use the Sightseer app to find the next package.'):
        assert parser.parse(text).mission_name == ''
    for name in ('Sightseer', 'Headhunter'):
        reading = parser.parse(f'MISSION PASSED\n{name}')
        assert reading.mission_name == name and reading.outcome == 'complete'


def test_actual_six_source_observation_identifies_sightseer_without_new_ocr():
    result, calls = detect(STATUS, **ACTUAL)
    assert result.state is GameState.MISSION_ACTIVE and result.confidence == .8
    assert result.mission.identity_status == 'known_name'
    assert result.mission.mission_name == 'Sightseer'
    assert result.mission.candidates == ('Sightseer',)
    assert result.mission.mission_type is MissionType.VIP_WORK
    assert result.mission.outcome is result.mission.outcome_scope is None
    assert getattr(result, 'active_title_evidence', None) == HEADER
    assert result.result_header_text == HEADER and result.result_header_evidence == ''
    assert result.vip_status_text == STATUS and result.vip_status_evidence == 'VIP WORK'
    assert result.bottom_objective_text == TEXT['bottom_objective']
    assert result.bottom_objective_command == ''
    assert result.banner_text == TEXT['mission_banner']
    assert [name for name, _ in calls] == ['top', 'center', 'banner', 'bottom', 'status', 'header']
    assert calls[-1][1] == {'threshold': False, 'invert': False, 'scale': 2.0}
    assert not GTABusinessManager._objective_evidence(result).entries
    assert result.mission.objective == ''


@pytest.mark.parametrize('raw', [HEADER, HEADER.lower(),
    '\r\n SIGHTSEER\t\r\n Collect\tthe  packages hidden around the map \r\n',
    HEADER.replace('\n', '\r')])
def test_only_observed_banner_text_with_ascii_layout_variants_is_admitted(raw):
    result, _ = detect(STATUS, header=raw)
    assert result.mission.mission_name == 'Sightseer'
    assert getattr(result, 'active_title_evidence', None) == raw
    assert result.mission.objective == ''
    assert not GTABusinessManager._objective_evidence(result).entries


@pytest.mark.parametrize('raw', NEGATIVES)
def test_incomplete_app_qualified_joined_or_extended_upper_text_stays_diagnostic(raw):
    baseline, _ = detect(STATUS)
    result, _ = detect(STATUS, header=raw)
    assert result.state is baseline.state and result.mission == baseline.mission
    assert result.result_header_text == raw and result.result_header_evidence == ''
    assert getattr(result, 'active_title_evidence', '') == ''


@pytest.mark.parametrize('status', ['', None, 'PACKAGES REMAINING 3', 'VIPWORKEND',
    'VIPWORKEND14:58', 'VIPWORKEND $25000', 'MISSION PASSED', HEADER])
def test_title_cannot_borrow_identity_support_from_absent_or_invalid_status(status):
    baseline, _ = detect(status)
    result, _ = detect(status, header=HEADER)
    assert result.state is baseline.state and result.mission == baseline.mission
    assert getattr(result, 'active_title_evidence', '') == ''


@pytest.mark.parametrize('source', ['bottom', 'status'])
def test_title_in_other_supplemental_sources_has_no_new_title_authority(source):
    kwargs = {'bottom': HEADER} if source == 'bottom' else {}
    result, _ = detect(HEADER if source == 'status' else STATUS, **kwargs)
    assert result.mission is None or result.mission.mission_name == ''
    assert getattr(result, 'active_title_evidence', '') == ''


@pytest.mark.parametrize('top', ['Headhunter', 'Hostile Takeover'])
def test_actual_upper_title_conflicting_with_independent_name_stays_uncertain(top):
    result, _ = detect(STATUS, top=top, header=HEADER)
    assert result.state is GameState.UNKNOWN and result.confidence == 0
    assert result.mission.identity_status == 'ambiguous'
    assert set(result.mission.candidates) == {top, 'Sightseer'}
    assert getattr(result, 'active_title_evidence', '') == ''


@pytest.mark.parametrize('top', [
    'MISSION PASSED\nHeadhunter', 'MISSION FAILED\nHeadhunter',
    'HEIST PASSED\nCayo Perico', 'HEIST PASSED',
    'Bunker Stock 40% Supplies 60% Value $7000', 'Headhunter\nSightseer',
    'Cayo Perico', 'Nightclub Promotion',
])
def test_existing_result_business_and_family_priority_is_unchanged(top):
    baseline, baseline_calls = detect(STATUS, top=top)
    result, calls = detect(STATUS, top=top, header=HEADER)
    assert asdict(result) == asdict(baseline)
    assert calls == baseline_calls


def test_actual_capture_starts_named_owner_without_accounting_or_objective_authority(app, hud):
    observed = hud.observe(status=STATUS, **ACTUAL)
    owner = app._activity_tracker.current_activity
    assert owner is not None and owner.name == 'Sightseer'
    assert owner.activity_type is ActivityType.VIP_WORK
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.activity_name == 'Sightseer' and observed.activity_type is ActivityType.VIP_WORK
    assert getattr(observed, 'active_title_evidence', None) == HEADER
    assert observed.result_header_evidence == observed.bottom_objective_command == ''
    assert app._data.mission_identity_status == 'known_name'
    assert not app._data.mission_objectives.entries
    assert_no_accounting(app)
    assert app._data.business_states == {} and observed.timer is None
    assert len(hud.grabs) == 1 and len(hud.batches[0]) == 9
    assert [region for region, _ in hud.ocr_calls].count(REGIONS.result_header) == 1
    before = owner_snapshot(app)
    hud.observe(status=STATUS, **ACTUAL)
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


def test_category_owner_refines_without_restart_or_new_objective(app, hud, clock):
    hud.observe(status=STATUS, money='$1000')
    owner = app._activity_tracker.current_activity
    before = owner.started_at, app._data.mission_start_time, app._data.mission_start_money
    clock.value += timedelta(seconds=25)
    hud.observe(status=STATUS, **ACTUAL)
    assert app._activity_tracker.current_activity is owner and owner.name == 'Sightseer'
    assert (owner.started_at, app._data.mission_start_time, app._data.mission_start_money) == before
    assert not app._data.mission_objectives.entries
    assert persisted(app)['activities'] == persisted(app)['earnings'] == []


@pytest.mark.parametrize('ending', ['MISSION PASSED', 'MISSION PASSED\nSightseer',
                                   'MISSION FAILED\nSightseer'])
def test_supported_result_retains_named_owner_once_and_blocks_start_banner_replay(app, hud, clock, ending):
    completed = []
    app.on_mission_complete(completed.append)
    hud.observe(status=STATUS, **ACTUAL)
    owner = app._activity_tracker.current_activity
    assert owner.name == 'Sightseer'
    clock.value += timedelta(seconds=45)
    result = hud.observe(banner=ending, status=STATUS, header=HEADER)
    success = 'PASSED' in ending
    assert result.game_state is (GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED)
    assert getattr(result, 'active_title_evidence', '') == ''
    assert app._activity_tracker.current_activity is None
    row, = persisted(app)['activities']
    assert (row['name'], row['type'], row['success'], row['duration_seconds']) == ('Sightseer', 'VIP_WORK', success, 45)
    assert row['earnings'] == 0 and app.current_money is None
    assert persisted(app)['earnings'] == []
    saved = persisted(app)
    hud.observe(banner=ending)
    replay = hud.observe(status=STATUS, **ACTUAL)
    assert replay.game_state is GameState.UNKNOWN and replay.state_confidence == 0
    assert app._activity_tracker.current_activity is None
    assert persisted(app) == saved and app.session_stats.activities_completed == 1
    assert completed == ([owner] if success else [])


def test_existing_different_owner_is_not_renamed_or_completed_by_active_title(app, hud):
    hud.observe(top='Headhunter')
    before = owner_snapshot(app)
    hud.observe(status=STATUS, **ACTUAL)
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


@pytest.mark.parametrize('status', ['', 'VIPWORKEND', 'PACKAGES REMAINING 3'])
def test_real_app_cannot_start_from_upper_title_without_independent_status(app, hud, status):
    hud.observe(header=HEADER, status=status)
    assert_not_started(app)


@pytest.mark.parametrize('raw', NEGATIVES)
def test_direct_call_revalidates_exact_banner_before_any_activity_or_ledger(app, raw):
    result = direct(header=raw)
    capture = CaptureResult()
    app._process_state(result, capture)
    assert capture.game_state is GameState.UNKNOWN and capture.state_confidence == 0
    assert getattr(capture, 'active_title_evidence', '') == ''
    assert_not_started(app)


@pytest.mark.parametrize('kwargs', [
    {'status': ''}, {'status': 'VIPWORKEND'}, {'marker': ''},
    {'evidence': HEADER + '\n$25000'}, {'header': 'Sightseer', 'evidence': HEADER},
    {'cached': MissionParser().parse('Headhunter')},
    {'cached': MissionParser().parse('MISSION PASSED\nSightseer')},
    {'cached': MissionParser().parse('Sightseer\nCollect the packages')},
    {'cached': replace(MissionParser().parse('Sightseer\nVIP WORK'), heist_phase=MissionType.HEIST_FINALE)},
    {'cached': replace(MissionParser().parse('Sightseer\nVIP WORK'), is_active=False)},
    {'state': GameState.MISSION_COMPLETE}, {'state': GameState.MISSION_FAILED},
    {'mission_text': 'MISSION PASSED'}, {'result_header_evidence': 'MISSION PASSED'},
])
def test_direct_call_rejects_stale_forged_or_result_mixed_title_evidence(app, hud, kwargs):
    hud.observe(top='Headhunter')
    before = owner_snapshot(app)
    capture = CaptureResult()
    app._process_state(direct(**kwargs), capture)
    assert capture.game_state is GameState.UNKNOWN and capture.state_confidence == 0
    assert getattr(capture, 'active_title_evidence', '') == ''
    assert owner_snapshot(app) == before
    assert_no_accounting(app)


def test_direct_fallback_rebuilds_same_named_reading_without_objective(app):
    result = direct()
    reading = app._mission_reading(result)
    assert reading.mission_name == 'Sightseer' and reading.identity_status == 'known_name'
    assert reading.objective == '' and reading.outcome is None
    capture = CaptureResult()
    app._process_state(result, capture)
    assert app._activity_tracker.current_activity.name == 'Sightseer'
    assert getattr(capture, 'active_title_evidence', None) == HEADER
    assert not app._data.mission_objectives.entries
    assert_no_accounting(app)


def test_sample_serializes_active_title_evidence_separately_from_result_evidence():
    result, _ = detect(STATUS, **ACTUAL)
    collector = make_collector()
    collector.stage('candidate', result)
    report = json.loads(collector.finish().json_bytes)
    candidate = report['detector_candidate']
    assert candidate.get('active_title_evidence') == HEADER
    assert candidate['result_header_evidence'] == ''
    assert candidate['result_header_text'] == HEADER
    assert candidate['identity']['mission_name'] == 'Sightseer'


def test_still_diagnostic_reports_active_title_evidence_with_unchanged_six_calls(diagnostic, image_path, native):
    calls = native([TEXT[name] for name in ('mission_text', 'center_prompt', 'mission_banner',
                                          'bottom_objective', 'vip_status', 'result_header')])
    report = diagnostic.diagnose_image(image_path)
    assert report['detector_candidate'].get('active_title_evidence') == HEADER
    assert report['detector_candidate']['identity']['mission_name'] == 'Sightseer'
    assert len(calls) == 6
