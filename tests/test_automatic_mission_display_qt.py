"""Opt-in native labels for automatic mission identity; synthetic capture data."""

import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native mission display tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt

from src.app import AppState, CaptureResult
from src.detection.parsers.mission_parser import MissionReading
from src.detection.parsers.timer_parser import TimerReading
from src.game.activities import Activity, ActivityType
from src.game.state_machine import GameState
from src.ui.overlay import OverlaySize, OverlayWindow
from src.ui.widgets.dashboard import DashboardWidget
from tests.test_automatic_mission_display import make_app


@pytest.fixture
def displays(native_qt_application):
    app = make_app()
    dashboard = DashboardWidget(app)
    overlay = OverlayWindow(app)
    dashboard._timer.stop()
    overlay._update_timer.stop()
    dashboard.show()
    overlay.show()
    native_qt_application.processEvents()
    yield app, dashboard, overlay, native_qt_application
    # Close roots before deleting child widgets; keep the session Qt anchor alive.
    for widget in (overlay, dashboard):
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    native_qt_application.processEvents()


def refresh(displays):
    _, dashboard, overlay, qt = displays
    dashboard._update_display()
    overlay._update_ui()
    qt.processEvents()


def test_ocr_activity_and_objective_labels_render_literal_text(displays):
    app, dashboard, overlay, _qt = displays
    literal_name = "<b>Headhunter & 雪</b>"
    literal_objective = "<img src='file:///missing'> Deliver the goods & wait"
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name=literal_name,
        activity_identity_status="known_name",
        mission=MissionReading(objective=literal_objective),
    )
    app.recent_activities = [Activity(ActivityType.VIP_WORK, name=literal_name, success=True)]
    refresh(displays)
    for label in (dashboard._activity_card._activity_label, overlay._activity_label):
        assert label.text() == literal_name
        assert label.textFormat() == Qt.TextFormat.PlainText
    assert dashboard._activity_card._objective_label.text() == literal_objective
    assert dashboard._activity_card._objective_label.textFormat() == Qt.TextFormat.PlainText
    recent = dashboard._recent_list.itemAt(0).widget()
    assert literal_name in recent.text()
    assert recent.textFormat() == Qt.TextFormat.PlainText
    assert overlay._state_badge.text() == "MISSION ACTIVE"


def test_native_name_category_completion_and_capture_clear(displays):
    app, dashboard, overlay, _qt = displays
    capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_type=ActivityType.VIP_WORK, activity_identity_status="known_name",
        mission=MissionReading(mission_name="Sightseer", identity_status="known_name"),
    )
    app.last_capture = capture
    for status, expected in (("known_name", "Headhunter"), ("type_only", "VIP Work")):
        capture.activity_identity_status = status
        refresh(displays)
        assert dashboard._activity_card._activity_label.text() == expected
        assert overlay._activity_label.text() == expected
        for state in (AppState.PAUSED, AppState.STOPPING, AppState.STOPPED, AppState.RUNNING):
            app.state = state
            refresh(displays)
            stopped = state in (AppState.STOPPING, AppState.STOPPED)
            assert dashboard._activity_card._activity_label.text() == ("Mission Active" if stopped else expected)
            assert overlay._activity_label.text() == ("MISSION ACTIVE" if stopped else expected)
            assert overlay._state_badge.text() == "MISSION ACTIVE"
    capture.activity_identity_status = "known_name"
    app.game_state = capture.game_state = GameState.MISSION_COMPLETE
    refresh(displays)
    assert dashboard._activity_card._activity_label.text() == "Mission Complete"
    assert overlay._activity_label.text() == "MISSION COMPLETE"
    app.last_capture = None
    refresh(displays)
    assert dashboard._activity_card._activity_label.text() == "Idle"
    assert dashboard._activity_card._objective_label.text() == ""
    assert overlay._activity_label.text() == "MISSION COMPLETE"


def test_timer_visibility_and_size_controls_keep_existing_behavior(displays):
    app, dashboard, overlay, _qt = displays
    app.last_capture = CaptureResult(
        game_state=app.game_state, activity_name="Headhunter",
        activity_identity_status="known_name",
        timer=TimerReading(minutes=5, seconds=30, total_seconds=330),
    )
    for mode in (OverlaySize.NORMAL, OverlaySize.COMPACT, OverlaySize.EXPANDED):
        overlay.set_size_mode(mode)
        refresh(displays)
        assert dashboard._activity_card._timer_label.text() == "5:30"
        assert overlay._timer_label.text() == "Timer: 5:30"
        assert overlay._timer_label.isVisible() is (mode != OverlaySize.COMPACT)
        assert overlay._state_badge.isVisible() is (mode != OverlaySize.COMPACT)
        assert overlay._activity_label.isVisible() is (mode != OverlaySize.COMPACT)
    app.last_capture.timer = None
    refresh(displays)
    assert dashboard._activity_card._timer_label.text() == ""
    assert overlay._timer_label.text() == ""
    assert overlay._timer_label.isHidden()
