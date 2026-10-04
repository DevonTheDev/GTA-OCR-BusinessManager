"""Actual business-tab batch review and SQLite history over disposable data."""

import copy
import json
import os
import sqlite3

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native bulk check-in workflow", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.app import AppState
from src.database.repository import Repository
from tests.test_business_checkins_app_qt import (
    app_window as app_window, editor_for, legacy_rows, open_board, qt as qt,
)
from tests.test_business_screen_target_app_qt import saved_rows, synthetic_business_io


NOTE = "  Shared <b>literal</b> observation\n\tSnow 雪 and NBSP\u00a0stay.  "


def live_state(manager):
    return copy.deepcopy((manager._data, manager._optimizer._business_states,
                          manager._business_parser.get_all_last_readings(),
                          manager.business_screen_target, manager._business_screen_generation,
                          manager.state, manager._optimizer._cooldowns))


def seed_readings(manager, monkeypatch):
    manager.clear_business_readings()
    manager.set_business_screen_target("bunker")
    calls = synthetic_business_io(manager, monkeypatch, text=["Meth Lab Stock: 5/10", "Supplies: 0%", "Value: $0"])
    manager._process_business_computer()
    assert len(calls) == 3
    manager.set_manual_business_reading("nightclub", stock_percent=0, value=2**63 - 1)


def batch_for(board, character_id, qt):
    board._character_combo.setCurrentIndex(board._character_combo.findData(character_id))
    assert hasattr(board, "live_batch_button"), "The manual board needs a reviewed multi-business save"
    board.live_batch_button.click()
    qt.processEvents()
    dialog = board._batch_editor
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    return dialog


def select_batch(dialog):
    assert set(dialog.row_checks) == {"bunker", "nightclub"}
    assert not any(control.isChecked() for control in dialog.row_checks.values())
    assert not dialog.save_button.isEnabled()
    dialog.select_all_button.click()
    dialog.note_edit.setPlainText(NOTE)
    assert all(control.isChecked() for control in dialog.row_checks.values())
    assert dialog.save_button.isEnabled()


@pytest.mark.parametrize("tracking_restart", [False, True])
def test_business_tab_captures_two_readings_and_saves_fixed_owner_without_touching_other_draft(
    app_window, qt, monkeypatch, tmp_path, tracking_restart,
):
    manager, window, repository, alpha, beta = app_window
    seed_readings(manager, monkeypatch)
    board = open_board(window, qt)
    ordinary = editor_for(board, alpha, qt)
    ordinary._stock_edit.setText("unsubmitted invalid draft")
    ordinary._note_edit.setPlainText("Keep the separate personal draft\u00a0unchanged")
    ordinary_draft = ordinary._draft()
    before_records = saved_rows(repository)
    dialog = batch_for(board, alpha, qt)
    snapshots = dialog._snapshots
    assert {item.business_id for item in snapshots} == {"bunker", "nightclub"}
    assert len({item.captured_at for item in snapshots}) == 1
    by_id = {item.business_id: item for item in snapshots}
    assert by_id["bunker"].identity_source == "selected_target"
    assert by_id["nightclub"].identity_source == "manual_entry"
    assert saved_rows(repository) == before_records
    select_batch(dialog)

    board._character_combo.setCurrentIndex(board._character_combo.findData(beta))
    manager._settings.set("general.character_name", "Beta")
    manager.clear_business_readings()
    manager.set_manual_business_reading("bunker", stock_percent=99, supply_percent=100, value=123)
    if tracking_restart:
        import src.app as app_module
        monkeypatch.setattr(app_module, "get_repository", lambda: repository)

        def initialize_without_native_capture():
            manager._initialize_database()
            manager._session_tracker.start_session(start_money=0)

        monkeypatch.setattr(manager, "_initialize_components", initialize_without_native_capture)
        monkeypatch.setattr(manager, "_capture_loop", lambda: manager._stop_event.wait(3))
        assert manager.start()
        assert manager._data.character_id == beta
        manager.stop()
        assert manager.state == AppState.STOPPED
    live = live_state(manager)
    legacy = legacy_rows(repository)
    settings = manager._settings._config_path.read_bytes()
    dialog.save_button.click()
    qt.processEvents()
    assert dialog._committed and not dialog.isVisible()
    assert ordinary._draft() == ordinary_draft and ordinary.isVisible()
    assert live_state(manager) == live and legacy_rows(repository) == legacy
    assert manager._settings._config_path.read_bytes() == settings
    assert board._character_combo.currentData() == beta
    assert repository.get_business_checkin_board(beta).rows == ()
    saved = repository.get_business_checkin_board(alpha)
    assert len(saved.rows) == 2
    assert len({row.recorded_at for row in saved.rows}) == 1
    for row in saved.rows:
        observed = by_id[row.business_id]
        assert (row.stock_percent, row.supply_percent, row.stock_value) == (
            observed.stock_percent, observed.supply_percent, observed.stock_value)
        assert row.note == NOTE
    assert next(row for row in saved.rows if row.business_id == "nightclub").supply_percent is None

    # Export/reopen uses ordinary saved schema, not preview provenance or live state.
    path = tmp_path / "saved-batch.json"
    assert board._exporter.export_business_checkins_snapshot(saved, path).success
    report = json.loads(path.read_text())
    assert report == saved.to_report()
    expected_fields = {"id", "character_id", "business_id", "recorded_at", "stock_percent",
                       "supply_percent", "stock_value", "note"}
    assert all(set(row) == expected_fields for row in report["rows"])
    reopened = Repository(repository._db_path)
    try:
        assert reopened.get_business_checkin_board(alpha).rows == saved.rows
    finally:
        reopened.close()
        reopened._session_factory.kw["bind"].dispose()


def test_real_second_insert_failure_rolls_back_then_explicit_retry_saves_one_batch(app_window, qt, monkeypatch):
    manager, window, repository, alpha, _beta = app_window
    seed_readings(manager, monkeypatch)
    board = open_board(window, qt)
    dialog = batch_for(board, alpha, qt)
    select_batch(dialog)
    with sqlite3.connect(repository._db_path) as database:
        database.execute("""CREATE TRIGGER reject_second_checkin BEFORE INSERT ON manual_business_checkins
            WHEN (SELECT COUNT(*) FROM manual_business_checkins) = 1
            BEGIN SELECT RAISE(ABORT, 'synthetic second-row failure'); END""")
    dialog.save_button.click()
    qt.processEvents()
    assert not dialog._uncertain and not dialog._committed and dialog.isVisible()
    assert dialog.save_button.isEnabled()
    assert all(control.isChecked() for control in dialog.row_checks.values())
    assert dialog.note_edit.document().toRawText().replace("\u2029", "\n") == NOTE
    assert repository.get_business_checkin_board(alpha).rows == ()
    with sqlite3.connect(repository._db_path) as database:
        database.execute("DROP TRIGGER reject_second_checkin")
    dialog.save_button.click()
    qt.processEvents()
    assert dialog._committed
    assert len(repository.get_business_checkin_board(alpha).rows) == 2


def test_actual_post_commit_exception_keeps_persisted_batch_but_freezes_retry(app_window, qt, monkeypatch):
    manager, window, repository, alpha, _beta = app_window
    seed_readings(manager, monkeypatch)
    board = open_board(window, qt)
    dialog = batch_for(board, alpha, qt)
    select_batch(dialog)

    def fail_after_commit(_session):
        raise OperationalError("synthetic acknowledgment", {}, RuntimeError("SYNTHETIC_PRIVATE_PATH"))

    event.listen(repository._session_factory, "after_commit", fail_after_commit)
    try:
        dialog.save_button.click()
        qt.processEvents()
    finally:
        event.remove(repository._session_factory, "after_commit", fail_after_commit)
    assert dialog._uncertain and not dialog._committed and dialog.isVisible()
    assert not dialog.save_button.isEnabled()
    assert all(control.isChecked() for control in dialog.row_checks.values())
    saved = repository.get_business_checkin_board(alpha)
    assert len(saved.rows) == 2
    dialog._save()
    dialog.save_button.click()
    qt.processEvents()
    assert repository.get_business_checkin_board(alpha).rows == saved.rows
    # The existing review stays frozen; the board cannot turn it into a new attempt.
    board.live_batch_button.click()
    qt.processEvents()
    assert board._batch_editor is dialog and dialog._uncertain
    assert dialog.close()
    qt.processEvents()
    assert board._batch_editor is None


def test_saved_batch_refresh_failure_does_not_restore_a_retryable_review(app_window, qt, monkeypatch):
    manager, window, repository, alpha, _beta = app_window
    seed_readings(manager, monkeypatch)
    board = open_board(window, qt)
    dialog = batch_for(board, alpha, qt)
    select_batch(dialog)
    with monkeypatch.context() as patch:
        patch.setattr(repository, "get_business_checkin_board",
                      lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic unavailable refresh")))
        dialog.save_button.click()
        qt.processEvents()
    assert dialog._committed and not dialog.isVisible()
    assert len(repository.get_business_checkin_board(alpha).rows) == 2
    assert "saved" in board._saved_notice_label.text().lower()
    assert str(alpha) in board._saved_notice_label.text()
