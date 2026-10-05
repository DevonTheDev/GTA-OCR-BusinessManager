"""Opt-in real Qt cooldown management with disposable files and a fixed clock."""

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native cooldown workflows", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt6 import sip

from src.tracking import cooldowns as storage
from src.tracking.cooldowns import ACTIVITY_COOLDOWNS, CooldownTracker
from src.ui.widgets.cooldown_widget import CooldownWidget, CompactCooldownWidget


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.value if tz is not None else cls.value.replace(tzinfo=None)

    monkeypatch.setattr(storage, "datetime", Clock)
    return Clock


@pytest.fixture
def tracker(tmp_path, clock):
    return CooldownTracker(tmp_path / "cooldowns.json")


@pytest.fixture
def widgets(qt):
    items = []
    yield items
    for widget in reversed(items):
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def own(widgets, widget):
    widgets.append(widget)
    widget.show()
    return widget


def control(widget, name, cls=QtWidgets.QPushButton):
    result = widget.findChild(cls, name)
    assert result is not None
    return result


def editor(widgets, tracker, cooldown=None):
    from src.ui.widgets.cooldown_manager_dialog import CooldownEditorDialog
    return own(widgets, CooldownEditorDialog(tracker, cooldown=cooldown))


def duration(dialog, seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    for part, value in (("hours", hours), ("minutes", minutes), ("seconds", seconds)):
        control(dialog, "cooldown_" + part, QtWidgets.QSpinBox).setValue(value)


def ordered_keys(widget):
    return [widget._container_layout.itemAt(i).widget().activity_name
            for i in range(widget._container_layout.count() - 1)]


def test_replacement_rebinds_existing_card_reorders_and_resets_style(tracker, widgets, clock):
    tracker.start_cooldown("first", "First", 100)
    clock.value += timedelta(seconds=95)
    tracker.start_cooldown("second", "Second", 30)
    widget = own(widgets, CooldownWidget(tracker))
    widget._update_cooldowns()
    original = widget._item_widgets["first"]
    assert ordered_keys(widget) == ["first", "second"]
    assert "#4CAF50" in original._time_label.styleSheet()
    tracker.start_cooldown("first", "<b>Replaced</b>", 300)
    widget._update_cooldowns()
    assert widget._item_widgets["first"] is original
    assert original._name_label.text() == "<b>Replaced</b>"
    assert original._name_label.textFormat() == Qt.TextFormat.PlainText
    assert original._time_label.text() == "5m 0s"
    assert "#4CAF50" not in original._time_label.styleSheet()
    assert ordered_keys(widget) == ["second", "first"]


def test_compact_names_literal_countdown_and_parented_timers(tracker, widgets, clock):
    tracker.start_cooldown("markup", "<b>x</b>", 1)
    full = own(widgets, CooldownWidget(tracker))
    compact = own(widgets, CompactCooldownWidget(tracker, max_display=1))
    clock.value += timedelta(milliseconds=100)
    full._update_cooldowns()
    compact._update_display()
    assert full._item_widgets["markup"]._time_label.text() == "1s"
    assert compact._labels[0].text() == "<b>x</b>: 1s"
    assert compact._labels[0].textFormat() == Qt.TextFormat.PlainText
    for widget in (full, compact):
        timer = widget._update_timer
        assert timer.parent() is widget
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(timer)


def test_editor_presets_and_custom_timers_keep_constants_and_stable_keys(tracker, widgets):
    original_presets = ACTIVITY_COOLDOWNS.copy()
    dialog = editor(widgets, tracker)
    combo = control(dialog, "cooldown_preset", QtWidgets.QComboBox)
    assert {combo.itemData(i) for i in range(combo.count())} == {
        None, *(key for key, value in ACTIVITY_COOLDOWNS.items() if value > 0)
    }
    combo.setCurrentIndex(combo.findData("headhunter"))
    name = control(dialog, "cooldown_name", QtWidgets.QLineEdit)
    assert name.isReadOnly() and name.text() == "Headhunter"
    assert control(dialog, "cooldown_minutes", QtWidgets.QSpinBox).value() == 5
    duration(dialog, 30)
    control(dialog, "cooldown_editor_save").click()
    assert tracker.get_cooldown("headhunter").duration_seconds == 30
    for _ in range(2):
        custom = editor(widgets, tracker)
        control(custom, "cooldown_name", QtWidgets.QLineEdit).setText("  <b>Same & literal</b>  ")
        duration(custom, 65)
        control(custom, "cooldown_editor_save").click()
    active = tracker.get_active_cooldowns()
    custom_timers = [item for item in active if item.activity_name.startswith("manual:")]
    assert len(custom_timers) == 2
    assert custom_timers[0].activity_name != custom_timers[1].activity_name
    assert {item.display_name for item in custom_timers} == {"<b>Same & literal</b>"}
    assert ACTIVITY_COOLDOWNS == original_presets


@pytest.mark.parametrize("name,seconds", [(" ", 30), ("x" * 201, 30), ("line\u2028break", 30),
                                           ("fine", 0), ("fine", 604801)])
def test_invalid_editor_input_changes_neither_memory_nor_file(tracker, widgets, name, seconds):
    tracker.start_cooldown("existing", "Existing", 30)
    before = tracker._data_path.read_bytes(), tracker._data_path.stat().st_mtime_ns
    dialog = editor(widgets, tracker)
    control(dialog, "cooldown_name", QtWidgets.QLineEdit).setText(name)
    duration(dialog, seconds)
    control(dialog, "cooldown_editor_save").click()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    assert not control(dialog, "cooldown_editor_error", QtWidgets.QLabel).isHidden()
    assert [item.activity_name for item in tracker.get_active_cooldowns()] == ["existing"]
    assert (tracker._data_path.read_bytes(), tracker._data_path.stat().st_mtime_ns) == before


def test_cancel_adjust_and_accepted_expired_editor_use_captured_key(tracker, widgets, clock):
    timer = tracker.start_custom_timer("Custom", 61)
    clock.value += timedelta(milliseconds=100)
    dialog = editor(widgets, tracker, timer)
    assert control(dialog, "cooldown_minutes", QtWidgets.QSpinBox).value() == 1
    assert control(dialog, "cooldown_seconds", QtWidgets.QSpinBox).value() == 1
    before = tracker._data_path.read_bytes()
    duration(dialog, 20)
    control(dialog, "cooldown_editor_cancel").click()
    assert tracker._data_path.read_bytes() == before
    adjusted = editor(widgets, tracker, tracker.get_cooldown(timer.activity_name))
    control(adjusted, "cooldown_name", QtWidgets.QLineEdit).setText("Renamed")
    duration(adjusted, 120)
    clock.value += timedelta(seconds=120)
    assert tracker.get_active_cooldowns() == []
    control(adjusted, "cooldown_editor_save").click()
    result = tracker.get_cooldown(timer.activity_name)
    assert result.display_name == "Renamed" and result.duration_seconds == 120
    assert result.started_at == clock.value
    fixed = editor(widgets, tracker, tracker.start_cooldown("headhunter"))
    assert control(fixed, "cooldown_name", QtWidgets.QLineEdit).isReadOnly()


def test_activity_manager_is_single_modeless_instance_and_deletes_timer(tracker, widgets, qt, tmp_path):
    from src.app import GTABusinessManager
    from src.config.settings import Settings
    from src.ui.widgets.activity_panel import ActivityPanel
    manager = GTABusinessManager(Settings(tmp_path / "activity-panel.yaml"))
    manager._cooldown_tracker = tracker
    panel = own(widgets, ActivityPanel(manager))
    panel._update_display()
    control(panel, "manage_cooldowns").click()
    first = panel._cooldown_dialog
    assert first is not None and not first.isModal()
    control(panel, "manage_cooldowns").click()
    assert panel._cooldown_dialog is first
    tracker.start_custom_timer("Keep on close", 100)
    first.refresh()
    timer = first._cooldown_list._update_timer
    first.close()
    assert panel._cooldown_dialog is None
    assert not timer.isActive()
    control(panel, "manage_cooldowns").click()
    second = panel._cooldown_dialog
    assert second is not None and second is not first
    panel._cooldown_finished(first)
    assert panel._cooldown_dialog is second
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(first) and sip.isdeleted(timer)
    assert len(second._cooldown_list._item_widgets) == 1


def test_manager_save_failure_retry_and_remove_targets_only_one_key(tracker, widgets, monkeypatch):
    from src.ui.widgets.cooldown_manager_dialog import CooldownManagerDialog
    first = tracker.start_custom_timer("Duplicate", 30)
    second = tracker.start_custom_timer("Duplicate", 40)
    manager = own(widgets, CooldownManagerDialog(tracker))
    previous = tracker._data_path.read_bytes()
    original = storage.atomic_text_writer
    def fail(*args, **kwargs):
        raise OSError("synthetic save failure")
    monkeypatch.setattr(storage, "atomic_text_writer", fail)
    control(manager._cooldown_list._item_widgets[first.activity_name], "cooldown_remove").click()
    assert tracker.get_cooldown(first.activity_name) is None
    assert tracker.get_cooldown(second.activity_name) is not None
    assert tracker._data_path.read_bytes() == previous
    warning = control(manager, "cooldown_storage_error", QtWidgets.QLabel)
    assert not warning.isHidden() and "couldn't be saved" in warning.text()
    assert not control(manager, "cooldown_retry").isHidden()
    started_at = tracker.get_cooldown(second.activity_name).started_at
    monkeypatch.setattr(storage, "atomic_text_writer", original)
    control(manager, "cooldown_retry").click()
    assert set(json.loads(tracker._data_path.read_text())["cooldowns"]) == {second.activity_name}
    assert tracker.get_cooldown(second.activity_name).started_at == started_at
    assert warning.isHidden() and control(manager, "cooldown_retry").isHidden()


def test_corrupt_load_warning_preserves_file_on_display_and_discloses_replacement(tmp_path, widgets, clock):
    from src.ui.widgets.cooldown_manager_dialog import CooldownManagerDialog
    path = tmp_path / "cooldowns.json"
    path.write_text('{"cooldowns":broken')
    before = path.read_bytes(), path.stat().st_mtime_ns
    tracker = CooldownTracker(path)
    manager = own(widgets, CooldownManagerDialog(tracker))
    for _ in range(3):
        manager._cooldown_list._update_timer.timeout.emit()
    warning = control(manager, "cooldown_storage_error", QtWidgets.QLabel)
    assert "replace" in warning.text() and "couldn't be loaded" in warning.text()
    assert not warning.isHidden() and control(manager, "cooldown_retry").isHidden()
    dialog = editor(widgets, tracker)
    assert "replace" in control(dialog, "cooldown_editor_storage_error", QtWidgets.QLabel).text()
    dialog.reject()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_long_literal_names_scroll_without_hiding_primary_controls(tracker, widgets, qt):
    from src.ui.widgets.cooldown_manager_dialog import CooldownManagerDialog
    name = "<b>" + "L" * 193 + "</b>"
    for i in range(15):
        tracker.start_custom_timer(name, 60 + i)
    manager = own(widgets, CooldownManagerDialog(tracker))
    manager.resize(560, 480)
    qt.processEvents()
    assert manager.width() == 560 and manager.height() == 480
    scroll = control(manager, "cooldown_scroll", QtWidgets.QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    for button_name in ("cooldown_start", "cooldown_close"):
        button = control(manager, button_name)
        point = button.mapTo(manager, QPoint())
        assert button.isVisible()
        assert point.x() + button.width() <= manager.width()
        assert point.y() + button.height() <= manager.height()
    first = next(iter(manager._cooldown_list._item_widgets.values()))
    assert first._name_label.text() == name
    assert first._name_label.textFormat() == Qt.TextFormat.PlainText
    # An unbroken name must have room for every glyph, not merely keep its raw
    # text intact while QLabel's default word-only wrapping clips most of it.
    text_layout, height = first._name_label._layout_text(first._name_label.width())
    assert sum(text_layout.lineAt(i).textLength() for i in range(text_layout.lineCount())) == len(name)
    assert text_layout.lineCount() >= 3
    assert first._name_label.height() >= height
    for button_name in ("cooldown_adjust", "cooldown_remove"):
        button = control(first, button_name)
        point = button.mapTo(first, QPoint())
        assert point.x() + button.width() <= first.width()
    dialog = editor(widgets, tracker)
    control(dialog, "cooldown_name", QtWidgets.QLineEdit).setText("😀" * 200)
    duration(dialog, 604800)
    control(dialog, "cooldown_editor_save").click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    accepted = next(item for item in tracker.get_active_cooldowns() if item.display_name == "😀" * 200)
    adjusted = editor(widgets, tracker, accepted)
    assert control(adjusted, "cooldown_name", QtWidgets.QLineEdit).text() == "😀" * 200
    duration(adjusted, 120)
    control(adjusted, "cooldown_editor_save").click()
    result = tracker.get_cooldown(accepted.activity_name)
    assert result.display_name == "😀" * 200 and result.duration_seconds == 120
