"""Pinned manual-business navigation through the real MainWindow and SQLite."""

import copy
import json
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for manual pin app integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import Qt

from src.app import AppState
from src.database.repository import Repository
from tests.test_business_checkins_app_qt import open_board, select_business, set_observation
from tests.test_saved_character_app_qt import (
    create_from_board, empty_app as empty_app, qt as qt, table_rows,
)


def business_order(board):
    return [board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole)
            for index in range(board._businesses_table.rowCount())]


def pin_selected(board, business_id, qt):
    select_business(board, business_id)
    assert hasattr(board, '_pin_button'), 'manual board needs a per-character Pin action'
    assert board._pin_button.isEnabled()
    board._pin_button.click()
    qt.processEvents()


def test_empty_install_owner_can_pin_reopen_record_and_export_all_observations(empty_app, qt, tmp_path, monkeypatch):
    manager, window, repository, settings = empty_app
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Pins started capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *a, **k: pytest.fail('Pins changed live business state'))
    board = open_board(window, qt)
    owner = create_from_board(board, qt, 'My manual character').id
    original_order = business_order(board)
    select_business(board, 'bunker')
    board._record_button.click()
    set_observation(board._editor, stock='0', supply='', value='0', note='First observation')
    board._editor._save_button.click()
    repository.save_business_checkin(owner, 'meth', stock_percent=23, note='Unpinned history stays visible')
    board.refresh()
    tables = ('characters', 'sessions', 'activities', 'earnings', 'business_snapshots', 'manual_business_checkins')
    before = table_rows(repository, tables)
    config = copy.deepcopy(settings._config)
    config_bytes = settings._config_path.read_bytes()
    data = copy.deepcopy(manager._data)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    monkeypatch.setattr(settings, 'set', lambda *a, **k: pytest.fail('Pins changed settings'))
    monkeypatch.setattr(repository, 'set_active_character', lambda *a: pytest.fail('Pins changed active character'))

    pin_selected(board, 'acid_lab', qt)
    pin_selected(board, 'bunker', qt)
    assert set(repository.get_manual_business_pins(owner).business_ids) == {'bunker', 'acid_lab'}
    assert business_order(board)[:2] == ['bunker', 'acid_lab']
    assert set(business_order(board)) == set(original_order)
    assert board._selected_business_id() == 'bunker'
    assert table_rows(repository, tables) == before
    assert manager._data == data and manager._optimizer._business_states == optimizer
    assert settings._config == config and settings._config_path.read_bytes() == config_bytes
    assert manager.state == AppState.STOPPED
    assert board.close()
    qt.processEvents()
    board = open_board(window, qt)
    assert business_order(board)[:2] == ['bunker', 'acid_lab']
    assert set(business_order(board)) == set(original_order)

    # Recording into a pinned business remains the ordinary append workflow.
    select_business(board, 'acid_lab')
    board._record_button.click()
    set_observation(board._editor, stock='', supply='0', value='', note='Pinned business, unknown stock')
    board._editor._save_button.click()
    accepted = board._board.to_report()
    assert {row['business_id'] for row in accepted['rows']} == {'bunker', 'acid_lab', 'meth'}
    destination = tmp_path / 'all-observations.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(destination), 'JSON'))
    board._export_board_button.click()
    assert json.loads(destination.read_text(encoding='utf-8')) == accepted
    reopened = Repository(repository._db_path)
    try:
        assert set(reopened.get_manual_business_pins(owner).business_ids) == {'bunker', 'acid_lab'}
        assert {row.business_id for row in reopened.get_business_checkin_board(owner).rows} == {'bunker', 'acid_lab', 'meth'}
    finally:
        reopened.close()
        reopened._session_factory.kw['bind'].dispose()


def test_pins_follow_saved_owner_across_open_draft_and_real_start_stop(empty_app, qt, monkeypatch):
    manager, window, repository, settings = empty_app
    board = open_board(window, qt)
    alpha = create_from_board(board, qt, 'Alpha').id
    pin_selected(board, 'meth', qt)
    beta = create_from_board(board, qt, 'Beta').id
    assert repository.get_manual_business_pins(beta).business_ids == ()
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board, 'bunker')
    board._record_button.click()
    draft = board._editor
    set_observation(draft, stock='25', supply='', value='', note='Fixed Alpha draft')
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    pin_selected(board, 'acid_lab', qt)
    assert board._editor is draft
    draft._save_button.click()
    assert repository.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repository.get_business_checkin_history(beta, 'bunker').total == 0
    settings.set('general.character_name', 'Alpha')

    def initialize_without_capture():
        manager._initialize_database()
        manager._session_tracker.start_session(start_money=0)

    monkeypatch.setattr(manager, '_initialize_components', initialize_without_capture)
    monkeypatch.setattr(manager, '_capture_loop', lambda: manager._stop_event.wait(3))
    assert manager.start()
    assert manager._data.character_id == alpha
    before = table_rows(repository, ('characters', 'sessions', 'activities', 'earnings', 'business_snapshots', 'manual_business_checkins'))
    pin_selected(board, 'bunker', qt)
    assert table_rows(repository, tuple(before)) == before
    assert repository.get_manual_business_pins(alpha).business_ids == ('meth',)
    assert set(repository.get_manual_business_pins(beta).business_ids) == {'acid_lab', 'bunker'}
    assert manager._data.character_id == alpha and board._character_combo.currentData() == beta
    manager.stop()
    window._update_ui()
    board.refresh()
    assert manager.state == AppState.STOPPED
    assert board._character_combo.currentData() == beta
    assert business_order(board)[:2] == ['bunker', 'acid_lab']


def test_export_keeps_full_captured_owner_and_blocks_pin_mutation_during_chooser(empty_app, qt, tmp_path, monkeypatch):
    _manager, window, repository, _settings = empty_app
    board = open_board(window, qt)
    alpha = create_from_board(board, qt, 'Alpha').id
    beta = create_from_board(board, qt, 'Beta').id
    repository.save_business_checkin(alpha, 'bunker', stock_value=0, note='Pinned row')
    repository.save_business_checkin(alpha, 'meth', stock_value=12, note='Unpinned row')
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    pin_selected(board, 'bunker', qt)
    accepted = board._board.to_report()
    destination = tmp_path / 'captured-alpha.json'

    def choose(*args, **kwargs):
        assert not board._pin_button.isEnabled()
        board._set_selected_business_pin()
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        board._set_selected_business_pin()
        return str(destination), 'JSON'

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    board._export_board_button.click()
    assert json.loads(destination.read_text(encoding='utf-8')) == accepted
    assert len(accepted['rows']) == 2
    assert repository.get_manual_business_pins(alpha).business_ids == ('bunker',)
    assert repository.get_manual_business_pins(beta).business_ids == ()
    assert board._character_combo.currentData() == beta
