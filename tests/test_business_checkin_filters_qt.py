"""History filter controls exercised with native Qt and disposable SQLite."""

import json
import os
from datetime import date

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for check-in filter UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QDate, QEvent, QTimer, Qt
from sqlalchemy import text

from src.database.models import Character
from src.database.repository import Repository
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog


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
    repo = Repository(str(tmp_path / 'filter-ui.db'))
    assert repo.initialize()
    alpha = repo.get_or_create_character('Alpha <b>literal</b>').id
    beta = repo.get_or_create_character('Beta').id
    yield repo, alpha, beta
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def choose_business(board, business='bunker'):
    for index in range(board._businesses_table.rowCount()):
        if board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == business:
            board._businesses_table.selectRow(index)
            return index
    raise AssertionError(f'missing business {business}')


def make_board(records, qt):
    repo, owner, _ = records
    board = BusinessCheckInsDialog(repo, character_id=owner)
    board.show()
    choose_business(board)
    qt.processEvents()
    return board


def apply_note(board, query):
    board._history_note_filter.setText(query)
    board._history_apply_button.click()


def seed_pages(repo, owner):
    for index in range(28):
        repo.save_business_checkin(owner, 'bunker', note=f'Keep {index}')
    repo.save_business_checkin(owner, 'bunker', note='outside match')


def test_dirty_controls_retire_only_history_and_clear_reloads(records, qt):
    repo, owner, _ = records
    repo.save_business_checkin(owner, 'bunker', stock_percent=0, note='Keep <b>literal</b>\nwhole note')
    repo.save_business_checkin(owner, 'bunker', note='other')
    board = make_board(records, qt)
    snapshot = board._board
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('unsaved editor draft')
    board._history_note_filter.setText('KEEP')
    assert board._page is None and board._history_table.rowCount() == 0
    assert board._note_edit.toPlainText() == ''
    assert board._board is snapshot and board._export_board_button.isEnabled()
    assert board._record_button.isEnabled() and board._pin_button.isEnabled()
    assert not board._export_history_button.isEnabled()
    assert not board._next_button.isEnabled() and not board._previous_button.isEnabled()
    assert 'apply' in board._history_filter_status.text().lower()
    assert editor._note_edit.toPlainText() == 'unsaved editor draft'
    board._history_apply_button.click()
    assert board._page.total == 1 and board._page.filters.note_query == 'KEEP'
    assert board._history_table.item(0, 2).text() == '0'
    assert board._history_table.item(0, 3).text() == '--'
    assert board._note_edit.toPlainText() == 'Keep <b>literal</b>\nwhole note'
    board._history_clear_button.click()
    assert board._page.total == 2 and board._page.filters is None
    assert board._history_note_filter.text() == ''


def test_paging_refresh_and_pin_reorder_preserve_accepted_filters(records, qt):
    repo, owner, _ = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'keep')
    board._next_button.click()
    assert board._page.offset == 25 and board._page.total == 28
    board.refresh()
    assert board._page.offset == 25 and board._page.filters.note_query == 'keep'
    board._pin_button.click()
    assert board._page.offset == 25 and board._page.total == 28
    board._previous_button.click()
    assert board._page.offset == 0


def test_refresh_and_pin_reorder_never_apply_dirty_controls(records, qt, monkeypatch):
    repo, owner, _ = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'keep')
    board._next_button.click()
    board._history_note_filter.setText('outside')
    calls = []
    original = repo.get_business_checkin_history
    def read(*args, **kwargs):
        calls.append(kwargs.get('filters'))
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'get_business_checkin_history', read)
    board.refresh()
    board._pin_button.click()
    assert board._page is None and not board._export_history_button.isEnabled()
    assert board._history_note_filter.text() == 'outside'
    assert all(filters is not None and filters.note_query == 'keep' for filters in calls)
    board._history_apply_button.click()
    assert board._page.offset == 0 and board._page.total == 1


@pytest.mark.parametrize('query', ['😀' * 201, 'x' * 201, 'bad\tcontrol', 'bad\u2028line'])
def test_invalid_apply_preserves_exact_query_and_board_without_storage_read(records, qt, monkeypatch, query):
    board = make_board(records, qt)
    snapshot = board._board
    board._history_note_filter.setText(query)
    assert board._history_note_filter.text() == query
    monkeypatch.setattr(records[0], 'get_business_checkin_history', lambda *a, **k: pytest.fail('invalid query reached storage'))
    board._history_apply_button.click()
    assert board._history_note_filter.text() == query
    assert board._page is None and board._board is snapshot
    assert board._export_board_button.isEnabled()
    assert 'invalid' in board._history_filter_status.text().lower()


def test_two_hundred_astral_characters_are_not_truncated(records, qt):
    repo, owner, _ = records
    query = '😀' * 200
    repo.save_business_checkin(owner, 'bunker', note=query)
    board = make_board(records, qt)
    apply_note(board, query)
    assert board._history_note_filter.text() == query
    assert board._page.total == 1 and board._page.filters.note_query == query


def test_optional_utc_dates_are_inclusive_and_inverted_bounds_stay_visible(records, qt):
    repo, owner, _ = records
    for stamp in ('2026-02-01T23:59:59.999999+00:00', '2026-02-02T00:00:00+00:00', '2026-02-02T23:59:59.999999+00:00', '2026-02-03T00:00:00+00:00'):
        row = repo.save_business_checkin(owner, 'bunker', note=stamp)
        with repo._session_scope() as db:
            db.execute(text('UPDATE manual_business_checkins SET recorded_at = :stamp WHERE id = :id'), {'stamp': stamp, 'id': row.id})
    board = make_board(records, qt)
    board._history_from_enabled.setChecked(True)
    board._history_until_enabled.setChecked(True)
    board._history_from_date.setDate(QDate(2026, 2, 2))
    board._history_until_date.setDate(QDate(2026, 2, 2))
    board._history_apply_button.click()
    assert board._page.total == 2
    assert board._page.filters.recorded_from == date(2026, 2, 2)
    board._history_from_date.setDate(QDate(2026, 2, 3))
    board._history_apply_button.click()
    assert board._page is None and board._board is not None
    assert board._history_from_date.date() == QDate(2026, 2, 3)
    assert 'invalid' in board._history_filter_status.text().lower()
    board._history_clear_button.click()
    assert not board._history_from_enabled.isChecked() and not board._history_until_enabled.isChecked()
    assert board._page.total == 4 and board._page.filters is None


def test_literal_symbols_unicode_and_whitespace_notes(records, qt):
    repo, owner, _ = records
    for note in ('100%_\\ É', '100xx é', 'two  spaces', 'two spaces'):
        repo.save_business_checkin(owner, 'bunker', note=note)
    board = make_board(records, qt)
    for query, expected in [('100%_\\', '100%_\\ É'), ('É', '100%_\\ É'), ('é', '100xx é'), ('  ', 'two  spaces')]:
        apply_note(board, query)
        assert board._page.total == 1 and board._page.rows[0].note == expected
    apply_note(board, 'no match')
    assert board._page.total == 0 and board._export_history_button.isEnabled()
    assert 'matching' in board._page_label.text().lower()


def test_business_and_character_changes_reset_filters_and_first_page(records, qt):
    repo, owner, beta = records
    seed_pages(repo, owner)
    repo.save_business_checkin(beta, 'bunker', note='different owner')
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._next_button.click()
    choose_business(board, 'meth')
    assert board._history_note_filter.text() == '' and board._page.filters is None and board._page.offset == 0
    choose_business(board)
    apply_note(board, 'Keep')
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    assert board._history_note_filter.text() == '' and board._page.filters is None and board._page.offset == 0
    assert board._page.character_id == beta


def test_filtered_read_failure_is_not_zero_matches_and_keeps_board(records, qt, monkeypatch):
    repo, _, _ = records
    board = make_board(records, qt)
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_NOTE_AND_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_business_checkin_history', fail)
        apply_note(board, 'keep')
        assert board._page is None and board._board is not None
        assert board._export_board_button.isEnabled() and not board._export_history_button.isEnabled()
        assert 'could not' in board._history_filter_status.text().lower()
        assert 'PRIVATE' not in board._history_filter_status.text()
        board.refresh()
        assert board._board is not None and board._page is None
    board.refresh()
    assert board._page.total == 0 and board._page.filters.note_query == 'keep'


@pytest.mark.parametrize('change', ['draft', 'owner', 'business', 'repository'])
def test_reentrant_old_history_read_cannot_publish(records, qt, monkeypatch, tmp_path, change):
    repo, owner, beta = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    original = repo.get_business_checkin_history
    replacement = Repository(str(tmp_path / 'replacement.db'))
    assert replacement.initialize()
    calls = []
    def read(*args, **kwargs):
        calls.append(True)
        page = original(*args, **kwargs)
        if change == 'draft':
            board._history_note_filter.setText('new draft')
        elif change == 'owner':
            board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        elif change == 'business':
            choose_business(board, 'meth')
        else:
            board._repository = replacement
        board._next_page()
        board._apply_history_filters()
        board._clear_history_filters()
        return page
    try:
        with monkeypatch.context() as patch:
            patch.setattr(repo, 'get_business_checkin_history', read)
            apply_note(board, 'Keep')
        assert calls == [True]
        assert board._page is None and board._history_table.rowCount() == 0
        assert not board._export_history_button.isEnabled()
    finally:
        board._repository = repo
        replacement.close()
        replacement._session_factory.kw['bind'].dispose()


def test_filter_edit_during_board_refresh_retires_only_history(records, qt, monkeypatch):
    repo, owner, _ = records
    repo.save_business_checkin(owner, 'bunker', note='Keep')
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    original = repo.get_business_checkin_board
    def read(*args, **kwargs):
        board._history_note_filter.setText('changed during read')
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'get_business_checkin_board', read)
    board.refresh()
    assert board._board is not None and board._page is None
    assert board._history_note_filter.text() == 'changed during read'
    assert board._export_board_button.isEnabled()


def test_stale_out_of_range_page_never_issues_a_clamp_read(records, qt, monkeypatch):
    repo, owner, beta = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._next_button.click()
    with repo._session_scope() as db:
        db.execute(text('DELETE FROM manual_business_checkins WHERE character_id = :owner'), {'owner': owner})
    original = repo.get_business_checkin_history
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs['offset'])
        page = original(*args, **kwargs)
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return page
    monkeypatch.setattr(repo, 'get_business_checkin_history', read)
    board.refresh()
    assert calls == [25]
    assert board._page is None and board._character_combo.currentData() == beta


def test_filtered_refresh_clamps_to_last_matching_page(records, qt):
    repo, owner, _ = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._next_button.click()
    with repo._session_scope() as db:
        db.execute(text("DELETE FROM manual_business_checkins WHERE character_id = :owner AND note LIKE 'Keep %' AND id > 2"), {'owner': owner})
    board.refresh()
    assert board._page.offset == 0 and board._page.total == 2
    assert board._page.filters.note_query == 'Keep'


def test_queued_actions_while_dirty_cannot_export_or_page_old_snapshot(records, qt, monkeypatch):
    repo, owner, _ = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._history_note_filter.setText('outside')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('dirty snapshot export'))
    QTimer.singleShot(0, board._next_page)
    QTimer.singleShot(0, board._previous_page)
    QTimer.singleShot(0, board._export_history)
    qt.processEvents()
    assert board._page is None and board._history_note_filter.text() == 'outside'


def test_return_in_note_filter_applies_once_without_changing_owner(records, qt, monkeypatch):
    from PyQt6.QtTest import QTest
    repo, owner, _ = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    original = repo.get_business_checkin_history
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs.get('filters'))
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'get_business_checkin_history', read)
    board._history_note_filter.setText('Keep')
    board._history_note_filter.setFocus()
    QTest.keyClick(board._history_note_filter, Qt.Key.Key_Return)
    assert len(calls) == 1
    assert board._page.total == 28 and board._page.character_id == owner


def test_editor_save_uses_accepted_filters_and_keeps_fixed_target(records, qt):
    repo, owner, _ = records
    repo.save_business_checkin(owner, 'bunker', note='Keep original')
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('outside match')
    editor._save_button.click()
    assert board._page.total == 1 and board._page.filters.note_query == 'Keep'
    assert repo.get_business_checkin_history(owner, 'bunker').total == 2
    assert 'Saved check-in' in board._saved_notice_label.text()


def test_export_captures_filtered_page_before_nested_chooser_changes(records, qt, monkeypatch, tmp_path):
    repo, owner, beta = records
    seed_pages(repo, owner)
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    board._next_button.click()
    snapshot = board._page
    path = tmp_path / 'captured.json'
    def choose(*args, **kwargs):
        board._history_note_filter.setText('outside')
        board._history_apply_button.click()
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(path), 'JSON'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    board._export_history_button.click()
    assert json.loads(path.read_text()) == snapshot.to_report()
    assert board._page.character_id == beta and board._page.filters is None
    assert 'Exported' in board._status_label.text()
    assert f'#{owner}' in board._status_label.text() and 'Bunker' in board._status_label.text()


@pytest.mark.parametrize('failure', ['result', 'exception'])
def test_export_failure_after_chooser_navigation_identifies_original_context(records, qt, monkeypatch, tmp_path, failure):
    from src.utils.exporter import ExportResult
    repo, owner, beta = records
    repo.save_business_checkin(owner, 'bunker', note='Keep')
    board = make_board(records, qt)
    apply_note(board, 'Keep')
    def choose(*args, **kwargs):
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(tmp_path / 'failed.json'), 'JSON'
    def fail(*args, **kwargs):
        if failure == 'exception':
            raise RuntimeError('PRIVATE_PATH')
        return ExportResult(False, error_message='PRIVATE_PATH')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    monkeypatch.setattr(board._exporter, 'export_business_checkins_snapshot', fail)
    board._export_history_button.click()
    assert 'failed' in board._status_label.text().lower()
    assert f'#{owner}' in board._status_label.text() and 'Bunker' in board._status_label.text()
    assert 'PRIVATE' not in board._status_label.text()


def test_filters_and_applied_status_fit_1000_by_700_with_long_text(records, qt):
    repo, owner, _ = records
    with repo._session_scope() as db:
        db.query(Character).filter_by(id=owner).update({'name': 'W' * 4096})
    repo.save_business_checkin(owner, 'bunker', note='😀' * 200)
    board = make_board(records, qt)
    apply_note(board, '😀' * 200)
    board.resize(1000, 700)
    qt.processEvents()
    assert board.width() == 1000 and board.height() == 700
    assert board.minimumSizeHint().width() <= 1000 and board.minimumSizeHint().height() <= 700
    for control in (board._history_note_filter, board._history_from_enabled, board._history_from_date,
                    board._history_until_enabled, board._history_until_date, board._history_apply_button,
                    board._history_clear_button, board._history_filter_status, board._export_history_button):
        assert control.isVisible()
        assert board.rect().contains(control.mapTo(board, control.rect().bottomRight()))
    assert board._history_filter_status.textFormat() == Qt.TextFormat.PlainText
    assert 'ASCII' in board._history_filter_status.text()
