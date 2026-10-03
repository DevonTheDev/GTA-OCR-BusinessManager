"""Opt-in real Qt history browsing and JSON export with disposable SQLite."""

import importlib
import importlib.util
import json
import os
from datetime import datetime, timedelta

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native History workflow tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtCore import Qt
from src.database.models import Session
from src.database.repository import Repository, DatabaseError


@pytest.fixture(scope='module')
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repository = Repository(str(tmp_path / 'history.db'))
    assert repository.initialize()
    alpha = repository.get_or_create_character('Alpha')
    beta = repository.get_or_create_character('Beta')
    repository.set_active_character(beta.id)
    ids = []
    for i in range(62):
        character = alpha if i % 2 == 0 else beta
        record = repository.start_session(character, start_money=1000)
        repository.log_activity(record.id, 'CONTACT_MISSION', f'Activity {i}', earnings=100, success=True, duration_seconds=60)
        repository.log_earning(record.id, amount=100, source=f'Event {i}', balance_after=1100)
        start = datetime(2026, 1, 1) + timedelta(minutes=i)
        with repository._session_scope() as session:
            session.query(Session).filter_by(id=record.id).update({
                'started_at': start, 'ended_at': start + timedelta(minutes=5),
                'end_money': 900 + i, 'total_earnings': -100 + i,
            })
        ids.append(record.id)
    opened = repository.start_session(alpha)
    yield repository, alpha, beta, ids, opened.id
    repository.close()


def panel(repository, qt):
    name = 'src.ui.widgets.history_panel'
    assert importlib.util.find_spec(name) is not None, 'the new History panel must exist'
    widget = importlib.import_module(name).SessionHistoryPanel(repository=repository)
    widget.refresh()
    qt.processEvents()
    return widget


def selected_id(widget):
    row = widget._sessions_table.currentRow()
    return widget._sessions_table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None


def test_browse_page_filter_and_drill_into_recorded_details(records, qt):
    repository, alpha, beta, ids, opened = records
    widget = panel(repository, qt)
    try:
        assert widget._sessions_table.rowCount() == 25
        assert selected_id(widget) == ids[-1]
        assert opened not in [widget._sessions_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                              for row in range(widget._sessions_table.rowCount())]
        assert widget._activities_table.item(0, 2).text() == 'Activity 61'
        assert widget._earnings_table.item(0, 2).text() == 'Event 61'
        assert '-39' in widget._sessions_table.item(0, 4).text()
        widget._next_button.click(); qt.processEvents()
        assert selected_id(widget) == ids[-26]
        widget._previous_button.click(); qt.processEvents()
        assert selected_id(widget) == ids[-1]
        widget._character_combo.setCurrentIndex(widget._character_combo.findData(alpha.id)); qt.processEvents()
        assert selected_id(widget) == ids[-2]
        assert widget._sessions_table.item(0, 1).text() == 'Alpha'
        widget._next_button.click(); qt.processEvents()
        assert widget._sessions_table.rowCount() == 6
        assert not widget._next_button.isEnabled()
        assert repository.get_active_character().id == beta.id
        assert repository.get_completed_session_history().total == 62
    finally:
        widget.close()


def test_export_uses_selected_record_id_after_filtering_and_paging(records, qt, tmp_path, monkeypatch):
    repository, alpha, _, _, _ = records
    widget = panel(repository, qt)
    destination = tmp_path / 'chosen.json'
    try:
        widget._character_combo.setCurrentIndex(widget._character_combo.findData(alpha.id))
        widget._next_button.click(); widget._sessions_table.selectRow(2); qt.processEvents()
        chosen_id = selected_id(widget)
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), 'JSON files (*.json)'))
        widget._export_button.click(); qt.processEvents()
        data = json.loads(destination.read_text())
        assert data['session']['id'] == chosen_id
        assert data == repository.export_session_data(chosen_id)
        assert 'Exported' in widget._status_label.text()
    finally:
        widget.close()


def test_export_cancellation_creates_no_file_and_keeps_selection(records, qt, tmp_path, monkeypatch):
    widget = panel(records[0], qt)
    try:
        chosen_id = selected_id(widget)
        before = sorted(path.name for path in tmp_path.iterdir())
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: ('', ''))
        widget._export_button.click(); qt.processEvents()
        assert sorted(path.name for path in tmp_path.iterdir()) == before
        assert selected_id(widget) == chosen_id
    finally:
        widget.close()


def test_history_load_failure_is_visible_and_refresh_recovers(records, qt, monkeypatch):
    repository = records[0]
    widget = panel(repository, qt)
    try:
        with monkeypatch.context() as patch:
            def unavailable(**kwargs):
                raise DatabaseError('synthetic read unavailable')
            patch.setattr(repository, 'get_completed_session_history', unavailable)
            widget._refresh_button.click(); qt.processEvents()
            assert 'synthetic read unavailable' in widget._status_label.text()
            assert not widget._export_button.isEnabled()
            assert widget._sessions_table.rowCount() == 0
        widget._refresh_button.click(); qt.processEvents()
        assert widget._sessions_table.rowCount() == 25
        assert widget._export_button.isEnabled()
    finally:
        widget.close()


def test_empty_history_does_not_create_characters_or_sessions(tmp_path, qt):
    repository = Repository(str(tmp_path / 'empty.db'))
    assert repository.initialize()
    widget = panel(repository, qt)
    try:
        assert widget._sessions_table.rowCount() == 0
        assert not widget._export_button.isEnabled()
        assert not widget._next_button.isEnabled()
        assert not widget._previous_button.isEnabled()
        assert 'No completed sessions' in widget._status_label.text()
        assert repository.get_all_characters() == []
    finally:
        widget.close(); repository.close()


def test_refresh_preserves_selected_id_and_character_filter(records, qt):
    repository, alpha, _, ids, _ = records
    widget = panel(repository, qt)
    try:
        widget._sessions_table.selectRow(7)
        chosen_id = selected_id(widget)
        widget._refresh_button.click(); qt.processEvents()
        assert selected_id(widget) == chosen_id
        widget._character_combo.setCurrentIndex(widget._character_combo.findData(alpha.id))
        widget._sessions_table.selectRow(3)
        chosen_id = selected_id(widget)
        widget._refresh_button.click(); qt.processEvents()
        assert selected_id(widget) == chosen_id
        assert widget._character_combo.currentData() == alpha.id
        assert ids[-1] != chosen_id
    finally:
        widget.close()


def test_nullable_legacy_record_can_be_inspected_and_exported(records, qt, tmp_path, monkeypatch):
    repository, _, _, ids, _ = records
    chosen_id = ids[-1]
    with repository._session_scope() as session:
        session.query(Session).filter_by(id=chosen_id).update({
            'started_at': None, 'start_money': None, 'end_money': None, 'total_earnings': None,
        })
    widget = panel(repository, qt)
    try:
        widget._next_button.click(); widget._next_button.click(); qt.processEvents()
        widget._sessions_table.selectRow(widget._sessions_table.rowCount() - 1); qt.processEvents()
        assert selected_id(widget) == chosen_id
        assert widget._sessions_table.item(widget._sessions_table.currentRow(), 2).text() == '--'
        assert widget._sessions_table.item(widget._sessions_table.currentRow(), 4).text() == '--'
        assert widget._export_button.isEnabled()
        destination = tmp_path / 'legacy.json'
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
        widget._export_button.click()
        data = json.loads(destination.read_text())
        assert data['session']['id'] == chosen_id
        assert data['session']['started_at'] is data['session']['duration_seconds'] is None
        assert data['session']['start_money'] is data['session']['end_money'] is None
    finally:
        widget.close()


def test_failed_export_preserves_old_file_and_reports_retry(records, qt, tmp_path, monkeypatch):
    from src.utils import persistence
    widget = panel(records[0], qt)
    destination = tmp_path / 'existing.json'
    destination.write_text('previous complete file')
    try:
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
        with monkeypatch.context() as patch:
            def unavailable(source, target):
                raise OSError('synthetic replace unavailable')
            patch.setattr(persistence.os, 'replace', unavailable)
            widget._export_button.click()
        assert destination.read_text() == 'previous complete file'
        assert 'Export failed' in widget._status_label.text()
        assert widget._export_button.isEnabled()
        widget._export_button.click()
        assert json.loads(destination.read_text())['session']['id'] == selected_id(widget)
    finally:
        widget.close()


def test_reentrant_export_is_ignored_and_original_selection_is_captured(records, qt, tmp_path, monkeypatch):
    widget = panel(records[0], qt)
    original_id = selected_id(widget)
    destination = tmp_path / 'captured.json'
    choices = []
    def choose(*args, **kwargs):
        choices.append(True)
        if len(choices) == 1:
            widget._sessions_table.selectRow(4)
            widget._export_button.click()
        return str(destination), ''
    try:
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
        widget._export_button.click()
        assert choices == [True]
        assert json.loads(destination.read_text())['session']['id'] == original_id
        assert selected_id(widget) != original_id
        assert widget._export_button.isEnabled()
    finally:
        widget.close()


def test_detail_display_limit_does_not_truncate_export(records, qt, tmp_path, monkeypatch):
    from src.database.models import Earnings
    repository, _, _, ids, _ = records
    with repository._session_scope() as session:
        session.add_all([Earnings(session_id=ids[-1], amount=i, source='Extra', balance_after=i)
                         for i in range(1001)])
    widget = panel(repository, qt)
    destination = tmp_path / 'complete.json'
    try:
        assert widget._earnings_table.rowCount() == 1000
        assert '1002 balance changes' in widget._status_label.text()
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
        widget._export_button.click()
        assert len(json.loads(destination.read_text())['earnings']) == 1002
    finally:
        widget.close()


def test_main_window_history_tab_opens_without_starting_capture(records, qt, tmp_path, monkeypatch):
    from src.app import GTABusinessManager, AppState
    from src.config import settings as settings_module
    from src.config.settings import Settings
    from src.ui.main_window import MainWindow
    settings = Settings(tmp_path / 'window.yaml')
    monkeypatch.setattr(settings_module, '_settings', settings)
    manager = GTABusinessManager(settings)
    manager._repository = records[0]
    window = MainWindow(manager)
    try:
        index = next(i for i in range(window._tabs.count()) if window._tabs.tabText(i) == 'History')
        window._tabs.setCurrentIndex(index); qt.processEvents()
        assert window._history_panel._sessions_table.rowCount() == 25
        assert manager.state == AppState.STOPPED
        assert manager._capture_thread is None
        assert window._history_panel._status_label.textFormat() == Qt.TextFormat.PlainText
        window.show(); qt.processEvents()
        assert not window.grab().isNull()
    finally:
        window._update_timer.stop()
        window.hide(); window.deleteLater(); qt.processEvents()


def test_export_provider_failure_is_visible_and_action_is_reenabled(records, qt, tmp_path, monkeypatch):
    widget = panel(records[0], qt)
    destination = tmp_path / 'unwritten.json'
    try:
        monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), ''))
        def unavailable():
            raise DatabaseError('synthetic provider unavailable')
        monkeypatch.setattr(widget, '_get_repository', unavailable)
        widget._export_selected()
        assert not destination.exists()
        assert 'synthetic provider unavailable' in widget._status_label.text()
        assert widget._export_button.isEnabled()
    finally:
        widget.close()


def test_character_without_completed_sessions_shows_empty_details(records, qt):
    repository = records[0]
    character = repository.get_or_create_character('Still playing')
    repository.start_session(character)
    widget = panel(repository, qt)
    try:
        widget._character_combo.setCurrentIndex(widget._character_combo.findData(character.id))
        qt.processEvents()
        assert widget._sessions_table.rowCount() == 0
        assert widget._activities_table.rowCount() == widget._earnings_table.rowCount() == 0
        assert not widget._export_button.isEnabled()
        assert 'No completed sessions' in widget._status_label.text()
    finally:
        widget.close()
