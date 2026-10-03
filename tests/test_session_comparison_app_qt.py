"""Opt-in real MainWindow comparison workflow over existing SQLite history."""

import json
import os
import sqlite3

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for app comparison workflows", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6 import sip

from src.app import GTABusinessManager, AppState
from src.config import settings as settings_module
from src.config.settings import Settings
from src.database.models import Session
from src.ui.main_window import MainWindow
from tests.test_history_panel_qt import qt as qt, records as records


def database_contents(repository):
    with sqlite3.connect(repository._db_path) as connection:
        return list(connection.iterdump())


@pytest.fixture
def view(records, qt, tmp_path, monkeypatch):
    repository = records[0]
    settings = Settings(tmp_path / "comparison-window.yaml")
    monkeypatch.setattr(settings_module, "_settings", settings)
    manager = GTABusinessManager(settings)
    manager._repository = repository
    window = MainWindow(manager)
    window.resize(1100, 800)
    window._tabs.setCurrentWidget(window._history_panel)
    window.show()
    qt.processEvents()
    try:
        yield manager, window, window._history_panel, records
    finally:
        window._update_timer.stop()
        for dialog in window.findChildren(QtWidgets.QDialog):
            if not sip.isdeleted(dialog):
                dialog.close()
        window.hide()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def compare_across_characters(view, qt):
    _, _, history, records = view
    assert hasattr(history, "_pin_baseline_button"), "History needs a session baseline action"
    history._pin_baseline_button.click()
    assert history._baseline_id == records[3][-1]
    history._character_combo.setCurrentIndex(history._character_combo.findData(records[1].id))
    qt.processEvents()
    assert history._selected_id == records[3][-2]
    assert history._compare_button.isEnabled()
    history._compare_button.click()
    qt.processEvents()
    assert history._comparison_dialog is not None
    return history._comparison_dialog


def test_main_window_compares_and_exports_without_capture_or_database_mutation(view, qt, monkeypatch, tmp_path):
    manager, _, history, records = view
    repository, _, active_character, ids, _ = records
    before = database_contents(repository)
    dialog = compare_across_characters(view, qt)
    assert dialog._comparison.baseline.session_id == ids[-1]
    assert dialog._comparison.comparison.session_id == ids[-2]
    assert dialog._comparison.baseline.metrics.net_change == -39
    assert dialog._comparison.comparison.metrics.net_change == -40
    assert dialog._comparison.differences.net_change == -1
    destination = tmp_path / "comparison.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    dialog._export_button.click()
    qt.processEvents()
    assert json.loads(destination.read_text()) == dialog._comparison.to_report()
    assert dialog._baseline_label.textFormat() == dialog._comparison_label.textFormat() == Qt.TextFormat.PlainText
    assert manager.state == AppState.STOPPED
    assert manager._capture_thread is None and manager.session_stats is None
    assert manager.data.db_session_id is None
    assert repository.get_active_character().id == active_character.id
    assert database_contents(repository) == before
    dialog.close()
    qt.processEvents()
    assert history._comparison_dialog is None
    assert history._export_button.isEnabled()


def test_main_window_export_keeps_displayed_snapshot_when_source_changes(view, qt, monkeypatch, tmp_path):
    _, _, history, records = view
    repository = records[0]
    dialog = compare_across_characters(view, qt)
    snapshot = dialog._comparison.to_report()
    destination = tmp_path / "frozen-comparison.json"
    calls = []

    def choose(*args, **kwargs):
        calls.append(True)
        with repository._session_scope() as session:
            session.query(Session).filter_by(id=dialog._comparison.comparison.session_id).update({"total_earnings": 9999})
        history._sessions_table.selectRow(4)
        dialog._export_button.click()
        return str(destination), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert calls == [True]
    assert json.loads(destination.read_text()) == snapshot
    assert dialog._comparison.to_report() == snapshot
    assert repository.get_session_comparison(snapshot["baseline"]["session_id"], snapshot["comparison"]["session_id"]).comparison.metrics.net_change == 9999
