"""Automatic identity through real capture/classifier/tracker and disposable SQLite.

OCR text is synthetic and comes from existing repository definitions. These tests
exercise integration and ownership, not actual Windows OCR or gameplay accuracy.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from src.app import CaptureResult
from src.detection.state_detector import StateDetector, StateDetectionResult
from src.detection.parsers.mission_parser import MissionType
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app
from tests.test_mission_result_accounting import clock as clock


def frame(app, mission_text="", center_text="", brightness=70, color=None, template=None):
    mission_image, center_image = object(), object()
    words = {id(mission_image): mission_text, id(center_image): center_text}
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(
            text=words[id(image)], confidence=1.0, words=[]
        ),
    )
    app._state_machine = app._state_machine or GameStateMachine()
    app._perf_monitor = app._perf_monitor or PerformanceMonitor()
    if app._state_detector is None:
        app._state_detector = StateDetector(
            template_matcher=SimpleNamespace(match_any=lambda *args: None),
            ocr_engine=app._ocr,
        )
    else:
        app._state_detector._ocr = app._ocr
    app._state_detector._templates = SimpleNamespace(match_any=lambda _image, names: template
                                                      if 'mission_banner' in names else None)
    full_image = np.full((120, 200, 3), brightness, dtype=np.uint8)
    if color is not None:
        full_image[5:15, 50:100] = color
    app._capture = SimpleNamespace(
        regions=SimpleNamespace(
            full_screen=0, money_display=1, mission_text=2,
            center_prompt=3, timer_bottom_right=4, mission_banner=5,
        ),
        capture_multiple_regions=lambda regions: [
            full_image,
            None, mission_image, center_image, None, None,
        ],
    )
    return app._do_capture_cycle()


def complete(app):
    app._process_state(StateDetectionResult(
        state=GameState.MISSION_COMPLETE, confidence=1.0, reason="audit completion"
    ), CaptureResult())
    return app._repository.export_session_data(app._data.db_session_id)["activities"]


@pytest.mark.parametrize("title, expected", [
    ("Headhunter", ActivityType.VIP_WORK),
    ("Sightseer", ActivityType.VIP_WORK),
    ("Hostile Takeover", ActivityType.VIP_WORK),
    ("Asset Recovery", ActivityType.VIP_WORK),
    ("Executive Search", ActivityType.VIP_WORK),
])
def test_known_vip_title_keeps_catalog_category(app, title, expected):
    observed = frame(app, mission_text=title)
    assert observed.game_state == GameState.MISSION_ACTIVE
    activity = app._activity_tracker.current_activity
    assert activity is not None
    assert activity.activity_type == expected


def test_center_title_reaches_activity_identity(app):
    observed = frame(app, center_text="Headhunter")
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert app._activity_tracker.current_activity.activity_type == ActivityType.VIP_WORK
    assert "headhunter" in app._activity_tracker.current_activity.name.lower()


def test_known_hostile_takeover_preserves_category_in_sqlite_and_cooldown(app):
    frame(app, mission_text="Hostile Takeover")
    rows = complete(app)
    assert rows[0]["type"] == "VIP_WORK"
    assert app.cooldown_tracker.get_cooldown("hostile_takeover") is not None


def test_customer_vehicle_is_auto_shop_not_sell(app):
    # Phrase exists in both detector.AUTO_SHOP_KEYWORDS and MissionParser.
    observed = frame(app, mission_text="customer vehicle")
    assert app._mission_parser.parse("customer vehicle").mission_type == MissionType.AUTO_SHOP_DELIVERY
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert app._activity_tracker.current_activity.activity_type == ActivityType.AUTO_SHOP_DELIVERY


def test_low_confidence_visual_frame_does_not_freeze_unknown_identity(app):
    first = frame(app, brightness=100)
    assert first.state_confidence == 0.6
    assert app._state_machine.state == GameState.UNKNOWN
    frame(app, mission_text="Headhunter")
    assert app._activity_tracker.current_activity.activity_type == ActivityType.VIP_WORK
    assert "headhunter" in app._activity_tracker.current_activity.name.lower()


@pytest.mark.parametrize("title", ["Recover Valuables", "Gang Termination", "Rescue Operation"])
def test_known_security_title_is_recognized(app, title):
    assert app._mission_parser.parse(title).mission_type == MissionType.SECURITY_CONTRACT
    frame(app, mission_text=title)
    assert app._activity_tracker.current_activity is not None
    assert app._activity_tracker.current_activity.activity_type == ActivityType.SECURITY_CONTRACT


@pytest.mark.parametrize('mission,center,kind,name', [
    ('Headhunter\nDeliver the goods', '', ActivityType.VIP_WORK, 'Headhunter'),
    ('Executive\nSearch', '', ActivityType.VIP_WORK, 'Executive Search'),
    ('Deliver the vehicle', 'Customer vehicle', ActivityType.AUTO_SHOP_DELIVERY, 'Deliver the vehicle'),
    ('Recover Valuables\nDeliver the valuables', '', ActivityType.SECURITY_CONTRACT, 'Recover Valuables'),
    ('Rooftop Rumble\nDeliver the documents', '', ActivityType.CONTACT_MISSION, 'Rooftop Rumble'),
])
def test_specific_identity_wins_over_generic_objectives(app, mission, center, kind, name):
    observed = frame(app, mission, center)
    assert observed.game_state == GameState.MISSION_ACTIVE
    current = app._activity_tracker.current_activity
    assert current is not None
    assert (current.activity_type, current.name) == (kind, name)
    assert observed.mission is not None
    assert observed.mission.identity_status in ('known_name', 'type_only')
    assert observed.mission.raw_text == '\n'.join(value for value in (mission, center) if value)
    assert observed.mission_text == mission
    assert observed.objective_text == center


def test_visual_only_uncertainty_does_not_start_any_activity(app):
    observed = frame(app, brightness=100)
    assert observed.state_confidence == 0.6
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None


def test_later_clear_identity_refines_unresolved_activity_without_restarting(app):
    from datetime import timedelta
    frame(app, 'Go to the location', brightness=100)
    current = app._activity_tracker.current_activity
    assert current is not None and current.activity_type == ActivityType.UNKNOWN
    started, balance = app._data.mission_start_time, app._data.mission_start_money
    current.started_at -= timedelta(minutes=2)
    tracked_started = current.started_at
    app._data.current_money = 25000
    observed = frame(app, center_text='Hostile Takeover')
    assert observed.mission.identity_status == 'known_name'
    assert app._activity_tracker.current_activity is current
    assert (current.activity_type, current.name) == (ActivityType.VIP_WORK, 'Hostile Takeover')
    assert current.started_at == tracked_started
    assert app._data.mission_start_time == started
    assert app._data.mission_start_money == balance
    assert app._activity_tracker.completed_activities == []
    assert app._repository.export_session_data(app._data.db_session_id)['activities'] == []
    complete(app)
    assert app.cooldown_tracker.get_cooldown('hostile_takeover') is not None


def test_confirmed_name_is_not_retargeted_by_later_different_title(app):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    frame(app, 'Sightseer')
    assert app._activity_tracker.current_activity is current
    assert current.name == 'Headhunter'
    (row,) = complete(app)
    assert row['name'] == 'Headhunter' and row['type'] == 'VIP_WORK'


def test_named_evidence_can_refine_same_explicit_category(app):
    frame(app, 'Security contract')
    current = app._activity_tracker.current_activity
    assert current.activity_type == ActivityType.SECURITY_CONTRACT
    frame(app, 'Recover Valuables')
    assert app._activity_tracker.current_activity is current
    assert current.name == 'Recover Valuables'


def test_strong_category_is_not_retargeted_to_incompatible_name(app):
    frame(app, 'Security contract')
    current = app._activity_tracker.current_activity
    frame(app, 'Headhunter')
    assert app._activity_tracker.current_activity is current
    assert current.activity_type == ActivityType.SECURITY_CONTRACT
    assert current.name == 'Security contract'


@pytest.mark.parametrize('text', ['Bonus available', 'Reward for delivery', 'Take out the targets', 'Cut through traffic'])
def test_generic_words_do_not_finish_or_assign_heist_phase(app, text):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    observed = frame(app, text, brightness=100)
    assert observed.game_state not in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED,
                                       GameState.HEIST_PREP, GameState.HEIST_FINALE)
    assert app._activity_tracker.current_activity is current
    assert app._activity_tracker.completed_activities == []


def test_conflicting_titles_do_not_pick_a_mission_or_replace_confirmed_identity(app):
    observed = frame(app, 'Headhunter\nSightseer', brightness=100)
    assert observed.mission.identity_status == 'ambiguous'
    assert app._activity_tracker.current_activity is None or app._activity_tracker.current_activity.activity_type == ActivityType.UNKNOWN
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    assert current.name == 'Headhunter'
    frame(app, 'Headhunter\nSightseer')
    assert app._activity_tracker.current_activity is current and current.name == 'Headhunter'


@pytest.mark.parametrize('confidence', [0.6, 0.0, -1.0, float('nan'), float('inf'), True, '0.9'])
def test_unaccepted_confidence_cannot_start_or_refine_identity(app, confidence):
    app._process_state(StateDetectionResult(
        state=GameState.MISSION_ACTIVE, confidence=confidence, reason='synthetic uncertain',
        mission_text='Headhunter',
    ), CaptureResult())
    assert app._activity_tracker.current_activity is None
    frame(app, 'Go to the location', brightness=100)
    current = app._activity_tracker.current_activity
    assert current is not None and current.activity_type == ActivityType.UNKNOWN
    app._process_state(StateDetectionResult(
        state=GameState.MISSION_ACTIVE, confidence=confidence, reason='synthetic uncertain',
        mission_text='Headhunter',
    ), CaptureResult())
    assert app._activity_tracker.current_activity is current
    assert current.activity_type == ActivityType.UNKNOWN


def test_existing_nightclub_promotion_identity_has_its_own_activity_category(app):
    observed = frame(app, 'Nightclub promotion')
    assert observed.game_state == GameState.MISSION_ACTIVE
    assert app._activity_tracker.current_activity.activity_type == ActivityType.NIGHTCLUB_PROMOTION
    (row,) = complete(app)
    assert row['type'] == 'NIGHTCLUB_PROMOTION'


@pytest.mark.parametrize('text', ['Objective complete', 'SUCCESS', 'COMPLETED', 'Well done'])
def test_generic_congratulations_do_not_finish_a_tracked_mission(app, text):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    observed = frame(app, center_text=text)
    assert observed.game_state != GameState.MISSION_COMPLETE
    assert app._activity_tracker.current_activity is current
    assert app._activity_tracker.completed_activities == []


def test_strong_template_keeps_original_ocr_identity_and_center_objective(app):
    from src.detection.parsers.mission_parser import MissionParser
    detector = StateDetector(template_matcher=object(), ocr_engine=object())
    evidence = MissionParser().parse('Hostile Takeover\nCollect the briefcase')
    ocr = StateDetectionResult(GameState.MISSION_ACTIVE, 0.8, 'named OCR',
                               mission_text='Hostile Takeover', objective_text='Collect the briefcase', mission=evidence)
    template = StateDetectionResult(GameState.MISSION_ACTIVE, 0.95, 'matching mission banner')
    combined = detector._combine_results(StateDetectionResult(GameState.IDLE, 0.5, 'visual'), ocr, template)
    assert combined.reason == template.reason and combined.confidence == 0.95
    assert combined.mission_text == 'Hostile Takeover'
    assert combined.objective_text == 'Collect the briefcase'
    assert combined.mission is evidence
    app._process_state(combined, CaptureResult())
    assert app._activity_tracker.current_activity.name == 'Hostile Takeover'
    assert template.mission is None and template.mission_text == ''


@pytest.mark.parametrize('color', [(0, 255, 255), (0, 0, 255)])
@pytest.mark.parametrize('objective', ['', 'Go to the location'])
def test_color_only_result_cues_cannot_finish_a_mission(app, color, objective):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    observed = frame(app, objective, color=color)
    assert observed.game_state not in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
    assert app._activity_tracker.current_activity is current
    assert complete(app)[0]['name'] == 'Headhunter'


def result_template(name):
    return SimpleNamespace(matched=True, confidence=0.99, template_name=name)


@pytest.mark.parametrize('title', ['', 'Headhunter\n', 'Security contract\n'])
@pytest.mark.parametrize('template_name', [None, 'mission_banner', 'mission_passed'])
def test_conflicting_results_cannot_start_activity_even_with_named_or_template_evidence(app, title, template_name):
    template = None if template_name is None else result_template(template_name)
    observed = frame(app, title + 'MISSION PASSED\nMISSION FAILED', template=template)
    assert observed.game_state == GameState.UNKNOWN
    assert observed.mission.outcome == 'conflicting'
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None


@pytest.mark.parametrize('result,state,template_name', [
    ('MISSION PASSED', GameState.MISSION_COMPLETE, 'mission_banner'),
    ('MISSION FAILED', GameState.MISSION_FAILED, 'mission_banner'),
    ('MISSION PASSED', GameState.UNKNOWN, 'mission_failed'),
    ('MISSION FAILED', GameState.UNKNOWN, 'mission_passed'),
])
def test_explicit_ocr_results_take_priority_over_active_template_and_abstain_on_result_conflict(
    app, result, state, template_name,
):
    frame(app, 'Headhunter')
    observed = frame(app, 'Headhunter\n' + result, template=result_template(template_name))
    assert observed.game_state == state
    rows = app._repository.export_session_data(app._data.db_session_id)['activities']
    if state == GameState.UNKNOWN:
        assert rows == [] and app._activity_tracker.current_activity is not None
    else:
        assert len(rows) == 1 and rows[0]['name'] == 'Headhunter'
        assert rows[0]['success'] is (state == GameState.MISSION_COMPLETE)
        assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize('result', ['MISSION PASSED', 'MISSION FAILED', 'MISSION PASSED\nMISSION FAILED'])
def test_result_reading_cannot_start_or_refine_through_direct_active_state(app, result):
    active = StateDetectionResult(GameState.MISSION_ACTIVE, 0.99, 'synthetic active template', mission_text='Headhunter\n' + result)
    app._process_state(active, CaptureResult())
    assert app._activity_tracker.current_activity is None
    frame(app, 'Go to the location', brightness=100)
    current = app._activity_tracker.current_activity
    app._process_state(active, CaptureResult())
    assert app._activity_tracker.current_activity is current
    assert current.activity_type == ActivityType.UNKNOWN


@pytest.mark.parametrize('state', [GameState.MISSION_COMPLETE, GameState.MISSION_FAILED])
@pytest.mark.parametrize('confidence', [0.6, 0.0, float('nan'), float('inf')])
def test_uncertain_results_do_not_end_or_persist_activity(app, state, confidence):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    app._process_state(StateDetectionResult(state, confidence, 'uncertain result'), CaptureResult())
    assert app._activity_tracker.current_activity is current
    assert app._repository.export_session_data(app._data.db_session_id)['activities'] == []


@pytest.mark.parametrize('first,second,kind,name,family,phase', [
    ('Casino Heist', 'The Big Con\nFinale', ActivityType.HEIST_FINALE, 'The Big Con', MissionType.CASINO_HEIST, MissionType.HEIST_FINALE),
    ('Cayo Perico', 'Cayo Perico Prep', ActivityType.HEIST_PREP, 'Cayo Perico', MissionType.CAYO_PERICO, MissionType.HEIST_PREP),
    ('The Big Con', 'The Big Con\nFinale', ActivityType.HEIST_FINALE, 'The Big Con', MissionType.CASINO_HEIST, MissionType.HEIST_FINALE),
    ('Heist prep', 'The Big Con', ActivityType.HEIST_PREP, 'The Big Con', MissionType.CASINO_HEIST, MissionType.HEIST_PREP),
])
def test_compatible_heist_family_name_and_phase_refine_independently(app, first, second, kind, name, family, phase):
    app._data.current_money = 1000
    frame(app, first)
    current = app._activity_tracker.current_activity
    started, money = app._data.mission_start_time, app._data.mission_start_money
    app._data.current_money = 2500
    frame(app, second)
    assert app._activity_tracker.current_activity is current
    assert (current.activity_type, current.name) == (kind, name)
    assert app._data.mission_identity_type == family
    assert app._data.mission_heist_phase == phase
    assert (app._data.mission_start_time, app._data.mission_start_money) == (started, money)
    (row,) = complete(app)
    assert (row['type'], row['name'], row['earnings']) == (kind.name, name, 1500)
    assert app._data.mission_identity_type == MissionType.UNKNOWN
    assert app._data.mission_heist_phase == MissionType.UNKNOWN
    frame(app, 'Cayo Perico')
    assert app._activity_tracker.current_activity.activity_type == ActivityType.CAYO_PERICO


@pytest.mark.parametrize('first,second', [
    ('Cayo Perico Prep', 'Cayo Perico Finale'),
    ('The Big Con\nPrep', 'The Big Con\nFinale'),
    ('The Big Con', 'Silent & Sneaky\nFinale'),
    ('Casino Heist', 'Doomsday Prep'),
    ('Headhunter', 'The Big Con\nFinale'),
])
def test_confirmed_heist_axes_and_names_are_not_switched_to_conflicting_evidence(app, first, second):
    frame(app, first)
    current = app._activity_tracker.current_activity
    before = (current.activity_type, current.name, app._data.mission_identity_type, app._data.mission_heist_phase)
    frame(app, second)
    assert app._activity_tracker.current_activity is current
    assert (current.activity_type, current.name, app._data.mission_identity_type, app._data.mission_heist_phase) == before


@pytest.mark.parametrize('first,terminal_title,kind,name', [
    ('Go to the location', 'Headhunter', ActivityType.VIP_WORK, 'Headhunter'),
    ('Security contract', 'Recover Valuables', ActivityType.SECURITY_CONTRACT, 'Recover Valuables'),
    ('Casino Heist', 'The Big Con\nFinale', ActivityType.HEIST_FINALE, 'The Big Con'),
])
@pytest.mark.parametrize('outcome,success', [('MISSION PASSED', True), ('MISSION FAILED', False)])
def test_first_clear_terminal_identity_refines_existing_activity_before_persistence(app, clock, first, terminal_title, kind, name, outcome, success):
    from datetime import timedelta
    app._data.current_money = 1000
    frame(app, first, brightness=100)
    current = app._activity_tracker.current_activity
    started, baseline = app._data.mission_start_time, app._data.mission_start_money
    assert current is not None
    tracked_started = current.started_at
    clock.value += timedelta(seconds=90)
    app._data.current_money = 2000
    observed = frame(app, center_text=outcome + '\n' + terminal_title)
    assert observed.mission.outcome == ('complete' if success else 'failed')
    (tracked,) = app._activity_tracker.completed_activities
    assert tracked is current and tracked.activity_type == kind and tracked.name == name
    assert tracked.started_at == tracked_started
    (row,) = app._repository.export_session_data(app._data.db_session_id)['activities']
    assert row['name'] == name and row['type'] == kind.name
    assert row['earnings'] == (2000 - baseline if success else 0)
    assert row['duration_seconds'] == 90
    assert started == clock.value - timedelta(seconds=90)
    assert row['success'] is success
    frame(app, center_text=outcome + '\n' + terminal_title)
    assert len(app._repository.export_session_data(app._data.db_session_id)['activities']) == 1
    assert app._activity_tracker.current_activity is None


@pytest.mark.parametrize('outcome', ['MISSION PASSED', 'MISSION FAILED'])
def test_terminal_identity_does_not_replace_confirmed_incompatible_name(app, outcome):
    frame(app, 'Headhunter')
    frame(app, center_text=outcome + '\nSightseer')
    (row,) = app._repository.export_session_data(app._data.db_session_id)['activities']
    assert row['name'] == 'Headhunter'


@pytest.mark.parametrize('state,text', [
    (GameState.MISSION_COMPLETE, 'MISSION FAILED'),
    (GameState.MISSION_FAILED, 'MISSION PASSED'),
    (GameState.MISSION_COMPLETE, 'MISSION PASSED\nMISSION FAILED'),
    (GameState.MISSION_FAILED, 'MISSION PASSED\nMISSION FAILED'),
])
def test_direct_mismatched_or_conflicting_result_evidence_cannot_complete(app, state, text):
    frame(app, 'Headhunter')
    current = app._activity_tracker.current_activity
    app._process_state(StateDetectionResult(state, 0.99, 'conflicting consumer result', mission_text=text), CaptureResult())
    assert app._activity_tracker.current_activity is current
    assert app._repository.export_session_data(app._data.db_session_id)['activities'] == []


def test_valid_result_with_ambiguous_names_finishes_without_invented_identity(app):
    frame(app, 'Go to the location', brightness=100)
    frame(app, center_text='MISSION PASSED\nHeadhunter\nSightseer')
    (row,) = app._repository.export_session_data(app._data.db_session_id)['activities']
    assert row['type'] == 'UNKNOWN'
    assert row['name'] == 'Go to the location'
