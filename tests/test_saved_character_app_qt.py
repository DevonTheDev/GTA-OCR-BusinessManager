"""Real MainWindow onboarding from an empty, disposable saved database."""

import copy
import json
import os
import sqlite3

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for saved-character app integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent

from src.app import AppState, GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from src.database.repository import Repository
from tests.test_business_checkins_app_qt import open_board, select_business, set_observation


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    return native_qt_application


@pytest.fixture
def empty_app(tmp_path, monkeypatch, qt):
    import src.app as app_module
    from src.ui.main_window import MainWindow

    repository = Repository(str(tmp_path / 'empty-app.db'))
    assert repository.initialize()
    settings = Settings(tmp_path / 'empty-app.yaml')
    monkeypatch.setattr(settings_module, '_settings', settings)
    monkeypatch.setattr(app_module, 'get_repository', lambda: repository)
    manager = GTABusinessManager(settings)
    window = MainWindow(manager)
    window.resize(1000, 700)
    window._tabs.setCurrentWidget(window._business_panel)
    window.show()
    qt.processEvents()
    assert repository.get_all_characters() == []
    try:
        yield manager, window, repository, settings
    finally:
        manager.stop()
        window._update_timer.stop()
        window._business_panel._timer.stop()
        window._business_panel._timer.deleteLater()
        for dialog in window.findChildren(QtWidgets.QDialog):
            if not sip.isdeleted(dialog):
                dialog.hide()
                dialog.deleteLater()
        window.hide()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        repository.close()
        repository._session_factory.kw['bind'].dispose()


def create_from_board(board, qt, name):
    board._add_character_button.click()
    qt.processEvents()
    creator = board._creator
    assert creator is not None and creator.isVisible() and not creator.isModal()
    creator._name_edit.setText(name)
    creator._save_button.click()
    qt.processEvents()
    assert board._creator is None
    assert board._board.character_id == creator._committed_result.id
    return creator._committed_result


def table_rows(repository, tables):
    with sqlite3.connect(repository._db_path) as database:
        return {table: database.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                for table in tables}


def test_empty_main_window_creates_records_reopens_and_exports_without_capture(empty_app, qt, tmp_path, monkeypatch):
    manager, window, repository, settings = empty_app
    config = copy.deepcopy(settings._config)
    config_bytes = settings._config_path.read_bytes()
    data = copy.deepcopy(manager._data)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    tables = ('sessions', 'activities', 'earnings', 'business_snapshots')
    before = table_rows(repository, tables)
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Manual creation started capture'))
    monkeypatch.setattr(manager, '_initialize_components', lambda: pytest.fail('Manual creation initialized capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *a, **k: pytest.fail('Manual workflow changed live observations'))
    monkeypatch.setattr(settings, 'set', lambda *a, **k: pytest.fail('Manual creation changed tracking settings'))
    monkeypatch.setattr(repository, 'set_active_character', lambda *a: pytest.fail('Manual creation changed active flags'))

    board = open_board(window, qt)
    saved = create_from_board(board, qt, '  Solo <b>雪</b> 😀  ')
    assert saved.created and saved.name == 'Solo <b>雪</b> 😀'
    character = repository.get_all_characters()[0]
    assert (character.id, character.name, character.is_active) == (saved.id, saved.name, False)
    select_business(board)
    board._record_button.click()
    set_observation(board._editor, stock='0', supply='', value='0', note='First manual observation')
    board._editor._save_button.click()
    observation = repository.get_business_checkin_board(saved.id).rows[0]
    assert (observation.stock_percent, observation.supply_percent, observation.stock_value) == (0, None, 0)
    assert board.close()
    qt.processEvents()
    board = open_board(window, qt)
    assert board._board.character_id == saved.id
    assert board._board.rows == (observation,)
    select_business(board)
    expected = board._page.to_report()
    destination = tmp_path / 'first-observation.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(destination), 'JSON'))
    board._export_history_button.click()
    assert json.loads(destination.read_text(encoding='utf-8')) == expected
    reopened = Repository(repository._db_path)
    try:
        assert reopened.get_business_checkin_board(saved.id).rows == (observation,)
    finally:
        reopened.close()
        reopened._session_factory.kw['bind'].dispose()
    assert table_rows(repository, tables) == before
    assert manager._data == data
    assert manager._optimizer._business_states == optimizer
    assert manager.state == AppState.STOPPED and manager._capture_thread is None
    assert settings._config == config and settings._config_path.read_bytes() == config_bytes


def test_created_owner_appears_in_history_without_fabricating_session(empty_app, qt):
    _manager, window, repository, _settings = empty_app
    saved = create_from_board(open_board(window, qt), qt, 'Research character')
    window._tabs.setCurrentWidget(window._history_panel)
    qt.processEvents()
    history = window._history_panel
    index = history._character_combo.findData(saved.id)
    assert index > 0 and history._character_combo.itemText(index) == saved.name
    history._character_combo.setCurrentIndex(index)
    assert history._total == 0
    assert table_rows(repository, ('sessions',)) == {'sessions': []}


def test_creation_during_tracking_keeps_active_session_and_old_checkin_target(empty_app, qt, monkeypatch):
    manager, window, repository, settings = empty_app
    settings.set('general.character_name', 'Tracking owner')

    def initialize_without_capture():
        manager._initialize_database()
        manager._session_tracker.start_session(start_money=0)

    monkeypatch.setattr(manager, '_initialize_components', initialize_without_capture)
    monkeypatch.setattr(manager, '_capture_loop', lambda: manager._stop_event.wait(3))
    assert manager.start()
    tracking_id = manager._data.character_id
    session_id = manager._data.db_session_id
    config = settings._config_path.read_bytes()
    before = table_rows(repository, ('sessions', 'activities', 'earnings', 'business_snapshots'))
    board = open_board(window, qt)
    assert board._character_combo.currentData() == tracking_id
    select_business(board)
    board._record_button.click()
    draft = board._editor
    set_observation(draft, stock='25', supply='', value='', note='Original tracking owner')
    saved = create_from_board(board, qt, 'Separate manual owner')
    assert saved.id != tracking_id and board._editor is draft
    assert manager._data.character_id == tracking_id and manager._data.db_session_id == session_id
    assert table_rows(repository, tuple(before)) == before
    assert settings._config_path.read_bytes() == config
    flags = {row.id: row.is_active for row in repository.get_all_characters()}
    assert flags == {tracking_id: True, saved.id: False}
    draft._save_button.click()
    assert repository.get_business_checkin_history(tracking_id, 'bunker').total == 1
    assert repository.get_business_checkin_history(saved.id, 'bunker').total == 0
    assert board._character_combo.currentData() == saved.id
    manager.stop()
    assert manager.state == AppState.STOPPED


def test_manual_owner_name_reused_on_later_explicit_tracking_start(empty_app, qt, monkeypatch):
    manager, window, repository, settings = empty_app
    saved = create_from_board(open_board(window, qt), qt, 'Next explicit run')
    assert not repository.get_all_characters()[0].is_active
    settings.set('general.character_name', saved.name)

    def initialize_without_capture():
        manager._initialize_database()
        manager._session_tracker.start_session(start_money=0)

    monkeypatch.setattr(manager, '_initialize_components', initialize_without_capture)
    monkeypatch.setattr(manager, '_capture_loop', lambda: manager._stop_event.wait(3))
    assert manager.start()
    assert manager._data.character_id == saved.id
    assert len(repository.get_all_characters()) == 1
    assert repository.get_all_characters()[0].is_active
    assert manager._data.db_session_id is not None
    manager.stop()
    assert manager.state == AppState.STOPPED
