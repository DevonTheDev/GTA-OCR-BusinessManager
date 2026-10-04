"""Real target controls, synthetic OCR and independent saved manual observations."""

import copy
import logging
import os
import sqlite3
import threading
from types import SimpleNamespace

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native business target integration', allow_module_level=True)

pytest.importorskip('PyQt6.QtWidgets')

from src.app import AppState
from src.capture.regions import ScreenRegions
from src.detection.parsers.business_parser import BusinessType
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
    set_observation,
)


def choose(panel, business_id):
    assert hasattr(panel, '_business_target_combo'), 'Businesses needs an explicit screen target'
    combo = panel._business_target_combo
    index = combo.findData(business_id)
    assert index >= 0
    combo.setCurrentIndex(index)


def saved_rows(repository):
    with sqlite3.connect(repository._db_path) as database:
        return {
            'checkins': database.execute('SELECT * FROM manual_business_checkins ORDER BY id').fetchall(),
            'pins': database.execute('SELECT * FROM manual_business_pins ORDER BY character_id, business_id').fetchall(),
        }


def synthetic_business_io(manager, monkeypatch, *, text=None, recognize=None):
    regions = ScreenRegions()
    parts = text or ['Meth Lab Stock: 5/10', 'Supplies: 3/4', 'Value: $123,456']
    contents = dict(zip(regions.get_business_regions().values(), parts))
    calls = []

    def capture(region, *, wait_for_rate):
        calls.append((region, wait_for_rate))
        return contents[region]

    monkeypatch.setattr(manager, '_capture', SimpleNamespace(
        regions=regions, capture_region=capture, close=lambda: None,
    ))
    monkeypatch.setattr(manager, '_ocr', SimpleNamespace(
        is_available=True,
        recognize_preprocessed=recognize or (lambda image, **kwargs: SimpleNamespace(text=image)),
    ))
    return calls


def test_real_selector_assigns_conflicting_readings_and_keeps_provenance_after_automatic(
    app_window, qt, monkeypatch, caplog,
):
    manager, window, repository, alpha, _beta = app_window
    repository.save_business_checkin(alpha, 'bunker', stock_percent=12, note='Manual observation')
    records = legacy_rows(repository), saved_rows(repository)
    settings = manager._settings._config_path.read_bytes()
    original = copy.deepcopy(manager._data.business_states)
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Selection started capture'))
    panel = window._business_panel
    choose(panel, 'acid_lab')
    assert manager.business_screen_target == 'acid_lab'
    assert manager._data.business_states == original
    assert manager._optimizer._business_states == {}
    assert (legacy_rows(repository), saved_rows(repository)) == records
    assert manager._settings._config_path.read_bytes() == settings

    calls = synthetic_business_io(manager, monkeypatch)
    caplog.set_level(logging.INFO)
    manager._process_business_computer()
    panel._update_display()
    qt.processEvents()
    assigned = copy.deepcopy(manager.get_business_state('acid_lab'))
    assert (assigned['stock'], assigned['supply'], assigned['value']) == (50, 75, 123456)
    assert assigned['identity_source'] == 'selected_target'
    assert manager.get_business_state('meth') is None
    assert manager._optimizer._business_states['acid_lab'].stock_percent == 50
    card = panel._cards['acid_lab']
    assert (card._stock_bar.value(), card._supply_bar.value()) == (50, 75)
    assert 'Selected target' in card._status_label.text()
    assert 'Business detected: ACID_LAB' not in caplog.text
    assert len(calls) == 3 and all(not wait for _, wait in calls)

    choose(panel, None)
    panel._update_display()
    assert manager.business_screen_target is None
    assert manager.get_business_state('acid_lab') == assigned
    assert 'Selected target' in card._status_label.text()
    manager._process_business_computer()
    panel._update_display()
    assert manager.get_business_state('meth')['identity_source'] == 'ocr_text'
    assert 'OCR text match' in panel._cards['meth']._status_label.text()
    assert manager.get_business_state('acid_lab') == assigned
    assert manager.get_business_state('bunker') == original['bunker']
    assert (legacy_rows(repository), saved_rows(repository)) == records
    assert manager._settings._config_path.read_bytes() == settings


def test_saved_manual_checkin_does_not_retarget_or_rewrite_live_observations(app_window, qt):
    manager, window, repository, alpha, _beta = app_window
    choose(window._business_panel, 'acid_lab')
    live = copy.deepcopy(manager._data.business_states)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    legacy = legacy_rows(repository)
    settings = manager._settings._config_path.read_bytes()
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt, 'bunker')
    set_observation(editor, stock='99', supply='0', value='1', note='Separate saved observation')
    editor._save_button.click()
    qt.processEvents()
    row = repository.get_business_checkin_history(alpha, 'bunker').rows[0]
    assert row.stock_percent == 99 and row.note == 'Separate saved observation'
    assert manager.business_screen_target == 'acid_lab'
    assert window._business_panel._business_target_combo.currentData() == 'acid_lab'
    assert manager._data.business_states == live and manager._optimizer._business_states == optimizer
    assert legacy_rows(repository) == legacy
    assert manager._settings._config_path.read_bytes() == settings


def test_real_lifecycle_keeps_preselection_and_pause_then_resets_on_normal_window_refresh(
    app_window, qt, monkeypatch,
):
    import src.app as app_module

    manager, window, repository, _alpha, _beta = app_window
    panel = window._business_panel
    choose(panel, 'acid_lab')
    monkeypatch.setattr(app_module, 'get_repository', lambda: repository)

    def initialize():
        manager._initialize_database()
        manager._session_tracker.start_session()

    monkeypatch.setattr(manager, '_initialize_components', initialize)
    monkeypatch.setattr(manager, '_capture_loop', lambda: manager._stop_event.wait(5))
    assert manager.start() and manager.business_screen_target == 'acid_lab'
    manager.pause()
    assert manager.state == AppState.PAUSED and manager.business_screen_target == 'acid_lab'
    window._tabs.setCurrentWidget(window._dashboard)
    window._tabs.setCurrentWidget(panel)
    qt.processEvents()
    assert panel._business_target_combo.currentData() == 'acid_lab'
    manager.resume()
    assert manager.state == AppState.RUNNING and manager.business_screen_target == 'acid_lab'
    manager.stop()
    assert manager.state == AppState.STOPPED and manager.business_screen_target is None
    # Exercise the same refresh the existing MainWindow timer calls. It should
    # mirror Stop without issuing a second setter call from a combo signal.
    monkeypatch.setattr(manager, 'set_business_screen_target', lambda *a: pytest.fail('Refresh echoed the target setter'))
    window._update_ui()
    assert panel._business_target_combo.currentData() is None


@pytest.mark.parametrize('aba', [False, True])
def test_ui_selection_change_discards_an_entire_inflight_ocr_batch(app_window, qt, monkeypatch, aba):
    manager, window, repository, _alpha, _beta = app_window
    panel = window._business_panel
    choose(panel, 'acid_lab')
    original = copy.deepcopy(manager._data.business_states)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    records = legacy_rows(repository), saved_rows(repository)
    parser_readings = copy.deepcopy(manager._business_parser._last_readings)
    entered, release = threading.Event(), threading.Event()
    first = True
    failures = []

    def recognize(image, **kwargs):
        nonlocal first
        if first:
            first = False
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Synthetic OCR was not released')
        return SimpleNamespace(text=image)

    synthetic_business_io(manager, monkeypatch, recognize=recognize)

    def process():
        try:
            manager._process_business_computer()
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=process, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        choose(panel, 'bunker')
        if aba:
            choose(panel, 'acid_lab')
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not failures
    assert manager._data.business_states == original
    assert manager._optimizer._business_states == optimizer
    assert manager._business_parser._last_readings == parser_readings
    assert (legacy_rows(repository), saved_rows(repository)) == records

    target = 'acid_lab' if aba else 'bunker'
    manager._process_business_computer()
    panel._update_display()
    qt.processEvents()
    state = manager.get_business_state(target)
    assert state['stock'] == 50 and state['identity_source'] == 'selected_target'
    assert manager._business_parser.get_last_reading(BusinessType[target.upper()]).stock_level == 50
    assert 'Selected target' in panel._cards[target]._status_label.text()
    assert (legacy_rows(repository), saved_rows(repository)) == records
