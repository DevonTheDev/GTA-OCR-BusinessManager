"""Real offscreen sample UI; synthetic data never validates native gameplay."""

import os
import threading
from types import SimpleNamespace

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native sample UI", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtTest import QTest

from src.app import AppState
from src.config import settings as settings_module
from src.ui.main_window import MainWindow
from tests.test_app_accounting import app as app


class SampleSlot:
    """UI boundary double; lifecycle behavior has separate real-worker coverage."""

    def __init__(self, app):
        self.app = app
        self.status = self.value("idle")
        self.claims = []
        self.completions = []
        self.cancellations = []
        self.sample = None

    def value(self, state, token=None, **changes):
        return SimpleNamespace(state=state, token=token, sample_id=changes.get("sample_id", "sample-old"),
                               run_id="run-old", requested_at="2026-10-08T16:00:00+00:00",
                               captured_at=changes.get("captured_at", "2026-10-08T16:00:02+00:00"),
                               message=changes.get("message", ""))

    def request(self):
        if self.status.state in {"idle", "saved", "failed"}:
            self.status = self.value("armed", object())
        return self.status

    def ready(self, **changes):
        token = self.request().token
        self.status = self.value("ready", token, **changes)
        return self.status

    def cancel(self, token):
        self.cancellations.append(token)
        if token is self.status.token and self.status.state != "saving":
            self.status = self.value("idle")
            return True
        return False

    def claim(self, token):
        self.claims.append(token)
        if token is self.status.token and self.status.state == "ready":
            self.status.state = "saving"
            return self.sample
        return None

    def complete(self, token, result):
        self.completions.append((token, result))
        if token is not self.status.token:
            return False
        self.status.state = "saved" if result.success else "failed"
        return True


@pytest.fixture
def view(app, monkeypatch, native_qt_application):
    app._state = AppState.PAUSED
    monkeypatch.setattr(settings_module, "_settings", app._settings)
    slot = SampleSlot(app)
    monkeypatch.setattr(app, "get_detection_sample_status", lambda: slot.status, raising=False)
    monkeypatch.setattr(app, "request_detection_sample", slot.request, raising=False)
    monkeypatch.setattr(app, "cancel_detection_sample", slot.cancel, raising=False)
    monkeypatch.setattr(app, "claim_detection_sample_for_save", slot.claim, raising=False)
    monkeypatch.setattr(app, "complete_detection_sample_save", slot.complete, raising=False)
    window = MainWindow(app)
    window.resize(900, 650)
    window.show()
    window._update_ui()
    native_qt_application.processEvents()
    yield app, window, slot
    for dialog in window.findChildren(QtWidgets.QDialog):
        dialog.close()
    for child in window.findChildren(QtWidgets.QWidget):
        if hasattr(child, "_timer"):
            child._timer.stop()
    window._update_timer.stop()
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    native_qt_application.processEvents()


def control(window, name, kind=QtWidgets.QLabel):
    result = window.findChild(kind, name)
    assert result is not None, f"Sample workflow requires {name}"
    return result


def action(window):
    return control(window, "save_detection_sample", QAction)


def open_save(window, slot, qt, **changes):
    status = slot.ready(**changes)
    window._update_ui()
    action(window).trigger()
    qt.processEvents()
    dialog = window.findChild(QtWidgets.QDialog, "detection_sample_save")
    assert dialog is not None and dialog.isVisible()
    return status, dialog


def test_arm_paused_hide_ready_never_opens_a_dialog(view, native_qt_application):
    app, window, slot = view
    assert action(window).isEnabled()
    assert "next" in action(window).text().lower()
    action(window).trigger()
    token = slot.status.token
    assert slot.status.state == "armed"
    assert not window.findChildren(QtWidgets.QDialog)
    window.close()  # Existing close-to-tray semantics must not cancel.
    native_qt_application.processEvents()
    assert not window.isVisible()
    assert slot.status.token is token
    slot.ready()
    window._update_ui()
    native_qt_application.processEvents()
    assert "captured" in action(window).text().lower()
    assert not window.isVisible()
    assert not window.findChildren(QtWidgets.QDialog)
    assert "sample-old" in control(window, "detection_sample_status").text()
    assert slot.status.captured_at in control(window, "detection_sample_status").text()


@pytest.mark.parametrize("state", ["idle", "unavailable"])
def test_ordinary_tracking_keeps_original_tab_footprint_and_preactivation_guidance(
    view, native_qt_application, state,
):
    app, window, slot = view
    slot.status = slot.value(state)
    window._update_ui()
    native_qt_application.processEvents()
    bar = control(window, "detection_sample_status").parentWidget()
    assert not bar.isVisible()
    info_bar = window.centralWidget().layout().itemAt(0).widget()
    assert window._tabs.y() == info_bar.geometry().bottom() + 1
    guidance = action(window).toolTip()
    assert "pause" in guidance.lower() and "arm" in guidance.lower() and "return to GTA" in guidance


def test_sample_strip_appears_only_for_sample_work_and_keeps_last_export_feedback(view, native_qt_application):
    app, window, slot = view
    bar = control(window, "detection_sample_status").parentWidget()
    action(window).trigger()
    native_qt_application.processEvents()
    assert bar.isVisible()
    action(window).trigger()
    native_qt_application.processEvents()
    assert not bar.isVisible()
    slot.status = slot.value("failed", object(), message="Capture unavailable")
    window._update_ui()
    assert bar.isVisible()
    slot.status = slot.value("idle")
    feedback = control(window, "detection_sample_export_result", QtWidgets.QPlainTextEdit)
    feedback.setPlainText("Saved sample: old\nCaptured at: 2026-10-08T16:00:00+00:00")
    window._update_ui()
    assert bar.isVisible()


def test_occupied_action_cancels_exact_request_without_rearming(view):
    app, window, slot = view
    action(window).trigger()
    token = slot.status.token
    assert "Cancel" in action(window).text()
    action(window).trigger()
    assert slot.cancellations == [token]
    assert slot.status.state == "idle"


@pytest.mark.parametrize("state", [AppState.STARTING, AppState.STOPPING, AppState.STOPPED])
def test_arm_requires_running_or_paused(view, state):
    app, window, slot = view
    app._state = state
    window._update_ui()
    assert not action(window).isEnabled()


@pytest.mark.parametrize("how", ["close", "escape", "cancel", "return"])
def test_disclosure_is_explicit_and_cancel_retires_without_writes(view, native_qt_application, how):
    app, window, slot = view
    status, dialog = open_save(window, slot, native_qt_application)
    disclosure = control(dialog, "detection_sample_disclosure")
    assert "full captured screen" in disclosure.text()
    assert "other visible content" in disclosure.text()
    assert "Saved locally; nothing is uploaded" in disclosure.text()
    assert disclosure.textFormat() == Qt.TextFormat.PlainText
    identity = control(dialog, "detection_sample_identity", QtWidgets.QPlainTextEdit)
    assert status.sample_id in identity.toPlainText()
    assert status.captured_at in identity.toPlainText()
    assert identity.isReadOnly()
    cancel = control(dialog, "detection_sample_cancel", QtWidgets.QPushButton)
    assert cancel.isDefault()
    action(window).trigger()
    assert len(window.findChildren(QtWidgets.QDialog, "detection_sample_save")) == 1
    if how == "close":
        dialog.close()
    elif how in {"escape", "return"}:
        QTest.keyClick(dialog, Qt.Key.Key_Escape if how == "escape" else Qt.Key.Key_Return)
    else:
        cancel.click()
    native_qt_application.processEvents()
    assert slot.cancellations == [status.token]
    assert slot.status.state == "idle"
    assert slot.claims == []


def test_cancel_directory_chooser_retires_exact_sample(view, native_qt_application, monkeypatch):
    app, window, slot = view
    status, dialog = open_save(window, slot, native_qt_application)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton).click()
    native_qt_application.processEvents()
    assert slot.cancellations == [status.token]
    assert slot.claims == []
    assert slot.status.state == "idle"


def test_stale_directory_dialog_cannot_claim_newer_sample(view, native_qt_application, monkeypatch, tmp_path):
    app, window, slot = view
    before = set(tmp_path.iterdir())
    old, dialog = open_save(window, slot, native_qt_application)

    def choose(*args, **kwargs):
        slot.status = slot.value("ready", object(), sample_id="sample-new")
        return str(tmp_path)

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", choose)
    control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton).click()
    native_qt_application.processEvents()
    assert slot.claims == [old.token]
    assert slot.status.state == "ready" and slot.status.sample_id == "sample-new"
    assert set(tmp_path.iterdir()) == before
    assert "sample-new" in control(window, "detection_sample_status").text()


@pytest.mark.parametrize("running,bindings,expected", [
    (True, {"toggle_tracking": "ctrl+alt+f9"}, "ctrl+alt+f9"),
    (True, {"toggle_tracking": "ctrl+shift+t"}, "ctrl+shift+t"),
    (True, {"toggle_tracking": '<b>ctrl+雪</b><img src=x>&"'}, '<b>ctrl+雪</b><img src=x>&"'),
    (True, {}, None),
    (False, {"toggle_tracking": "ctrl+alt+f9"}, None),
])
def test_single_monitor_guidance_uses_only_running_registered_toggle(view, monkeypatch, running, bindings, expected):
    from PyQt6.QtGui import QTextDocument
    from src import hotkeys

    app, window, slot = view
    monkeypatch.setattr(hotkeys, "_hotkey_manager", SimpleNamespace(is_running=running, registered_hotkeys=bindings))
    window._update_ui()
    guidance = control(window, "detection_sample_guidance")
    assert guidance.textFormat() == Qt.TextFormat.PlainText
    text = guidance.text()
    tooltip = QTextDocument()
    tooltip.setHtml(action(window).toolTip())
    assert tooltip.toPlainText() == text
    assert "pause" in text.lower() and "arm" in text.lower() and "return to GTA" in text
    if expected:
        assert expected in text
        assert "resume" in text.lower()
    else:
        assert "No registered" in text and "foreground" in text
        assert "ctrl+shift+t" not in text.lower()
        assert "ctrl+alt+f9" not in text.lower()


def test_hostile_identity_is_literal_and_fits_minimum_window(view, native_qt_application):
    app, window, slot = view
    hostile = '<b>Mission & 雪</b> <img src=x> "literal" ' * 30 + "W" * 2000
    status, dialog = open_save(window, slot, native_qt_application, sample_id=hostile)
    identity = control(dialog, "detection_sample_identity", QtWidgets.QPlainTextEdit)
    assert hostile in identity.toPlainText()
    assert control(window, "detection_sample_status").textFormat() == Qt.TextFormat.PlainText
    assert window.width() == 900 and window.height() == 650
    assert dialog.width() <= 900 and dialog.height() <= 650
    for name in ("detection_sample_save_choose", "detection_sample_cancel"):
        button = control(dialog, name, QtWidgets.QPushButton)
        assert dialog.rect().contains(button.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    assert dialog.grab().save("../evidence/gta-detection-sample-ui/disclosure-literal.png")


@pytest.mark.parametrize("success", [True, False])
def test_single_export_worker_keeps_gui_responsive_and_reports_exact_old_sample(
    view, native_qt_application, monkeypatch, tmp_path, success,
):
    from src.ui import main_window as window_module

    app, window, slot = view
    slot.sample = SimpleNamespace(sample_id="sample-old", captured_at="2026-10-08T16:00:02+00:00",
                                  run_id="run-old", png_bytes=b"frozen png", json_bytes=b"{}")
    old, dialog = open_save(window, slot, native_qt_application)
    entered, release = threading.Event(), threading.Event()
    export_threads, exported = [], []
    path = str(tmp_path / '<b>literal&雪</b>')
    outcome = SimpleNamespace(success=success, path=path, completed_files=("frame.png",),
                              partial_files=() if success else ("sample.json",),
                              message="Complete" if success else "Write failed")

    def export(sample, directory):
        export_threads.append(threading.current_thread())
        exported.append((sample, directory))
        entered.set()
        assert release.wait(3), "The test must release the blocked disk writer"
        return outcome

    monkeypatch.setattr(window_module, "export_detection_sample", export, raising=False)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    try:
        control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton).click()
        assert entered.wait(2)
        assert export_threads[0] is not threading.current_thread()
        assert slot.claims == [old.token] and slot.status.state == "saving"
        assert not action(window).isEnabled()
        action(window).trigger()
        timer_ran = []
        QTimer.singleShot(0, lambda: timer_ran.append(True))
        native_qt_application.processEvents()
        assert timer_ran == [True]
        assert len(exported) == 1
        # Stop/restart can install a newer request while this admitted write finishes.
        slot.status = slot.value("ready", object(), sample_id="sample-new")
        window._update_ui()
        assert "sample-new" in control(window, "detection_sample_status").text()
    finally:
        release.set()
        for worker in export_threads:
            worker.join(3)
    window._update_ui()
    native_qt_application.processEvents()
    assert slot.status.state == "ready" and slot.status.sample_id == "sample-new"
    assert slot.completions == [(old.token, outcome)]
    assert len(exported) == 1
    feedback = control(window, "detection_sample_export_result", QtWidgets.QPlainTextEdit)
    text = feedback.toPlainText()
    assert "sample-old" in text and old.captured_at in text and path in text
    assert "sample-new" not in text
    if success:
        assert "Saved sample" in text
    else:
        assert "not saved" in text.lower()
        assert "Completed files: frame.png" in text
        assert "Partial files: sample.json" in text
    assert action(window).isEnabled()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not window.findChildren(QtWidgets.QDialog, "detection_sample_save")


def test_closing_disclosure_during_destination_selection_cannot_write(view, native_qt_application, monkeypatch, tmp_path):
    app, window, slot = view
    before = set(tmp_path.iterdir())
    old, dialog = open_save(window, slot, native_qt_application)

    def choose(*args, **kwargs):
        dialog.close()
        native_qt_application.processEvents()
        return str(tmp_path)

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", choose)
    control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton).click()
    assert slot.claims == []
    assert slot.cancellations == [old.token]
    assert set(tmp_path.iterdir()) == before


def test_destination_dialog_failure_leaves_ready_sample_available(view, native_qt_application, monkeypatch):
    app, window, slot = view
    old, dialog = open_save(window, slot, native_qt_application)
    choose = control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton)

    def fail(*args, **kwargs):
        raise RuntimeError("<img src=x> private failure details")

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", fail)
    window._choose_detection_sample_destination(dialog, old, choose)
    assert slot.status.token is old.token and slot.status.state == "ready"
    assert slot.claims == [] and slot.cancellations == []
    assert choose.isEnabled()
    feedback = control(dialog, "detection_sample_save_error")
    assert "folder chooser" in feedback.text().lower()
    assert "private" not in feedback.text() and "<img" not in feedback.text()
    assert feedback.textFormat() == Qt.TextFormat.PlainText


def test_worker_construction_failure_is_terminal_and_releases_ui(view, native_qt_application, monkeypatch, tmp_path):
    from src.ui import main_window as window_module

    app, window, slot = view
    slot.sample = SimpleNamespace(sample_id="sample-old", captured_at="2026-10-08T16:00:02+00:00")
    old, dialog = open_save(window, slot, native_qt_application)
    choose = control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic thread resource exhaustion")

    monkeypatch.setattr(window_module, "Thread", fail)
    window._choose_detection_sample_destination(dialog, old, choose)
    window._update_ui()
    assert slot.status.state == "failed"
    assert slot.claims == [old.token]
    assert len(slot.completions) == 1 and not slot.completions[0][1].success
    assert action(window).isEnabled()
    feedback = control(window, "detection_sample_export_result", QtWidgets.QPlainTextEdit).toPlainText()
    assert "no files were written" in feedback.lower()


def test_unexpected_writer_exception_reports_unknown_output_without_crashing(view, native_qt_application, monkeypatch, tmp_path):
    from src.ui import main_window as window_module

    app, window, slot = view
    slot.sample = SimpleNamespace(sample_id="sample-old", captured_at="2026-10-08T16:00:02+00:00")
    old, dialog = open_save(window, slot, native_qt_application)
    threads = []

    def fail(*args, **kwargs):
        threads.append(threading.current_thread())
        raise RuntimeError("<img src=x> secret OCR or filesystem failure")

    monkeypatch.setattr(window_module, "export_detection_sample", fail)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    control(dialog, "detection_sample_save_choose", QtWidgets.QPushButton).click()
    for thread in threads:
        thread.join(2)
    window._update_ui()
    assert slot.status.state == "failed"
    text = control(window, "detection_sample_export_result", QtWidgets.QPlainTextEdit).toPlainText()
    assert "unknown" in text.lower()
    assert "Completed files: none" not in text
    assert "secret" not in text and "<img" not in text
