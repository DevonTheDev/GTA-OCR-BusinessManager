"""Opt-in actual MainWindow/overlay goals with real app accounting and SQLite."""

import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for app goal workflows", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt6 import sip

from src.app import GTABusinessManager
from src.config import settings as settings_module
from src.detection.parsers.money_parser import MoneyReading
from src.tracking.goals import GoalType
from src.tracking import session_goals as storage_module
from src.ui.main_window import MainWindow
from src.ui.overlay import OverlayWindow
from src.ui.widgets.goal_widget import GoalSetterDialog
from tests.test_app_accounting import app as _accounting_app

accounting_app = _accounting_app


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def view(accounting_app, qt, monkeypatch):
    # SettingsPanel uses the application's shared settings accessor. Bind it to
    # this disposable fixture so opening the real window never touches home.
    monkeypatch.setattr(settings_module, "_settings", accounting_app._settings)
    overlay = OverlayWindow(accounting_app)
    window = None
    try:
        window = MainWindow(accounting_app, overlay=overlay)
        window.resize(900, 650)
        window._tabs.setCurrentWidget(window._session_panel)
        window.show()
        qt.processEvents()
        yield accounting_app, window, overlay, window._session_panel._goals_panel
    finally:
        for widget in (window, overlay):
            if widget is not None and not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def press(widget, name):
    button = widget.findChild(QtWidgets.QPushButton, name)
    assert button is not None and button.isEnabled()
    button.click()


def choose_preset(monkeypatch, label):
    original = GoalSetterDialog.__init__
    def initialize(dialog, parent=None):
        original(dialog, parent)
        def click():
            button = next(item for item in dialog.findChildren(QtWidgets.QPushButton) if item.text() == label)
            button.click()
        QTimer.singleShot(0, click)
    monkeypatch.setattr(GoalSetterDialog, "__init__", initialize)


def test_main_window_preset_tracks_real_gross_income_in_card_and_overlay(view, monkeypatch, qt):
    app, window, overlay, card = view
    choose_preset(monkeypatch, "Quick 500K")
    press(card, "session_goal_set")
    for balance in (1_000_000, 900_000, 950_000):
        app._process_money_change(MoneyReading(total=balance))
    card.refresh()
    overlay._update_goal()
    qt.processEvents()
    assert card.findChild(QtWidgets.QProgressBar, "session_goal_progress_bar").value() == 10
    assert card.findChild(QtWidgets.QLabel, "session_goal_remaining").text() == "$450K to go"
    assert overlay._goal_progress.value() == 10
    assert overlay._goal_name_label.text() == "Quick 500K"
    assert not overlay._goal_frame.isHidden()
    assert card.geometry().bottom() < window._session_panel.height()
    assert window._session_panel.findChild(QtWidgets.QScrollArea) is not None
    record = app._repository.export_session_data(app._data.db_session_id)
    assert [item["amount"] for item in record["earnings"]] == [50_000]
    assert len(record["activities"]) == 0


def test_change_and_cancel_use_current_period_totals(view, monkeypatch):
    app, _, _, card = view
    app._session_tracker.record_activity_complete(success=False)
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    with monkeypatch.context() as choice:
        choose_preset(choice, "5 Activities")
        press(card, "session_goal_set")
    assert app.goal_tracker.current_goal.goal_type is GoalType.ACTIVITIES
    assert app.goal_tracker.current_goal.current_value == 1
    assert card.findChild(QtWidgets.QProgressBar, "session_goal_progress_bar").value() == 20
    real_constructor = GoalSetterDialog.__init__
    def cancel(dialog, parent=None):
        real_constructor(dialog, parent)
        QTimer.singleShot(0, dialog.reject)
    monkeypatch.setattr(GoalSetterDialog, "__init__", cancel)
    prior = app.goal_tracker.current_goal.to_dict()
    press(card, "session_goal_set")
    assert app.goal_tracker.current_goal.to_dict() == prior


def test_reset_and_clear_update_actual_card_overlay_and_remembered_target(view, qt):
    app, _, overlay, card = view
    app.set_session_goal(GoalType.ACTIVITIES, 1)
    app._session_tracker.record_activity_complete(success=False)
    card.refresh(); overlay._update_goal()
    assert app.goal_tracker.current_goal.is_complete
    app.reset_session()
    card.refresh(); overlay._update_goal()
    assert card.findChild(QtWidgets.QProgressBar, "session_goal_progress_bar").value() == 0
    assert overlay._goal_progress.value() == 0
    assert not app.goal_tracker.current_goal.is_complete
    press(card, "session_goal_clear")
    overlay._update_goal()
    qt.processEvents()
    assert app.goal_tracker.current_goal is None
    assert overlay._goal_frame.isHidden()
    assert GTABusinessManager(app._settings).goal_tracker.current_goal is None


def test_target_save_failure_is_visible_and_native_retry_commits_current_selection(view, monkeypatch):
    app, _, _, card = view
    app.set_session_goal(GoalType.EARNINGS, 100_000)
    target = app._settings.data_dir / "session_goal_target.json"
    previous = target.read_bytes()
    original = storage_module.atomic_text_writer
    def fail(*args, **kwargs):
        raise OSError("synthetic save failure")
    monkeypatch.setattr(storage_module, "atomic_text_writer", fail)
    app.set_session_goal(GoalType.EARNINGS, 200_000)
    card.refresh()
    assert target.read_bytes() == previous
    assert app.goal_tracker.current_goal.target_value == 200_000
    assert not card.findChild(QtWidgets.QLabel, "session_goal_storage_error").isHidden()
    assert not card.findChild(QtWidgets.QPushButton, "session_goal_retry").isHidden()
    monkeypatch.setattr(storage_module, "atomic_text_writer", original)
    press(card, "session_goal_retry")
    assert json.loads(target.read_text())["target"]["target_value"] == 200_000
    assert card.findChild(QtWidgets.QLabel, "session_goal_storage_error").isHidden()


def test_actual_names_are_literal_and_progress_refresh_does_not_rewrite_target(view):
    app, _, overlay, card = view
    name = '<b>Literal goal & title</b>'
    app.set_session_goal(GoalType.EARNINGS, 100_000, name)
    target = app._settings.data_dir / "session_goal_target.json"
    before = target.read_bytes(), target.stat().st_mtime_ns
    for balance in (100_000, 150_000):
        app._process_money_change(MoneyReading(total=balance))
    for _ in range(5):
        card.refresh(); overlay._update_goal()
    label = card.findChild(QtWidgets.QLabel, "session_goal_name")
    assert label.text() == name and label.textFormat() == Qt.TextFormat.PlainText
    assert overlay._goal_name_label.text() == name
    assert overlay._goal_name_label.textFormat() == Qt.TextFormat.PlainText
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
