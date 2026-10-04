"""Actual MainWindow → manual check-in → filtered history → captured export."""

import copy
from datetime import datetime, timezone
import json
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native filtered-history integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QDate, QPoint, QRect

from src.app import AppState
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
    select_business, set_observation,
)


def apply_filters(board, query, day=QDate(2026, 10, 3)):
    board._history_note_filter.setText(query)
    board._history_from_date.setDate(day)
    board._history_until_date.setDate(day)
    board._history_from_enabled.setChecked(True)
    board._history_until_enabled.setChecked(True)
    board._history_apply_button.click()


def test_real_business_workflow_records_filters_pages_and_exports_without_tracking(app_window, qt, tmp_path, monkeypatch):
    import src.database.repository as repository_module

    manager, window, repository, alpha, beta = app_window
    before = legacy_rows(repository)
    live = copy.deepcopy(manager._data.business_states)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Search started capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *a, **k: pytest.fail('Search entered live business updates'))
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt)
    set_observation(editor, stock='0', supply='', value='0', note='My PLAN 50%_\\ <b>雪</b>\nFull personal note')
    editor._save_button.click()
    qt.processEvents()
    initial = repository.get_business_checkin_history(alpha, 'bunker').rows[0]
    matches = [initial]
    for index in range(29):
        matches.append(repository.save_business_checkin(alpha, 'bunker', note=f'plan 50%_\\ observation {index}'))
    for _ in range(3):
        repository.save_business_checkin(alpha, 'bunker', note='Does not contain the literal phrase')
    repository.save_business_checkin(alpha, 'acid_lab', note='plan 50%_\\ different business')
    repository.save_business_checkin(beta, 'bunker', note='plan 50%_\\ different owner')
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    repository.save_business_checkin(alpha, 'bunker', note='plan 50%_\\ outside UTC day')
    board.refresh()
    accepted_board = board._board
    board._history_note_filter.setText('PLAN 50%_\\')
    assert board._page is None and board._history_table.rowCount() == 0
    assert board._board is accepted_board and board._record_button.isEnabled()
    assert not board._export_history_button.isEnabled()
    apply_filters(board, 'PLAN 50%_\\')
    assert board._page.total == 30 and len(board._page.rows) == 25
    assert [row.id for row in board._page.rows] == [row.id for row in reversed(matches)][0:25]
    board._next_button.click()
    assert board._page.offset == 25 and len(board._page.rows) == 5
    assert board._page.rows[-1] == initial
    board._history_table.selectRow(4)
    assert board._note_edit.toPlainText() == initial.note
    assert (initial.stock_percent, initial.supply_percent, initial.stock_value) == (0, None, 0)
    captured = board._page.to_report()
    assert captured['filters']['note_query'] == 'PLAN 50%_\\'
    assert captured['filters']['recorded_from'] == captured['filters']['recorded_until'] == '2026-10-03'
    path = tmp_path / 'filtered-page.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(path), 'JSON'))
    board._export_history_button.click()
    assert json.loads(path.read_text()) == captured
    assert captured['pagination']['rows_exported'] == 5
    board._history_clear_button.click()
    assert board._page.offset == 0 and board._page.total == 34
    assert board._page.to_report()['filters'] == {'character_id': alpha, 'business_id': 'bunker'}
    assert legacy_rows(repository) == before
    assert manager._data.business_states == live and manager._optimizer._business_states == optimizer
    assert manager.state == AppState.STOPPED


def test_filtered_export_retains_accepted_owner_filters_and_rows_through_file_chooser(app_window, qt, tmp_path, monkeypatch):
    import src.database.repository as repository_module

    _manager, window, repository, alpha, beta = app_window
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    saved = repository.save_business_checkin(alpha, 'bunker', stock_percent=0, note='Find my original observation')
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    apply_filters(board, 'ORIGINAL')
    captured = board._page.to_report()
    assert board._page.rows == (saved,)
    path = tmp_path / 'fixed-filtered-context.json'

    def choose(*_args, **_kwargs):
        repository.save_business_checkin(alpha, 'bunker', note='Original newer observation')
        board._history_note_filter.setText('A different pending query')
        assert board._page is None
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(path), 'JSON'

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    board._export_history_button.click()
    assert json.loads(path.read_text()) == captured
    assert board._character_combo.currentData() == beta
    assert board._history_note_filter.text() == ''
    assert repository.get_business_checkin_history(alpha, 'bunker').total == 2


def test_filters_remain_reachable_in_the_existing_small_window(app_window, qt, monkeypatch):
    import src.database.repository as repository_module

    _manager, window, repository, alpha, _beta = app_window
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    repository.save_business_checkin(alpha, 'bunker', note='A long literal history note <b>雪</b> ' * 12)
    board = open_board(window, qt)
    board.resize(1000, 700)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    apply_filters(board, 'A long literal history note <b>雪</b>')
    qt.processEvents()
    assert board.size().width() == 1000 and board.size().height() == 700
    for widget in (board._history_note_filter, board._history_from_enabled,
                   board._history_until_enabled, board._history_from_date, board._history_until_date,
                   board._history_apply_button, board._history_clear_button, board._export_history_button):
        assert widget.isVisible()
        bounds = QRect(widget.mapTo(board, QPoint(0, 0)), widget.size())
        assert board.rect().contains(bounds), (type(widget).__name__, bounds, board.rect())
    if os.environ.get('GTA_FILTER_SCREENSHOT'):
        assert board.grab().save(os.environ['GTA_FILTER_SCREENSHOT'])
