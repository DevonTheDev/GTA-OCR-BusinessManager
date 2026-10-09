"""Actual app worker and Qt sample flow, with synthetic capture and keyboard I/O."""

import os
import sys
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native sample workflow", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt6.QtGui import QAction

from src import hotkeys
from src.app import AppState
from src.config import settings as settings_module
from src.main import _setup_hotkeys
from src.ui.main_window import MainWindow
from tests.test_detection_recovery_worker import pipeline as pipeline


@pytest.fixture
def actual_view(pipeline, monkeypatch, native_qt_application):
    app = pipeline.app
    monkeypatch.setattr(settings_module, "_settings", app._settings)
    window = MainWindow(app)
    window.resize(900, 650)
    window.show()
    window._update_ui()
    native_qt_application.processEvents()
    yield pipeline, window
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


@pytest.mark.parametrize("binding,registration_fails", [
    ("ctrl+shift+t", False), ("ctrl+alt+f9", False), ("ctrl+shift+t", True),
])
def test_registered_resume_callback_captures_next_admission_without_showing_dialog(
    actual_view, monkeypatch, native_qt_application, binding, registration_fails,
):
    p, window = actual_view
    app = p.app
    registered = {}

    def add_hotkey(key, callback):
        if key == binding and registration_fails:
            raise RuntimeError("Synthetic unavailable keyboard permission")
        registered[key] = callback

    # Exercise the real manager registration and real main callback; never add a hook.
    monkeypatch.setitem(sys.modules, "keyboard", SimpleNamespace(add_hotkey=add_hotkey,
                                                                 remove_hotkey=lambda *a: None))
    app._settings.set("hotkeys.toggle_tracking", binding)
    manager = hotkeys.HotkeyManager()
    monkeypatch.setattr(hotkeys, "_hotkey_manager", manager)
    _setup_hotkeys(app, window, None, None)
    assert manager.is_running
    window._update_ui()
    guidance = window.findChild(QtWidgets.QLabel, "detection_sample_guidance")
    assert guidance is not None, "Sample workflow must explain actual resume registration"
    assert guidance.textFormat() == Qt.TextFormat.PlainText
    if registration_fails:
        assert "toggle_tracking" not in manager.registered_hotkeys
        assert "No registered" in guidance.text() and "foreground" in guidance.text()
        assert binding not in guidance.text()
        return
    assert manager.registered_hotkeys["toggle_tracking"] == binding
    assert binding in guidance.text()
    assert app.state is AppState.PAUSED
    count = app._data.total_captures
    action = window.findChild(QAction, "save_detection_sample")
    assert action is not None, "Sample workflow must expose its File action"
    action.trigger()
    old = app.get_detection_sample_status()
    assert old.state == "armed"
    window.close()
    native_qt_application.processEvents()
    assert not window.isVisible()
    app._stop_event.idle.clear()
    registered[binding]()
    assert app._stop_event.idle.wait(3)
    ready = app.get_detection_sample_status()
    assert ready.state == "ready", ready.message
    assert ready.token is old.token
    assert app._data.total_captures == count + 1
    window._update_ui()
    native_qt_application.processEvents()
    assert not window.isVisible()
    assert not window.findChildren(QtWidgets.QDialog)
    status_label = window.findChild(QtWidgets.QLabel, "detection_sample_status")
    assert ready.sample_id in status_label.text() and ready.captured_at in status_label.text()


def collect_ready(p, window, qt):
    action = window.findChild(QAction, "save_detection_sample")
    action.trigger()
    p.app._stop_event.idle.clear()
    p.app.resume()
    assert p.app._stop_event.idle.wait(3)
    ready = p.app.get_detection_sample_status()
    assert ready.state == "ready", ready.message
    window._update_ui()
    qt.processEvents()
    return ready


def test_ready_and_disclosure_render_without_clipping_at_minimum_size(actual_view, native_qt_application, tmp_path):
    p, window = actual_view
    ready = collect_ready(p, window, native_qt_application)
    window._tabs.setCurrentWidget(window._activity_panel)
    window._activity_panel._update_display()
    native_qt_application.processEvents()
    assert window.width() == 900 and window.height() == 650
    for name in ("detection_sample_status", "detection_sample_guidance"):
        label = window.findChild(QtWidgets.QLabel, name)
        content = label.contentsRect()
        needed = label.fontMetrics().boundingRect(QRect(0, 0, content.width(), 1000),
                                                  int(Qt.TextFlag.TextWordWrap), label.text())
        assert content.height() >= needed.height(), (label.text(), content, needed)
        assert content.width() >= needed.width(), (label.text(), content, needed)
        assert label.visibleRegion().contains(content)
    assert window.grab().save(str(tmp_path / "main-ready-900x650.png"))
    window.findChild(QAction, "save_detection_sample").trigger()
    native_qt_application.processEvents()
    dialog = window.findChild(QtWidgets.QDialog, "detection_sample_save")
    assert dialog is not None and dialog.isVisible()
    text = dialog.findChild(QtWidgets.QPlainTextEdit, "detection_sample_identity").toPlainText()
    assert ready.sample_id in text and ready.captured_at in text
    for child in dialog.findChildren(QtWidgets.QPushButton):
        assert dialog.rect().contains(child.mapTo(dialog, QPoint(0, 0)))
        assert dialog.rect().contains(child.mapTo(dialog, child.rect().bottomRight()))
    assert dialog.grab().save(str(tmp_path / "save-disclosure.png"))


@pytest.mark.parametrize("partial_failure", [False, True])
def test_real_collected_sample_exports_detached_bytes_and_reports_disk_outcome(
    actual_view, native_qt_application, monkeypatch, tmp_path, partial_failure,
):
    from src.detection.detection_sample import export_detection_sample
    from src.ui import main_window as window_module

    p, window = actual_view
    ready = collect_ready(p, window, native_qt_application)
    destination = tmp_path / "local samples & 雪"
    destination.mkdir()
    before_captures = p.app._data.total_captures
    seen = []
    entered = threading.Event()

    def export(sample, parent):
        seen.append((sample, threading.current_thread()))
        try:
            return export_detection_sample(sample, parent)
        finally:
            entered.set()

    monkeypatch.setattr(window_module, "export_detection_sample", export)
    real_open = Path.open

    class PartialWrite:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, value):
            self.file.write(value[:7])
            raise OSError("Synthetic disk-full failure")

    if partial_failure:
        def fail_open(path, *args, **kwargs):
            file = real_open(path, *args, **kwargs)
            return PartialWrite(file) if path.name == "sample.json" and args == ("xb",) else file

        monkeypatch.setattr(Path, "open", fail_open)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(destination))
    window.findChild(QAction, "save_detection_sample").trigger()
    dialog = window.findChild(QtWidgets.QDialog, "detection_sample_save")
    dialog.findChild(QtWidgets.QPushButton, "detection_sample_save_choose").click()
    assert entered.wait(3)
    sample, worker = seen[0]
    worker.join(3)
    assert not worker.is_alive()
    window._update_ui()
    native_qt_application.processEvents()
    assert p.app._data.total_captures == before_captures
    folders = list(destination.iterdir())
    assert len(folders) == 1
    folder = folders[0]
    assert (folder / "frame.png").read_bytes() == sample.png_bytes
    feedback = window.findChild(QtWidgets.QPlainTextEdit, "detection_sample_export_result").toPlainText()
    assert str(folder) in feedback and ready.sample_id in feedback and ready.captured_at in feedback
    if partial_failure:
        assert p.app.get_detection_sample_status().state == "failed"
        assert (folder / "sample.json").read_bytes() == sample.json_bytes[:7]
        assert not (folder / "COMPLETE.json").exists()
        assert "Sample not saved" in feedback
        assert "Completed files: frame.png" in feedback and "Partial files: sample.json" in feedback
    else:
        assert p.app.get_detection_sample_status().state == "saved"
        assert (folder / "sample.json").read_bytes() == sample.json_bytes
        marker = json.loads((folder / "COMPLETE.json").read_bytes())
        for name, payload in (("frame.png", sample.png_bytes), ("sample.json", sample.json_bytes)):
            assert marker["files"][name] == {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        assert "Saved sample" in feedback


def test_actual_restart_during_folder_chooser_cannot_consume_new_ready_sample(
    actual_view, native_qt_application, monkeypatch, tmp_path,
):
    p, window = actual_view
    old = collect_ready(p, window, native_qt_application)
    destination = tmp_path / "stale destination"
    destination.mkdir()
    window.findChild(QAction, "save_detection_sample").trigger()
    dialog = window.findChild(QtWidgets.QDialog, "detection_sample_save")
    newer = []

    def choose(*args, **kwargs):
        p.app.stop()
        p.app._stop_event.idle.clear()
        assert p.app.start()
        assert p.app._stop_event.idle.wait(3)
        p.app.request_detection_sample()
        p.app._stop_event.idle.clear()
        p.app.resume()
        assert p.app._stop_event.idle.wait(3)
        newer.append(p.app.get_detection_sample_status())
        assert newer[0].state == "ready"
        assert newer[0].token is not old.token
        return str(destination)

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", choose)
    dialog.findChild(QtWidgets.QPushButton, "detection_sample_save_choose").click()
    window._update_ui()
    assert p.app.get_detection_sample_status().token is newer[0].token
    assert p.app.get_detection_sample_status().state == "ready"
    assert list(destination.iterdir()) == []
    assert newer[0].sample_id in window.findChild(QtWidgets.QLabel, "detection_sample_status").text()
