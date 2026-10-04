"""Opt-in MainWindow -> daily snapshot -> captured source sessions -> export."""

import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native daily overview integration", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QDate, Qt

from src.app import AppState
from src.database.models import Session
from tests.test_activity_ledger_app_qt import window as window, contents
from tests.test_history_panel_qt import qt as qt, records as records


def open_daily(window, qt):
    _manager, main, data = window
    history = main._history_panel
    history._character_combo.setCurrentIndex(history._character_combo.findData(data[1].id))
    assert hasattr(history, "_daily_overview_button"), "History needs a daily session overview"
    history._daily_overview_button.click()
    qt.processEvents()
    assert history._daily_overview_dialog is not None
    dialog = history._daily_overview_dialog
    dialog._from_date.setDate(QDate(2026, 1, 1))
    dialog._until_date.setDate(QDate(2026, 1, 2))
    dialog._apply_button.click()
    qt.processEvents()
    assert dialog._snapshot is not None
    return history, dialog


def select_day(dialog, day, qt):
    row = next(index for index in range(dialog._days_table.rowCount())
               if dialog._days_table.item(index, 0).text() == day)
    dialog._days_table.selectRow(row)
    qt.processEvents()


def test_mainwindow_daily_net_coverage_sources_and_export_use_saved_sessions(window, qt, monkeypatch, tmp_path):
    manager, main, data = window
    repository, alpha, active, _sessions, _open = data
    before = contents(repository)
    history, dialog = open_daily(window, qt)
    snapshot = dialog._snapshot
    assert snapshot.filters.character_id == alpha.id
    assert snapshot.overall.sessions == 31
    assert snapshot.overall.known_net == 31
    assert snapshot.overall.net_change_total == -2170
    assert snapshot.overall.duration_seconds_total == 9300
    assert snapshot.overall.paired_sessions == 31
    assert snapshot.overall.net_per_hour == -840
    assert len(snapshot.days) == 2 and len(snapshot.rows) == 31
    assert dialog._sources_table.rowCount() == 25
    dialog._next_button.click(); qt.processEvents()
    assert dialog._sources_table.rowCount() == 6
    # Parent search/selection and an independently pinned baseline do not retarget
    # this read-only period. Source browsing comes from the accepted rows.
    history._annotation_query_edit.setText("no saved note matches")
    history._character_combo.setCurrentIndex(history._character_combo.findData(active.id))
    history._daily_overview_button.click()
    assert history._daily_overview_dialog is dialog and dialog._snapshot is snapshot
    monkeypatch.setattr(repository, "get_daily_session_overview",
                        lambda *_a, **_k: pytest.fail("Day selection reread storage"))
    select_day(dialog, "2026-01-02", qt)
    assert dialog._sources_table.rowCount() == 0
    assert "No completed sessions" in dialog._day_details_edit.toPlainText()
    select_day(dialog, "2026-01-01", qt)
    assert dialog._sources_table.rowCount() == 25
    target = tmp_path / "daily.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    dialog._export_button.click(); qt.processEvents()
    assert json.loads(target.read_text()) == snapshot.to_report()
    assert manager.state == AppState.STOPPED and manager._capture_thread is None
    assert manager.session_stats is None and manager.data.db_session_id is None
    assert repository.get_active_character().id == active.id
    assert contents(repository) == before
    assert main._tabs.currentWidget() is history
    dialog.close(); qt.processEvents()
    assert history._daily_overview_dialog is None


def test_export_keeps_original_complete_snapshot_through_chooser_changes(window, qt, monkeypatch, tmp_path):
    _manager, _main, data = window
    repository = data[0]
    history, dialog = open_daily(window, qt)
    snapshot = dialog._snapshot
    expected = snapshot.to_report()
    target = tmp_path / "captured-period.json"
    choices = []

    def choose(*_args, **_kwargs):
        choices.append(True)
        with repository._session_scope() as db:
            db.query(Session).filter_by(id=snapshot.rows[0].session_id).update({"total_earnings": 99999})
        history._character_combo.setCurrentIndex(0)
        dialog._from_date.setDate(QDate(2026, 1, 2))
        dialog._export_button.click()
        return str(target), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click(); qt.processEvents()
    assert choices == [True]
    assert json.loads(target.read_text()) == expected
    assert snapshot.to_report() == expected
    assert dialog._snapshot is None and not dialog._export_button.isEnabled()
    assert dialog._days_table.rowCount() == 0 and dialog._sources_table.rowCount() == 0
    dialog._apply_button.click(); qt.processEvents()
    assert dialog._snapshot.overall.sessions == 0


def test_selected_day_is_detached_until_explicit_refresh(window, qt):
    _manager, _main, data = window
    repository = data[0]
    _history, dialog = open_daily(window, qt)
    snapshot = dialog._snapshot
    row = snapshot.rows[0]
    with repository._session_scope() as db:
        db.query(Session).filter_by(id=row.session_id).update({"total_earnings": 123456})
    select_day(dialog, "2026-01-02", qt)
    select_day(dialog, "2026-01-01", qt)
    assert dialog._snapshot is snapshot
    assert dialog._snapshot.rows[0].net_change == row.net_change
    dialog._refresh_button.click(); qt.processEvents()
    assert dialog._snapshot.overall.net_change_total == -2170 - row.net_change + 123456


def test_history_open_failure_is_visible_and_retry_is_allowed(window, qt, monkeypatch):
    import src.ui.widgets.daily_session_overview_dialog as module
    _manager, main, _data = window
    history = main._history_panel
    assert hasattr(history, "_daily_overview_button")
    original = module.DailySessionOverviewDialog
    def failed(*_args, **_kwargs):
        raise RuntimeError("synthetic constructor failure")
    monkeypatch.setattr(module, "DailySessionOverviewDialog", failed)
    history._daily_overview_button.click(); qt.processEvents()
    assert history._daily_overview_dialog is None
    assert "could not be opened" in history._status_label.text()
    monkeypatch.setattr(module, "DailySessionOverviewDialog", original)
    history._daily_overview_button.click(); qt.processEvents()
    assert history._daily_overview_dialog is not None


def test_mainwindow_and_daily_dialog_keep_normal_small_and_literal_layout(window, qt, tmp_path):
    _manager, main, data = window
    repository, alpha, *_rest = data
    history, dialog = open_daily(window, qt)
    main.resize(1000, 700); dialog.resize(1080, 820); qt.processEvents()
    assert main.width() == 1000
    assert history._daily_overview_button.isVisibleTo(main)
    assert dialog.grab().save(str(tmp_path / "daily-normal.png"))
    long_name = "<b>Literal character Ω</b> " * 30
    with repository._session_scope() as db:
        from src.database.models import Character
        db.query(Character).filter_by(id=alpha.id).update({"name": long_name})
    dialog._refresh_button.click(); qt.processEvents()
    assert dialog._snapshot.rows[0].character_name == long_name
    assert long_name in dialog._source_details_edit.toPlainText()
    assert dialog._source_details_edit.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    dialog.resize(480, 440); qt.processEvents()
    assert dialog.width() == 480 and dialog.height() == 440
    assert dialog.grab().save(str(tmp_path / "daily-small-literal.png"))
