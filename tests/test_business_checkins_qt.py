"""Native offscreen manual check-in UI backed by disposable SQLite."""

import importlib
import importlib.util
import json
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for manual check-in UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from sqlalchemy import text
from src.database.models import Character
from src.database.repository import Repository


@pytest.fixture(scope='module')
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def clean_checkin_widgets(qt):
    """Retire only this test's Qt objects before the module app can be released."""
    existing = set(qt.topLevelWidgets())
    yield
    created = [widget for widget in qt.topLevelWidgets() if widget not in existing]
    for widget in created:
        if sip.isdeleted(widget):
            continue
        # BusinessPanel's pre-existing update timer is not parented to its widget.
        timer = getattr(widget, '_timer', None)
        if isinstance(timer, QTimer) and not sip.isdeleted(timer):
            timer.stop()
            timer.deleteLater()
        # Tests already exercise discard prompts. Teardown must never open one.
        widget.hide()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(sip.isdeleted(widget) for widget in created)


@pytest.fixture
def records(tmp_path):
    repo = Repository(str(tmp_path / 'checkins.db'))
    assert repo.initialize()
    alpha = repo.get_or_create_character('Alpha <b>literal</b>').id
    beta = repo.get_or_create_character('Beta').id
    with repo._session_scope() as db:
        db.query(Character).update({'is_active': False})
    yield repo, alpha, beta
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def make_editor(repo, owner, qt):
    name = 'src.ui.widgets.business_checkin_editor'
    assert importlib.util.find_spec(name) is not None, 'manual check-in editor must exist'
    editor = importlib.import_module(name).BusinessCheckInEditor(
        repo, owner, 'bunker', character_name='Alpha <b>literal</b>',
    )
    editor.show()
    qt.processEvents()
    return editor


def make_board(repo, owner, qt):
    name = 'src.ui.widgets.business_checkins_dialog'
    assert importlib.util.find_spec(name) is not None, 'manual check-in board must exist'
    board = importlib.import_module(name).BusinessCheckInsDialog(repo, character_id=owner)
    board.show()
    qt.processEvents()
    return board


def discard(monkeypatch):
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)


def choose_business(board, business='bunker'):
    for index in range(board._businesses_table.rowCount()):
        if board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == business:
            board._businesses_table.selectRow(index)
            return index
    raise AssertionError(f'missing business {business}')


def test_blank_unknown_and_zero_known_are_saved_literally(records, qt):
    repo, alpha, _ = records
    editor = make_editor(repo, alpha, qt)
    assert editor._stock_edit.text() == editor._supply_edit.text() == editor._value_edit.text() == ''
    saved = []
    editor.saved.connect(saved.append)
    editor._stock_edit.setText('0')
    editor._value_edit.setText('9223372036854775807')
    editor._note_edit.setPlainText('  Personal <b>literal</b>\nkeep\u00a0space  ')
    editor._save_button.click()
    assert not editor.isVisible()
    row = repo.get_business_checkin_board(alpha).rows[0]
    assert saved == [row]
    assert (row.stock_percent, row.supply_percent, row.stock_value) == (0, None, 9223372036854775807)
    assert row.note == '  Personal <b>literal</b>\nkeep\u00a0space  '
    editor._save()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    reopened = make_editor(repo, alpha, qt)
    assert reopened._stock_edit.text() == reopened._note_edit.toPlainText() == ''
    reopened.close()


@pytest.mark.parametrize('field,value', [('stock', '101'), ('supply', '-1'), ('value', '1,000'), ('value', '9223372036854775808'), ('stock', '١'), ('note', '😀' * 2001)], ids=['stock-range', 'negative-supply', 'comma', 'overflow', 'unicode-digits', 'note-length'])
def test_invalid_input_keeps_exact_draft_and_writes_nothing(records, qt, monkeypatch, field, value):
    repo, alpha, _ = records
    editor = make_editor(repo, alpha, qt)
    control = getattr(editor, f'_{field}_edit')
    if field == 'note':
        control.setPlainText(value)
    else:
        control.setText(value)
    try:
        editor._save_button.click()
        assert editor.isVisible()
        assert editor._draft()[('stock', 'supply', 'value', 'note').index(field)] == value
        assert repo.get_business_checkin_board(alpha).rows == ()
        assert 'not saved' in editor._status_label.text().lower()
    finally:
        discard(monkeypatch)
        editor.close()


def test_empty_save_and_cancel_discard_behavior(records, qt, monkeypatch):
    repo, alpha, _ = records
    editor = make_editor(repo, alpha, qt)
    editor._save_button.click()
    assert 'not saved' in editor._status_label.text().lower()
    editor._note_edit.setPlainText('unsaved')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    editor.close()
    assert editor.isVisible()
    editor._cancel_button.click()
    assert editor.isVisible()
    discard(monkeypatch)
    editor.close()
    assert not editor.isVisible()
    editor._save()
    assert repo.get_business_checkin_board(alpha).rows == ()


def test_failed_save_preserves_draft_and_reentrant_save_only_appends_once(records, qt, monkeypatch):
    repo, alpha, _ = records
    editor = make_editor(repo, alpha, qt)
    editor._note_edit.setPlainText('retry this')
    original = repo.save_business_checkin
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_DATABASE_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'save_business_checkin', fail)
        editor._save_button.click()
        assert editor.isVisible()
        assert editor._note_edit.toPlainText() == 'retry this'
        assert 'SECRET' not in editor._status_label.text()
    calls = []
    def save_once(*args, **kwargs):
        calls.append(True)
        editor._save()
        editor.reject()
        editor.close()
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'save_business_checkin', save_once)
    editor._save_button.click()
    assert calls == [True]
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1


def test_ambiguous_characters_require_selection_and_names_are_plain(records, qt):
    repo, alpha, _ = records
    board = make_board(repo, None, qt)
    try:
        assert board._character_combo.currentData() is None
        assert board._board is None
        assert not board._record_button.isEnabled()
        assert 'Choose' in board._status_label.text()
        board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
        assert board._board.character_id == alpha
        assert board._context_label.textFormat() == Qt.TextFormat.PlainText
        assert '<b>literal</b>' in board._context_label.text()
    finally:
        board.close()


def test_explicit_active_and_only_character_selection(records, qt):
    repo, alpha, beta = records
    repo.set_active_character(beta)
    active = make_board(repo, None, qt)
    explicit = make_board(repo, alpha, qt)
    try:
        assert active._board.character_id == beta
        assert explicit._board.character_id == alpha
    finally:
        active.close()
        explicit.close()
    with repo._session_scope() as db:
        db.query(Character).filter_by(id=beta).delete()
        db.query(Character).update({'is_active': False})
    only = make_board(repo, None, qt)
    assert only._board.character_id == alpha
    only.close()


def test_empty_character_state_offers_explicit_creation_without_creating_records(tmp_path, qt):
    repo = Repository(str(tmp_path / 'empty.db'))
    assert repo.initialize()
    board = make_board(repo, None, qt)
    try:
        assert 'Add saved character' in board._status_label.text()
        assert 'without starting tracking or OCR' in board._status_label.text()
        assert board._add_character_button.isEnabled()
        assert repo.get_all_characters() == []
        assert not board._record_button.isEnabled()
        assert not board._export_board_button.isEnabled()
    finally:
        board.close()
        repo.close()
        repo._session_factory.kw['bind'].dispose()


def test_catalog_unknown_latest_literal_note_and_history_paging(records, qt):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    board.close()
    for index in range(28):
        repo.save_business_checkin(alpha, 'bunker', stock_percent=index, note=f'<b>literal {index}</b>\nfull note')
    repo.save_business_checkin(beta, 'bunker', stock_percent=99)
    unknown = repo.save_business_checkin(alpha, 'meth', note='retained')
    with repo._session_scope() as db:
        db.execute(text('UPDATE manual_business_checkins SET business_id = :bid WHERE id = :id'), {'bid': 'legacy_business', 'id': unknown.id})
    board = make_board(repo, alpha, qt)
    try:
        from src.database.business_checkins import BUSINESS_LABELS
        assert board._businesses_table.rowCount() == len(BUSINESS_LABELS) + 1
        index = choose_business(board)
        assert board._businesses_table.item(index, 1).text() == '27'
        assert board._businesses_table.item(index, 2).text() == '--'
        assert board._page.total == 28
        assert len(board._page.rows) == 25
        assert board._note_edit.toPlainText() == '<b>literal 27</b>\nfull note'
        board._next_button.click()
        assert board._page.offset == 25
        assert len(board._page.rows) == 3
        board._previous_button.click()
        assert board._page.offset == 0
        choose_business(board, 'legacy_business')
        assert board._page.rows[0].note == 'retained'
        assert not board._record_button.isEnabled()
        index = choose_business(board, 'cash')
        assert board._businesses_table.item(index, 1).text() == '--'
        assert board._page.rows == ()
    finally:
        board.close()


def test_editor_remains_fixed_when_board_character_changes_and_refresh_preserves_owner(records, qt):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    try:
        choose_business(board)
        board._record_button.click()
        editor = board._editor
        assert not editor.isModal()
        editor._note_edit.setPlainText('belongs to Alpha')
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        choose_business(board, 'meth')
        board._record_button.click()
        assert board._editor is editor
        editor._save_button.click()
        assert repo.get_business_checkin_board(alpha).rows[0].business_id == 'bunker'
        assert repo.get_business_checkin_board(beta).rows == ()
        assert board._board.character_id == beta
        assert board._selected_business_id() == 'meth'
        assert board._editor is None
        assert 'Saved' in board._saved_notice_label.text()
    finally:
        board.close()


def test_failed_refresh_retires_snapshots_but_preserves_saved_truth(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('committed before refresh')
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_DATABASE_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_business_checkin_board', fail)
        editor._save_button.click()
        assert 'Saved' in board._saved_notice_label.text()
        assert 'could not' in board._status_label.text().lower()
        assert 'SECRET' not in board._status_label.text()
        assert board._board is board._page is None
        assert not board._export_board_button.isEnabled()
        assert not board._export_history_button.isEnabled()
    board.refresh()
    assert len(board._board.rows) == 1
    board.close()


def test_character_read_failure_is_not_an_empty_database(records, qt, monkeypatch):
    repo, alpha, _ = records
    def fail():
        raise RuntimeError('SECRET_DATABASE_PATH')
    monkeypatch.setattr(repo, 'get_business_checkin_characters', fail)
    board = make_board(repo, alpha, qt)
    try:
        assert 'could not' in board._status_label.text().lower()
        assert 'Start' not in board._status_label.text()
        assert 'SECRET' not in board._status_label.text()
        assert board._board is None
    finally:
        board.close()


def test_board_close_honors_unsaved_editor_and_stale_callback_preserves_new_editor(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    choose_business(board)
    board._record_button.click()
    old = board._editor
    old._note_edit.setPlainText('draft')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    board.close()
    assert board.isVisible() and old.isVisible()
    discard(monkeypatch)
    old.close()
    board._record_button.click()
    new = board._editor
    assert new is not old
    board._editor_finished(old)
    assert board._editor is new
    board.close()
    assert not board.isVisible() and not new.isVisible()


@pytest.mark.parametrize('kind', ['board', 'history'])
def test_export_freezes_snapshot_before_chooser_and_blocks_nested_exports(records, qt, monkeypatch, tmp_path, kind):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    repo.save_business_checkin(alpha, 'bunker', stock_percent=0, note='original')
    board.refresh()
    choose_business(board)
    snapshot = board._board if kind == 'board' else board._page
    target = tmp_path / f'{kind}.json'
    calls = []
    def choose(*args, **kwargs):
        calls.append(True)
        board._export_board()
        board._export_history()
        repo.save_business_checkin(alpha, 'bunker', note='later')
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return str(target), 'JSON'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    getattr(board, f'_export_{kind}')()
    try:
        assert calls == [True]
        assert json.loads(target.read_text()) == snapshot.to_report()
        assert board._board.character_id == beta
    finally:
        board.close()


def test_panel_launcher_is_lazy_single_owned_and_identity_safe(records, qt, monkeypatch):
    from types import SimpleNamespace
    from src.ui.widgets.business_panel import BusinessPanel
    repo, alpha, _ = records
    reads = []
    class StoppedApp:
        data = SimpleNamespace(character_id=alpha)
        @property
        def history_repository(self):
            reads.append(True)
            return repo
        def get_business_state(self, business):
            return None
    panel = BusinessPanel(StoppedApp())
    panel.show()
    try:
        assert hasattr(panel, '_manual_checkins_button'), 'business panel must launch manual check-ins'
        assert reads == []
        panel._manual_checkins_button.click()
        old = panel._checkins_dialog
        assert old._board.character_id == alpha
        assert not old.isModal()
        panel._manual_checkins_button.click()
        assert panel._checkins_dialog is old
        assert reads == [True]
        old.close()
        assert panel._checkins_dialog is None
        panel._manual_checkins_button.click()
        new = panel._checkins_dialog
        panel._checkins_finished(old)
        assert panel._checkins_dialog is new
        new.close()
    finally:
        panel._timer.stop()
        panel.close()


def test_export_keeps_owned_board_alive_until_chooser_unwinds(records, qt, monkeypatch, tmp_path):
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    board.finished.connect(board.deleteLater)
    target = tmp_path / 'still-owned.json'
    close_results = []
    def choose(*args, **kwargs):
        close_results.append(board.close())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return str(target), 'JSON'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    board._export_board()
    assert close_results == [False]
    assert not sip.isdeleted(board)
    assert target.is_file()
    assert board.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(board)


def test_removed_character_never_retargets_editor_or_refresh(records, qt, monkeypatch):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('keep my target')
    with repo._session_scope() as db:
        db.query(Character).filter_by(id=alpha).delete()
    repo.set_active_character(beta)
    board.refresh()
    assert board._character_combo.currentData() is None
    assert board._board is None
    editor._save_button.click()
    assert editor.isVisible()
    assert editor._note_edit.toPlainText() == 'keep my target'
    assert 'not saved' in editor._status_label.text().lower()
    assert repo.get_business_checkin_board(beta).rows == ()
    discard(monkeypatch)
    board.close()


def test_reentrant_editor_open_and_board_refresh_do_not_duplicate_or_retarget(records, qt, monkeypatch):
    from src.ui.widgets import business_checkin_editor as module
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    choose_business(board)
    original_editor = module.BusinessCheckInEditor
    opens = []
    def create(*args, **kwargs):
        opens.append(True)
        board._open_editor()
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return original_editor(*args, **kwargs)
    monkeypatch.setattr(module, 'BusinessCheckInEditor', create)
    board._record_button.click()
    assert opens == [True]
    editor = board._editor
    editor._note_edit.setPlainText('original owner')
    editor._save_button.click()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert board._board.character_id == beta
    original_read = repo.get_business_checkin_board
    reads = []
    def read_once(*args, **kwargs):
        reads.append(True)
        board.refresh()
        board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
        return original_read(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_business_checkin_board', read_once)
        board.refresh()
    assert reads == [True]
    assert board._board is board._page is None
    assert not board._export_board_button.isEnabled()
    board.refresh()
    assert board._board.character_id == alpha
    board.close()


def test_read_failure_keeps_existing_draft_and_retire_both_exports(records, qt, monkeypatch):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    editor._stock_edit.setText('0')
    editor._note_edit.setPlainText('do not clear')
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_STORED_NOTE')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_business_checkin_history', fail)
        board.refresh()
    assert editor._draft() == ('0', '', '', 'do not clear')
    assert board._board is board._page is None
    assert 'SECRET' not in board._status_label.text()
    assert not board._export_board_button.isEnabled()
    assert not board._export_history_button.isEnabled()
    editor._save_button.click()
    assert repo.get_business_checkin_board(alpha).rows[0].note == 'do not clear'
    board.close()


def test_note_astral_limit_and_plain_context_are_preserved(records, qt):
    repo, alpha, _ = records
    editor = make_editor(repo, alpha, qt)
    editor._note_edit.setPlainText('😀' * 2000)
    assert editor._context_label.textFormat() == Qt.TextFormat.PlainText
    editor._save_button.click()
    assert repo.get_business_checkin_board(alpha).rows[0].note == '😀' * 2000


def test_export_cancel_and_failure_leave_snapshot_retryable(records, qt, monkeypatch, tmp_path):
    from src.utils.exporter import ExportResult
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    snapshot = board._board
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: ('', ''))
    board._export_board()
    assert board._board is snapshot and board._export_board_button.isEnabled()
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'failed.json'), 'JSON'))
    monkeypatch.setattr(board._exporter, 'export_business_checkins_snapshot', lambda *a: ExportResult(False, error_message='SECRET_PATH'))
    board._export_board()
    assert board._board is snapshot and board._export_board_button.isEnabled()
    assert 'failed' in board._status_label.text().lower()
    assert 'SECRET' not in board._status_label.text()
    board.close()


def test_board_and_editor_controls_reachable_at_small_window_size(records, qt):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    board.resize(1000, 700)
    editor = make_editor(repo, alpha, qt)
    editor.resize(560, 420)
    qt.processEvents()
    try:
        assert board.size().width() == 1000 and board.size().height() == 700
        for button in (board._record_button, board._refresh_button, board._export_board_button, board._export_history_button):
            assert board.rect().contains(button.mapTo(board, button.rect().bottomRight()))
        for button in (editor._save_button, editor._cancel_button):
            assert editor.rect().contains(button.mapTo(editor, button.rect().bottomRight()))
        assert editor._scroll_area.verticalScrollBar().maximum() > 0
    finally:
        editor.close()
        board.close()


@pytest.mark.parametrize('name', ['long character\n' * 100, 'W' * 4096, '😀' * 1024], ids=['multiline', 'unbroken-wide', 'emoji'])
def test_long_legacy_names_keep_board_bounded_and_identity_visible(records, qt, name):
    repo, alpha, _ = records
    with repo._session_scope() as db:
        db.query(Character).filter_by(id=alpha).update({'name': name})
    board = make_board(repo, alpha, qt)
    board.resize(1000, 700)
    qt.processEvents()
    try:
        assert board.size().width() == 1000
        assert board.size().height() == 700
        assert board.minimumSizeHint().width() <= 1000
        assert board.minimumSizeHint().height() <= 700
        assert f'#{alpha}' in board._context_label.text()
        assert name in board._character_combo.currentText()
        assert board._board.to_report()['character']['name'] == name
        assert board._context_label.textFormat() == Qt.TextFormat.PlainText
        for button in (board._record_button, board._export_board_button, board._export_history_button):
            assert board.rect().contains(button.mapTo(board, button.rect().bottomRight()))
    finally:
        board.close()
