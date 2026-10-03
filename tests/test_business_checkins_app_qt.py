"""Actual MainWindow manual check-in workflow over disposable SQLite."""

import copy
import json
import os
import sqlite3

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for native check-in integration', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt

from src.app import AppState, GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from src.database.repository import Repository


@pytest.fixture(scope='module')
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def legacy_rows(repository):
    with sqlite3.connect(repository._db_path) as database:
        return {table: database.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
                for table in ('characters', 'sessions', 'activities', 'earnings', 'business_snapshots')}


@pytest.fixture
def app_window(tmp_path, monkeypatch, qt):
    from src.ui.main_window import MainWindow
    repository = Repository(str(tmp_path / 'manual-app.db'))
    assert repository.initialize()
    alpha = repository.get_or_create_character('Alpha <b>literal</b>').id
    beta = repository.get_or_create_character('Beta').id
    repository.set_active_character(beta)
    settings = Settings(tmp_path / 'manual-app.yaml')
    monkeypatch.setattr(settings_module, '_settings', settings)
    manager = GTABusinessManager(settings)
    manager._repository = repository
    # A live observation remains independent of the manual records under test.
    manager._data.business_states['bunker'] = {'stock': 7, 'supply': 8, 'value': 90}
    window = MainWindow(manager)
    window.resize(1000, 700)
    window._tabs.setCurrentWidget(window._business_panel)
    window.show()
    window._update_ui()
    qt.processEvents()
    try:
        yield manager, window, repository, alpha, beta
    finally:
        manager.stop()
        window._update_timer.stop()
        # The existing BusinessPanel timer has no QObject parent. Retire this
        # fixture's timer explicitly while its QApplication is still alive.
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


def open_board(window, qt):
    panel = window._business_panel
    assert hasattr(panel, '_manual_checkins_button'), 'Businesses needs the manual check-in workflow'
    panel._manual_checkins_button.click()
    qt.processEvents()
    assert panel._checkins_dialog is not None
    assert panel._checkins_dialog.isVisible() and not panel._checkins_dialog.isModal()
    return panel._checkins_dialog


def select_business(board, business_id='bunker'):
    row = next(i for i in range(board._businesses_table.rowCount())
               if board._businesses_table.item(i, 0).data(Qt.ItemDataRole.UserRole) == business_id)
    board._businesses_table.selectRow(row)


def editor_for(board, character_id, qt, business_id='bunker'):
    board._character_combo.setCurrentIndex(board._character_combo.findData(character_id))
    select_business(board, business_id)
    board._record_button.click()
    qt.processEvents()
    assert board._editor is not None and board._editor.isVisible()
    return board._editor


def set_observation(editor, *, stock='0', supply='', value='9223372036854775807', note='  Literal <b>雪</b>\nkeep\u00a0space  '):
    editor._stock_edit.setText(stock)
    editor._supply_edit.setText(supply)
    editor._value_edit.setText(value)
    editor._note_edit.setPlainText(note)


def test_actual_business_tab_records_reopens_and_exports_without_tracking(app_window, qt, tmp_path, monkeypatch):
    manager, window, repository, alpha, beta = app_window
    before = legacy_rows(repository)
    live = copy.deepcopy(manager._data.business_states)
    optimizer = copy.deepcopy(manager._optimizer._business_states)
    monkeypatch.setattr(manager, 'start', lambda: pytest.fail('Manual workflow started capture'))
    monkeypatch.setattr(manager, 'update_business_state', lambda *a, **kw: pytest.fail('Manual workflow entered live optimizer path'))
    board = open_board(window, qt)
    assert board._character_combo.currentData() == beta
    editor = editor_for(board, alpha, qt)
    set_observation(editor)
    editor._save_button.click()
    qt.processEvents()
    assert not editor.isVisible()
    saved = repository.get_business_checkin_board(alpha).rows[0]
    assert (saved.stock_percent, saved.supply_percent, saved.stock_value) == (0, None, 9223372036854775807)
    assert saved.note == '  Literal <b>雪</b>\nkeep\u00a0space  '
    assert board._page.rows == (saved,)
    assert legacy_rows(repository) == before
    assert manager._data.business_states == live and manager._optimizer._business_states == optimizer
    assert manager.state == AppState.STOPPED
    board.close()
    qt.processEvents()
    assert window._business_panel._checkins_dialog is None
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    assert board._page.rows == (saved,)
    path = tmp_path / 'accepted-page.json'
    accepted = board._page.to_report()
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(path), 'JSON'))
    board._export_history_button.click()
    assert json.loads(path.read_text()) == accepted
    # A fresh connection reads the same persisted manual observations.
    reopened = Repository(repository._db_path)
    try:
        assert reopened.get_business_checkin_board(alpha).rows == (saved,)
    finally:
        reopened.close()
        reopened._session_factory.kw['bind'].dispose()


def test_settings_board_and_real_start_stop_cannot_retarget_open_editor(app_window, qt, monkeypatch):
    import src.app as app_module
    manager, window, repository, alpha, beta = app_window
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt)
    set_observation(editor, stock='25', supply='0', value='0', note='Fixed Alpha observation')
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    manager._settings.set('general.character_name', 'Beta')
    monkeypatch.setattr(app_module, 'get_repository', lambda: repository)

    def initialize_without_native_capture():
        manager._initialize_database()
        manager._session_tracker.start_session(start_money=0)
    monkeypatch.setattr(manager, '_initialize_components', initialize_without_native_capture)
    monkeypatch.setattr(manager, '_capture_loop', lambda: manager._stop_event.wait(3))
    assert manager.start()
    assert manager._data.character_id == beta
    manager.stop()
    assert manager.state == AppState.STOPPED
    before_save = legacy_rows(repository)
    editor._save_button.click()
    qt.processEvents()
    assert repository.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repository.get_business_checkin_history(beta, 'bunker').total == 0
    assert board._character_combo.currentData() == beta
    assert legacy_rows(repository) == before_save


@pytest.mark.parametrize('which', ['board', 'history'])
def test_export_uses_captured_snapshot_through_modal_navigation_and_new_save(app_window, qt, tmp_path, monkeypatch, which):
    _manager, window, repository, alpha, beta = app_window
    repository.save_business_checkin(alpha, 'bunker', stock_percent=10, note='original')
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    snapshot = board._board if which == 'board' else board._page
    report = snapshot.to_report()
    path = tmp_path / (which + '.json')

    def choose(*args, **kwargs):
        repository.save_business_checkin(alpha, 'bunker', stock_percent=90, note='later')
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(path), 'JSON'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    getattr(board, '_export_' + which + '_button').click()
    assert json.loads(path.read_text()) == report
    assert board._character_combo.currentData() == beta
    assert repository.get_business_checkin_history(alpha, 'bunker').total == 2


def test_save_success_then_refresh_failure_is_not_reported_as_failed_save(app_window, qt, monkeypatch):
    _manager, window, repository, alpha, _beta = app_window
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt)
    set_observation(editor, stock='', supply='', value='', note='Saved once')
    original = repository.get_business_checkin_board
    with monkeypatch.context() as patch:
        patch.setattr(repository, 'get_business_checkin_board', lambda *a: (_ for _ in ()).throw(RuntimeError('SYNTHETIC_PRIVATE_PATH')))
        editor._save_button.click()
        qt.processEvents()
        assert not editor.isVisible()
        assert 'saved' in board._saved_notice_label.text().lower()
        assert 'SYNTHETIC_PRIVATE_PATH' not in board._status_label.text()
        assert not board._export_board_button.isEnabled()
    assert original(alpha).rows[0].note == 'Saved once'
    board.refresh()
    assert repository.get_business_checkin_history(alpha, 'bunker').total == 1


def test_history_pages_exact_business_and_never_carries_forward_unknowns(app_window, qt):
    _manager, window, repository, alpha, beta = app_window
    for index in range(31):
        repository.save_business_checkin(alpha, 'bunker', stock_percent=index, note=f'observation {index}')
    repository.save_business_checkin(alpha, 'acid_lab', stock_value=1, note='different business')
    repository.save_business_checkin(beta, 'bunker', stock_value=2, note='different character')
    latest = repository.save_business_checkin(alpha, 'bunker', note='Latest note with no measurements')
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board)
    assert board._page.total == 32 and len(board._page.rows) == 25
    assert board._page.rows[0] == latest
    assert all(getattr(latest, field) is None for field in ('stock_percent', 'supply_percent', 'stock_value'))
    board._next_button.click()
    assert board._page.offset == 25 and len(board._page.rows) == 7
    assert all(row.character_id == alpha and row.business_id == 'bunker' for row in board._page.rows)
    board._previous_button.click()
    assert board._page.rows[0] == latest
