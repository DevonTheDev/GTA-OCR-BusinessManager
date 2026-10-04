"""Explicit native multi-business snapshot review over disposable SQLite."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native batch review tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtTest import QTest

from src.database.business_checkins import BUSINESS_LABELS, BusinessCheckInUnavailable
from src.game.live_business_snapshot import create_live_business_reading_snapshot
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog
from tests.test_business_checkins_qt import (
    choose_business, clean_checkin_widgets as clean_checkin_widgets, records as records,
)

CAPTURED = datetime(2026, 10, 4, 11, 12, 13, 123456, tzinfo=timezone.utc)
NOTE = ' \tPersonal <b>雪😀</b>\nkeep\u00a0space\n  '


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    return native_qt_application


def reading(business='bunker', **changes):
    fields = dict(stock_percent=0, supply_percent=None, stock_value=9223372036854775807,
                  updated_at=datetime(2026, 10, 4, 10, 2, 3),
                  identity_source='manual_entry', captured_at=CAPTURED)
    fields.update(changes)
    return create_live_business_reading_snapshot(business, **fields)


def editor_for(records, qt, snapshots=None, **kwargs):
    name = 'src.ui.widgets.business_checkin_batch_editor'
    assert importlib.util.find_spec(name) is not None, 'batch snapshot review editor must exist'
    editor = importlib.import_module(name).BusinessCheckInBatchEditor(
        records[0], records[1], snapshots if snapshots is not None else (reading(), reading('nightclub')),
        character_name=kwargs.pop('character_name', 'Alpha <b>literal</b>'), **kwargs,
    )
    editor.show()
    qt.processEvents()
    return editor


def board_for(records, qt, provider=None):
    board = BusinessCheckInsDialog(records[0], character_id=records[1], live_readings_provider=provider)
    board.show()
    qt.processEvents()
    return board


def test_review_has_no_default_selection_or_passive_capture(records, qt):
    calls = []
    board = board_for(records, qt, lambda: (calls.append(True), (reading(),))[1])
    board.refresh()
    choose_business(board)
    board._record_button.click()
    assert calls == []
    assert board.live_batch_button.isEnabled()
    board.live_batch_button.click()
    review = board._batch_editor
    assert calls == [True]
    assert review is not None and not review.isModal()
    assert not any(check.isChecked() for check in review.row_checks.values())
    assert not review.save_button.isEnabled()
    assert review.selection_label.text() == '0 selected'
    assert review.save_button.text() == 'Save selected check-ins (0)'
    assert '1 available' in review._availability_label.text()
    assert '10 missing' in review._availability_label.text()
    board.live_batch_button.click()
    assert calls == [True] and board._batch_editor is review
    assert 'original capture' in board._status_label.text().lower()


def test_missing_provider_and_invalid_board_disable_action(records, qt):
    board = board_for(records, qt)
    assert not board.live_batch_button.isEnabled()
    board._live_readings_provider = lambda: ()
    board._character_combo.setCurrentIndex(0)
    assert not board.live_batch_button.isEnabled()
    board._open_batch_editor()
    assert board._batch_editor is None


def test_empty_capture_is_truthful_and_unsaveable(records, qt):
    editor = editor_for(records, qt, ())
    assert not editor.row_checks and not editor.save_button.isEnabled()
    assert '0 available' in editor._availability_label.text()
    assert '11 missing' in editor._availability_label.text()
    assert 'No live readings' in editor._empty_label.text()
    editor.select_all_button.click()
    editor._save()
    assert records[0].get_business_checkin_board(records[1]).rows == ()


@pytest.mark.parametrize('bad', [
    None, [reading()], (object(),), (reading(), reading()),
    (replace(reading(), uses_supplies=1),), (replace(reading(), identity_source='invented'),),
    (replace(reading(), updated_at='yesterday'),),
    (replace(reading(), captured_at=CAPTURED.replace(tzinfo=None)),),
    (reading(), reading('nightclub', captured_at=CAPTURED + timedelta(seconds=1))),
    (replace(reading('nightclub'), supply_percent=3),),
    (replace(reading(), stock_percent=None, stock_value=None),),
    (reading('nightclub'), reading()),
    (reading(),) * 12,
])
def test_malformed_capture_aborts_whole_review(records, qt, bad):
    board = board_for(records, qt, lambda: bad)
    board.live_batch_button.click()
    assert board._batch_editor is None
    assert 'could not be opened' in board._status_label.text()
    assert records[0].get_business_checkin_board(records[1]).rows == ()


@pytest.mark.parametrize('mutation', ['selection', 'refresh', 'repository'])
def test_stale_provider_cannot_open_or_retarget_review(records, qt, mutation):
    board = board_for(records, qt)
    def provider():
        if mutation == 'selection':
            board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
        elif mutation == 'refresh':
            board.refresh()
        else:
            board._repository = object()
        return (reading(),)
    board._live_readings_provider = provider
    board._update_actions()
    board.live_batch_button.click()
    assert board._batch_editor is None
    assert not board._opening_batch_editor


def test_selection_zero_unknown_non_supply_and_note_are_saved_exactly_once(records, qt):
    repo, alpha, _ = records
    editor = editor_for(records, qt)
    assert editor.row_labels['bunker']['stock'].text() == '0%'
    assert editor.row_labels['bunker']['supply'].text() == 'Unknown'
    assert editor.row_labels['nightclub']['supply'].text() == 'N/A'
    assert editor.row_labels['bunker']['value'].text() == '$9223372036854775807'
    assert 'local time' in editor.row_labels['bunker']['updated'].text()
    assert 'Manual live entry' in editor.row_labels['bunker']['source'].text()
    assert 'UTC' in editor._captured_label.text()
    assert 'not character-tagged' in editor._help_label.text()
    assert 'freshness' in editor._help_label.text()
    assert '<b>literal</b>' in editor._context_label.text()
    for label in editor.findChildren(QtWidgets.QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
    editor.select_all_button.click()
    assert editor.selection_label.text() == '2 selected'
    editor.clear_selection_button.click()
    assert editor.selection_label.text() == '0 selected'
    editor.select_all_button.click()
    editor.note_edit.setPlainText(NOTE)
    saved = []
    editor.saved.connect(saved.append)
    editor.save_button.click()
    assert editor._committed and editor._closed and not editor.isVisible()
    assert len(saved) == 1 and type(saved[0]) is tuple and len(saved[0]) == 2
    assert all(row.character_id == alpha and row.note == NOTE for row in saved[0])
    assert len({row.recorded_at for row in saved[0]}) == 1
    editor._save()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repo.get_business_checkin_history(alpha, 'nightclub').total == 1


def test_note_validates_before_call_and_preserves_selected_subset(records, qt, monkeypatch):
    editor = editor_for(records, qt)
    calls = []
    monkeypatch.setattr(records[0], 'save_business_checkins', lambda *args: calls.append(args))
    editor.row_checks['nightclub'].setChecked(True)
    editor.note_edit.setPlainText('😀' * 2001)
    editor._save()
    assert calls == [] and editor.row_checks['nightclub'].isChecked()
    assert not editor._uncertain and not editor._closed
    assert '2000' in editor._status_label.text()


def test_known_precommit_failure_preserves_selection_for_explicit_retry(records, qt, monkeypatch):
    repo, alpha, _ = records
    editor = editor_for(records, qt)
    real_save = repo.save_business_checkins
    calls = []
    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise BusinessCheckInUnavailable()
        return real_save(*args)
    monkeypatch.setattr(repo, 'save_business_checkins', fail_once)
    editor.row_checks['nightclub'].setChecked(True)
    editor.note_edit.setPlainText(NOTE)
    editor.save_button.click()
    assert editor.isVisible() and editor.save_button.isEnabled() and not editor._uncertain
    assert editor.note_edit.document().toRawText().replace('\u2029', '\n') == NOTE
    editor.save_button.click()
    assert len(calls) == 2 and not editor.isVisible()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 0
    assert repo.get_business_checkin_history(alpha, 'nightclub').total == 1


@pytest.mark.parametrize('failure', ['uncertain', 'unexpected', 'list', 'owner', 'business', 'values', 'note', 'id', 'order'])
def test_uncertain_outcome_freezes_review_and_never_resubmits(records, qt, monkeypatch, failure):
    from src.database.business_checkin_batches import BusinessCheckInBatchUncertain
    repo, _, _ = records
    editor = editor_for(records, qt)
    editor.select_all_button.click()
    calls = []
    real_save = repo.save_business_checkins
    def save(*args):
        calls.append(args)
        if failure == 'uncertain':
            raise BusinessCheckInBatchUncertain()
        if failure == 'unexpected':
            raise RuntimeError('private storage path')
        rows = real_save(*args)
        if failure == 'list':
            return list(rows)
        if failure == 'order':
            return rows[::-1]
        changes = {'owner': {'character_id': records[2]}, 'business': {'business_id': 'meth'},
                   'values': {'stock_percent': 88}, 'note': {'note': 'changed'}, 'id': {'id': 0}}
        return (replace(rows[0], **changes[failure]),) + rows[1:]
    monkeypatch.setattr(repo, 'save_business_checkins', save)
    editor._save()
    assert editor.isVisible() and editor._uncertain
    assert not editor.save_button.isEnabled() and not editor.note_edit.isEnabled()
    assert all(not check.isEnabled() for check in editor.row_checks.values())
    assert editor._cancel_button.isEnabled()
    assert 'may already be saved' in editor._status_label.text()
    assert 'private storage path' not in editor._status_label.text()
    editor._save()
    assert len(calls) == 1
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *args: pytest.fail('uncertain is not an unsaved draft'))
    assert editor.close()


def test_save_reentry_queued_click_and_busy_parent_close_are_guarded(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = board_for(records, qt, lambda: (reading(),))
    board.live_batch_button.click()
    editor = board._batch_editor
    editor.select_all_button.click()
    real_save = repo.save_business_checkins
    calls = []
    def reentrant(*args):
        calls.append(args)
        editor._save()
        assert not editor.close() and not board.close()
        QTimer.singleShot(0, editor._save)
        qt.processEvents()
        return real_save(*args)
    monkeypatch.setattr(repo, 'save_business_checkins', reentrant)
    editor._save()
    qt.processEvents()
    assert len(calls) == 1 and repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert board._batch_editor is None


def test_review_fixed_owner_and_ordinary_draft_independence(records, qt):
    repo, alpha, beta = records
    calls = []
    board = board_for(records, qt, lambda: (calls.append(True), (reading(),))[1])
    choose_business(board)
    board._record_button.click()
    ordinary = board._editor
    ordinary._stock_edit.setText('77')
    ordinary._note_edit.setPlainText('ordinary draft')
    board.live_batch_button.click()
    review = board._batch_editor
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    repo.set_active_character(beta)
    board.live_batch_button.click()
    assert board._batch_editor is review and calls == [True]
    assert review._character_id == alpha and review._repository is repo
    review.select_all_button.click()
    review._save()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repo.get_business_checkin_history(beta, 'bunker').total == 0
    assert ordinary._stock_edit.text() == '77' and ordinary._note_edit.toPlainText() == 'ordinary draft'
    assert f'#{alpha}' in board._saved_notice_label.text()
    assert board._character_combo.currentData() == beta


def test_success_refresh_failure_is_not_retryable(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = board_for(records, qt, lambda: (reading(),))
    board.live_batch_button.click()
    editor = board._batch_editor
    editor.select_all_button.click()
    def fail():
        editor._save()
        raise RuntimeError('private refresh path')
    monkeypatch.setattr(board, 'refresh', fail)
    editor._save()
    assert editor._committed and editor._closed and not editor.isVisible()
    assert board._batch_editor is None
    assert 'Saved 1 check-in' in board._saved_notice_label.text()
    assert 'already saved' in board._status_label.text()
    assert 'private refresh path' not in board._status_label.text()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1


@pytest.mark.parametrize('dirty', ['selected', 'note'])
@pytest.mark.parametrize('close', ['cancel', 'escape', 'parent'])
def test_discard_cancel_and_nested_actions_preserve_review(records, qt, monkeypatch, dirty, close):
    board = board_for(records, qt, lambda: (reading(),))
    board.live_batch_button.click()
    editor = board._batch_editor
    if dirty == 'selected':
        editor.select_all_button.click()
    else:
        editor.note_edit.setPlainText(NOTE)
    prompts = []
    def confirm(*args):
        prompts.append(True)
        editor._save()
        editor.reject()
        assert not editor.close() and not board.close()
        return QtWidgets.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', confirm)
    if close == 'cancel':
        editor._cancel_button.click()
    elif close == 'escape':
        QTest.keyClick(editor, Qt.Key.Key_Escape)
    else:
        board.close()
    assert prompts == [True] and editor.isVisible() and not editor._closed
    assert records[0].get_business_checkin_board(records[1]).rows == ()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *args: QtWidgets.QMessageBox.StandardButton.Discard)
    assert board.close()


def test_long_literal_labels_scroll_and_buttons_fit_small_viewport(records, qt):
    snapshots = tuple(reading(business) for business in BUSINESS_LABELS)
    editor = editor_for(records, qt, snapshots, character_name='<b>' + 'W' * 900 + '</b> 😀')
    editor.resize(460, 380)
    qt.processEvents()
    assert editor.width() <= 460 and editor.height() <= 380
    assert editor._scroll_area.verticalScrollBar().maximum() > 0
    for button in (editor.save_button, editor._cancel_button, editor.select_all_button, editor.clear_selection_button):
        point = button.mapTo(editor, QPoint(0, 0))
        assert point.x() >= 0 and point.y() >= 0
        assert point.x() + button.width() <= editor.width()
        assert point.y() + button.height() <= editor.height()
    assert editor._context_label.text().startswith('Saved character: <b>')
    assert editor._context_label.textFormat() == Qt.TextFormat.PlainText
    assert editor.row_labels['special_cargo']['value'].text() == '$9223372036854775807'


def test_constructor_reentry_is_rechecked_before_owning_child(records, qt, monkeypatch):
    from src.ui.widgets import business_checkin_batch_editor as module
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    board = board_for(records, qt, lambda: (reading(),))
    real_editor = module.BusinessCheckInBatchEditor
    children = []
    def stale(*args, **kwargs):
        child = real_editor(*args, **kwargs)
        children.append(child)
        board._open_batch_editor()
        assert not board.close()
        board._character_combo.setCurrentIndex(board._character_combo.findData(records[2]))
        return child
    monkeypatch.setattr(module, 'BusinessCheckInBatchEditor', stale)
    board._open_batch_editor()
    assert len(children) == 1 and board._batch_editor is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(children[0])


def test_provider_cannot_close_board_or_recapture_through_reentry(records, qt):
    board = board_for(records, qt)
    calls = []
    def provider():
        calls.append(True)
        board._open_batch_editor()
        assert not board.close()
        return (reading(),)
    board._live_readings_provider = provider
    board._open_batch_editor()
    assert calls == [True] and board._batch_editor is not None and board.isVisible()


def test_stale_child_callback_cannot_refresh_or_retire_replacement(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = board_for(records, qt, lambda: (reading(),))
    board._open_batch_editor()
    original = board._batch_editor
    original.select_all_button.click()
    original._save()
    saved = repo.get_business_checkin_history(alpha, 'bunker').rows
    board._open_batch_editor()
    replacement = board._batch_editor
    calls = []
    monkeypatch.setattr(board, 'refresh', lambda: calls.append(True))
    board._batch_checkins_saved(original, saved)
    board._batch_editor_finished(original)
    assert board._batch_editor is replacement and calls == []


@pytest.mark.parametrize('change', ['count', 'duplicate_id', 'bool_id', 'bool_value', 'timestamp'])
def test_other_malformed_postcommit_results_freeze_without_retries(records, qt, monkeypatch, change):
    repo = records[0]
    editor = editor_for(records, qt)
    editor.select_all_button.click()
    real_save = repo.save_business_checkins
    def malformed(*args):
        rows = real_save(*args)
        if change == 'count':
            return rows[:1]
        if change == 'duplicate_id':
            return (rows[0], replace(rows[1], id=rows[0].id))
        if change == 'bool_id':
            return (replace(rows[0], id=True), rows[1])
        if change == 'bool_value':
            return (replace(rows[0], stock_percent=False), rows[1])
        return (rows[0], replace(rows[1], recorded_at=rows[1].recorded_at + timedelta(seconds=1)))
    monkeypatch.setattr(repo, 'save_business_checkins', malformed)
    editor._save()
    assert editor._uncertain and not editor._committed and editor.isVisible()


def test_success_signal_reentry_cannot_duplicate_save(records, qt):
    editor = editor_for(records, qt)
    editor.select_all_button.click()
    seen = []
    def saved(rows):
        seen.append((editor._closed, editor._committed, len(rows)))
        editor._save()
        editor.reject()
    editor.saved.connect(saved)
    editor._save()
    assert seen == [(True, True, 2)] and not editor.isVisible()
    assert records[0].get_business_checkin_history(records[1], 'bunker').total == 1


def test_independent_manual_draft_close_can_cancel_parent_after_batch_save(records, qt, monkeypatch):
    board = board_for(records, qt, lambda: (reading(),))
    choose_business(board)
    board._open_editor()
    manual = board._editor
    manual._note_edit.setPlainText('Keep this separate draft')
    board._open_batch_editor()
    board._batch_editor.select_all_button.click()
    board._batch_editor._save()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *args: QtWidgets.QMessageBox.StandardButton.Cancel)
    assert not board.close() and manual.isVisible()
    assert manual._note_edit.toPlainText() == 'Keep this separate draft'


@pytest.mark.parametrize('size', [(720, 700), (480, 440)])
def test_snapshot_form_titles_remain_readable_at_each_viewport(records, qt, size):
    editor = editor_for(records, qt, (reading(),))
    editor.resize(*size)
    qt.processEvents()
    forms = editor._scroll_area.widget().findChildren(QtWidgets.QFormLayout)
    assert len(forms) == 1
    form = forms[0]
    for key in ('stock', 'supply', 'value', 'source', 'updated'):
        title = form.labelForField(editor.row_labels['bunker'][key])
        assert isinstance(title, QtWidgets.QLabel)
        editor._scroll_area.ensureWidgetVisible(title)
        qt.processEvents()
        metrics = title.fontMetrics()
        readable_width = max(metrics.horizontalAdvance(word) for word in title.text().split())
        assert title.width() >= readable_width, (size, title.text(), title.geometry())
        assert title.height() >= metrics.height()
        position = title.mapTo(editor._scroll_area.viewport(), QPoint(0, 0))
        assert 0 <= position.x() < editor._scroll_area.viewport().width()
        assert position.y() + title.height() > 0
        assert position.y() < editor._scroll_area.viewport().height()


@pytest.mark.parametrize('discard', [False, True])
def test_batch_save_waits_until_owner_finishes_nested_discard(records, qt, monkeypatch, discard):
    from PyQt6.QtCore import QEventLoop
    repo, alpha, _ = records
    board = board_for(records, qt, lambda: (reading(),))
    choose_business(board)
    board._open_editor()
    ordinary = board._editor
    ordinary._note_edit.setPlainText('Keep this independent draft')
    board._open_batch_editor()
    batch = board._batch_editor
    batch.select_all_button.click()
    observed = []
    def question(parent, *args):
        if parent is ordinary:
            nested = QEventLoop()
            def queued_save():
                batch._save()
                observed.append((batch._committed, board._batch_editor is batch))
                QTimer.singleShot(0, nested.quit)
            QTimer.singleShot(0, queued_save)
            nested.exec()
        return QtWidgets.QMessageBox.StandardButton.Discard if discard else QtWidgets.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', question)
    result = board._prepare_close()
    assert observed == [(False, True)]
    assert result is discard
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 0
    if not discard:
        assert batch.isVisible() and batch.save_button.isEnabled()
        batch._save()
        assert repo.get_business_checkin_history(alpha, 'bunker').total == 1


@pytest.mark.parametrize('sibling', ['batch', 'creator', 'comparison'])
def test_parent_close_skips_sibling_retired_during_nested_prompt(records, qt, monkeypatch, sibling):
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop
    repo, alpha, _ = records
    if sibling == 'comparison':
        repo.save_business_checkin(alpha, 'bunker', stock_percent=1)
        repo.save_business_checkin(alpha, 'bunker', stock_percent=2)
    board = board_for(records, qt, lambda: (reading(),))
    choose_business(board)
    board._open_editor()
    ordinary = board._editor
    ordinary._note_edit.setPlainText('Ordinary draft')
    if sibling == 'batch':
        board._open_batch_editor()
        child = board._batch_editor
    elif sibling == 'creator':
        board._open_creator()
        child = board._creator
    else:
        board._history_table.selectRow(0)
        board._use_as_baseline()
        board._history_table.selectRow(1)
        board._open_comparison()
        child = board._comparison_dialog
    assert child is not None
    def question(parent, *args):
        assert parent is ordinary and board._closing
        nested = QEventLoop()
        def retire():
            child.reject()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            nested.quit()
        QTimer.singleShot(0, retire)
        nested.exec()
        assert sip.isdeleted(child)
        return QtWidgets.QMessageBox.StandardButton.Discard
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', question)
    assert board._prepare_close()
    assert board._closed and not board._closing


def test_review_action_and_existing_board_controls_fit_1000_by_700(records, qt):
    from PyQt6.QtCore import QRect
    repo, alpha, _ = records
    repo.save_business_checkin(alpha, 'bunker', stock_percent=0, note='Baseline')
    repo.save_business_checkin(alpha, 'bunker', stock_percent=10, note='Comparison')
    board = board_for(records, qt, lambda: (reading(),))
    choose_business(board)
    board._history_table.selectRow(0)
    board._use_as_baseline()
    board._history_table.selectRow(1)
    board.resize(1000, 700)
    qt.processEvents()
    assert (board.width(), board.height()) == (1000, 700)
    for control in (board.live_batch_button, board._pin_button, board._pin_scope_label,
                    board._record_button, board._history_note_filter, board._compare_button,
                    board._history_apply_button, board._history_clear_button,
                    board._export_board_button, board._export_history_button):
        assert control.isVisible()
        assert board.rect().contains(QRect(control.mapTo(board, QPoint(0, 0)), control.size()))
    assert board.live_batch_button.isEnabled()
    board.live_batch_button.click()
    assert board._batch_editor is not None and board._batch_editor.isVisible()


def test_canceling_parent_close_restores_batch_editing_and_original_save(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = board_for(records, qt, lambda: (reading(), reading('nightclub')))
    board.live_batch_button.click()
    batch = board._batch_editor
    batch.row_checks['bunker'].setChecked(True)
    batch.note_edit.setPlainText(NOTE)
    prompts = []
    def cancel(parent, *args):
        prompts.append(parent)
        assert parent is batch and board._closing and batch._confirming_discard
        assert not batch.note_edit.isEnabled() and not batch.save_button.isEnabled()
        return QtWidgets.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', cancel)
    assert not board.close()
    assert prompts == [batch] and not board._closing
    assert batch.isVisible() and board._batch_editor is batch
    assert batch.note_edit.isEnabled() and batch.save_button.isEnabled()
    assert batch.select_all_button.isEnabled() and batch.clear_selection_button.isEnabled()
    assert all(check.isEnabled() for check in batch.row_checks.values())
    assert batch.row_checks['bunker'].isChecked() and not batch.row_checks['nightclub'].isChecked()
    assert batch.note_edit.document().toRawText().replace('\u2029', '\n') == NOTE
    batch.save_button.click()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repo.get_business_checkin_history(alpha, 'bunker').rows[0].note == NOTE
    assert repo.get_business_checkin_history(alpha, 'nightclub').total == 0
