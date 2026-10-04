"""Real live reading -> reviewed draft -> committed check-in and captured export."""

import copy
import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for live check-in workflow", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QTimer

from src.app import AppState
from src.database.repository import Repository
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
    select_business,
)
from tests.test_business_screen_target_app_qt import saved_rows, synthetic_business_io


PERSONAL_NOTE = "  Keep my personal note <b>literal</b>\n\tSnow 雪 and NBSP\u00a0stay.  "


def preview(editor, qt, interaction):
    assert hasattr(editor, "_preview_live_button"), "Manual drafts need explicit live-value preview"
    errors = []

    def interact():
        dialog = QtWidgets.QApplication.activeModalWidget()
        try:
            assert dialog is editor._live_preview and dialog is not None
            assert editor._busy
            interaction(dialog)
        except BaseException as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, interact)
    editor._preview_live_button.click()
    qt.processEvents()
    if errors:
        raise errors[0]
    assert not editor._busy


def state(manager):
    return copy.deepcopy((
        manager._data, manager._optimizer._business_states,
        manager._business_parser.get_all_last_readings(),
        manager.business_screen_target, manager._business_screen_generation,
        manager.state, manager._optimizer._cooldowns,
    ))


@pytest.mark.parametrize("kind,business,expected", [
    ("ocr_text", "bunker", (50, 0, 0)),
    ("selected_target", "bunker", (50, 0, 0)),
    ("manual_entry", "bunker", (0, None, 2**63 - 1)),
    ("manual_entry", "nightclub", (25, None, None)),
])
def test_live_measurements_are_reviewed_once_before_save_and_reopen_export(
    app_window, qt, monkeypatch, tmp_path, kind, business, expected,
):
    manager, window, repository, alpha, beta = app_window
    if kind == "manual_entry":
        manager.set_manual_business_reading(business, expected[0], expected[1], expected[2])
    else:
        manager.set_business_screen_target("bunker" if kind == "selected_target" else None)
        label = "Meth Lab" if kind == "selected_target" else "Bunker"
        calls = synthetic_business_io(manager, monkeypatch, text=[
            label + " Stock: 5/10", "Supplies: 0%", "Value: $0",
        ])
        manager._process_business_computer()
        assert len(calls) == 3
    observed = copy.deepcopy(manager.get_business_state(business))
    assert tuple(observed[key] for key in ("stock", "supply", "value")) == expected
    assert observed["identity_source"] == kind
    before_records = legacy_rows(repository), saved_rows(repository)
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt, business)
    editor._stock_edit.setText("unsubmitted invalid value")
    editor._supply_edit.setText("77")
    editor._value_edit.setText("88")
    editor._note_edit.setPlainText(PERSONAL_NOTE)
    before_draft = editor._draft()
    live = state(manager)
    settings = manager._settings._config_path.read_bytes()

    def use(dialog):
        snapshot = dialog._snapshot
        assert snapshot.business_id == business
        assert (snapshot.stock_percent, snapshot.supply_percent, snapshot.stock_value) == expected
        assert snapshot.identity_source == kind
        assert snapshot.updated_at == observed["updated"]
        assert snapshot.captured_at.utcoffset().total_seconds() == 0
        assert str(alpha) in dialog._context_label.text()
        assert editor._draft() == before_draft
        assert (legacy_rows(repository), saved_rows(repository)) == before_records
        dialog._use_button.click()

    preview(editor, qt, use)
    assert editor._draft() == tuple("" if value is None else str(value) for value in expected) + (PERSONAL_NOTE,)
    assert editor.isVisible() and not editor._closed
    assert (legacy_rows(repository), saved_rows(repository)) == before_records
    assert state(manager) == live and manager._settings._config_path.read_bytes() == settings
    editor._save_button.click()
    qt.processEvents()
    assert editor._closed and not editor.isVisible()
    saved = repository.get_business_checkin_history(alpha, business).rows[0]
    assert (saved.stock_percent, saved.supply_percent, saved.stock_value) == expected
    assert saved.note == PERSONAL_NOTE and saved.recorded_at.utcoffset().total_seconds() == 0
    assert repository.get_business_checkin_history(beta, business).total == 0
    assert legacy_rows(repository) == before_records[0]
    assert state(manager) == live and manager._settings._config_path.read_bytes() == settings

    board.close()
    qt.processEvents()
    board = open_board(window, qt)
    board._character_combo.setCurrentIndex(board._character_combo.findData(alpha))
    select_business(board, business)
    assert board._page.rows[0] == saved
    accepted = board._page.to_report()
    destination = tmp_path / "reviewed-checkin.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(destination), "JSON"))
    board._export_history_button.click()
    assert json.loads(destination.read_text()) == accepted
    assert accepted["rows"][0] == saved.to_dict()
    assert "User-recorded" in accepted["field_notes"]["stock_value"]
    assert not {"identity_source", "updated_at", "live_updated_at", "preview_captured_at"}.intersection(accepted["rows"][0])
    reopened = Repository(repository._db_path)
    try:
        assert reopened.get_business_checkin_history(alpha, business).rows[0] == saved
    finally:
        reopened.close()
        reopened._session_factory.kw["bind"].dispose()


def test_modal_capture_survives_board_tracking_and_live_changes_without_retargeting(
    app_window, qt, monkeypatch,
):
    import src.app as app_module

    manager, window, repository, alpha, beta = app_window
    manager.set_manual_business_reading("bunker", 12, None, 0)
    observed = copy.deepcopy(manager.get_business_state("bunker"))
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt, "bunker")
    editor._note_edit.setPlainText(PERSONAL_NOTE)
    monkeypatch.setattr(app_module, "get_repository", lambda: repository)

    def initialize_without_capture():
        manager._initialize_database()
        manager._session_tracker.start_session()

    monkeypatch.setattr(manager, "_initialize_components", initialize_without_capture)
    monkeypatch.setattr(manager, "_capture_loop", lambda: manager._stop_event.wait(3))
    after_explicit_changes = {}

    def use_old_preview(dialog):
        snapshot = dialog._snapshot
        board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
        select_business(board, "agency")
        manager._settings.set("general.character_name", "Beta")
        manager.set_manual_business_reading("bunker", 99, 100, 999)
        assert manager.start()
        manager.stop()
        assert manager.state is AppState.STOPPED
        after_explicit_changes["state"] = state(manager)
        after_explicit_changes["records"] = legacy_rows(repository), saved_rows(repository)
        assert snapshot.updated_at == observed["updated"]
        assert (snapshot.stock_percent, snapshot.supply_percent, snapshot.stock_value) == (12, None, 0)
        assert (editor._character_id, editor._business_id) == (alpha, "bunker")
        editor._save()
        assert saved_rows(repository)["checkins"] == after_explicit_changes["records"][1]["checkins"]
        assert not board.close(), "An owned modal preview must keep its fixed editor alive"
        dialog._use_button.click()

    preview(editor, qt, use_old_preview)
    assert editor._draft() == ("12", "", "0", PERSONAL_NOTE)
    assert state(manager) == after_explicit_changes["state"]
    assert (legacy_rows(repository), saved_rows(repository)) == after_explicit_changes["records"]
    editor._save_button.click()
    qt.processEvents()
    saved = repository.get_business_checkin_history(alpha, "bunker").rows[0]
    assert (saved.stock_percent, saved.supply_percent, saved.stock_value, saved.note) == (12, None, 0, PERSONAL_NOTE)
    assert repository.get_business_checkin_history(beta, "agency").total == 0
    assert board._character_combo.currentData() == beta
    assert state(manager) == after_explicit_changes["state"]


def test_cancelling_live_preview_preserves_invalid_draft_and_creates_no_record(app_window, qt):
    manager, window, repository, alpha, _beta = app_window
    manager.set_manual_business_reading("bunker", 1, 2, 3)
    board = open_board(window, qt)
    editor = editor_for(board, alpha, qt, "bunker")
    editor._stock_edit.setText("bad numeric draft")
    editor._supply_edit.setText("  ")
    editor._value_edit.setText("0000000000000")
    editor._note_edit.setPlainText(PERSONAL_NOTE)
    before = editor._draft(), state(manager), legacy_rows(repository), saved_rows(repository)
    preview(editor, qt, lambda dialog: dialog._cancel_button.click())
    assert (editor._draft(), state(manager), legacy_rows(repository), saved_rows(repository)) == before
