"""Forget live observations without resetting capture, accounting or saved plans."""

import copy
import threading
from datetime import datetime, timedelta

import pytest

from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from src.optimization.optimizer import Optimizer
from tests.test_business_screen_target import SyntheticBusinessCapture, business_snapshot


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / 'settings.yaml'))
    assert 'Error processing business computer:' not in caplog.text
    assert 'Capture cycle error:' not in caplog.text


def seed(manager):
    for business_id in ('bunker', 'agency'):
        manager.update_business_state(business_id, 95, 0, 123456)
        manager._business_parser.parse(
            'Stock: 95% Supplies: 0% Value: $123,456',
            business_hint=BusinessType[business_id.upper()],
        )


def test_parser_can_forget_all_readings_and_accept_a_new_one():
    parser = BusinessParser()
    old = parser.parse('Bunker Stock: 95%')
    parser.parse('Agency Stock: 75%')
    parser.clear_readings()
    assert parser.get_all_last_readings() == {}
    assert parser.get_last_reading(BusinessType.BUNKER) is None
    assert old.stock_level == 95
    new = parser.parse('Bunker Stock: 25%')
    assert parser.get_last_reading(BusinessType.BUNKER) is new
    assert new.stock_level == 25


def test_optimizer_forgets_observations_but_retains_cooldowns_and_schedule():
    optimizer = Optimizer(solo_mode=False)
    optimizer.update_business_state('bunker', 95, 0, 123456)
    optimizer.set_cooldown('payphone_hit', 20)
    action = optimizer._scheduler.schedule_check('bunker', datetime.now() + timedelta(hours=1))
    cooldowns = copy.deepcopy(optimizer._cooldowns)
    priority_calculator, scheduler = optimizer._priority_calc, optimizer._scheduler
    assert any(rec.business_type == 'bunker' for rec in optimizer.get_recommendations())
    optimizer.clear_business_states()
    assert optimizer._business_states == {}
    assert optimizer._cooldowns == cooldowns and optimizer.is_on_cooldown('payphone_hit')
    assert optimizer._scheduler is scheduler and optimizer._priority_calc is priority_calculator
    assert optimizer._scheduler._scheduled == [action]
    assert optimizer._solo_mode is False
    assert all(rec.business_type is None for rec in optimizer.get_recommendations())
    optimizer.update_business_state('bunker', 99, 10, 42)
    assert any(rec.business_type == 'bunker' for rec in optimizer.get_recommendations())


@pytest.mark.parametrize('state', list(AppState))
@pytest.mark.parametrize('target', [None, 'agency'])
def test_clear_only_live_observations_in_every_app_state(manager, state, target, monkeypatch):
    seed(manager)
    manager._state = state
    manager.set_business_screen_target(target)
    manager._data.current_money = 1000
    manager._data.session_earnings = 250
    manager._data.character_id = 7
    manager._data.db_session_id = 11
    manager._data.current_mission = 'A mission in progress'
    manager._data.mission_start_time = datetime.now()
    manager._session_tracker.start_session(start_money=750)
    before = manager.data
    before.business_states.clear()
    stats = manager.session_stats
    stop_marker = manager._stop_event.is_set()
    generation = manager._business_screen_generation
    settings = copy.deepcopy(manager._settings._config)
    efficiency, breakdown = object(), object()
    manager._cached_efficiency, manager._cached_breakdown = efficiency, breakdown
    components = manager._business_parser, manager._optimizer
    for method in ('start', 'stop', 'pause', 'resume', 'reset_session', '_initialize_database'):
        monkeypatch.setattr(manager, method, lambda: pytest.fail('Clear changed application lifecycle'))

    manager.clear_business_readings()
    assert business_snapshot(manager) == ({}, {}, {})
    assert manager.data == before
    assert manager.state is state and manager.business_screen_target == target
    assert manager._stop_event.is_set() is stop_marker
    assert manager._business_screen_generation == generation + 1
    assert manager.session_stats is stats
    assert manager._settings._config == settings
    assert (manager._business_parser, manager._optimizer) == components
    assert manager._cached_efficiency is efficiency and manager._cached_breakdown is breakdown
    assert all(rec.business_type is None for rec in manager.recommendations)
    manager.clear_business_readings()
    assert business_snapshot(manager) == ({}, {}, {})
    assert manager._business_screen_generation == generation + 2


@pytest.mark.parametrize('boundary,index', [('capture', 1), ('ocr', 1), ('ocr', 3)])
@pytest.mark.parametrize('populated', [False, True])
def test_clear_retires_pending_batch_even_if_empty_then_accepts_fresh_ocr(
    manager, boundary, index, populated,
):
    if populated:
        seed(manager)
    manager.set_business_screen_target('agency')
    entered, release = threading.Event(), threading.Event()

    def pause_boundary(kind, count):
        if (kind, count) == (boundary, index):
            entered.set()
            assert release.wait(3)

    capture = SyntheticBusinessCapture(manager, [
        ('Stock: 95%', 'Supplies: 0%', 'Value: $123,456'),
        ('Stock: 30%', 'Supplies: 80%', 'Value: $42'),
    ], hook=pause_boundary)
    errors = []

    def run():
        try:
            capture.run()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        manager.clear_business_readings()
        assert business_snapshot(manager) == ({}, {}, {})
        assert manager.business_screen_target == 'agency'
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and errors == []
    assert capture.observations[0][1] == ({}, {}, {})
    assert manager.get_business_state('agency')['stock'] == 30
    assert manager._optimizer._business_states['agency'].stock_percent == 30
    assert manager._business_parser.get_last_reading(BusinessType.AGENCY).stock_level == 30
    assert manager.get_business_state('bunker') is None


@pytest.mark.parametrize('mutation', ['clear', 'update'])
def test_recommendation_read_serializes_business_mutation(manager, mutation, monkeypatch):
    seed(manager)
    entered, release, started, finished = (threading.Event() for _ in range(4))
    original = manager._optimizer._priority_calc.calculate_sell_priority

    def blocked_priority(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager._optimizer._priority_calc, 'calculate_sell_priority', blocked_priority)
    results, errors = [], []

    def read():
        try:
            results.append(manager.recommendations)
        except BaseException as error:
            errors.append(error)

    def mutate():
        started.set()
        try:
            if mutation == 'clear':
                manager.clear_business_readings()
            else:
                manager.update_business_state('acid_lab', 99, 0, 999)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    reader = threading.Thread(target=read, daemon=True)
    writer = threading.Thread(target=mutate, daemon=True)
    reader.start()
    try:
        assert entered.wait(2)
        writer.start()
        assert started.wait(2)
        assert not finished.wait(0.05), 'Mutation must wait for the existing recommendation snapshot'
    finally:
        release.set()
        reader.join(3)
        if writer.ident is not None:
            writer.join(3)
    assert not reader.is_alive() and not writer.is_alive() and errors == []
    assert results and any(rec.business_type == 'bunker' for rec in results[0])
    if mutation == 'clear':
        assert all(rec.business_type is None for rec in manager.recommendations)
    else:
        assert any(rec.business_type == 'acid_lab' for rec in manager.recommendations)


@pytest.mark.parametrize('boundary', ['parser', 'optimizer'])
def test_clear_waits_for_already_admitted_publication_then_forgets_it(manager, boundary, monkeypatch):
    manager.set_business_screen_target('agency')
    entered, release, clear_started, clear_done = (threading.Event() for _ in range(4))
    component, method = ((manager._business_parser, 'parse') if boundary == 'parser'
                         else (manager._optimizer, 'update_business_state'))
    original = getattr(component, method)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(component, method, blocked)
    capture = SyntheticBusinessCapture(manager, [('Stock: 95%', 'Supplies: 0%', 'Value: $42')])
    errors = []

    def run():
        try:
            capture.run()
        except BaseException as error:
            errors.append(error)

    def clear():
        clear_started.set()
        try:
            manager.clear_business_readings()
        except BaseException as error:
            errors.append(error)
        finally:
            clear_done.set()

    worker = threading.Thread(target=run, daemon=True)
    clearer = threading.Thread(target=clear, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        clearer.start()
        assert clear_started.wait(2)
        assert not clear_done.wait(0.05)
    finally:
        release.set()
        worker.join(3)
        if clearer.ident is not None:
            clearer.join(3)
    assert not worker.is_alive() and not clearer.is_alive() and errors == []
    assert business_snapshot(manager) == ({}, {}, {})
    assert manager.business_screen_target == 'agency'
