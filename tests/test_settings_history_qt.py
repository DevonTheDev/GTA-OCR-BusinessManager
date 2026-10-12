"""Opt-in Settings navigation into the real History and JSON export workflow."""

import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for Settings History workflows", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QSignalSpy, QTest

from src.app import AppState
from src.utils.exporter import DataExporter
from tests.test_history_panel_qt import qt as qt, records as records, selected_id
from tests.test_session_comparison_app_qt import database_contents, view as view


HELP = (
    "To export, select a completed session in History, then choose "
    "“Export selected session as JSON…” and a save location."
)


def settings_button(window, qt):
    settings = window._settings_panel
    window._tabs.setCurrentWidget(settings)
    button = settings.findChild(QtWidgets.QPushButton, "openSessionHistory")
    assert button is not None, "Settings must offer an Open History navigation button"
    settings.findChild(QtWidgets.QScrollArea).ensureWidgetVisible(button)
    qt.processEvents()
    return button


def draft_state(settings):
    """Snapshot every editable Settings control and its unsaved feedback."""
    values = []
    for kind, getter in (
        (QtWidgets.QLineEdit, "text"),
        (QtWidgets.QCheckBox, "isChecked"),
        (QtWidgets.QComboBox, "currentIndex"),
        (QtWidgets.QAbstractSlider, "value"),
        (QtWidgets.QSpinBox, "value"),
        (QtWidgets.QDoubleSpinBox, "value"),
    ):
        values.extend(getattr(control, getter)() for control in settings.findChildren(kind))
    return (
        values, settings.has_unsaved_changes(), settings._save_btn.isEnabled(),
        settings._status_label.text(), settings._status_label.styleSheet(),
    )


def observe_refresh(history, monkeypatch):
    calls = []
    refresh = history.refresh

    def refresh_and_record():
        calls.append(True)
        return refresh()

    monkeypatch.setattr(history, "refresh", refresh_and_record)
    return calls


@pytest.mark.parametrize("activation", ["mouse", "space", "mnemonic"])
def test_shortcut_navigates_without_saving_draft_or_exporting(view, qt, monkeypatch, activation):
    manager, window, history, records = view
    repository, _, active_character, ids, opened = records
    settings = window._settings_panel
    button = settings_button(window, qt)
    assert button.text().replace("&", "") == "Open History"
    assert not button.shortcut().isEmpty()
    assert button.shortcut() == QKeySequence.mnemonic(button.text())
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert button.toolTip() == button.accessibleDescription() == HELP
    help_labels = [label for label in settings.findChildren(QtWidgets.QLabel) if label.text() == HELP]
    assert len(help_labels) == 1
    assert help_labels[0].wordWrap()
    assert help_labels[0].textFormat() == Qt.TextFormat.PlainText

    settings._name_edit.setText("Unfinished character draft")
    settings._active_fps.setValue(3.0)
    settings._audio_check.setChecked(not settings._audio_check.isChecked())
    before_draft = draft_state(settings)
    assert before_draft[1:4] == (True, True, "Unsaved changes")
    before_config = settings._settings.config_path.read_bytes()
    before_database = database_contents(repository)
    tabs = window._tabs.count()
    refreshes = observe_refresh(history, monkeypatch)
    requests = QSignalSpy(settings.history_requested)
    unexpected = []

    def choose(*args, **kwargs):
        unexpected.append("chooser")
        return "", ""

    def validate():
        unexpected.append("validate")
        return True, ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(DataExporter, "export_to_json", lambda *a, **k: unexpected.append("export"))
    monkeypatch.setattr(settings._settings, "_save", lambda: unexpected.append("save"))
    monkeypatch.setattr(settings, "_validate_settings", validate)
    if activation == "mouse":
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    else:
        window.activateWindow()
        button.setFocus(Qt.FocusReason.TabFocusReason)
        qt.processEvents()
        assert button.hasFocus()
        if activation == "space":
            QTest.keyClick(button, Qt.Key.Key_Space)
        else:
            mnemonic = button.shortcut()[0]
            QTest.keyClick(button, mnemonic.key(), mnemonic.keyboardModifiers())
            if not requests:
                requests.wait(500)
    qt.processEvents()

    assert window._tabs.currentWidget() is history
    assert window._tabs.count() == tabs
    assert len(requests) == len(refreshes) == 1
    assert history._sessions_table.rowCount() == 25
    assert selected_id(history) == ids[-1]
    assert opened not in [
        history._sessions_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(history._sessions_table.rowCount())
    ]
    assert history._activities_table.item(0, 2).text() == "Activity 61"
    assert unexpected == []
    window._tabs.setCurrentWidget(settings)
    qt.processEvents()
    assert draft_state(settings) == before_draft
    assert settings._settings.config_path.read_bytes() == before_config
    assert database_contents(repository) == before_database
    assert repository.get_active_character().id == active_character.id
    assert manager.state == AppState.STOPPED
    assert manager._capture_thread is None and manager.session_stats is None
    assert manager.data.db_session_id is None


@pytest.mark.parametrize("history_state", ["filtered_page", "dirty_note_search"])
def test_revisits_use_normal_history_refresh_without_duplicate_wiring(view, qt, monkeypatch, history_state):
    _, window, history, records = view
    history._character_combo.setCurrentIndex(history._character_combo.findData(records[1].id))
    history._next_button.click()
    history._sessions_table.selectRow(2)
    qt.processEvents()
    chosen_id = selected_id(history)
    assert chosen_id is not None
    assert history._offset == 25
    history._pin_baseline_button.click()
    if history_state == "dirty_note_search":
        history._annotation_query_edit.setText("Unapplied session-note draft")
    refreshes = observe_refresh(history, monkeypatch)
    requests = QSignalSpy(window._settings_panel.history_requested)
    tabs = window._tabs.count()

    for visit in range(2):
        button = settings_button(window, qt)
        if visit:
            window.hide()
            window.show()
            qt.processEvents()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        qt.processEvents()
        assert window._tabs.currentWidget() is history
        assert window._tabs.count() == tabs
        assert len(refreshes) == len(requests) == visit + 1
        assert history._character_combo.currentData() == records[1].id
        assert history._offset == 25
        assert history._baseline_id == chosen_id
        if history_state == "filtered_page":
            assert history._sessions_table.rowCount() == 6
            assert selected_id(history) == chosen_id
            assert history._export_button.isEnabled()
        else:
            assert history._annotation_query_edit.text() == "Unapplied session-note draft"
            assert history._annotation_query_dirty
            assert history._applied_annotation_query is None
            assert history._sessions_table.rowCount() == 0
            assert selected_id(history) is None
            assert not history._export_button.isEnabled()
            assert "Apply search" in history._status_label.text()

    # A request while already on History must not force an additional refresh.
    window._settings_panel.history_requested.emit()
    assert len(requests) == 3
    assert len(refreshes) == 2


def test_shortcut_continues_selected_session_export_after_cancellation(view, qt, monkeypatch, tmp_path):
    manager, window, history, records = view
    repository = records[0]
    destination = tmp_path / "explicitly-chosen-session.json"
    choices = []

    def choose(*args, **kwargs):
        choices.append(True)
        return ("", "") if len(choices) == 1 else (str(destination), "JSON files (*.json)")

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    QTest.mouseClick(settings_button(window, qt), Qt.MouseButton.LeftButton)
    qt.processEvents()
    assert window._tabs.currentWidget() is history
    assert choices == []
    history._sessions_table.selectRow(4)
    qt.processEvents()
    chosen_id = selected_id(history)
    assert chosen_id == records[3][-5]
    expected = repository.export_session_data(chosen_id)
    before_database = database_contents(repository)
    before_files = sorted(path.name for path in tmp_path.iterdir())

    QTest.mouseClick(history._export_button, Qt.MouseButton.LeftButton)
    qt.processEvents()
    assert choices == [True]
    assert not destination.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == before_files
    assert selected_id(history) == chosen_id
    assert history._export_button.isEnabled()

    QTest.mouseClick(history._export_button, Qt.MouseButton.LeftButton)
    qt.processEvents()
    assert choices == [True, True]
    assert json.loads(destination.read_text()) == expected
    assert selected_id(history) == chosen_id
    assert database_contents(repository) == before_database
    assert manager.state == AppState.STOPPED
    assert manager._capture_thread is None
