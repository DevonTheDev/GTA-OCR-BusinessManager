"""MainWindow recording, cross-page selection, pair review and captured export."""

import copy
from datetime import datetime, timezone
import json
import os
import sqlite3

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native check-in comparison integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QPoint, QRect

from src.app import AppState
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
    select_business, set_observation,
)


def stored_manual_rows(repository):
    with sqlite3.connect(repository._db_path) as database:
        return {
            'checkins': database.execute('SELECT * FROM manual_business_checkins ORDER BY id').fetchall(),
            'pins': database.execute('SELECT * FROM manual_business_pins ORDER BY character_id, business_id').fetchall(),
        }


def choose_history(board, checkin_id):
    index = next(index for index, row in enumerate(board._page.rows) if row.id == checkin_id)
    board._history_table.selectRow(index)


def test_record_page_filter_compare_and_export_leave_tracking_and_saved_data_unchanged(app_window, qt, tmp_path, monkeypatch):
    import src.database.repository as repository_module
    manager, window, repository, alpha, beta = app_window
    board = open_board(window, qt)
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 4, 12, 0, 0, 123456, tzinfo=timezone.utc))
    editor = editor_for(board, alpha, qt)
    set_observation(editor, stock='0', supply='', value='9223372036854775807', note='Baseline A <b>雪</b>\nMy recorded observation')
    editor._save_button.click()
    qt.processEvents()
    baseline = repository.get_business_checkin_history(alpha, 'bunker').rows[0]
    for number in range(29):
        repository.save_business_checkin(alpha, 'bunker', note=f'Intermediate observation {number}')
    # Save order and recorded wall clock can disagree; comparison keeps explicit roles.
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 11, tzinfo=timezone.utc))
    editor = editor_for(board, alpha, qt)
    set_observation(editor, stock='75', supply='0', value='0', note='Comparison B <i>literal</i>\nSecond observation')
    editor._save_button.click()
    qt.processEvents()
    comparison = repository.get_business_checkin_history(alpha, 'bunker').rows[0]
    before = legacy_rows(repository), stored_manual_rows(repository)
    live, optimizer = copy.deepcopy(manager._data), copy.deepcopy(manager._optimizer._business_states)
    settings = copy.deepcopy(manager._settings._config)
    settings_bytes = manager._settings._config_path.read_bytes()
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Comparison started capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *a, **k: pytest.fail('Comparison changed live business state'))
    board._next_button.click()
    choose_history(board, baseline.id)
    board._baseline_button.click()
    assert board._baseline_id == baseline.id
    board._history_note_filter.setText('Comparison B')
    assert board._page is None and not board._compare_button.isEnabled()
    assert board._baseline_id == baseline.id
    board._history_apply_button.click()
    assert board._page.rows == (comparison,) and board._page.offset == 0
    board._compare_button.click()
    qt.processEvents()
    child = board._comparison_dialog
    assert child is not None and child.isVisible() and not child.isModal()
    captured = child._comparison
    assert captured.baseline == baseline and captured.comparison == comparison
    assert captured.differences.stock_percent == 75
    assert captured.differences.supply_percent is None
    assert captured.differences.stock_value == -9223372036854775807
    assert captured.comparison.recorded_at < captured.baseline.recorded_at
    report = captured.to_report()
    destination = tmp_path / 'reviewed-observations.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(destination), 'JSON'))
    child._export_button.click()
    assert json.loads(destination.read_text(encoding='utf-8')) == report
    assert legacy_rows(repository) == before[0] and stored_manual_rows(repository) == before[1]
    assert manager._data == live and manager._optimizer._business_states == optimizer
    assert manager._settings._config == settings and manager._settings._config_path.read_bytes() == settings_bytes
    assert manager.state == AppState.STOPPED
    assert child.close()
    qt.processEvents()
    assert board._comparison_dialog is None
    assert board.close()
    qt.processEvents()
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    assert board._baseline_id is None and board._page.total == 31
    assert repository.get_business_checkin_history(beta, 'bunker').total == 0


def test_compare_rereads_pinned_id_once_then_export_retains_pair_through_navigation(app_window, qt, tmp_path, monkeypatch):
    _manager, window, repository, alpha, beta = app_window
    baseline = repository.save_business_checkin(alpha, 'bunker', stock_percent=10, stock_value=50, note='Original A')
    comparison = repository.save_business_checkin(alpha, 'bunker', stock_percent=30, stock_value=75, note='B')
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    choose_history(board, baseline.id)
    board._baseline_button.click()
    with sqlite3.connect(repository._db_path) as database:
        database.execute('UPDATE manual_business_checkins SET stock_percent=20, note=? WHERE id=?', ('Changed before Compare', baseline.id))
    choose_history(board, comparison.id)
    board._compare_button.click()
    child = board._comparison_dialog
    assert child._comparison.baseline.stock_percent == 20
    assert child._comparison.baseline.note == 'Changed before Compare'
    assert child._comparison.differences.stock_percent == 10
    captured = child._comparison.to_report()
    destination = tmp_path / 'fixed-pair.json'

    def choose(*_args, **_kwargs):
        assert not board.close(), 'An active child export keeps the parent open'
        with sqlite3.connect(repository._db_path) as database:
            database.execute('UPDATE manual_business_checkins SET stock_percent=99 WHERE id=?', (baseline.id,))
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        assert board._baseline_id is None
        return str(destination), 'JSON'

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    child._export_button.click()
    assert json.loads(destination.read_text()) == captured
    assert child._comparison.to_report() == captured
    assert board._character_combo.currentData() == beta
    assert child.isVisible()


@pytest.mark.parametrize('long_content', [False, True])
def test_comparison_board_and_dialog_fit_normal_window_with_exact_values(app_window, qt, monkeypatch, long_content):
    _manager, window, repository, alpha, _beta = app_window
    baseline_note = ('Before <b>literal 雪</b>\n' * 100)[:2000] if long_content else 'Before <b>literal 雪</b>\nFull baseline note'
    comparison_note = ('After <i>literal 🦙</i>\n' * 100)[:2000] if long_content else 'After <i>literal</i>\nFull comparison note'
    baseline = repository.save_business_checkin(alpha, 'bunker', stock_percent=10, supply_percent=90,
                                               stock_value=9223372036854775807, note=baseline_note)
    comparison = repository.save_business_checkin(alpha, 'bunker', stock_percent=80, supply_percent=0,
                                                 stock_value=0, note=comparison_note)
    business = 'bunker'
    if long_content:
        business = 'legacy_' + 'x' * 43
        name = ('Long <b>literal</b> ' * 250)[:4096]
        with sqlite3.connect(repository._db_path) as database:
            database.execute('UPDATE characters SET name=? WHERE id=?', (name, alpha))
            database.execute('UPDATE manual_business_checkins SET business_id=? WHERE character_id=?', (business, alpha))
    board = open_board(window, qt)
    board.resize(1000, 700)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board, business)
    choose_history(board, baseline.id)
    board._baseline_button.click()
    choose_history(board, comparison.id)
    qt.processEvents()
    assert board.size().width() == 1000 and board.size().height() == 700
    for widget in (board._baseline_button, board._clear_baseline_button, board._compare_button,
                   board._history_apply_button, board._export_history_button):
        assert widget.isVisible()
        assert board.rect().contains(QRect(widget.mapTo(board, QPoint(0, 0)), widget.size()))
    if not long_content and os.environ.get('GTA_CHECKIN_COMPARISON_BOARD_SCREENSHOT'):
        assert board.grab().save(os.environ['GTA_CHECKIN_COMPARISON_BOARD_SCREENSHOT'])
    board._compare_button.click()
    child = board._comparison_dialog
    child.resize(900, 700)
    qt.processEvents()
    assert child.size().width() == 900 and child.size().height() == 700
    assert child.rect().contains(QRect(child._export_button.mapTo(child, QPoint(0, 0)), child._export_button.size()))
    assert child._baseline_note.toPlainText() == baseline_note
    assert child._comparison_note.toPlainText() == comparison_note
    if long_content:
        assert name in child._context_label.text() and business in child._context_label.text()
        child._scroll_area.verticalScrollBar().setValue(child._scroll_area.verticalScrollBar().maximum())
        qt.processEvents()
    screenshot = 'GTA_CHECKIN_COMPARISON_LONG_SCREENSHOT' if long_content else 'GTA_CHECKIN_COMPARISON_SCREENSHOT'
    if os.environ.get(screenshot):
        assert child.grab().save(os.environ[screenshot])
