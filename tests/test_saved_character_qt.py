"""Native saved-character onboarding, with no tracking or capture required."""

import importlib
import importlib.util
import json
import os

import pytest

if os.environ.get('GTA_RUN_QT_TESTS') != '1':
    pytest.skip('set GTA_RUN_QT_TESTS=1 for saved-character UI tests', allow_module_level=True)

QtWidgets = pytest.importorskip('PyQt6.QtWidgets')
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.database.models import Character
from src.database.repository import Repository
from src.ui.widgets.business_checkins_dialog import BusinessCheckInsDialog


@pytest.fixture
def qt(native_qt_application):
    assert native_qt_application is not None
    return native_qt_application


@pytest.fixture(autouse=True)
def clean_widgets(qt):
    existing = set(qt.topLevelWidgets())
    yield
    for widget in qt.topLevelWidgets():
        if widget not in existing and not sip.isdeleted(widget):
            widget.hide()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def repo(tmp_path):
    repository = Repository(str(tmp_path / 'characters.db'))
    assert repository.initialize()
    yield repository
    repository.close()
    repository._session_factory.kw['bind'].dispose()


def board_for(repo, qt, owner=None):
    board = BusinessCheckInsDialog(repo, character_id=owner)
    board.show()
    qt.processEvents()
    return board


def creator_for(repo, qt):
    name = 'src.ui.widgets.saved_character_dialog'
    assert importlib.util.find_spec(name) is not None, 'saved-character editor must exist'
    creator = importlib.import_module(name).SavedCharacterDialog(repo)
    creator.show()
    qt.processEvents()
    return creator


def choose_business(board, business='bunker'):
    for index in range(board._businesses_table.rowCount()):
        if board._businesses_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == business:
            board._businesses_table.selectRow(index)
            return
    raise AssertionError(f'missing business {business}')


def fail_secret(*args, **kwargs):
    raise RuntimeError('SECRET_DATABASE_PATH')


def test_empty_board_can_create_select_record_export_and_reopen(repo, qt, monkeypatch, tmp_path):
    board = board_for(repo, qt)
    assert hasattr(board, '_add_character_button'), 'empty board needs an Add saved character action'
    assert board._add_character_button.isEnabled()
    assert 'Add saved character' in board._status_label.text()
    board._add_character_button.click()
    creator = board._creator
    assert creator.parent() is board and not creator.isModal()
    assert creator._repository is repo
    creator._name_edit.setText('  Mei <b>literal</b> 😀  ')
    creator._save_button.click()
    owner = board._character_combo.currentData()
    assert board._creator is None
    assert owner == board._board.character_id
    assert board._board.character_name == 'Mei <b>literal</b> 😀'
    assert 'Created' in board._character_saved_notice_label.text()
    assert f'#{owner}' in board._character_saved_notice_label.text()
    assert '<b>literal</b>' in board._character_saved_notice_label.text()
    assert board._character_saved_notice_label.textFormat() == Qt.TextFormat.PlainText
    assert repo.get_all_characters()[0].is_active is False
    choose_business(board)
    board._record_button.click()
    board._editor._value_edit.setText('0')
    board._editor._note_edit.setPlainText('My manual note')
    board._editor._save_button.click()
    target = tmp_path / 'manual.json'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), 'JSON'))
    board._export_board_button.click()
    report = json.loads(target.read_text())
    assert report == board._board.to_report()
    assert board.close()
    reopened_repo = Repository(str(tmp_path / 'characters.db'))
    assert reopened_repo.initialize()
    try:
        reopened = board_for(reopened_repo, qt, owner)
        assert reopened._board.character_id == owner
        saved = reopened_repo.get_business_checkin_board(owner).rows[0]
        assert (saved.stock_value, saved.note) == (0, 'My manual note')
        with reopened_repo._session_scope() as db:
            for table in ('sessions', 'activities', 'earnings', 'business_snapshots'):
                assert db.execute(text(f'SELECT count(*) FROM {table}')).scalar() == 0
        reopened.close()
    finally:
        reopened_repo.close()
        reopened_repo._session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('name', ['😀' * 50, 'A\u00a0B', 'e\u0301', '<b>Alice</b>'])
def test_valid_literal_unicode_names_are_preserved(repo, qt, name):
    creator = creator_for(repo, qt)
    results = []
    creator.saved.connect(results.append)
    creator._name_edit.setText(name)
    creator._save_button.click()
    assert results[0].name == name
    assert repo.get_all_characters()[0].name == name
    assert not creator.isVisible()


@pytest.mark.parametrize('value', ['', '   ', '😀' * 51, 'W' * 100000, 'Alice\nBob', '\tAlice', 'Alice\x00Bob', 'Alice\u2028Bob', '\u200b', '\ud800'], ids=['empty', 'blank', 'astral-over-limit', 'huge', 'newline', 'edge-tab', 'nul', 'separator', 'format-only', 'surrogate'])
def test_invalid_full_input_is_retained_without_writing(repo, qt, value):
    creator = creator_for(repo, qt)
    creator._name_edit.setText(value)
    assert creator._name_edit.text() == value
    creator._save_button.click()
    assert creator.isVisible()
    assert creator._name_edit.text() == value
    assert repo.get_all_characters() == []
    assert 'not saved' in creator._status_label.text().lower()
    assert creator._save_button.isEnabled()


def test_exact_name_reuse_selects_existing_id_and_keeps_active_flags(repo, qt, monkeypatch):
    old = repo.get_or_create_character('Existing').id
    other = repo.get_or_create_character('Other').id
    repo.set_active_character(other)
    board = board_for(repo, qt, other)
    monkeypatch.setattr(repo, 'set_active_character', fail_secret)
    board._add_character_button.click()
    board._creator._name_edit.setText(' Existing ')
    board._creator._save_button.click()
    assert board._board.character_id == old
    assert 'already exists' in board._character_saved_notice_label.text().lower()
    assert len(repo.get_all_characters()) == 2
    assert repo.get_active_character().id == other
    board.close()


def test_duplicate_legacy_names_keep_draft_and_require_id_selection(repo, qt):
    with repo._session_scope() as db:
        db.add_all([Character(name='Twin', is_active=False), Character(name='Twin', is_active=False)])
    board = board_for(repo, qt)
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('Twin')
    creator._save_button.click()
    assert creator.isVisible() and creator._name_edit.text() == 'Twin'
    assert 'ID' in creator._status_label.text()
    assert board._character_combo.currentData() is None
    assert len(repo.get_all_characters()) == 2


def test_real_commit_failure_keeps_draft_then_retry_commits_once(repo, qt, monkeypatch):
    creator = creator_for(repo, qt)
    creator._name_edit.setText('Retry me')
    results = []
    creator.saved.connect(results.append)
    def fail_commit(*args, **kwargs):
        raise SQLAlchemyError('SECRET_DATABASE_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail_commit)
        creator._save_button.click()
    assert results == []
    assert creator.isVisible() and creator._name_edit.text() == 'Retry me'
    assert 'SECRET' not in creator._status_label.text()
    assert repo.get_all_characters() == []
    creator._save_button.click()
    creator._save()
    assert len(results) == len(repo.get_all_characters()) == 1


@pytest.mark.parametrize('read', ['get_business_checkin_characters', 'get_business_checkin_board', 'get_business_checkin_history'])
def test_successful_creation_remains_committed_when_refresh_fails(repo, qt, monkeypatch, read):
    old = repo.get_or_create_character('Old active').id
    board = board_for(repo, qt, old)
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('New saved')
    with monkeypatch.context() as patch:
        patch.setattr(repo, read, fail_secret)
        creator._save_button.click()
    new = next(c for c in repo.get_all_characters() if c.name == 'New saved')
    assert creator._closed and creator._committed_result.id == new.id
    assert not creator.isVisible() and board._creator is None
    assert 'Created' in board._character_saved_notice_label.text()
    assert f'#{new.id}' in board._character_saved_notice_label.text()
    assert 'could not' in board._status_label.text().lower()
    assert 'SECRET' not in board._status_label.text()
    assert board._board is board._page is None
    if read == 'get_business_checkin_characters':
        assert board._character_combo.currentData() is None
    assert not board._record_button.isEnabled() and not board._export_board_button.isEnabled()
    creator._save()
    assert len(repo.get_all_characters()) == 2
    board.refresh()
    assert board._board.character_id == new.id
    board.close()


def test_disappearing_created_id_never_falls_back_to_active_or_only_row(repo, qt, monkeypatch):
    old = repo.get_or_create_character('Old active').id
    board = board_for(repo, qt, old)
    original = repo.create_saved_character
    committed = []
    def create_and_remove(name):
        result = original(name)
        committed.append(result)
        with repo._session_scope() as db:
            db.query(Character).filter_by(id=result.id).delete()
        return result
    monkeypatch.setattr(repo, 'create_saved_character', create_and_remove)
    board._add_character_button.click()
    board._creator._name_edit.setText('Removed after commit')
    board._creator._save_button.click()
    assert board._character_combo.currentData() is None and board._board is None
    assert f'#{committed[0].id}' in board._character_saved_notice_label.text()
    assert 'unavailable' in board._status_label.text().lower()
    assert not board._record_button.isEnabled()
    board.refresh()
    assert board._character_combo.currentData() is None and board._board is None
    with repo._session_scope() as db:
        db.add(Character(id=committed[0].id, name=committed[0].name, is_active=False))
    board.refresh()
    assert board._board.character_id == committed[0].id
    board.close()


def test_repeated_open_save_and_reentry_have_one_owned_creator_and_one_row(repo, qt, monkeypatch):
    board = board_for(repo, qt)
    board._add_character_button.click()
    creator = board._creator
    board._add_character_button.click()
    assert board._creator is creator
    creator._name_edit.setText('Once')
    original = repo.create_saved_character
    def create_once(name):
        assert not creator._save_button.isEnabled() and not creator._name_edit.isEnabled()
        creator._save()
        creator.reject()
        assert creator.close() is False
        assert board.close() is False
        return original(name)
    monkeypatch.setattr(repo, 'create_saved_character', create_once)
    emitted = []
    def observe(result):
        assert creator._closed and creator._committed_result is result
        creator._save()
        emitted.append(result)
    creator.saved.connect(observe)
    creator._save_button.click()
    assert len(emitted) == len(repo.get_all_characters()) == 1
    assert board._creator is None
    board._add_character_button.click()
    newer = board._creator
    assert newer is not creator
    board._creator_finished(creator)
    assert board._creator is newer
    board.close()


@pytest.mark.parametrize('action', ['cancel', 'escape', 'close', 'accept', 'done'])
def test_dirty_draft_close_paths_require_discard_and_cannot_accept(repo, qt, monkeypatch, action):
    creator = creator_for(repo, qt)
    creator._name_edit.setText('Unsaved')
    def invoke():
        if action == 'cancel':
            creator._cancel_button.click()
        elif action == 'escape':
            QTest.keyClick(creator, Qt.Key.Key_Escape)
        elif action == 'close':
            creator.close()
        elif action == 'accept':
            creator.accept()
        else:
            creator.done(QtWidgets.QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Cancel)
    invoke()
    assert creator.isVisible() and creator._name_edit.text() == 'Unsaved'
    assert creator._save_button.isEnabled()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    invoke()
    assert not creator.isVisible() and creator._closed
    assert creator.result() == QtWidgets.QDialog.DialogCode.Rejected
    creator._save()
    assert repo.get_all_characters() == []


def test_board_close_guards_both_drafts_and_reentrant_close(repo, qt, monkeypatch):
    owner = repo.get_or_create_character('Owner').id
    board = board_for(repo, qt, owner)
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    editor._note_edit.setPlainText('Keep check-in')
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('Keep character')
    seen = []
    def cancel_close(parent, *args):
        seen.append(parent)
        # Qt suppresses nested closeEvent delivery and returns True for a
        # recursive close() even when the outer close is ultimately rejected.
        board.close()
        board.reject()
        assert not board._closed and board.isVisible()
        board._open_creator()
        board._open_editor()
        if parent is creator:
            assert not creator._name_edit.isEnabled() and not creator._save_button.isEnabled()
            creator._save()
            creator.reject()
        assert not board._add_character_button.isEnabled()
        return QtWidgets.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', cancel_close)
    assert board.close() is False
    assert board.isVisible() and editor.isVisible() and creator.isVisible()
    # Exercise the second child's confirmation too, after the first is clean.
    editor._note_edit.clear()
    assert board.close() is False
    assert creator in seen and creator.isVisible()
    assert repo.get_all_characters()[0].id == owner
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard)
    assert board.close()
    assert not board.isVisible() and not creator.isVisible() and board._creator is None


def test_existing_checkin_editor_stays_fixed_while_new_character_is_selected(repo, qt):
    owner = repo.get_or_create_character('Original').id
    board = board_for(repo, qt, owner)
    choose_business(board)
    board._record_button.click()
    editor = board._editor
    editor._stock_edit.setText('0')
    editor._note_edit.setPlainText('Original draft')
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('New owner')
    creator._save_button.click()
    new_owner = board._character_combo.currentData()
    assert new_owner != owner
    assert board._selected_business_id() == 'bunker'
    choose_business(board, 'meth')
    board._record_button.click()
    assert board._editor is editor
    assert (editor._character_id, editor._business_id) == (owner, 'bunker')
    editor._save_button.click()
    assert repo.get_business_checkin_board(owner).rows[0].note == 'Original draft'
    assert repo.get_business_checkin_board(new_owner).rows == ()
    assert board._board.character_id == new_owner
    assert board._selected_business_id() == 'meth'
    board.close()


def test_creator_open_is_guarded_during_busy_export_and_construction(repo, qt, monkeypatch, tmp_path):
    owner = repo.get_or_create_character('Owner').id
    board = board_for(repo, qt, owner)
    board._busy = True
    board._open_creator()
    assert board._creator is None
    board._busy = False
    def choose(*args, **kwargs):
        board._open_creator()
        assert board._creator is None
        assert not board._add_character_button.isEnabled()
        return '', ''
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', choose)
    board._export_board()
    module = importlib.import_module('src.ui.widgets.saved_character_dialog')
    original = module.SavedCharacterDialog
    def construct(*args, **kwargs):
        board._open_creator()
        assert board.close() is False
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'SavedCharacterDialog', construct)
    board._open_creator()
    assert len(board.findChildren(original)) == 1
    assert board._creator is not None
    board.close()


def test_large_input_and_literal_names_keep_layout_bounded(repo, qt):
    board = board_for(repo, qt)
    board.resize(1000, 700)
    board._add_character_button.click()
    creator = board._creator
    creator.resize(560, 340)
    creator._name_edit.setText('W' * 100000)
    creator._save_button.click()
    qt.processEvents()
    assert creator.size().width() == 560 and creator.size().height() == 340
    for button in (creator._save_button, creator._cancel_button):
        assert creator.rect().contains(button.mapTo(creator, button.rect().bottomRight()))
    creator._name_edit.setText('W' * 50)
    creator._save_button.click()
    qt.processEvents()
    assert board.size().width() == 1000 and board.size().height() == 700
    for button in (board._add_character_button, board._record_button, board._export_board_button):
        assert board.rect().contains(button.mapTo(board, button.rect().bottomRight()))
    board.close()


def test_board_selection_change_does_not_change_creator_repository_or_name(repo, qt):
    first = repo.get_or_create_character('First').id
    second = repo.get_or_create_character('Second').id
    board = board_for(repo, qt, first)
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('Third')
    board._character_combo.setCurrentIndex(board._character_combo.findData(second))
    assert creator._repository is repo and creator._name_edit.text() == 'Third'
    creator._save_button.click()
    assert board._board.character_name == 'Third'
    assert board._board.character_id not in (first, second)
    board.close()


@pytest.mark.parametrize('error', ['CharacterProfileLimitError', 'CharacterProfileUnavailable'])
def test_safe_capacity_and_storage_errors_retain_retryable_draft(repo, qt, monkeypatch, error):
    module = importlib.import_module('src.database.character_profiles')
    failure = getattr(module, error)()
    creator = creator_for(repo, qt)
    creator._name_edit.setText('Keep this')
    def fail(name):
        raise failure
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'create_saved_character', fail)
        creator._save_button.click()
    assert str(failure) in creator._status_label.text()
    assert creator._name_edit.text() == 'Keep this' and creator.isVisible()
    assert creator._save_button.isEnabled() and creator._committed_result is None
    assert repo.get_all_characters() == []
    creator._save_button.click()
    assert repo.get_all_characters()[0].name == 'Keep this'


def test_failed_creator_construction_is_retryable_and_close_guard_is_released(repo, qt, monkeypatch):
    module = importlib.import_module('src.ui.widgets.saved_character_dialog')
    board = board_for(repo, qt)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'SavedCharacterDialog', fail_secret)
        board._open_creator()
    assert board._creator is None and not board._opening_creator
    assert board._add_character_button.isEnabled()
    assert 'could not' in board._status_label.text().lower()
    assert 'SECRET' not in board._status_label.text()
    board._add_character_button.click()
    assert board._creator is not None
    board.close()


def test_empty_creator_closes_without_discard_confirmation(repo, qt, monkeypatch):
    creator = creator_for(repo, qt)
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', fail_secret)
    assert creator.close()
    assert creator._closed and not creator.isVisible()
    assert repo.get_all_characters() == []


def test_user_selection_can_replace_pending_created_id_after_refresh_failure(repo, qt, monkeypatch):
    old = repo.get_or_create_character('Old').id
    board = board_for(repo, qt, old)
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('New')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_business_checkin_characters', fail_secret)
        creator._save_button.click()
    assert board._character_combo.currentData() is None
    board._character_combo.setCurrentIndex(board._character_combo.findData(old))
    assert board._board.character_id == old
    board.refresh()
    assert board._board.character_id == old
    assert 'Created' in board._character_saved_notice_label.text()
    board.close()


@pytest.mark.parametrize('read', ['get_business_checkin_board', 'get_business_checkin_history'])
def test_creation_during_board_read_retires_old_generation_and_selects_committed_id(repo, qt, monkeypatch, read):
    old = repo.get_or_create_character('Old').id
    board = board_for(repo, qt, old)
    board._add_character_button.click()
    creator = board._creator
    creator._name_edit.setText('Created during read')
    original = getattr(repo, read)
    triggered = False
    def read_with_creation(*args, **kwargs):
        nonlocal triggered
        if not triggered:
            triggered = True
            creator._save_button.click()
            assert board._board is None
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(repo, read, read_with_creation)
        board.refresh()
    assert creator._closed and board._creator is None
    assert board._board.character_id == creator._committed_result.id
    assert board._character_combo.currentData() == creator._committed_result.id
    assert board._board.character_id != old
    board.close()
