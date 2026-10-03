"""Opt-in MainWindow → saved journal → search/reopen/export with real SQLite."""

import json
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native session journal integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt

from src.app import AppState, GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from tests.test_history_panel_qt import qt as qt, records as records, selected_id


def captured_records(repository):
    # Compare every captured table, including the active character and open run.
    with sqlite3.connect(repository._db_path) as connection:
        return {table: connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                for table in ('characters', 'sessions', 'activities', 'earnings', 'business_snapshots')}


@pytest.fixture
def journal_window(records, qt, tmp_path, monkeypatch):
    from src.ui.main_window import MainWindow

    settings = Settings(tmp_path / 'journal-app.yaml')
    monkeypatch.setattr(settings_module, '_settings', settings)
    manager = GTABusinessManager(settings)
    manager._repository = records[0]
    window = MainWindow(manager)
    window.resize(1000, 700)
    window._tabs.setCurrentWidget(window._history_panel)
    window.show()
    window._update_ui()
    qt.processEvents()
    try:
        yield manager, window, records
    finally:
        window._update_timer.stop()
        for dialog in window.findChildren(QtWidgets.QDialog):
            if not sip.isdeleted(dialog):
                dialog.hide()
                dialog.deleteLater()
        window.hide()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def open_editor(history, qt):
    assert hasattr(history, '_edit_annotation_button'), 'History needs the saved session journal action'
    assert history._edit_annotation_button.isEnabled()
    history._edit_annotation_button.click()
    qt.processEvents()
    assert history._annotation_dialog is not None
    return history._annotation_dialog


def set_draft(dialog, *, label='Solo Cayo route', tags='solo, heist, SOLO', note='Try the north dock next time.'):
    dialog._label_edit.setText(label)
    dialog._tags_edit.setText(tags)
    dialog._note_edit.setPlainText(note)


def save(dialog, qt):
    dialog._save_button.click()
    qt.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def apply_search(history, query, qt):
    history._annotation_query_edit.setText(query)
    history._annotation_apply_button.click()
    qt.processEvents()


def test_actual_app_labels_later_page_searches_reopens_and_exports(journal_window, qt, tmp_path, monkeypatch):
    manager, window, records = journal_window
    repository, alpha, active, _, _ = records
    before = captured_records(repository)
    history = window._history_panel
    history._character_combo.setCurrentIndex(history._character_combo.findData(alpha.id))
    history._next_button.click()
    history._sessions_table.selectRow(2)
    qt.processEvents()
    chosen = selected_id(history)
    dialog = open_editor(history, qt)
    set_draft(dialog, label='Solo <literal> 雪 🛰', note='Personal context **not verified**\nTry a different route.')
    save(dialog, qt)
    assert history._annotation_dialog is None
    stored = repository.get_session_annotation(chosen)
    assert stored.label == 'Solo <literal> 雪 🛰'
    assert stored.tags == ('solo', 'heist')
    assert stored.note == 'Personal context **not verified**\nTry a different route.'
    history._character_combo.setCurrentIndex(0)
    apply_search(history, 'HEIST', qt)
    assert history._sessions_table.rowCount() == 1 and selected_id(history) == chosen
    assert history._sessions_table.item(0, 6).text()
    assert stored.note in history._annotation_preview.toPlainText()
    again = open_editor(history, qt)
    assert again._label_edit.text() == stored.label
    assert again._note_edit.toPlainText() == stored.note
    again._cancel_button.click()
    qt.processEvents()
    destination = tmp_path / 'saved-session-context.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_a, **_k: (str(destination), ''))
    history._export_button.click()
    qt.processEvents()
    exported = json.loads(destination.read_text())
    assert exported['session']['id'] == chosen
    assert exported['annotation'] == stored.to_dict()
    assert manager.state == AppState.STOPPED and manager._capture_thread is None
    assert manager.session_stats is None and manager.data.db_session_id is None
    assert repository.get_active_character().id == active.id
    assert captured_records(repository) == before


def test_open_editor_cannot_be_retargeted_by_history_selection(journal_window, qt):
    _, window, records = journal_window
    repository = records[0]
    before = captured_records(repository)
    history = window._history_panel
    first = selected_id(history)
    dialog = open_editor(history, qt)
    history._sessions_table.selectRow(1)
    qt.processEvents()
    second = selected_id(history)
    assert first != second
    set_draft(dialog, label='Captured first session', tags='solo', note='The original selection owns this note.')
    save(dialog, qt)
    assert repository.get_session_annotation(first).label == 'Captured first session'
    assert repository.get_session_annotation(second) is None
    assert selected_id(history) == second
    assert captured_records(repository) == before


def test_json_chooser_keeps_selected_id_and_reads_saved_note_not_editor_draft(journal_window, qt, tmp_path, monkeypatch):
    _, window, records = journal_window
    repository = records[0]
    history = window._history_panel
    chosen = selected_id(history)
    initial = repository.save_session_annotation(chosen, 'Saved label', ('solo',), 'First saved note', 0)
    history.refresh()
    dialog = open_editor(history, qt)
    set_draft(dialog, label='Unsaved draft', tags='draft', note='Never export this pending text')
    destination = tmp_path / 'owned-session.json'
    called = []

    def choose(*_args, **_kwargs):
        called.append(True)
        repository.save_session_annotation(chosen, 'New saved label', ('solo',), 'Latest committed note', initial.revision)
        history._sessions_table.selectRow(1)
        qt.processEvents()
        history._export_button.click()  # reentrant export remains suppressed
        return str(destination), ''

    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    history._export_button.click()
    qt.processEvents()
    exported = json.loads(destination.read_text())
    assert called == [True]
    assert exported['session']['id'] == chosen
    assert exported['annotation']['label'] == 'New saved label'
    assert exported['annotation']['note'] == 'Latest committed note'
    assert 'Unsaved draft' not in destination.read_text()
    assert dialog._note_edit.toPlainText() == 'Never export this pending text'


def test_export_failure_preserves_destination_and_saved_annotations(journal_window, qt, tmp_path, monkeypatch):
    from src.utils import exporter

    _, window, records = journal_window
    repository = records[0]
    history = window._history_panel
    chosen = selected_id(history)
    saved = repository.save_session_annotation(chosen, 'Saved context', (), 'Preserve this', 0)
    history.refresh()
    destination = tmp_path / 'old-export.json'
    destination.write_text('previous complete export')
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_a, **_k: (str(destination), ''))

    def fail(*_args, **_kwargs):
        raise OSError('synthetic staging failure')

    monkeypatch.setattr(exporter, 'atomic_text_writer', fail)
    history._export_button.click()
    qt.processEvents()
    assert destination.read_text() == 'previous complete export'
    assert repository.get_session_annotation(chosen) == saved
    assert 'failed' in history._status_label.text().lower()


def test_corrupt_annotation_keeps_recorded_history_and_export_available(journal_window, qt, tmp_path, monkeypatch):
    _, window, records = journal_window
    repository = records[0]
    history = window._history_panel
    chosen = selected_id(history)
    repository.save_session_annotation(chosen, 'Saved context', ('solo',), 'Original note', 0)
    with sqlite3.connect(repository._db_path) as connection:
        connection.execute('UPDATE session_annotations SET tags_text=? WHERE session_id=?', ('solo,,crew', chosen))
    history.refresh()
    assert selected_id(history) == chosen
    assert 'unavailable' in history._annotation_preview.toPlainText().lower()
    assert history._activities_table.rowCount() == 1
    assert history._earnings_table.rowCount() == 1
    destination = tmp_path / 'captured-with-unavailable-notes.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_a, **_k: (str(destination), ''))
    history._export_button.click()
    qt.processEvents()
    exported = json.loads(destination.read_text())
    assert exported['session']['id'] == chosen
    assert exported['annotation'] == {'available': False, 'error_code': 'invalid_annotation'}
    assert len(exported['activities']) == len(exported['earnings']) == 1
    with sqlite3.connect(repository._db_path) as connection:
        assert connection.execute('SELECT tags_text FROM session_annotations WHERE session_id=?', (chosen,)).fetchone()[0] == 'solo,,crew'


def test_actual_journal_window_renders_literal_long_notes_and_reachable_controls(journal_window, qt):
    _, window, records = journal_window
    history = window._history_panel
    chosen = selected_id(history)
    literal = '<img src=x onerror=alert(1)> 雪 🛰 '
    records[0].save_session_annotation(chosen, '<b>Literal label</b>', ('solo', 'heist'), literal * 100, 0)
    history.refresh()
    assert history._annotation_preview.isReadOnly()
    assert '<img src=x onerror=alert(1)>' in history._annotation_preview.toPlainText()
    assert history._annotation_preview.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert window.grab().save('/tmp/gta-session-journal-main.png')
    dialog = open_editor(history, qt)
    dialog.resize(640, 560)
    qt.processEvents()
    assert dialog._label_edit.text() == '<b>Literal label</b>'
    assert dialog._note_edit.toPlainText() == literal * 100
    assert dialog._save_button.isVisible() and dialog._cancel_button.isVisible()
    assert dialog.rect().contains(dialog._save_button.mapTo(dialog, dialog._save_button.rect().center()))
    assert dialog.grab().save('/tmp/gta-session-journal-editor.png')


def test_large_sqlite_session_id_survives_editor_signal_and_saved_acknowledgement(journal_window, qt):
    from src.database.models import Session

    _, window, records = journal_window
    repository, character, _, _, _ = records
    large_id = 2**31 + 7
    start = datetime(2026, 2, 1, 12)
    with repository._session_scope() as session:
        session.add(Session(id=large_id, character_id=character.id, started_at=start,
                            ended_at=start + timedelta(minutes=5), start_money=100,
                            end_money=100, total_earnings=0))
    before = captured_records(repository)
    history = window._history_panel
    history.refresh()
    row = next(index for index in range(history._sessions_table.rowCount())
               if history._sessions_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == large_id)
    history._sessions_table.selectRow(row)
    qt.processEvents()
    assert selected_id(history) == large_id
    dialog = open_editor(history, qt)
    emitted = []
    dialog.saved.connect(emitted.append)
    set_draft(dialog, label='Large ID keeps identity', tags='', note='Exact captured session')
    save(dialog, qt)
    assert emitted == [large_id]
    assert f'#{large_id}' in history._annotation_notice_label.text()
    assert repository.get_session_annotation(large_id).session_id == large_id
    assert captured_records(repository) == before
