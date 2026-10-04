"""Native manual pin controls with real disposable SQLite preferences."""

import json
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for manual pin UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from sqlalchemy import text

from src.database.business_checkins import BUSINESS_LABELS
from src.database.models import Character
from src.database.repository import Repository
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog


@pytest.fixture(scope='module')
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def clean_pin_widgets(qt):
    existing = set(qt.topLevelWidgets())
    yield
    created = [widget for widget in qt.topLevelWidgets() if widget not in existing]
    for widget in created:
        if not sip.isdeleted(widget):
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(sip.isdeleted(widget) for widget in created)


@pytest.fixture
def records(tmp_path):
    repo = Repository(str(tmp_path / 'pins.db'))
    assert repo.initialize()
    alpha = repo.get_or_create_character('Alpha <b>literal</b>').id
    beta = repo.get_or_create_character('Beta').id
    with repo._session_scope() as db:
        db.query(Character).update({'is_active': False})
    yield repo, alpha, beta
    repo.close()
    repo._session_factory.kw['bind'].dispose()


def make_board(repo, owner, qt):
    board = BusinessCheckInsDialog(repo, character_id=owner)
    board.show()
    qt.processEvents()
    return board


def business_ids(board):
    return [board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole)
            for index in range(board._businesses_table.rowCount())]


def choose_business(board, business):
    board._businesses_table.selectRow(business_ids(board).index(business))


def seed_unknown_pin(repo, owner, business):
    with repo._session_scope() as db:
        db.execute(text('INSERT INTO manual_business_pins (character_id, business_id) VALUES (:owner, :business)'),
                   {'owner': owner, 'business': business})


def seed_unknown_history(repo, owner, business):
    row = repo.save_business_checkin(owner, 'meth', note=f'History for {business}')
    with repo._session_scope() as db:
        db.execute(text('UPDATE manual_business_checkins SET business_id = :business WHERE id = :id'),
                   {'business': business, 'id': row.id})
    return row.id


def fail_private(*args, **kwargs):
    raise RuntimeError('SECRET_DATABASE_PATH_AND_NOTE')


def test_empty_pins_preserve_catalog_names_and_order(records, qt):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    assert hasattr(board, '_pin_button'), 'manual board must offer a pin control'
    assert business_ids(board) == list(BUSINESS_LABELS)
    assert [board._businesses_table.item(i, 0).text() for i in range(len(BUSINESS_LABELS))] == list(BUSINESS_LABELS.values())
    assert board._pin_button.text() == 'Pin selected business'
    assert board._pin_button.isEnabled()
    assert '0 pinned' in board._pin_status_label.text()
    assert 'personal' in board._pin_scope_label.text().lower()
    assert 'manual' in board._pin_scope_label.text().lower()


def test_pin_reorders_persists_and_unpins_without_hiding_businesses(records, qt):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    assert hasattr(board, '_pin_button'), 'manual board must offer a pin control'
    choose_business(board, 'bunker')
    board._pin_button.click()
    assert business_ids(board)[0] == 'bunker'
    assert business_ids(board)[1:] == [key for key in BUSINESS_LABELS if key != 'bunker']
    assert board._selected_business_id() == 'bunker'
    assert board._pin_button.text() == 'Unpin selected business'
    assert board._businesses_table.item(0, 0).text() == '★ ' + BUSINESS_LABELS['bunker']
    assert 'Saved' in board._pin_saved_notice_label.text()
    assert f'#{alpha}' in board._pin_saved_notice_label.text()
    assert repo.get_manual_business_pins(alpha).business_ids == ('bunker',)
    board.close()
    reopened = make_board(repo, alpha, qt)
    assert business_ids(reopened)[0] == 'bunker'
    reopened._pin_button.click()
    assert business_ids(reopened) == list(BUSINESS_LABELS)
    assert reopened._selected_business_id() == 'bunker'
    assert repo.get_manual_business_pins(alpha).business_ids == ()


def test_no_selected_owner_disables_pins_without_creating_preferences(records, qt):
    repo, _, _ = records
    board = make_board(repo, None, qt)
    assert hasattr(board, '_pin_button'), 'manual board must offer a pin control'
    assert board._pins is None
    assert not board._pin_button.isEnabled()
    board._set_selected_business_pin()
    with repo._session_scope() as db:
        assert db.execute(text('SELECT count(*) FROM manual_business_pins')).scalar_one() == 0


def test_catalog_and_legacy_pins_have_deterministic_order_and_remain_unpinnable(records, qt):
    repo, alpha, _ = records
    for key in ('bunker', 'acid_lab'):
        repo.set_manual_business_pin(alpha, key, True)
    for key in ('z_legacy', 'a_legacy'):
        seed_unknown_pin(repo, alpha, key)
    seed_unknown_history(repo, alpha, 'y_history')
    seed_unknown_history(repo, alpha, 'b_history')
    seed_unknown_history(repo, alpha, 'z_legacy')
    board = make_board(repo, alpha, qt)
    known_pins = [key for key in BUSINESS_LABELS if key in ('bunker', 'acid_lab')]
    unpinned = [key for key in BUSINESS_LABELS if key not in known_pins]
    assert business_ids(board) == known_pins + ['a_legacy', 'z_legacy'] + unpinned + ['b_history', 'y_history']
    choose_business(board, 'a_legacy')
    assert board._page.total == 0
    assert not board._record_button.isEnabled()
    assert board._pin_button.text() == 'Unpin selected business'
    assert board._pin_button.isEnabled()
    board._pin_button.click()
    assert 'a_legacy' not in business_ids(board)
    choose_business(board, 'z_legacy')
    board._pin_button.click()
    assert board._selected_business_id() == 'z_legacy'
    assert business_ids(board)[-1] == 'z_legacy'
    assert board._page.total == 1
    assert not board._pin_button.isEnabled()
    assert not board._record_button.isEnabled()
    board._set_selected_business_pin()
    assert repo.get_manual_business_pins(alpha).business_ids == tuple(sorted(known_pins))


def test_pins_are_per_character_and_refresh_preserves_selected_history_page(records, qt):
    repo, alpha, beta = records
    repo.set_manual_business_pin(alpha, 'bunker', True)
    repo.set_manual_business_pin(beta, 'acid_lab', True)
    for index in range(28):
        repo.save_business_checkin(alpha, 'bunker', note=f'Entry {index}')
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    board._next_button.click()
    assert board._page.offset == 25
    board._pin_button.click()
    assert board._selected_business_id() == 'bunker'
    assert board._page.offset == 25
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    assert business_ids(board)[0] == 'acid_lab'
    assert board._pins.character_id == beta
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    assert business_ids(board) == list(BUSINESS_LABELS)
    assert board._pins.business_ids == ()


@pytest.mark.parametrize('read_error', ['exception', 'corrupt', 'over-limit'])
def test_pin_read_failure_keeps_full_board_history_exports_and_disables_writes(records, qt, monkeypatch, tmp_path, read_error):
    repo, alpha, _ = records
    repo.save_business_checkin(alpha, 'bunker', stock_percent=0, note='saved history')
    repo.set_manual_business_pin(alpha, 'bunker', True)
    board = make_board(repo, alpha, qt)
    snapshot = board._board.to_report()
    with monkeypatch.context() as patch:
        if read_error == 'exception':
            patch.setattr(repo, 'get_manual_business_pins', fail_private)
        else:
            with repo._session_scope() as db:
                db.execute(text('DELETE FROM manual_business_pins'))
            if read_error == 'corrupt':
                seed_unknown_pin(repo, alpha, 'invalid pin')
            else:
                for index in range(257):
                    seed_unknown_pin(repo, alpha, f'legacy_{index}')
        board.refresh()
        choose_business(board, 'bunker')
        assert board._board.to_report()['rows'] == snapshot['rows']
        assert business_ids(board) == list(BUSINESS_LABELS)
        assert board._page.total == 1
        assert board._note_edit.toPlainText() == 'saved history'
        assert board._pins is None
        assert not board._pin_button.isEnabled()
        assert board._record_button.isEnabled()
        assert board._export_board_button.isEnabled() and board._export_history_button.isEnabled()
        assert 'could not be loaded' in board._pin_status_label.text()
        assert 'SECRET' not in board._pin_status_label.text()
        board._set_selected_business_pin()
        target = tmp_path / 'available.json'
        patch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), 'JSON'))
        board._export_board()
        assert json.loads(target.read_text()) == board._board.to_report()
    if read_error != 'exception':
        with repo._session_scope() as db:
            db.execute(text('DELETE FROM manual_business_pins'))
    board.refresh()
    assert board._pins is not None and board._pin_button.isEnabled()


def test_write_failure_keeps_selection_observations_and_retry_available(records, qt, monkeypatch):
    repo, alpha, _ = records
    repo.save_business_checkin(alpha, 'bunker', note='unchanged')
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    snapshot = board._board
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'set_manual_business_pin', fail_private)
        board._pin_button.click()
        assert board._board is snapshot
        assert board._selected_business_id() == 'bunker'
        assert board._page.rows[0].note == 'unchanged'
        assert board._pins.business_ids == ()
        assert 'not saved' in board._pin_status_label.text()
        assert 'SECRET' not in board._pin_status_label.text()
        assert not board._pin_saved_notice_label.isVisible()
        assert board._pin_button.isEnabled()
    board._pin_button.click()
    assert repo.get_manual_business_pins(alpha).business_ids == ('bunker',)


def test_cap_failure_explains_unpin_then_allows_addition(records, qt):
    repo, alpha, _ = records
    for index in range(256):
        seed_unknown_pin(repo, alpha, f'legacy_{index:03}')
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    board._pin_button.click()
    assert 'not saved' in board._pin_status_label.text()
    assert 'Unpin' in board._pin_status_label.text()
    assert len(repo.get_manual_business_pins(alpha).business_ids) == 256
    choose_business(board, 'legacy_000')
    board._pin_button.click()
    assert len(repo.get_manual_business_pins(alpha).business_ids) == 255
    choose_business(board, 'bunker')
    board._pin_button.click()
    assert business_ids(board)[0] == 'bunker'
    assert len(repo.get_manual_business_pins(alpha).business_ids) == 256


@pytest.mark.parametrize('read_method', ['get_business_checkin_characters', 'get_business_checkin_board', 'get_business_checkin_history', 'get_manual_business_pins'])
def test_committed_pin_acknowledgment_survives_following_read_failure(records, qt, monkeypatch, read_method):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    with monkeypatch.context() as patch:
        patch.setattr(repo, read_method, fail_private)
        board._pin_button.click()
        assert 'Saved' in board._pin_saved_notice_label.text()
        assert f'#{alpha}' in board._pin_saved_notice_label.text()
        assert 'Bunker: pinned' in board._pin_saved_notice_label.text()
        assert 'SECRET' not in board._pin_saved_notice_label.text()
        assert not board._pin_button.isEnabled()
        if read_method == 'get_manual_business_pins':
            assert board._board is not None and board._page is not None
        else:
            assert board._board is board._page is None
    assert repo.get_manual_business_pins(alpha).business_ids == ('bunker',)
    board.refresh()
    assert business_ids(board)[0] == 'bunker'


def test_pin_action_captures_owner_business_desired_and_blocks_reentrant_writes_close_export(records, qt, monkeypatch):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    original = repo.set_manual_business_pin
    calls = []
    def save(owner, business, desired):
        calls.append((owner, business, desired))
        assert board._busy
        assert not board._pin_button.isEnabled()
        assert not board._export_board_button.isEnabled()
        board._set_selected_business_pin()
        board._export_board()
        assert not board.close()
        board.reject()
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        board._set_selected_business_pin()
        return original(owner, business, desired)
    monkeypatch.setattr(repo, 'set_manual_business_pin', save)
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('export during pin write'))
    board._pin_button.click()
    assert calls == [(alpha, 'bunker', True)]
    assert repo.get_manual_business_pins(alpha).business_ids == ('bunker',)
    assert repo.get_manual_business_pins(beta).business_ids == ()
    assert board._pins is None and board._board is None
    assert board._character_combo.currentData() == beta
    assert f'#{alpha}' in board._pin_saved_notice_label.text()
    board.refresh()
    assert board._pins.character_id == beta
    assert business_ids(board) == list(BUSINESS_LABELS)


def test_reentrant_pin_read_cannot_render_previous_characters_preferences(records, qt, monkeypatch):
    repo, alpha, beta = records
    repo.set_manual_business_pin(alpha, 'bunker', True)
    board = make_board(repo, alpha, qt)
    original = repo.get_manual_business_pins
    def read(owner):
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        return original(owner)
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_manual_business_pins', read)
        board.refresh()
    assert board._board is board._pins is board._page is None
    assert not board._pin_button.isEnabled()
    assert board._character_combo.currentData() == beta
    board.refresh()
    assert board._pins.character_id == beta
    assert business_ids(board) == list(BUSINESS_LABELS)


def test_reentrant_business_change_preserves_new_selection_after_original_pin_commits(records, qt, monkeypatch):
    repo, alpha, _ = records
    repo.save_business_checkin(alpha, 'acid_lab', note='selected during pin save')
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    original = repo.set_manual_business_pin
    def save(owner, business, desired):
        choose_business(board, 'acid_lab')
        return original(owner, business, desired)
    monkeypatch.setattr(repo, 'set_manual_business_pin', save)
    board._pin_button.click()
    assert repo.get_manual_business_pins(alpha).business_ids == ('bunker',)
    assert board._selected_business_id() == 'acid_lab'
    assert board._pins.business_ids == ('bunker',)
    assert business_ids(board)[0] == 'bunker'
    assert board._page.rows[0].note == 'selected during pin save'
    assert board._pin_button.text() == 'Pin selected business'


@pytest.mark.parametrize('guard', ['_busy', '_closing', '_exporting', '_closed'])
def test_direct_pin_action_honors_lifecycle_guards(records, qt, monkeypatch, guard):
    repo, alpha, _ = records
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    monkeypatch.setattr(repo, 'set_manual_business_pin', lambda *a, **k: pytest.fail('guarded pin write'))
    if guard == '_closed':
        assert board.close()
    else:
        setattr(board, guard, True)
    board._set_selected_business_pin()
    assert repo.get_manual_business_pins(alpha).business_ids == ()
    if guard != '_closed':
        setattr(board, guard, False)


@pytest.mark.parametrize('kind', ['board', 'history'])
def test_export_retains_full_observations_and_pin_action_is_blocked_in_chooser(records, qt, monkeypatch, tmp_path, kind):
    repo, alpha, _ = records
    repo.save_business_checkin(alpha, 'bunker', note='first')
    repo.save_business_checkin(alpha, 'acid_lab', note='second')
    seed_unknown_history(repo, alpha, 'legacy_history')
    repo.set_manual_business_pin(alpha, 'acid_lab', True)
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    snapshot = board._board if kind == 'board' else board._page
    target = tmp_path / f'{kind}.json'
    def choose(*args, **kwargs):
        assert not board._pin_button.isEnabled()
        board._set_selected_business_pin()
        return str(target), 'JSON'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    getattr(board, f'_export_{kind}')()
    assert json.loads(target.read_text()) == snapshot.to_report()
    assert len(board._board.rows) == 3
    assert repo.get_manual_business_pins(alpha).business_ids == ('acid_lab',)
    assert board._pin_button.isEnabled()


def test_pin_changes_leave_existing_checkin_and_character_drafts_owned(records, qt, monkeypatch):
    repo, alpha, beta = records
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('Alpha bunker draft')
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('Gamma draft')
    board._pin_button.click()
    assert board._editor is editor and board._creator is creator
    assert editor._note_edit.toPlainText() == 'Alpha bunker draft'
    assert creator._name_edit.text() == 'Gamma draft'
    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    choose_business(board, 'acid_lab')
    board._pin_button.click()
    editor._save_button.click()
    assert repo.get_business_checkin_history(alpha, 'bunker').total == 1
    assert repo.get_business_checkin_board(beta).rows == ()
    creator._save_button.click()
    gamma = board._board.character_id
    assert gamma not in (alpha, beta)
    assert board._board.character_name == 'Gamma draft'
    assert board._pins.business_ids == ()
    assert repo.get_manual_business_pins(beta).business_ids == ('acid_lab',)


@pytest.mark.parametrize('name', ['W' * 4096, 'many lines\n' * 100, '😀' * 1024], ids=['wide', 'multiline', 'emoji'])
def test_pin_controls_and_notices_fit_1000_by_700_with_wide_legacy_names(records, qt, name):
    repo, alpha, _ = records
    with repo._session_scope() as db:
        db.query(Character).filter_by(id=alpha).update({'name': name})
    board = make_board(repo, alpha, qt)
    choose_business(board, 'bunker')
    board._pin_button.click()
    board.resize(1000, 700)
    qt.processEvents()
    assert board.width() == 1000 and board.height() == 700
    assert board.minimumSizeHint().width() <= 1000
    assert board.minimumSizeHint().height() <= 700
    assert board._board.character_name == name
    assert board._pin_scope_label.height() <= 2 * board._pin_scope_label.fontMetrics().lineSpacing()
    for control in (board._pin_button, board._pin_scope_label, board._pin_status_label,
                    board._pin_saved_notice_label, board._record_button, board._add_character_button,
                    board._export_board_button, board._export_history_button):
        assert board.rect().contains(control.mapTo(board, control.rect().topLeft()))
        assert board.rect().contains(control.mapTo(board, control.rect().bottomRight()))
    for label in (board._pin_status_label, board._pin_saved_notice_label, board._pin_scope_label):
        assert label.textFormat() == Qt.TextFormat.PlainText
