"""Opt-in native comparison controls and immutable child ownership over SQLite."""

import importlib
import importlib.util
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for check-in comparison UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from sqlalchemy import text

from src.database.business_checkins import BusinessCheckInUnavailable
from src.database.repository import Repository
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog
from src.utils.exporter import DataExporter, ExportResult


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    existing = set(native_qt_application.topLevelWidgets())
    yield native_qt_application
    created = [widget for widget in native_qt_application.topLevelWidgets() if widget not in existing]
    for widget in created:
        if not sip.isdeleted(widget):
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(sip.isdeleted(widget) for widget in created)


@pytest.fixture
def records(tmp_path):
    repo = Repository(str(tmp_path / 'comparison-ui.db'))
    assert repo.initialize()
    owner = repo.get_or_create_character('Alpha <b>literal</b>').id
    beta = repo.get_or_create_character('Beta').id
    rows = tuple(repo.save_business_checkin(
        owner, 'bunker', stock_percent=index, supply_percent=0 if index == 0 else None,
        stock_value=9223372036854775807 if index == 0 else 0,
        note=f'Keep {index} <b>literal</b>\ncomplete note\u00a0{index}',
    ) for index in range(28))
    yield repo, owner, beta, rows
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def choose_business(board, business='bunker'):
    for index in range(board._businesses_table.rowCount()):
        if board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == business:
            board._businesses_table.selectRow(index)
            return
    raise AssertionError(f'missing business {business}')


def make_board(records, qt):
    board = BusinessCheckInsDialog(records[0], character_id=records[1])
    board.show()
    choose_business(board)
    qt.processEvents()
    assert hasattr(board, '_baseline_button'), 'history must provide baseline controls'
    return board


def select_pair(board):
    board._history_table.selectRow(0)
    board._baseline_button.click()
    board._history_table.selectRow(1)


def open_comparison(board):
    select_pair(board)
    board._compare_button.click()
    assert board._comparison_dialog is not None
    return board._comparison_dialog


def make_dialog(records, qt, **changes):
    name = 'src.ui.widgets.business_checkin_comparison_dialog'
    assert importlib.util.find_spec(name) is not None, 'read-only comparison dialog must exist'
    from src.database.business_checkin_comparison import BusinessCheckInComparison
    repo, owner, _, rows = records
    comparison = BusinessCheckInComparison(
        character_id=owner, character_name='Alpha <b>literal</b>', business_id='bunker',
        baseline=rows[0], comparison=rows[-1], captured_at=datetime.now(timezone.utc),
    )
    if changes:
        comparison = replace(comparison, **changes)
    dialog = importlib.import_module(name).BusinessCheckInComparisonDialog(
        comparison, exporter=DataExporter(repo),
    )
    dialog.show()
    qt.processEvents()
    return dialog


def test_baseline_controls_require_accepted_distinct_selection(records, qt):
    board = make_board(records, qt)
    assert board._baseline_button.isEnabled() and not board._compare_button.isEnabled()
    board._baseline_button.click()
    assert board._baseline_id == records[3][-1].id
    assert not board._compare_button.isEnabled()
    board._history_table.selectRow(1)
    assert board._compare_button.isEnabled()
    board._history_table.clearSelection()
    assert not board._compare_button.isEnabled() and not board._baseline_button.isEnabled()
    board._clear_baseline_button.click()
    assert board._baseline_id is None and board._baseline_context is None


def test_baseline_survives_pages_drafts_filters_refresh_and_pins(records, qt):
    board = make_board(records, qt)
    board._baseline_button.click()
    baseline = board._baseline_id
    board._next_button.click()
    assert board._baseline_id == baseline and board._page.offset == 25
    assert board._compare_button.isEnabled()
    board._history_note_filter.setText('Keep 0 ')
    assert board._page is None and not board._compare_button.isEnabled()
    assert board._baseline_id == baseline
    board._history_apply_button.click()
    assert board._page.total == 1 and board._compare_button.isEnabled()
    board.refresh()
    board._pin_button.click()
    assert board._baseline_id == baseline and board._page.total == 1
    board._history_clear_button.click()
    assert board._baseline_id == baseline and board._page.total == 28
    assert 'outside' in board._baseline_label.toolTip().lower()
    assert 'reread' in board._compare_button.toolTip().lower()


@pytest.mark.parametrize('change', ['owner', 'business', 'repository', 'close'])
def test_baseline_clears_when_context_or_parent_changes(records, qt, change):
    board = make_board(records, qt)
    board._baseline_button.click()
    if change == 'owner':
        board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
    elif change == 'business':
        choose_business(board, 'meth')
    elif change == 'repository':
        replacement = Repository(':memory:')
        try:
            assert replacement.initialize()
            replacement.get_or_create_character('Replacement')
            board._repository = replacement
            board.refresh()
        finally:
            replacement.close()
            replacement._session_factory.kw['bind'].dispose()
    else:
        board.close()
    assert board._baseline_id is None and board._baseline_context is None


def test_failed_history_retains_baseline_but_has_no_current_comparison(records, qt, monkeypatch):
    board = make_board(records, qt)
    select_pair(board)
    baseline = board._baseline_id
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_PATH')
    monkeypatch.setattr(records[0], 'get_business_checkin_history', fail)
    board._next_button.click()
    assert board._baseline_id == baseline and board._page is None
    assert not board._compare_button.isEnabled()
    assert 'SECRET_PATH' not in board._status_label.text()


def test_comparison_reads_current_pair_and_preserves_selected_orientation(records, qt):
    board = make_board(records, qt)
    board._baseline_button.click()
    baseline = board._baseline_id
    board._next_button.click()
    comparison_id = board._page.rows[0].id
    with records[0]._session_scope() as db:
        db.execute(text('UPDATE manual_business_checkins SET stock_percent = 99 WHERE id = :id'), {'id': baseline})
    board._compare_button.click()
    captured = board._comparison_dialog._comparison
    assert (captured.baseline.id, captured.comparison.id) == (baseline, comparison_id)
    assert captured.baseline.stock_percent == 99
    assert captured.differences.stock_percent == captured.comparison.stock_percent - 99


@pytest.mark.parametrize('which', ['baseline', 'comparison'])
def test_missing_pair_only_clears_baseline_when_baseline_is_missing(records, qt, which):
    board = make_board(records, qt)
    select_pair(board)
    baseline = board._baseline_id
    missing = baseline if which == 'baseline' else board._page.rows[1].id
    with records[0]._session_scope() as db:
        db.execute(text('DELETE FROM manual_business_checkins WHERE id = :id'), {'id': missing})
    board._compare_button.click()
    assert board._comparison_dialog is None
    assert board._baseline_id == (None if which == 'baseline' else baseline)
    assert 'unavailable' in board._status_label.text().lower()


def test_comparison_storage_failure_preserves_retry_context(records, qt, monkeypatch):
    board = make_board(records, qt)
    select_pair(board)
    baseline, page = board._baseline_id, board._page
    def fail(*args, **kwargs):
        raise BusinessCheckInUnavailable()
    with monkeypatch.context() as patch:
        patch.setattr(records[0], 'get_business_checkin_comparison', fail)
        board._compare_button.click()
    assert board._baseline_id == baseline and board._page is page
    assert board._comparison_dialog is None and board._compare_button.isEnabled()
    board._compare_button.click()
    assert board._comparison_dialog is not None


@pytest.mark.parametrize('change', ['selection', 'selection_back', 'baseline', 'draft', 'owner', 'business', 'repository'])
def test_reentrant_pair_read_never_opens_stale_child(records, qt, monkeypatch, change):
    board = make_board(records, qt)
    select_pair(board)
    original = records[0].get_business_checkin_comparison
    calls = []
    def read(*args):
        calls.append(args)
        result = original(*args)
        if change in ('selection', 'selection_back'):
            board._history_table.selectRow(2)
            if change == 'selection_back':
                board._history_table.selectRow(1)
        elif change == 'baseline':
            board._clear_baseline()
        elif change == 'draft':
            board._history_note_filter.setText('new draft')
        elif change == 'owner':
            board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
        elif change == 'business':
            choose_business(board, 'meth')
        else:
            board._repository = object()
        board._open_comparison()
        return result
    monkeypatch.setattr(records[0], 'get_business_checkin_comparison', read)
    board._compare_button.click()
    assert len(calls) == 1 and board._comparison_dialog is None
    if change == 'repository':
        assert board._baseline_id is None


def test_existing_child_is_fixed_and_finished_cleanup_is_identity_safe(records, qt):
    board = make_board(records, qt)
    first = open_comparison(board)
    snapshot = first._comparison
    board._history_table.selectRow(3)
    board._baseline_button.click()
    board._history_table.selectRow(4)
    board._compare_button.click()
    assert board._comparison_dialog is first and first._comparison is snapshot
    first.close()
    assert board._comparison_dialog is None
    board._compare_button.click()
    second = board._comparison_dialog
    assert second is not first
    board._comparison_finished(first)
    assert board._comparison_dialog is second
    board.close()
    assert not second.isVisible() and board._comparison_dialog is None


def test_dialog_renders_exact_unknowns_values_utc_notes_and_literal_names(records, qt):
    baseline = replace(records[3][0], recorded_at=datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    comparison = replace(records[3][-1], recorded_at=datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc))
    dialog = make_dialog(records, qt, baseline=baseline, comparison=comparison)
    table = dialog._metrics_table
    assert [[table.item(row, col).text() for col in range(4)] for row in range(3)] == [
        ['Stock (%)', '0', '27', '+27 percentage points'],
        ['Supplies (%)', '0', '--', '--'],
        ['Observed stock value ($)', '9223372036854775807', '0', '-9223372036854775807'],
    ]
    assert dialog._context_label.textFormat() == Qt.TextFormat.PlainText
    assert 'Alpha <b>literal</b>' in dialog._context_label.text()
    assert '2026-01-01 00:00:00 UTC' in dialog._baseline_label.text()
    assert '2025-12-31 23:00:00 UTC' in dialog._comparison_label.text()
    assert dialog._baseline_note.document().toRawText().replace('\u2029', '\n') == baseline.note
    assert dialog._comparison_note.document().toRawText().replace('\u2029', '\n') == comparison.note
    assert dialog._baseline_note.isReadOnly() and dialog._comparison_note.isReadOnly()
    assert not dialog.isModal()


def test_long_literal_context_and_notes_scroll_at_small_size(records, qt):
    dialog = make_dialog(records, qt, character_name=('Long <b>literal</b> character\n' * 100),
                         baseline=replace(records[3][0], note=('😀 <b>literal</b>\n' * 100)))
    dialog.resize(640, 460)
    qt.processEvents()
    assert dialog.width() == 640 and dialog.height() == 460
    assert dialog._scroll_area.widgetResizable()
    assert dialog._scroll_area.verticalScrollBar().maximum() > 0
    assert dialog._context_label.text().count('Long <b>literal</b> character') == 100
    assert dialog._baseline_note.document().toRawText().count('<b>literal</b>') == 100
    assert dialog._export_button.isVisible() and dialog._close_button.isVisible()


def test_export_uses_captured_pair_despite_storage_changes_and_nested_controls(records, qt, monkeypatch, tmp_path):
    board = make_board(records, qt)
    dialog = open_comparison(board)
    snapshot = dialog._comparison
    path = tmp_path / 'captured.json'
    expected = snapshot.to_report()
    choices = []
    def choose(*args, **kwargs):
        choices.append(True)
        dialog._export_comparison()
        dialog.close()
        dialog.reject()
        dialog.accept()
        board.close()
        assert board.isVisible() and dialog.isVisible()
        with records[0]._session_scope() as db:
            db.execute(text('DELETE FROM manual_business_checkins'))
        return str(path), ''
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    dialog._export_button.click()
    assert len(choices) == 1
    assert json.loads(path.read_text()) == expected
    assert dialog._comparison is snapshot and not dialog._exporting
    board.close()
    assert board._comparison_dialog is None and not dialog.isVisible()


@pytest.mark.parametrize('outcome', ['cancel', 'raise', 'failure'])
def test_export_cancellation_or_failure_preserves_dialog_pair_and_existing_file(records, qt, monkeypatch, tmp_path, outcome):
    dialog = make_dialog(records, qt)
    snapshot = dialog._comparison
    path = tmp_path / 'existing.json'
    path.write_text('previous file')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: ('', '') if outcome == 'cancel' else (str(path), ''))
    def fail(*args):
        if outcome == 'raise':
            raise RuntimeError('SECRET_PATH')
        return ExportResult(False, error_message='SECRET_PATH')
    monkeypatch.setattr(dialog._exporter, 'export_business_checkin_comparison', fail)
    dialog._export_button.click()
    assert path.read_text() == 'previous file'
    assert dialog._comparison is snapshot and dialog.isVisible()
    assert not dialog._exporting and dialog._export_button.isEnabled()
    assert 'SECRET_PATH' not in dialog._status_label.text()
    if outcome != 'cancel':
        assert 'failed' in dialog._status_label.text().lower()


def test_constructor_cannot_publish_pair_after_baseline_changes(records, qt, monkeypatch):
    board = make_board(records, qt)
    select_pair(board)
    module = importlib.import_module('src.ui.widgets.business_checkin_comparison_dialog')
    original = module.BusinessCheckInComparisonDialog
    children = []
    def construct(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        board._clear_baseline()
        return child
    monkeypatch.setattr(module, 'BusinessCheckInComparisonDialog', construct)
    board._compare_button.click()
    assert len(children) == 1 and board._comparison_dialog is None
    assert not children[0].isVisible() and board._baseline_id is None


def test_retired_read_failure_does_not_override_changed_context_status(records, qt, monkeypatch):
    board = make_board(records, qt)
    select_pair(board)
    def read(*args):
        board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
        board._status_label.setText('Status for the new context')
        raise RuntimeError('SECRET_PATH')
    monkeypatch.setattr(records[0], 'get_business_checkin_comparison', read)
    board._compare_button.click()
    assert board._comparison_dialog is None and board._baseline_id is None
    assert board._status_label.text() == 'Status for the new context'


def test_parent_export_guard_precedes_dirty_editor_prompt(records, qt, monkeypatch):
    board = make_board(records, qt)
    dialog = open_comparison(board)
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('Keep this unsaved draft')
    def unexpected_prompt(*args, **kwargs):
        pytest.fail('parent attempted to close an editor during comparison export')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', unexpected_prompt)
    def choose(*args, **kwargs):
        board.close()
        board.reject()
        assert board.isVisible() and editor.isVisible() and dialog.isVisible()
        return '', ''
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    dialog._export_button.click()
    assert board._editor is editor and board._comparison_dialog is dialog
    assert editor._note_edit.toPlainText() == 'Keep this unsaved draft'
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    board.close()
    assert board.isVisible() and dialog.isVisible()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    board.close()
    assert not board.isVisible() and not editor.isVisible() and not dialog.isVisible()


def test_fixed_child_survives_parent_context_change_until_parent_closes(records, qt):
    board = make_board(records, qt)
    dialog = open_comparison(board)
    snapshot = dialog._comparison
    board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
    assert board._baseline_id is None
    assert dialog._comparison is snapshot and dialog.isVisible()
    board.close()
    assert board._comparison_dialog is None and not dialog.isVisible()


def test_compact_baseline_controls_leave_two_complete_history_rows_at_1000_by_700(records, qt):
    from src.ui.styles import DarkTheme
    repo, owner, _beta, rows = records
    with repo._session_scope() as db:
        db.execute(text('DELETE FROM manual_business_checkins WHERE id < :id'), {'id': rows[-2].id})
    board = BusinessCheckInsDialog(repo, character_id=owner)
    board.setStyleSheet(DarkTheme.get_stylesheet())
    board.resize(1000, 700)
    board.show()
    choose_business(board)
    select_pair(board)
    qt.processEvents()
    table = board._history_table
    assert table.rowCount() == 2
    assert table.viewport().height() >= sum(table.rowHeight(row) for row in range(2))
    assert table.viewport().rect().contains(table.visualItemRect(table.item(1, 0)))


def test_failed_board_refresh_keeps_baseline_business_for_successful_retry(records, qt, monkeypatch):
    board = make_board(records, qt)
    select_pair(board)
    baseline = board._baseline_id
    def fail(*args, **kwargs):
        raise BusinessCheckInUnavailable()
    with monkeypatch.context() as patch:
        patch.setattr(records[0], 'get_business_checkin_board', fail)
        board.refresh()
    assert board._board is None and board._page is None
    assert board._baseline_id == baseline
    board.refresh()
    assert board._selected_business_id() == 'bunker'
    assert board._baseline_id == baseline and board._baseline_context == (records[0], records[1], 'bunker')
    board._history_table.selectRow(1)
    board._compare_button.click()
    assert board._comparison_dialog._comparison.baseline.id == baseline
