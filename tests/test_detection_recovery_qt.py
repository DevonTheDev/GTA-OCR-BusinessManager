"""Opt-in actual Activities panel recovery with a real manager and SQLite.

Offscreen native Qt and synthetic OCR do not validate Windows capture/gameplay.
"""

import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native detection recovery", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt

from src.app import AppState
from src.config import settings as settings_module
from src.detection.parsers.timer_parser import TimerReading
from src.game.state_machine import GameState
from src.ui.main_window import MainWindow
from src.ui.overlay import OverlayWindow
from tests.test_app_accounting import app as app
from tests.test_detection_recovery import database_contents
from tests.test_detection_recovery_worker import pipeline as pipeline
from tests.test_terminal_mission_identity_ownership import capture_frame


@pytest.fixture
def views(native_qt_application, monkeypatch):
    roots = []

    def create(app):
        monkeypatch.setattr(settings_module, "_settings", app._settings)
        overlay = OverlayWindow(app)
        window = MainWindow(app, overlay=overlay)
        roots.extend([overlay, window])
        window.resize(900, 650)
        window._tabs.setCurrentWidget(window._activity_panel)
        overlay.show()
        window.show()
        refresh(window, overlay, native_qt_application)
        return window, overlay, window._activity_panel

    yield create
    for root in reversed(roots):
        for child in root.findChildren(QtWidgets.QWidget):
            # Root/child ownership handles Qt destruction; manually stop the
            # existing parentless dashboard timer before deferred deletion.
            if hasattr(child, "_timer"):
                child._timer.stop()
        root._update_timer.stop()
        for dialog in root.findChildren(QtWidgets.QDialog):
            if not sip.isdeleted(dialog):
                dialog.close()
        root.hide()
        root.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    native_qt_application.processEvents()


def refresh(window, overlay, qt):
    window._update_ui()
    window._activity_panel._update_display()
    window._dashboard._update_display()
    overlay._update_ui()
    qt.processEvents()


def control(widget, name, kind=QtWidgets.QPushButton):
    result = widget.findChild(kind, name)
    assert result is not None, f"Activities recovery needs {name}"
    return result


def seed(app, name="Headhunter"):
    result = capture_frame(app, banner=name, mission="Eliminate the targets", balance=1000)
    result.timer = TimerReading(minutes=5, seconds=30, total_seconds=330)
    app._last_capture_result = result
    app._state = AppState.RUNNING
    return app._activity_tracker.current_activity


def open_confirmation(panel, qt):
    control(panel, "discard_detection").click()
    qt.processEvents()
    dialog = panel._recovery_dialog
    assert dialog is not None and dialog.isVisible()
    return dialog


def test_pause_cancel_close_and_repeat_click_are_inert(app, views, native_qt_application):
    qt = native_qt_application
    original = seed(app)
    window, overlay, panel = views(app)
    toggle = control(panel, "detection_pause_resume")
    discard = control(panel, "discard_detection")
    assert toggle.text() == "Pause capture" and toggle.isEnabled()
    assert not discard.isEnabled()
    before = database_contents(app)
    toggle.click()
    assert app.state is AppState.PAUSED
    assert toggle.text() == "Resume capture" and discard.isEnabled()
    dialog = open_confirmation(panel, qt)
    assert "Headhunter" in control(dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText()
    assert original.started_at.isoformat(sep=" ", timespec="seconds") in control(
        dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText()
    explanation = control(dialog, "recovery_explanation", QtWidgets.QLabel)
    assert "Completed history and session earnings" in explanation.text()
    assert explanation.textFormat() == Qt.TextFormat.PlainText
    cancel = control(dialog, "recovery_cancel")
    assert cancel.isDefault()
    assert not control(dialog, "recovery_accept").isDefault()
    discard.click()
    panel._open_detection_recovery()
    assert panel._recovery_dialog is dialog
    assert len(panel.findChildren(QtWidgets.QDialog, "detection_recovery_confirmation")) == 1
    cancel.click()
    qt.processEvents()
    assert panel._recovery_dialog is None
    assert app._activity_tracker.current_activity is original
    assert app.state is AppState.PAUSED
    assert database_contents(app) == before
    dialog = open_confirmation(panel, qt)
    dialog.close()
    qt.processEvents()
    assert panel._recovery_dialog is None
    assert app._activity_tracker.current_activity is original
    assert app.state is AppState.PAUSED
    assert database_contents(app) == before
    refresh(window, overlay, qt)
    assert window.grab().save("/tmp/gta-detection-recovery-default.png")


def test_actual_confirmation_clears_stale_displays_and_keeps_history_controls(app, views, native_qt_application):
    qt = native_qt_application
    capture_frame(app, banner="Hostile Takeover", balance=700)
    capture_frame(app, banner="MISSION PASSED\nHostile Takeover", balance=1000)
    original = seed(app)
    window, overlay, panel = views(app)
    rows = panel._table.rowCount()
    before = database_contents(app)
    badge = overlay._state_badge.text()
    assert window._dashboard._activity_card._activity_label.text() == "Headhunter"
    control(panel, "detection_pause_resume").click()
    dialog = open_confirmation(panel, qt)
    control(dialog, "recovery_accept").click()
    refresh(window, overlay, qt)
    assert panel._recovery_dialog is None
    assert app._activity_tracker.current_activity is None
    assert app.last_capture is None
    assert app.state is AppState.PAUSED
    assert app.get_detection_recovery_status().waiting_for_balance
    assert database_contents(app) == before
    assert panel._table.rowCount() == rows == 1
    assert "Resume" in control(panel, "detection_status", QtWidgets.QLabel).text()
    assert "discarded" in control(panel, "detection_feedback", QtWidgets.QLabel).text().lower()
    assert not control(panel, "discard_detection").isEnabled()
    assert window._dashboard._activity_card._activity_label.text() == "Idle"
    assert window._dashboard._activity_card._objective_label.text() == ""
    assert window._dashboard._activity_card._timer_label.text() == ""
    assert overlay._activity_label.text() != original.name
    assert overlay._timer_label.text() == ""
    assert overlay._state_badge.text() == badge  # Last observed state, no invented transition.
    control(panel, "manage_cooldowns").click()
    qt.processEvents()
    assert panel._cooldown_dialog is not None and panel._cooldown_dialog.isVisible()
    panel._cooldown_dialog.close()
    control(panel, "detection_pause_resume").click()
    refresh(window, overlay, qt)
    assert app.state is AppState.RUNNING
    assert "Waiting" in control(panel, "detection_status", QtWidgets.QLabel).text()


@pytest.mark.parametrize("change", ["resume_pause", "replacement", "refinement", "completion"])
def test_dialog_acceptance_never_substitutes_a_changed_target(app, views, native_qt_application, change):
    qt = native_qt_application
    original = seed(app, "Go to the location" if change == "refinement" else "Headhunter")
    window, overlay, panel = views(app)
    control(panel, "detection_pause_resume").click()
    dialog = open_confirmation(panel, qt)
    frozen_text = control(dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText()
    if change == "resume_pause":
        count = app._data.total_captures
        app.resume()  # Same effect as the tray controls while confirmation is open.
        app.pause()
        assert app._data.total_captures == count
    elif change == "replacement":
        newer = app._activity_tracker.start_activity(original.activity_type, original.name)
        newer.started_at = original.started_at
    elif change == "refinement":
        capture_frame(app, banner="Headhunter")
        assert app._activity_tracker.current_activity is original
    else:
        capture_frame(app, banner="MISSION PASSED\nHeadhunter")
    target = app._activity_tracker.current_activity
    before = database_contents(app)
    assert control(dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText() == frozen_text
    control(dialog, "recovery_accept").click()
    refresh(window, overlay, qt)
    assert app._activity_tracker.current_activity is target
    assert database_contents(app) == before
    assert not app.get_detection_recovery_status().waiting_for_balance
    assert "changed" in control(panel, "detection_feedback", QtWidgets.QLabel).text().lower()


def test_actual_worker_busy_then_drained_updates_controls(pipeline, views, native_qt_application):
    qt, p, app = native_qt_application, pipeline, pipeline.app
    window, overlay, panel = views(app)
    dialog = open_confirmation(panel, qt)
    old = app._activity_tracker.current_activity
    p.arm("capture")
    app.pause()
    refresh(window, overlay, qt)
    assert not control(panel, "discard_detection").isEnabled()
    assert "finish" in control(panel, "detection_status", QtWidgets.QLabel).text()
    control(dialog, "recovery_accept").click()
    qt.processEvents()
    assert app._activity_tracker.current_activity is old
    assert "finish" in control(panel, "detection_feedback", QtWidgets.QLabel).text()
    p.drain()
    refresh(window, overlay, qt)
    assert control(panel, "discard_detection").isEnabled()
    dialog = open_confirmation(panel, qt)
    control(dialog, "recovery_accept").click()
    refresh(window, overlay, qt)
    assert app._activity_tracker.current_activity is None
    assert app.last_capture is None


@pytest.mark.parametrize("state", [AppState.STARTING, AppState.STOPPING, AppState.STOPPED])
def test_controls_are_disabled_outside_a_running_or_paused_session(app, views, native_qt_application, state):
    seed(app)
    app._state = state
    window, overlay, panel = views(app)
    assert not control(panel, "detection_pause_resume").isEnabled()
    assert not control(panel, "discard_detection").isEnabled()
    assert control(panel, "manage_cooldowns").isEnabled()


def test_long_ocr_labels_and_confirmation_are_literal_and_fit_minimum_window(app, views, native_qt_application):
    qt = native_qt_application
    original = seed(app)
    literal = '<b>Mission & 雪</b> <img src=x> "literal" ' * 30 + "W" * 2000
    original.name = app._data.current_mission = literal
    app._last_capture_result.activity_name = literal
    window, overlay, panel = views(app)
    field = control(panel, "detection_name", QtWidgets.QLineEdit)
    assert field.text() == literal and field.isReadOnly()
    assert "<b>" not in field.toolTip() and "<img" not in field.toolTip()
    assert window.width() == 900 and window.height() == 650
    assert field.width() <= panel.width()
    assert panel._table.height() >= 100
    control(panel, "detection_pause_resume").click()
    dialog = open_confirmation(panel, qt)
    target = control(dialog, "recovery_target", QtWidgets.QPlainTextEdit)
    assert literal in target.toPlainText() and target.isReadOnly()
    assert dialog.width() <= 900 and dialog.height() <= 650
    for name in ("recovery_accept", "recovery_cancel"):
        button = control(dialog, name)
        assert button.isVisible()
        assert dialog.rect().contains(button.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    assert dialog.grab().save("/tmp/gta-detection-recovery-long.png")
    assert window.grab().save("/tmp/gta-detection-recovery-long-panel.png")
    dialog.close()
    qt.processEvents()
    assert app._activity_tracker.current_activity is original


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_confirmation_keyboard_default_and_escape_cancel_without_mutation(app, views, native_qt_application, key):
    from PyQt6.QtTest import QTest

    qt = native_qt_application
    original = seed(app)
    window, overlay, panel = views(app)
    control(panel, "detection_pause_resume").click()
    dialog = open_confirmation(panel, qt)
    before = database_contents(app)
    QTest.keyClick(dialog, key)
    refresh(window, overlay, qt)
    assert panel._recovery_dialog is None
    assert app._activity_tracker.current_activity is original
    assert app.state is AppState.PAUSED
    assert database_contents(app) == before


def test_confirmation_from_an_earlier_capture_run_cannot_clear_the_new_run(pipeline, views, native_qt_application):
    qt, app = native_qt_application, pipeline.app
    window, overlay, panel = views(app)
    dialog = open_confirmation(panel, qt)
    original = app._activity_tracker.current_activity
    frozen = control(dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText()
    app.stop()
    refresh(window, overlay, qt)
    assert not control(panel, "discard_detection").isEnabled()
    app._stop_event.idle.clear()
    assert app.start()
    assert app._stop_event.idle.wait(2)
    current = app._activity_tracker.current_activity
    assert current is not original and current.name == original.name
    before = database_contents(app)
    assert control(dialog, "recovery_target", QtWidgets.QPlainTextEdit).toPlainText() == frozen
    control(dialog, "recovery_accept").click()
    refresh(window, overlay, qt)
    assert app._activity_tracker.current_activity is current
    assert database_contents(app) == before
    assert "changed" in control(panel, "detection_feedback", QtWidgets.QLabel).text().lower()
    assert not app.get_detection_recovery_status().waiting_for_balance
