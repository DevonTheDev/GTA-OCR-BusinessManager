"""Opt-in MainWindow → timer editor → shared overlay, with real temporary storage."""

import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for app reminder workflows", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt6 import sip

from src.app import AppState, CaptureResult, GTABusinessManager
from src.config import settings as settings_module
from src.config.settings import Settings
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState
from src.tracking import cooldowns as storage
from src.ui.main_window import MainWindow
from src.ui.overlay import OverlayWindow
from src.ui.widgets import cooldown_widget
from tests.test_app_accounting import app as _accounting_app
from tests.test_app_cooldown_management import stored_session_data

accounting_app = _accounting_app


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def windows(qt, monkeypatch):
    owned = []

    def forbidden():
        pytest.fail("App-owned timer views must not obtain the global fallback tracker")

    monkeypatch.setattr(cooldown_widget, "get_cooldown_tracker", forbidden)

    def create(app):
        monkeypatch.setattr(settings_module, "_settings", app._settings)
        overlay = OverlayWindow(app)
        window = MainWindow(app, overlay=overlay)
        owned.extend([overlay, window])
        window.resize(900, 650)
        window._tabs.setCurrentWidget(window._activity_panel)
        overlay.show()
        window.show()
        qt.processEvents()
        return window, overlay

    yield create
    for widget in reversed(owned):
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def control(widget, name, kind=QtWidgets.QPushButton):
    result = widget.findChild(kind, name)
    assert result is not None, name
    return result


def manager(window, qt):
    button = control(window._activity_panel, "manage_cooldowns")
    assert button.isVisible() and button.isEnabled()
    button.click()
    qt.processEvents()
    result = window._activity_panel._cooldown_dialog
    assert result is not None and result.isVisible()
    return result


def edit(monkeypatch, qt, trigger, *, preset=None, name="Custom <literal>", seconds=300, cancel=False, before_accept=None):
    from src.ui.widgets.cooldown_manager_dialog import CooldownEditorDialog
    original = CooldownEditorDialog.__init__
    errors = []

    def initialize(dialog, *args, **kwargs):
        original(dialog, *args, **kwargs)

        def choose():
            try:
                if cancel:
                    control(dialog, "cooldown_editor_cancel").click()
                    return
                combo = control(dialog, "cooldown_preset", QtWidgets.QComboBox)
                if combo.isEnabled():
                    index = combo.findData(preset)
                    assert index >= 0
                    combo.setCurrentIndex(index)
                field = control(dialog, "cooldown_name", QtWidgets.QLineEdit)
                if not field.isReadOnly():
                    field.setText(name)
                hours, rest = divmod(seconds, 3600)
                minutes, secs = divmod(rest, 60)
                for part, value in (("hours", hours), ("minutes", minutes), ("seconds", secs)):
                    control(dialog, "cooldown_" + part, QtWidgets.QSpinBox).setValue(value)
                if before_accept:
                    before_accept()
                save = control(dialog, "cooldown_editor_save")
                assert save.isEnabled()
                save.click()
            except BaseException as exc:
                errors.append(exc)
                dialog.reject()

        QTimer.singleShot(0, choose)

    with monkeypatch.context() as choice:
        choice.setattr(CooldownEditorDialog, "__init__", initialize)
        trigger()
        qt.processEvents()
    assert errors == [], errors


def refresh(manager, overlay, qt):
    control(manager, "cooldown_list", cooldown_widget.CooldownWidget).refresh()
    overlay._cooldown_widget._update_display()
    qt.processEvents()


def test_stopped_app_main_window_can_create_persisted_timer_visible_in_overlay(tmp_path, windows, qt, monkeypatch):
    app = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    window, overlay = windows(app)
    dialog = manager(window, qt)
    assert app.state is AppState.STOPPED and app._repository is None and app._capture is None
    assert overlay._cooldown_widget._tracker is app.cooldown_tracker
    edit(monkeypatch, qt, control(dialog, "cooldown_start").click, name="Prepare <b>literal</b>", seconds=901)
    refresh(dialog, overlay, qt)
    timers = app.cooldown_tracker.get_active_cooldowns()
    assert len(timers) == 1 and timers[0].activity_name.startswith("manual:")
    assert timers[0].duration_seconds == 901
    assert timers[0].display_name == "Prepare <b>literal</b>"
    visible = [label for label in overlay._cooldown_widget._labels if not label.isHidden()]
    assert visible and visible[0].text().startswith(timers[0].display_name[:10] + ": ")
    assert visible[0].textFormat() == Qt.TextFormat.PlainText
    listing = control(dialog, "cooldown_list", cooldown_widget.CooldownWidget)
    full_name = control(listing._item_widgets[timers[0].activity_name], "cooldown_name_label", QtWidgets.QLabel)
    assert full_name.text() == timers[0].display_name
    assert full_name.textFormat() == Qt.TextFormat.PlainText
    assert app._repository is None and app._capture is None
    assert not app._session_tracker.is_active
    restored = GTABusinessManager(app._settings).cooldown_tracker.get_cooldown(timers[0].activity_name)
    assert restored.started_at == timers[0].started_at
    assert restored.display_name == timers[0].display_name
    assert dialog.width() <= 900 and dialog.height() <= 650


def test_preset_adjust_cancel_remove_and_reopen_keep_captured_key(accounting_app, windows, qt, monkeypatch):
    app = accounting_app
    window, overlay = windows(app)
    dialog = manager(window, qt)
    before = stored_session_data(app)
    edit(monkeypatch, qt, control(dialog, "cooldown_start").click, preset="headhunter", seconds=180)
    refresh(dialog, overlay, qt)
    listing = control(dialog, "cooldown_list", cooldown_widget.CooldownWidget)
    row = listing._item_widgets["headhunter"]
    assert app.cooldown_tracker.get_cooldown("headhunter").duration_seconds == 180
    original = app.cooldown_tracker.get_cooldown("headhunter").to_dict()
    edit(monkeypatch, qt, control(row, "cooldown_adjust").click, cancel=True)
    assert app.cooldown_tracker.get_cooldown("headhunter").to_dict() == original
    edit(monkeypatch, qt, control(row, "cooldown_adjust").click, seconds=90,
         before_accept=lambda: app.cooldown_tracker.set_timer("headhunter", "Headhunter", 600))
    refresh(dialog, overlay, qt)
    assert app.cooldown_tracker.get_cooldown("headhunter").duration_seconds == 90
    assert listing._item_widgets["headhunter"] is row
    assert row._cooldown.duration_seconds == 90
    assert manager(window, qt) is dialog
    control(row, "cooldown_remove").click()
    refresh(dialog, overlay, qt)
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert all(label.isHidden() for label in overlay._cooldown_widget._labels)
    assert stored_session_data(app) == before
    dialog.close()
    qt.processEvents()
    reopened = manager(window, qt)
    assert reopened is not dialog
    assert control(reopened, "cooldown_list", cooldown_widget.CooldownWidget)._tracker is app.cooldown_tracker


def test_native_save_failure_retry_saves_latest_timer_without_restarting(accounting_app, windows, qt, monkeypatch):
    app = accounting_app
    window, overlay = windows(app)
    dialog = manager(window, qt)
    edit(monkeypatch, qt, control(dialog, "cooldown_start").click, preset="headhunter", seconds=180)
    path = app._settings.data_dir / "cooldowns.json"
    old = path.read_bytes()
    writer = storage.atomic_text_writer

    def fail(*_args, **_kwargs):
        raise OSError("synthetic failure with PRIVATE_PATH_MARKER")

    monkeypatch.setattr(storage, "atomic_text_writer", fail)
    edit(monkeypatch, qt, control(dialog, "cooldown_start").click, name="Unsaved reminder", seconds=700)
    refresh(dialog, overlay, qt)
    assert path.read_bytes() == old
    warning = control(dialog, "cooldown_storage_error", QtWidgets.QLabel)
    assert not warning.isHidden() and "PRIVATE_PATH_MARKER" not in warning.text()
    retry = control(dialog, "cooldown_retry")
    assert retry.isEnabled() and not retry.isHidden()
    timer = next(value for value in app.cooldown_tracker.get_active_cooldowns() if value.display_name == "Unsaved reminder")
    monkeypatch.setattr(storage, "atomic_text_writer", writer)
    retry.click()
    refresh(dialog, overlay, qt)
    stored = json.loads(path.read_text())["cooldowns"][timer.activity_name]
    assert stored["started_at"] == timer.started_at.isoformat()
    assert stored["duration_seconds"] == 700
    assert app.cooldown_tracker.storage_error is None
    assert warning.isHidden()


def test_real_mission_completion_replaces_shared_preset_seen_by_both_views(accounting_app, windows, qt, monkeypatch):
    app = accounting_app
    window, overlay = windows(app)
    dialog = manager(window, qt)
    edit(monkeypatch, qt, control(dialog, "cooldown_start").click, preset="headhunter", seconds=60)
    listing = control(dialog, "cooldown_list", cooldown_widget.CooldownWidget)
    row = listing._item_widgets["headhunter"]
    app._process_state(StateDetectionResult(state=GameState.MISSION_ACTIVE, confidence=1,
                                           reason="fixture", mission_text="Headhunter"), CaptureResult())
    app._process_state(StateDetectionResult(state=GameState.MISSION_COMPLETE, confidence=1,
                                           reason="fixture"), CaptureResult())
    refresh(dialog, overlay, qt)
    assert listing._item_widgets["headhunter"] is row
    assert row._cooldown.duration_seconds == 300
    assert "Headhunter" in overlay._cooldown_widget._labels[0].text()
    assert app.session_stats.activities_completed == 1
    record = app._repository.export_session_data(app._data.db_session_id)
    assert len(record["activities"]) == 1 and record["earnings"] == []
    assert app.session_earnings == 0
