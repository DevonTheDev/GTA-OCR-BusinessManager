"""Real offscreen Qt session-journal workflows backed by disposable SQLite."""

import importlib
import importlib.util
import os
from datetime import datetime, timedelta

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for session journal UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import Qt
from sqlalchemy import text
from src.database.models import Session
from src.database.repository import Repository, DatabaseError
from src.ui.widgets.history_panel import SessionHistoryPanel


@pytest.fixture(scope='module')
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repo = Repository(str(tmp_path / 'journal.db'))
    assert repo.initialize()
    alpha = repo.get_or_create_character('Alpha <b>literal</b>')
    beta = repo.get_or_create_character('Beta')
    ids = []
    for i in range(30):
        owner = alpha if i % 2 == 0 else beta
        row = repo.start_session(owner, start_money=1000)
        start = datetime(2026, 1, 1) + timedelta(minutes=i)
        with repo._session_scope() as db:
            db.query(Session).filter_by(id=row.id).update({
                'started_at': start, 'ended_at': start + timedelta(minutes=5),
                'end_money': 1200, 'total_earnings': 200,
            })
        ids.append(row.id)
    opened = repo.start_session(alpha)
    yield repo, alpha.id, beta.id, ids, opened.id
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def dialog(repo, session_id, qt):
    module = 'src.ui.widgets.session_annotation_dialog'
    assert importlib.util.find_spec(module) is not None, 'completed-session note editor must exist'
    widget = importlib.import_module(module).SessionAnnotationDialog(
        repo, session_id, character_name='Alpha <b>literal</b>', started_at=datetime(2026, 1, 1),
    )
    widget.show()
    qt.processEvents()
    return widget


def panel(repo, qt):
    widget = SessionHistoryPanel(repository=repo)
    widget.resize(1000, 700)
    widget.show()
    widget.refresh()
    qt.processEvents()
    return widget


def set_draft(widget, label='Heist prep', tags='crew, Weekend, CREW', note='Personal\n<b>literal</b> note'):
    widget._label_edit.setText(label)
    widget._tags_edit.setText(tags)
    widget._note_edit.setPlainText(note)


def discard(monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)


def test_editor_saves_normalized_context_and_reopens_saved_snapshot(records, qt):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    saved_ids = []
    widget.saved.connect(saved_ids.append)
    set_draft(widget)
    widget._save_button.click()
    qt.processEvents()
    assert saved_ids == [ids[0]]
    assert not widget.isVisible()
    annotation = repo.get_session_annotation(ids[0])
    assert annotation.label == 'Heist prep'
    assert annotation.tags == ('crew', 'Weekend')
    assert annotation.note == 'Personal\n<b>literal</b> note'
    reopened = dialog(repo, ids[0], qt)
    try:
        assert reopened._label_edit.text() == annotation.label
        assert reopened._tags_edit.text() == 'crew, Weekend'
        assert reopened._note_edit.toPlainText() == annotation.note
        assert reopened._context_label.textFormat() == Qt.TextFormat.PlainText
    finally:
        reopened.close()


def test_cancel_close_and_clear_draft_write_nothing(records, qt, monkeypatch):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    set_draft(widget)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    widget._cancel_button.click()
    assert widget.isVisible()
    assert widget._note_edit.toPlainText()
    widget.close()
    assert widget.isVisible()
    assert repo.get_session_annotation(ids[0]) is None
    widget._clear_button.click()
    assert not widget._note_edit.toPlainText()
    assert repo.get_session_annotation(ids[0]) is None
    set_draft(widget)
    discard(monkeypatch)
    widget._cancel_button.click()
    assert not widget.isVisible()
    assert repo.get_session_annotation(ids[0]) is None


def test_clear_existing_context_is_only_persisted_on_save(records, qt):
    repo, _, _, ids, _ = records
    assert hasattr(repo, 'save_session_annotation'), 'journal persistence must exist'
    before = repo.save_session_annotation(ids[0], 'old', ('tag',), 'old note', 0)
    widget = dialog(repo, ids[0], qt)
    widget._clear_button.click()
    assert repo.get_session_annotation(ids[0]) == before
    widget._save_button.click()
    after = repo.get_session_annotation(ids[0])
    assert after.revision > before.revision
    assert (after.label, after.tags, after.note) == ('', (), '')


@pytest.mark.parametrize('field,value', [('label', '😀' * 81), ('tags', 'x' * 33), ('tags', ','.join(str(i) for i in range(9))), ('note', '😀' * 4001)], ids=['label', 'tag-length', 'tag-count', 'note'])
def test_code_point_limits_reject_without_truncating_or_losing_draft(records, qt, monkeypatch, field, value):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    values = dict(label='😀' * 80, tags='tag', note='😀' * 4000)
    values[field] = value
    set_draft(widget, **values)
    try:
        widget._save_button.click()
        assert widget.isVisible()
        assert repo.get_session_annotation(ids[0]) is None
        assert widget._label_edit.text() == values['label']
        assert widget._tags_edit.text() == values['tags']
        assert widget._note_edit.toPlainText() == values['note']
        assert 'Invalid' in widget._status_label.text()
    finally:
        discard(monkeypatch)
        widget.close()


def test_astral_limits_are_accepted_as_code_points(records, qt):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    set_draft(widget, label='😀' * 80, tags='😀' * 32, note='😀' * 4000)
    widget._save_button.click()
    saved = repo.get_session_annotation(ids[0])
    assert saved.label == '😀' * 80
    assert saved.tags == ('😀' * 32,)
    assert saved.note == '😀' * 4000


def test_conflict_keeps_draft_and_requires_explicit_reload(records, qt, monkeypatch):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    set_draft(widget, label='my draft')
    # A second real repository observes and changes the same database.
    other = Repository(str(repo._session_factory.kw['bind'].url.database))
    assert other.initialize()
    try:
        other.save_session_annotation(ids[0], 'their saved label', ('other',), 'their note', 0)
        widget._save_button.click()
        assert widget.isVisible()
        assert widget._label_edit.text() == 'my draft'
        assert 'changed' in widget._status_label.text().lower()
        assert repo.get_session_annotation(ids[0]).label == 'their saved label'
        monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
        widget._reload_button.click()
        assert widget._label_edit.text() == 'my draft'
        discard(monkeypatch)
        widget._reload_button.click()
        assert widget._label_edit.text() == 'their saved label'
        widget._label_edit.setText('resolved')
        widget._save_button.click()
        assert repo.get_session_annotation(ids[0]).label == 'resolved'
    finally:
        discard(monkeypatch)
        widget.close()
        other.close()
        other._session_factory.kw['bind'].dispose()


def test_failed_save_preserves_draft_and_can_retry(records, qt, monkeypatch):
    repo, _, _, ids, _ = records
    widget = dialog(repo, ids[0], qt)
    set_draft(widget)
    with monkeypatch.context() as patch:
        def fail(*a, **k):
            raise DatabaseError('synthetic unavailable')
        patch.setattr(repo, 'save_session_annotation', fail)
        widget._save_button.click()
        assert widget.isVisible()
        assert widget._label_edit.text() == 'Heist prep'
        assert 'not saved' in widget._status_label.text().lower()
    widget._save_button.click()
    assert repo.get_session_annotation(ids[0]).label == 'Heist prep'


def test_corrupt_annotation_keeps_history_exportable_but_never_blank_editable(records, qt):
    repo, _, _, ids, _ = records
    assert hasattr(repo, 'save_session_annotation'), 'journal persistence must exist'
    repo.save_session_annotation(ids[-1], 'safe', (), 'note', 0)
    with repo._session_scope() as db:
        db.execute(text('UPDATE session_annotations SET label = :bad WHERE session_id = :sid'), {'bad': 'SECRET\x00CORRUPT', 'sid': ids[-1]})
    widget = panel(repo, qt)
    editor = dialog(repo, ids[-1], qt)
    try:
        assert widget._sessions_table.rowCount() == 25
        assert widget._selected_id == ids[-1]
        assert widget._export_button.isEnabled()
        assert 'unavailable' in widget._annotation_preview.toPlainText().lower()
        assert not widget._edit_annotation_button.isEnabled()
        assert not editor._save_button.isEnabled()
        assert not editor._label_edit.isEnabled()
        assert 'unavailable' in editor._status_label.text().lower()
        assert 'SECRET' not in editor._status_label.text() + widget._annotation_preview.toPlainText()
    finally:
        editor.close()
        widget.close()


def test_open_session_cannot_be_edited_and_reopened_target_cannot_be_saved(records, qt, monkeypatch):
    repo, _, _, ids, opened = records
    editor = dialog(repo, opened, qt)
    assert not editor._save_button.isEnabled()
    assert not editor._note_edit.isEnabled()
    editor.close()
    editor = dialog(repo, ids[0], qt)
    set_draft(editor)
    with repo._session_scope() as db:
        db.query(Session).filter_by(id=ids[0]).update({'ended_at': None})
    editor._save_button.click()
    assert editor.isVisible()
    assert editor._label_edit.text() == 'Heist prep'
    assert 'unavailable' in editor._status_label.text().lower()
    discard(monkeypatch)
    editor.close()


def test_search_is_literal_and_edits_retire_rows_until_explicit_apply(records, qt):
    repo, alpha, _, ids, _ = records
    assert hasattr(repo, 'save_session_annotation'), 'journal persistence must exist'
    repo.save_session_annotation(ids[0], 'crew 100%_done', ('first',), '<b>literal</b>\nPersonal', 0)
    repo.save_session_annotation(ids[1], 'crew 100XXdone', (), '', 0)
    widget = panel(repo, qt)
    try:
        widget._pin_baseline_button.click()
        baseline = widget._baseline_id
        widget._annotation_query_edit.setText('100%_')
        assert widget._sessions_table.rowCount() == 0
        assert widget._selected_id is None
        assert not widget._export_button.isEnabled()
        assert not widget._edit_annotation_button.isEnabled()
        assert not widget._next_button.isEnabled()
        widget._refresh_button.click()
        widget._character_combo.setCurrentIndex(widget._character_combo.findData(alpha))
        assert widget._sessions_table.rowCount() == 0
        widget._annotation_apply_button.click()
        assert widget._selected_id == ids[0]
        assert widget._sessions_table.rowCount() == 1
        assert widget._sessions_table.item(0, 6).text() == 'crew 100%_done · first'
        assert '<b>literal</b>' in widget._annotation_preview.toPlainText()
        assert widget._baseline_id == baseline
        widget._annotation_query_edit.clear()
        widget._annotation_apply_button.click()
        assert widget._total == 15
    finally:
        widget.close()


def test_query_validation_keeps_unapplied_state(records, qt):
    widget = panel(records[0], qt)
    try:
        assert hasattr(widget, '_annotation_query_edit'), 'journal search must exist'
        widget._annotation_query_edit.setText('x' * 201)
        widget._annotation_apply_button.click()
        assert widget._sessions_table.rowCount() == 0
        assert 'Invalid' in widget._status_label.text()
        assert not widget._export_button.isEnabled()
    finally:
        widget.close()


def test_one_modeless_editor_keeps_captured_id_across_selection_and_search(records, qt):
    repo, _, _, ids, _ = records
    widget = panel(repo, qt)
    try:
        assert hasattr(widget, '_edit_annotation_button'), 'journal editor action must exist'
        original = widget._selected_id
        widget._edit_annotation_button.click()
        editor = widget._annotation_dialog
        assert not editor.isModal()
        set_draft(editor, label='captured')
        widget._sessions_table.selectRow(3)
        second = widget._selected_id
        widget._edit_annotation_button.click()
        assert widget._annotation_dialog is editor
        editor._save_button.click()
        assert repo.get_session_annotation(original).label == 'captured'
        assert repo.get_session_annotation(second) is None
        assert widget._selected_id == second
        assert widget._annotation_dialog is None
        assert 'Saved' in widget._annotation_notice_label.text()
        widget._edit_annotation_button.click()
        editor = widget._annotation_dialog
        set_draft(editor, label='find me')
        widget._annotation_query_edit.setText('find me')
        editor._save_button.click()
        assert widget._sessions_table.rowCount() == 0
        assert not widget._export_button.isEnabled()
        widget._annotation_apply_button.click()
        assert widget._selected_id == second
    finally:
        widget.close()


def test_saved_acknowledgement_survives_refresh_failure(records, qt, monkeypatch):
    widget = panel(records[0], qt)
    try:
        assert hasattr(widget, '_edit_annotation_button'), 'journal editor action must exist'
        widget._edit_annotation_button.click()
        editor = widget._annotation_dialog
        set_draft(editor)
        with monkeypatch.context() as patch:
            def fail(**kwargs):
                raise DatabaseError('synthetic history unavailable')
            patch.setattr(records[0], 'get_completed_session_history', fail)
            editor._save_button.click()
        assert 'Saved' in widget._annotation_notice_label.text()
        assert 'Could not load history' in widget._status_label.text()
        assert not widget._export_button.isEnabled()
    finally:
        widget.close()


def test_editor_controls_reachable_at_small_size_and_history_at_1000_by_700(records, qt):
    widget = panel(records[0], qt)
    editor = dialog(records[0], records[3][0], qt)
    try:
        assert hasattr(widget, '_edit_annotation_button'), 'journal editor action must exist'
        editor.resize(560, 420)
        qt.processEvents()
        assert widget.size().width() == 1000
        assert widget.size().height() == 700
        for button in (widget._annotation_apply_button, widget._edit_annotation_button, widget._export_button):
            assert widget.rect().contains(button.mapTo(widget, button.rect().bottomRight()))
        for button in (editor._save_button, editor._cancel_button, editor._reload_button):
            assert editor.rect().contains(button.mapTo(editor, button.rect().bottomRight()))
        assert editor._scroll_area.verticalScrollBar().maximum() > 0
    finally:
        editor.close()
        widget.close()


def test_editing_label_preserves_unedited_saved_note_line_endings(records, qt):
    repo, _, _, ids, _ = records
    original_note = 'First\r\nSecond\rThird\u2028Fourth\u2029Fifth'
    repo.save_session_annotation(ids[0], 'old label', (), original_note, 0)
    widget = dialog(repo, ids[0], qt)
    widget._label_edit.setText('new label')
    widget._save_button.click()
    assert repo.get_session_annotation(ids[0]).note == original_note


def test_reentrant_open_and_save_create_one_editor_and_one_revision(records, qt, monkeypatch):
    from src.ui.widgets import session_annotation_dialog as module
    repo, _, _, _, _ = records
    widget = panel(repo, qt)
    target = widget._selected_id
    original_dialog = module.SessionAnnotationDialog
    opens = []

    def open_once(*args, **kwargs):
        opens.append(True)
        widget._open_annotation()
        return original_dialog(*args, **kwargs)

    try:
        monkeypatch.setattr(module, 'SessionAnnotationDialog', open_once)
        widget._open_annotation()
        assert opens == [True]
        editor = widget._annotation_dialog
        set_draft(editor)
        original_save = repo.save_session_annotation
        saves = []

        def save_once(*args, **kwargs):
            saves.append(True)
            editor._save()
            editor.reject()
            widget._sessions_table.selectRow(2)
            return original_save(*args, **kwargs)

        monkeypatch.setattr(repo, 'save_session_annotation', save_once)
        editor._save_button.click()
        assert saves == [True]
        assert repo.get_session_annotation(target).revision == 1
        assert widget._selected_id != target
        assert widget._annotation_dialog is None
    finally:
        widget.close()


def test_failed_reload_preserves_draft_and_requires_successful_load_before_save(records, qt, monkeypatch):
    repo, _, _, ids, _ = records
    editor = dialog(repo, ids[0], qt)
    set_draft(editor)
    discard(monkeypatch)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise DatabaseError('synthetic note read failure')
        patch.setattr(repo, 'get_session_annotation', fail)
        editor._reload_button.click()
        assert editor._label_edit.text() == 'Heist prep'
        assert not editor._save_button.isEnabled()
        assert editor._note_edit.isEnabled()
        assert 'unchanged' in editor._status_label.text()
    editor._reload_button.click()
    assert editor._label_edit.text() == ''
    assert editor._save_button.isEnabled()
    assert repo.get_session_annotation(ids[0]) is None
    editor.close()


def test_initial_failed_load_is_not_an_editable_blank_annotation(records, qt, monkeypatch):
    repo, _, _, ids, _ = records
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise DatabaseError('synthetic note read failure')
        patch.setattr(repo, 'get_session_annotation', fail)
        editor = dialog(repo, ids[0], qt)
    try:
        assert not editor._label_edit.isEnabled()
        assert not editor._save_button.isEnabled()
        editor._reload_button.click()
        assert editor._label_edit.isEnabled()
        assert editor._save_button.isEnabled()
    finally:
        editor.close()


def test_saved_and_edited_notes_preserve_nonbreaking_spaces(records, qt):
    from PyQt6.QtGui import QTextCursor
    repo, _, _, ids, _ = records
    original = 'keep\u00a0spaces\nsecond line'
    repo.save_session_annotation(ids[0], '', (), original, 0)
    editor = dialog(repo, ids[0], qt)
    cursor = editor._note_edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText('\nnew\u00a0line')
    editor._save_button.click()
    assert repo.get_session_annotation(ids[0]).note == original + '\nnew\u00a0line'


def test_history_does_not_autodetect_user_markup_as_rich_tooltips(records, qt):
    repo, _, _, ids, _ = records
    repo.save_session_annotation(ids[-1], '<b>literal</b>', (), '', 0)
    widget = panel(repo, qt)
    try:
        assert widget._sessions_table.item(0, 6).text() == '<b>literal</b>'
        assert widget._sessions_table.item(0, 6).toolTip() == ''
        assert widget._sessions_table.item(1, 1).toolTip() == ''
    finally:
        widget.close()


def test_saved_signal_preserves_large_sqlite_session_id(records, qt):
    repo, _, _, ids, _ = records
    large_id = 2**31 + 7
    with repo._session_scope() as db:
        db.query(Session).filter_by(id=ids[0]).update({'id': large_id})
    editor = dialog(repo, large_id, qt)
    captured = []
    editor.saved.connect(captured.append)
    set_draft(editor)
    editor._save_button.click()
    assert captured == [large_id]
    assert repo.get_session_annotation(large_id).label == 'Heist prep'
