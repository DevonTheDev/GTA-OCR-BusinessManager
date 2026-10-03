"""Opt-in real Qt goal controls; synthetic app state, no capture or user data."""

import os
from types import SimpleNamespace

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native Qt goal tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PyQt6 import sip

from src.tracking.goals import GoalTracker, GoalType
from src.ui.widgets.goal_widget import GoalProgressWidget, GoalSetterDialog


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def widgets(qt):
    opened = []
    yield opened
    for widget in reversed(opened):
        if not sip.isdeleted(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class SyntheticApp:
    """Small app boundary for testing real widgets and dialog event loops."""

    def __init__(self):
        self.goal_tracker = GoalTracker()
        self.goal_tracker.storage_error = None
        self.goal_tracker.needs_save_retry = False
        self.earnings = 0
        self.activities = 0
        self.minutes = 0
        self.set_calls = []
        self.clear_calls = 0
        self.retry_calls = 0
        self.current_money = None
        self.session_earnings = 0
        self.session_stats = None
        self.game_state = SimpleNamespace(name="IDLE")
        self.last_capture = None
        self.recommendations = []

    def refresh_session_goal(self):
        self.goal_tracker.update_earnings(self.earnings)
        self.goal_tracker.update_activities(self.activities)
        self.goal_tracker.update_time(self.minutes)

    def set_session_goal(self, goal_type, target, name=""):
        self.set_calls.append((goal_type, target, name))
        self.goal_tracker.set_goal(goal_type, target, name)
        self.refresh_session_goal()

    def clear_session_goal(self):
        self.clear_calls += 1
        self.goal_tracker.clear_goal()

    def retry_session_goal_save(self):
        self.retry_calls += 1
        self.goal_tracker.storage_error = None
        self.goal_tracker.needs_save_retry = False
        return True


def card(app, widgets, qt):
    from src.ui.widgets.session_goals_panel import SessionGoalsPanel
    result = SessionGoalsPanel(app)
    widgets.append(result)
    result.resize(720, 300)
    result.show()
    qt.processEvents()
    return result


def button(widget, name):
    result = widget.findChild(QtWidgets.QPushButton, name)
    assert result is not None, f"Missing accessible control: {name}"
    return result


def label(widget, name):
    result = widget.findChild(QtWidgets.QLabel, name)
    assert result is not None, f"Missing accessible label: {name}"
    return result


def use_dialog(widget, interact):
    """Drive the actual modal dialog and propagate event-loop assertion failures."""
    errors = []

    def act():
        dialog = QtWidgets.QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, GoalSetterDialog)
            interact(dialog)
        except BaseException as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()

    QTimer.singleShot(0, act)
    button(widget, "session_goal_set").click()
    if errors:
        raise errors[0]


def test_controlled_progress_has_literal_names_no_eta_and_restores_incomplete_style(qt, widgets):
    tracker = GoalTracker()
    tracker.set_goal(GoalType.EARNINGS, 100, "<b>Literal target</b>")
    tracker.update_earnings(100)
    widget = GoalProgressWidget(tracker, show_eta=False, auto_refresh=False)
    widgets.append(widget)
    widget.show()
    qt.processEvents()
    assert widget._goal_name.textFormat() == Qt.TextFormat.PlainText
    assert widget._goal_name.text() == "<b>Literal target</b>"
    assert widget._remaining_label.text() == "Complete!"
    assert "#FFD700" in widget._progress_bar.styleSheet()
    assert not widget.findChildren(QTimer)
    tracker.set_goal(GoalType.EARNINGS, 100)
    tracker.update_earnings(20)
    widget._update_display()
    assert widget._progress_bar.value() == 20
    assert "#FFD700" not in widget._progress_bar.styleSheet()
    assert "#4CAF50" in widget._progress_label.styleSheet()
    assert widget._eta_label.isHidden()


def test_standalone_progress_timer_is_owned(qt, widgets):
    widget = GoalProgressWidget(GoalTracker())
    widgets.append(widget)
    assert widget._update_timer.parent() is widget
    assert widget._update_timer.isActive()


def test_no_app_controls_are_disabled_without_using_global_tracker(qt, widgets, monkeypatch):
    from src.ui.widgets import goal_widget
    monkeypatch.setattr(goal_widget, "get_goal_tracker", lambda: pytest.fail("global tracker used"))
    widget = card(None, widgets, qt)
    assert not button(widget, "session_goal_set").isEnabled()
    assert not button(widget, "session_goal_clear").isEnabled()
    assert "No goal" in label(widget, "session_goal_empty").text()


def test_real_preset_dialog_sets_target_with_existing_session_progress(qt, widgets):
    app = SyntheticApp()
    app.earnings = 125_000
    widget = card(app, widgets, qt)
    use_dialog(widget, lambda dialog: next(
        item for item in dialog.findChildren(QtWidgets.QPushButton)
        if item.text() == "Quick 500K").click())
    assert app.set_calls == [(GoalType.EARNINGS, 500_000, "Quick 500K")]
    assert label(widget, "session_goal_name").text() == "Quick 500K"
    assert label(widget, "session_goal_progress").text() == "25%"
    assert label(widget, "session_goal_remaining").text() == "$375K to go"
    assert button(widget, "session_goal_set").text() == "Change Goal"
    explanation = label(widget, "session_goal_meaning").text()
    assert "positive" in explanation and "spending" in explanation
    policy = label(widget, "session_goal_policy").text()
    assert all(word in policy for word in ("remembered", "Start", "Reset Session", "restart"))
    assert "existing" in policy
    assert not app.goal_tracker._on_goal_complete
    assert len(widget.findChildren(QTimer)) == 1
    assert widget.findChildren(QTimer)[0].parent() is widget


@pytest.mark.parametrize("kind,target,total,meaning", [
    (GoalType.EARNINGS, 2000, 1000, "positive"),
    (GoalType.ACTIVITIES, 8, 4, "failed"),
    (GoalType.TIME, 30, 15, "paused"),
])
def test_real_custom_dialog_changes_each_goal_type(qt, widgets, kind, target, total, meaning):
    app = SyntheticApp()
    app.earnings = app.activities = app.minutes = total
    app.set_session_goal(GoalType.EARNINGS, 1_000_000)
    widget = card(app, widgets, qt)

    def interact(dialog):
        dialog._type_combo.setCurrentIndex(dialog._type_combo.findData(kind))
        dialog._value_spin.setValue(target)
        dialog._custom_btn.click()

    use_dialog(widget, interact)
    assert app.goal_tracker.current_goal.goal_type == kind
    assert app.goal_tracker.current_goal.target_value == target
    assert label(widget, "session_goal_progress").text() == "50%"
    assert meaning in label(widget, "session_goal_meaning").text()


def test_real_dialog_cancel_leaves_goal_unchanged(qt, widgets):
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    widget = card(app, widgets, qt)

    def cancel(dialog):
        box = dialog.findChild(QtWidgets.QDialogButtonBox)
        box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).click()

    use_dialog(widget, cancel)
    assert len(app.set_calls) == 1
    assert app.goal_tracker.current_goal.target_value == 5


def test_save_failure_keeps_goal_visible_and_retry_is_explicit(qt, widgets):
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    app.goal_tracker.storage_error = "Cannot write <target>."
    app.goal_tracker.needs_save_retry = True
    widget = card(app, widgets, qt)
    error = label(widget, "session_goal_storage_error")
    assert not error.isHidden()
    assert error.textFormat() == Qt.TextFormat.PlainText
    assert "active" in error.text() and "not remembered" in error.text()
    assert "Cannot write <target>." in error.text()
    assert not button(widget, "session_goal_retry").isHidden()
    assert not widget._progress_widget.isHidden()
    button(widget, "session_goal_retry").click()
    assert app.retry_calls == 1
    assert error.isHidden()
    assert button(widget, "session_goal_retry").isHidden()


def test_load_error_does_not_offer_destructive_retry(qt, widgets):
    app = SyntheticApp()
    app.goal_tracker.storage_error = "The saved target could not be read."
    widget = card(app, widgets, qt)
    assert not label(widget, "session_goal_storage_error").isHidden()
    assert button(widget, "session_goal_retry").isHidden()
    assert button(widget, "session_goal_clear").isEnabled()


def test_clear_and_overlay_share_controller_and_reset_completion_color(qt, widgets):
    from src.ui.overlay import OverlayWindow
    app = SyntheticApp()
    app.activities = 5
    app.set_session_goal(GoalType.ACTIVITIES, 5, "<i>Literal goal</i>")
    widget = card(app, widgets, qt)
    overlay = OverlayWindow(app)
    widgets.append(overlay)
    overlay.show()
    overlay._update_ui()
    assert overlay._goal_tracker is app.goal_tracker
    assert overlay._goal_name_label.textFormat() == Qt.TextFormat.PlainText
    assert overlay._goal_name_label.text() == "<i>Literal goal</i>"
    assert overlay._goal_percent_label.text() == "100%"
    assert "#4CAF50" in overlay._goal_progress.styleSheet()
    assert overlay._update_timer.parent() is overlay
    app.activities = 1
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    overlay._update_ui()
    assert overlay._goal_percent_label.text() == "20%"
    assert "#9C27B0" in overlay._goal_progress.styleSheet()
    assert "#4CAF50" not in overlay._goal_percent_label.styleSheet()
    app.activities = 2
    overlay._update_goal()
    assert overlay._goal_percent_label.text() == "40%"
    button(widget, "session_goal_clear").click()
    overlay._update_ui()
    assert app.clear_calls == 1
    assert not app.goal_tracker.has_goal
    assert widget._progress_widget.isHidden()
    assert overlay._goal_frame.isHidden()
    assert button(widget, "session_goal_set").text() == "Set Goal"


@pytest.mark.parametrize("mode_name", ["COMPACT", "NORMAL", "EXPANDED"])
def test_overlay_adds_goal_space_and_preserves_size_mode(qt, widgets, mode_name):
    from src.ui.overlay import OverlaySize, OverlayWindow
    app = SyntheticApp()
    overlay = OverlayWindow(app)
    widgets.append(overlay)
    overlay.set_size_mode(OverlaySize[mode_name])
    before = overlay.size()
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    overlay._update_goal()
    assert overlay.width() == before.width()
    assert overlay.height() == before.height() + 35
    app.clear_session_goal()
    overlay._update_goal()
    assert overlay.size() == before


def test_panel_timer_refreshes_progress_and_is_deleted_with_panel(qt, widgets):
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 4)
    widget = card(app, widgets, qt)
    timer = widget._timer
    app.activities = 2
    timer.timeout.emit()
    assert label(widget, "session_goal_progress").text() == "50%"
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(timer)
    assert not app.goal_tracker._on_goal_complete


def test_failed_clear_explains_current_state_and_can_retry(qt, widgets):
    app = SyntheticApp()
    app.goal_tracker.storage_error = "Cannot save."
    app.goal_tracker.needs_save_retry = True
    widget = card(app, widgets, qt)
    assert "cleared" in label(widget, "session_goal_storage_error").text()
    assert "not remembered" in label(widget, "session_goal_storage_error").text()
    button(widget, "session_goal_retry").click()
    assert app.retry_calls == 1
    assert label(widget, "session_goal_storage_error").isHidden()


def test_long_goal_names_do_not_push_overlay_percentage_outside_window(qt, widgets):
    from src.ui.overlay import OverlayWindow
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 5, "A remembered target " * 15)
    overlay = OverlayWindow(app)
    widgets.append(overlay)
    overlay.show()
    overlay._update_ui()
    qt.processEvents()
    percent = overlay._goal_percent_label
    position = percent.mapTo(overlay, QPoint(0, 0))
    assert percent.width() >= percent.fontMetrics().horizontalAdvance(percent.text())
    assert position.x() + percent.width() <= overlay.width()
    assert position.y() + percent.height() <= overlay.height()


def assert_readable_label(overlay, label, *, allow_clipped_name=False):
    """Assert the displayed glyphs have room, not just the outer window size."""
    assert label.isVisible()
    assert label.height() >= label.minimumSizeHint().height()
    assert label.height() >= label.fontMetrics().height()
    if not allow_clipped_name:
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
    else:
        assert label.width() >= label.fontMetrics().horizontalAdvance("Target")
    position = label.mapTo(overlay, QPoint(0, 0))
    assert 0 <= position.x() and 0 <= position.y()
    assert position.x() + label.width() <= overlay.width()
    assert position.y() + label.height() <= overlay.height()


@pytest.mark.parametrize("name", ["Quick 500K", "<b>" + "Long literal target " * 9 + "</b>"])
def test_compact_goal_money_and_rate_are_readable_through_refresh_and_mode_changes(
    qt, widgets, tmp_path, name
):
    from src.tracking.session_goals import SessionGoalController
    from src.ui.overlay import OverlaySize, OverlayWindow
    # The real controller validates the long name accepted by persisted targets.
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.EARNINGS, 500_000, name)
    app = SyntheticApp()
    app.set_session_goal(GoalType.EARNINGS, 500_000, controller.current_goal.display_name)
    app.earnings = 125_000
    app.current_money = 950_000
    app.session_stats = SimpleNamespace(duration_seconds=1800, earnings_per_hour=250_000)
    app.last_capture = SimpleNamespace(timer=SimpleNamespace(has_value=True, formatted="5:00"))
    app.recommendations = [SimpleNamespace(action="Continue the current mission")]
    overlay = OverlayWindow(app)
    widgets.append(overlay)
    bonus = SimpleNamespace(multiplier_text="2X", name="Synthetic bonus")
    overlay.set_bonus_tracker(SimpleNamespace(has_bonuses=True, active_bonuses=[bonus]))
    overlay.show()

    for mode in (OverlaySize.COMPACT, OverlaySize.NORMAL, OverlaySize.EXPANDED, OverlaySize.COMPACT):
        overlay.set_size_mode(mode)
        for _ in range(2):
            overlay._update_ui()
            qt.processEvents()
            for current in (overlay._money_label, overlay._rate_label, overlay._goal_percent_label):
                assert_readable_label(overlay, current)
            assert_readable_label(overlay, overlay._goal_name_label, allow_clipped_name=True)
            assert overlay._goal_name_label.text() == name
            assert overlay._goal_name_label.textFormat() == Qt.TextFormat.PlainText
            for detail in (
                overlay._state_badge, overlay._activity_label, overlay._timer_label,
                overlay._recommendation_label, overlay._bonus_frame,
            ):
                assert detail.isVisible() is (mode is not OverlaySize.COMPACT)
            assert not overlay._cooldown_widget.isVisible()

    app.clear_session_goal()
    overlay._update_ui()
    qt.processEvents()
    assert overlay._size_mode is OverlaySize.COMPACT
    assert overlay._goal_frame.isHidden()
    for current in (overlay._money_label, overlay._rate_label):
        assert_readable_label(overlay, current)
    assert overlay._timer_label.isHidden()
    assert overlay._recommendation_label.isHidden()
    assert overlay._bonus_frame.isHidden()
    app.set_session_goal(GoalType.EARNINGS, 500_000, name)
    overlay._update_goal()
    qt.processEvents()
    assert overlay._size_mode is OverlaySize.COMPACT
    assert_readable_label(overlay, overlay._goal_percent_label)
    assert_readable_label(overlay, overlay._goal_name_label, allow_clipped_name=True)
    # Restoring larger mode after data disappears must not resurrect stale rows.
    app.last_capture = None
    app.recommendations = []
    overlay._update_ui()
    overlay.set_size_mode(OverlaySize.NORMAL)
    qt.processEvents()
    assert overlay._timer_label.isHidden()
    assert overlay._recommendation_label.isHidden()


def test_maximum_accepted_literal_name_keeps_session_goal_controls_reachable(qt, widgets, tmp_path):
    from src.tracking.session_goals import MAX_DISPLAY_NAME_LENGTH, SessionGoalController
    name = ("<b> A literal remembered target </b> " * 10)[:MAX_DISPLAY_NAME_LENGTH]
    controller = SessionGoalController(tmp_path / "target.json")
    controller.set_goal(GoalType.ACTIVITIES, 5, name)
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 5, controller.current_goal.display_name)
    widget = card(app, widgets, qt)
    widget.resize(650, 300)
    qt.processEvents()
    assert widget.width() == 650
    assert label(widget, "session_goal_name").text() == name
    assert label(widget, "session_goal_name").textFormat() == Qt.TextFormat.PlainText
    for control in (button(widget, "session_goal_set"), button(widget, "session_goal_clear")):
        position = control.mapTo(widget, QPoint(0, 0))
        assert control.isVisible()
        assert control.height() >= control.minimumSizeHint().height()
        assert position.x() + control.width() <= widget.width()
        assert position.y() + control.height() <= widget.height()


def test_cooldown_timer_cannot_override_compact_mode_or_restore_empty_content(qt, widgets):
    from src.tracking.cooldowns import CooldownTracker
    from src.ui.overlay import OverlaySize, OverlayWindow
    app = SyntheticApp()
    app.set_session_goal(GoalType.ACTIVITIES, 5)
    overlay = OverlayWindow(app)
    widgets.append(overlay)
    cooldowns = CooldownTracker(data_path=None)
    overlay._cooldown_widget._tracker = cooldowns
    cooldowns.start_cooldown("synthetic", "Synthetic cooldown", 300)
    overlay.show()
    overlay.set_size_mode(OverlaySize.COMPACT)
    overlay._update_ui()
    # Exercise the existing child's actual timer connection after the parent refresh.
    overlay._cooldown_widget._update_timer.timeout.emit()
    qt.processEvents()
    assert not overlay._cooldown_widget.isVisible()
    assert_readable_label(overlay, overlay._money_label)
    assert_readable_label(overlay, overlay._goal_percent_label)

    for mode in (OverlaySize.NORMAL, OverlaySize.EXPANDED):
        overlay.set_size_mode(mode)
        overlay._update_ui()
        overlay._cooldown_widget._update_timer.timeout.emit()
        qt.processEvents()
        assert overlay._cooldown_widget.isVisible()
        assert overlay._cooldown_widget._labels[0].isVisible()
        cooldowns.clear_cooldown("synthetic")
        overlay._cooldown_widget._update_timer.timeout.emit()
        assert overlay._cooldown_widget.isHidden()
        # A later overlay tick must not force the empty child visible again.
        overlay._update_timer.timeout.emit()
        qt.processEvents()
        assert not overlay._cooldown_widget.isVisible()
        cooldowns.start_cooldown("synthetic", "Synthetic cooldown", 300)

    overlay.set_size_mode(OverlaySize.COMPACT)
    overlay._cooldown_widget._update_timer.timeout.emit()
    app.clear_session_goal()
    overlay._update_ui()
    overlay._cooldown_widget._update_timer.timeout.emit()
    qt.processEvents()
    assert not overlay._cooldown_widget.isVisible()
    assert_readable_label(overlay, overlay._money_label)


def test_session_controls_remain_reachable_with_scrollable_statistics(qt, widgets):
    from src.ui.widgets.session_panel import SessionPanel
    widget = SessionPanel()
    widgets.append(widget)
    widget.resize(650, 500)
    widget.show()
    qt.processEvents()
    scroll = widget.findChild(QtWidgets.QScrollArea, "session_statistics_scroll")
    assert scroll is not None
    assert scroll.widgetResizable()
    assert scroll.verticalScrollBar().maximum() > 0
    assert button(widget, "session_goal_set").isVisible()
    assert widget.height() == 500
    assert widget._timer.parent() is widget
