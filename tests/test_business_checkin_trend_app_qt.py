"""Real Businesses → saved observations → all-matching trend → exact export."""

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native check-in trend workflow', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QDate, QPoint, QRect

from src.app import AppState
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
    select_business, set_observation,
)
from tests.test_business_checkin_comparison_app_qt import stored_manual_rows


def apply_filters(board, query, first=QDate(2026, 10, 3), last=QDate(2026, 10, 4)):
    board._history_note_filter.setText(query)
    board._history_from_date.setDate(first)
    board._history_until_date.setDate(last)
    board._history_from_enabled.setChecked(True)
    board._history_until_enabled.setChecked(True)
    board._history_apply_button.click()


def open_trend(board, qt):
    assert hasattr(board, '_trend_button'), 'Manual check-ins need an all-matching history trend'
    assert board._trend_button.isEnabled()
    board._trend_button.click()
    qt.processEvents()
    child = board._trend_dialog
    assert child is not None and child.isVisible() and not child.isModal()
    return child


def live_state(manager):
    return copy.deepcopy((manager._data, manager._optimizer._business_states,
                          manager._optimizer._cooldowns, manager.state))


def test_record_filter_page_trend_and_export_preserve_live_and_saved_state(app_window, qt, monkeypatch, tmp_path):
    import src.database.repository as repository_module

    manager, window, repository, alpha, beta = app_window
    board = open_board(window, qt)
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 4, 12, 0, 0, 123456, tzinfo=timezone.utc))
    editor = editor_for(board, alpha, qt)
    first_note = 'TREND 50%_\\ <b>雪</b>\nFirst complete personal note'
    set_observation(editor, stock='0', supply='', value=str(2**63 - 1), note=first_note)
    editor._save_button.click()
    qt.processEvents()
    first = repository.get_business_checkin_history(alpha, 'bunker').rows[0]
    expected = [first]
    for number in range(29):
        expected.append(repository.save_business_checkin(alpha, 'bunker', note=f'trend 50%_\\ note-only {number}'))
    # A rollback does not reverse insertion order or become a negative elapsed rate.
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 11, 0, 0, 654321, tzinfo=timezone.utc))
    editor = editor_for(board, alpha, qt)
    last_note = 'trend 50%_\\ <i>literal</i>\nLast complete personal note'
    set_observation(editor, stock='75', supply='0', value='0', note=last_note)
    editor._save_button.click()
    qt.processEvents()
    expected.append(repository.get_business_checkin_history(alpha, 'bunker').rows[0])
    repository.save_business_checkin(alpha, 'bunker', note='Outside the literal note query')
    repository.save_business_checkin(alpha, 'acid_lab', note='trend 50%_\\ other business')
    repository.save_business_checkin(beta, 'bunker', note='trend 50%_\\ other character')
    board.refresh()
    before = legacy_rows(repository), stored_manual_rows(repository), live_state(manager)
    settings = copy.deepcopy(manager._settings._config), manager._settings._config_path.read_bytes()
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Trend started capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *_a, **_k: pytest.fail('Trend changed live readings'))
    apply_filters(board, 'TREND 50%_\\')
    assert board._page.total == 31 and len(board._page.rows) == 25
    board._next_button.click()
    assert board._page.offset == 25 and len(board._page.rows) == 6
    board._history_table.clearSelection()
    assert board._baseline_id is None
    child = open_trend(board, qt)
    snapshot = child._trend
    assert snapshot.rows == tuple(expected)
    assert snapshot.rows[0].recorded_at > snapshot.rows[-1].recorded_at
    assert snapshot.filters.note_query == 'TREND 50%_\\'
    assert child._rows_table.rowCount() == 31
    metrics = {item.field: item for item in snapshot.metrics}
    assert (metrics['stock_percent'].known_count, metrics['stock_percent'].unknown_count) == (2, 29)
    assert (metrics['stock_percent'].first_value, metrics['stock_percent'].last_value, metrics['stock_percent'].change) == (0, 75, 75)
    assert metrics['supply_percent'].first_value is None and metrics['supply_percent'].change is None
    assert metrics['stock_value'].change == -(2**63 - 1)
    assert metrics['stock_value'].plot_status == 'precision_limit'
    child._rows_table.selectRow(0)
    assert child._note_edit.toPlainText() == first_note
    child._rows_table.selectRow(30)
    assert child._note_edit.toPlainText() == last_note
    report = snapshot.to_report()
    assert report['kind'] == 'manual_business_checkin_trend'
    assert report['rows'] == [row.to_dict() for row in expected]
    path = tmp_path / 'complete-history-trend.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_a, **_k: (str(path), 'JSON'))
    child._export_button.click()
    assert json.loads(path.read_text(encoding='utf-8')) == report
    assert (legacy_rows(repository), stored_manual_rows(repository), live_state(manager)) == before
    assert (manager._settings._config, manager._settings._config_path.read_bytes()) == settings
    assert manager.state == AppState.STOPPED


def test_open_trend_and_export_keep_original_capture_during_parent_and_database_changes(
    app_window, qt, monkeypatch, tmp_path,
):
    _manager, window, repository, alpha, beta = app_window
    first = repository.save_business_checkin(alpha, 'bunker', stock_percent=10, stock_value=50, note='Original first')
    last = repository.save_business_checkin(alpha, 'bunker', stock_percent=30, stock_value=75, note='Original last')
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    child = open_trend(board, qt)
    capture = child._trend.to_report()
    assert [row['id'] for row in capture['rows']] == [first.id, last.id]
    monkeypatch.setattr(repository, 'get_business_checkin_trend', lambda *_a, **_k: pytest.fail('Captured trend was reread'))
    destination = tmp_path / 'fixed-trend.json'

    def choose(*_args, **_kwargs):
        assert not board.close(), 'An active trend export must block parent close before discarding drafts'
        with sqlite3.connect(repository._db_path) as database:
            database.execute('UPDATE manual_business_checkins SET stock_percent=99, note=? WHERE id=?', ('Changed after capture', first.id))
        repository.save_business_checkin(alpha, 'bunker', stock_percent=100, note='Newer saved record')
        board._history_note_filter.setText('Pending changed filter')
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(destination), 'JSON'

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    child._export_button.click()
    assert json.loads(destination.read_text()) == capture
    assert child._trend.to_report() == capture and child.isVisible()
    assert board._character_combo.currentData() == beta
    assert child.close()
    qt.processEvents()
    assert board._trend_dialog is None


def test_oversized_history_is_refused_then_applied_filter_opens_complete_small_selection(app_window, qt):
    _manager, window, repository, alpha, _beta = app_window
    first = repository.save_business_checkin(alpha, 'bunker', stock_percent=0, note='Keep only this record')
    with sqlite3.connect(repository._db_path) as database:
        database.executemany(
            'INSERT INTO manual_business_checkins(character_id,business_id,recorded_at,stock_percent,supply_percent,stock_value,note) VALUES(?,?,?,?,?,?,?)',
            [(alpha, 'bunker', first.recorded_at.isoformat(), None, None, None, f'Other record {number}') for number in range(1000)],
        )
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    assert board._page.total == 1001
    assert hasattr(board, '_trend_button')
    board._trend_button.click()
    assert board._trend_dialog is None
    assert '1,000' in board._status_label.text() or '1000' in board._status_label.text()
    board._history_note_filter.setText('KEEP ONLY THIS')
    assert not board._trend_button.isEnabled()
    board._history_apply_button.click()
    child = open_trend(board, qt)
    assert child._trend.rows == (first,)
    assert child._rows_table.rowCount() == 1
    assert all(item.change is None for item in child._trend.metrics)


def test_bulk_saved_live_observations_feed_trend_without_changing_live_state(app_window, qt, monkeypatch, tmp_path):
    import src.database.repository as repository_module

    manager, window, repository, alpha, _beta = app_window
    manager.clear_business_readings()
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    for index, (stock, supply, value) in enumerate([(0, 100, 0), (25, 40, 12000)]):
        monkeypatch.setattr(repository_module, 'utc_now', lambda index=index: datetime(2026, 10, 3, 12 + index, tzinfo=timezone.utc))
        manager.set_manual_business_reading('bunker', stock_percent=stock, supply_percent=supply, value=value)
        manager.set_manual_business_reading('nightclub', stock_percent=5, value=100)
        board.live_batch_button.click()
        batch = board._batch_editor
        assert batch is not None
        batch.select_all_button.click()
        batch.note_edit.setPlainText('Captured for trend')
        batch.save_button.click()
        qt.processEvents()
        assert batch._committed
    before = legacy_rows(repository), stored_manual_rows(repository), live_state(manager)
    select_business(board, 'bunker')
    child = open_trend(board, qt)
    assert [row.stock_percent for row in child._trend.rows] == [0, 25]
    assert [row.supply_percent for row in child._trend.rows] == [100, 40]
    assert [row.stock_value for row in child._trend.rows] == [0, 12000]
    metrics = {item.field: item for item in child._trend.metrics}
    assert [metrics[field].change for field in ('stock_percent', 'supply_percent', 'stock_value')] == [25, -60, 12000]
    destination = tmp_path / 'saved-bulk-history.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_a, **_k: (str(destination), 'JSON'))
    child._export_button.click()
    assert json.loads(destination.read_text())['rows'] == [row.to_dict() for row in child._trend.rows]
    assert (legacy_rows(repository), stored_manual_rows(repository), live_state(manager)) == before


@pytest.mark.parametrize('long_content', [False, True])
def test_actual_main_window_trend_fits_normal_and_small_sizes_with_literal_context(app_window, qt, long_content):
    _manager, window, repository, alpha, _beta = app_window
    note = ('Full <b>literal 雪</b> note\n' * 100)[:2000] if long_content else 'Full <b>literal 雪</b> note'
    values = [(0, 100, 0), (None, None, None), (25, 40, 12000), (75, 0, 24000)]
    for stock, supply, value in values:
        repository.save_business_checkin(alpha, 'bunker', stock_percent=stock, supply_percent=supply, stock_value=value, note=note)
    business = 'bunker'
    if long_content:
        business = 'legacy_' + 'x' * 43
        name = ('Long <b>literal</b> ' * 220)[:4096]
        with sqlite3.connect(repository._db_path) as database:
            database.execute('UPDATE characters SET name=? WHERE id=?', (name, alpha))
            database.execute('UPDATE manual_business_checkins SET business_id=? WHERE character_id=?', (business, alpha))
    board = open_board(window, qt)
    board.resize(1000, 700)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board, business)
    qt.processEvents()
    assert (board.width(), board.height()) == (1000, 700)
    assert board.rect().contains(QRect(board._trend_button.mapTo(board, QPoint(0, 0)), board._trend_button.size()))
    child = open_trend(board, qt)
    shots = os.environ.get('GTA_CHECKIN_TREND_APP_SCREENSHOTS')
    for width, height in [(900, 720), (480, 440)]:
        child.resize(width, height)
        qt.processEvents()
        assert (child.width(), child.height()) == (width, height)
        assert child.rect().contains(QRect(child._export_button.mapTo(child, QPoint(0, 0)), child._export_button.size()))
        child._rows_table.selectRow(0)
        assert child._note_edit.toPlainText() == note
        if shots:
            directory = Path(shots)
            directory.mkdir(parents=True, exist_ok=True)
            name = f'trend-main-{"long" if long_content else "normal"}-{width}x{height}.png'
            assert child.grab().save(str(directory / name))
    child._scroll_area.verticalScrollBar().setValue(child._scroll_area.verticalScrollBar().maximum())
    qt.processEvents()
    if shots:
        assert child.grab().save(str(Path(shots) / f'trend-main-{"long" if long_content else "normal"}-scrolled.png'))
