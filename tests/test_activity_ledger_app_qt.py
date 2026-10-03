"""Opt-in actual MainWindow → ledger → captured-page export workflows."""

import json
import os
import sqlite3

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native ledger integration", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt

from src.app import AppState, GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from src.database.models import Activity
from src.ui.main_window import MainWindow
from tests.test_history_panel_qt import qt as qt, records as records


def contents(repository):
    with sqlite3.connect(repository._db_path) as connection:
        return list(connection.iterdump())


@pytest.fixture
def window(records, qt, tmp_path, monkeypatch):
    settings = Settings(tmp_path / "ledger-app.yaml")
    monkeypatch.setattr(settings_module, "_settings", settings)
    manager = GTABusinessManager(settings)
    manager._repository = records[0]
    view = MainWindow(manager)
    view.resize(1100, 850)
    view._tabs.setCurrentWidget(view._history_panel)
    view.show()
    qt.processEvents()
    try:
        yield manager, view, records
    finally:
        view._update_timer.stop()
        for dialog in view.findChildren(QtWidgets.QDialog):
            if not sip.isdeleted(dialog):
                dialog.close()
        view.hide()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def open_ledger(window, qt):
    manager, view, data = window
    history = view._history_panel
    assert hasattr(history, "_activity_ledger_button"), "History needs a recorded-activity browser"
    history._character_combo.setCurrentIndex(history._character_combo.findData(data[1].id))
    history._activity_ledger_button.click()
    qt.processEvents()
    assert history._activity_ledger_dialog is not None
    return history, history._activity_ledger_dialog


def test_main_history_opens_filters_and_exports_without_tracking_or_record_changes(window, qt, monkeypatch, tmp_path):
    manager, view, data = window
    repository, alpha, active, _, _ = data
    before = contents(repository)
    history, dialog = open_ledger(window, qt)
    assert dialog._page.filters.character_id == alpha.id
    assert dialog._page.total == 31 and len(dialog._page.rows) == 25
    assert all(row.character_id == alpha.id for row in dialog._page.rows)
    assert dialog._details_edit.isReadOnly()
    history._activity_ledger_button.click()
    assert history._activity_ledger_dialog is dialog
    dialog._next_button.click()
    qt.processEvents()
    assert dialog._page.offset == 25 and len(dialog._page.rows) == 6
    dialog._query_edit.setText("activity 1")
    assert dialog._page is None and not dialog._export_button.isEnabled()
    dialog._type_edit.setText("CONTACT_MISSION")
    dialog._outcome_combo.setCurrentIndex(dialog._outcome_combo.findData("passed"))
    dialog._apply_button.click()
    qt.processEvents()
    snapshot = dialog._page
    assert snapshot.offset == 0 and snapshot.total == 5
    assert {row.name for row in snapshot.rows} == {f"Activity {i}" for i in (10, 12, 14, 16, 18)}
    destination = tmp_path / "selected-ledger-page.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    dialog._export_button.click()
    qt.processEvents()
    assert json.loads(destination.read_text()) == snapshot.to_report()
    assert manager.state == AppState.STOPPED and manager._capture_thread is None
    assert manager.session_stats is None and manager.data.db_session_id is None
    assert repository.get_active_character().id == active.id
    assert contents(repository) == before
    assert view._tabs.currentWidget() is history
    dialog.close()
    qt.processEvents()
    assert history._activity_ledger_dialog is None


def test_file_choice_cannot_switch_export_to_edited_filters_or_database(window, qt, monkeypatch, tmp_path):
    _, _, data = window
    repository = data[0]
    _, dialog = open_ledger(window, qt)
    snapshot = dialog._page
    expected = snapshot.to_report()
    destination = tmp_path / "captured-ledger-page.json"
    chosen = []

    def choose(*args, **kwargs):
        chosen.append(True)
        with repository._session_scope() as session:
            session.query(Activity).filter_by(id=snapshot.rows[0].id).update({"activity_name": "Later stored name", "earnings": 99999})
        dialog._query_edit.setText("Later stored name")
        dialog._export_button.click()
        return str(destination), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    qt.processEvents()
    assert chosen == [True]
    assert json.loads(destination.read_text()) == expected
    assert dialog._page is None and not dialog._export_button.isEnabled()
    assert dialog._activities_table.rowCount() == 0
    dialog._apply_button.click()
    qt.processEvents()
    assert dialog._page.total == 1
    assert dialog._page.rows[0].name == "Later stored name"
    assert snapshot.to_report() == expected


def test_actual_ledger_renders_literal_long_data_and_default_window(window, qt, tmp_path):
    _, _, data = window
    repository = data[0]
    _, dialog = open_ledger(window, qt)
    dialog.resize(1100, 800)
    qt.processEvents()
    assert dialog.grab().save("/tmp/gta-activity-ledger-default.png")
    target = dialog._page.rows[0].id
    literal = '<b>Saved mission</b> 雪 🚀 "literal" '
    with repository._session_scope() as session:
        session.query(Activity).filter_by(id=target).update({
            "activity_name": literal * 18,
            "notes": '<img src=x onerror=alert(1)>\n' + "Long saved note " * 150,
        })
    dialog._refresh_button.click()
    qt.processEvents()
    index = next(index for index, row in enumerate(dialog._page.rows) if row.id == target)
    dialog._activities_table.selectRow(index)
    qt.processEvents()
    assert "<b>Saved mission</b> 雪 🚀" in dialog._details_edit.toPlainText()
    assert "<img src=x onerror=alert(1)>" in dialog._details_edit.toPlainText()
    assert dialog._details_edit.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert dialog.grab().save("/tmp/gta-activity-ledger-long.png")
