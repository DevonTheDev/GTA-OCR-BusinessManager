"""Explicit controls for the app-owned, current-session goal."""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...constants import UI
from ...tracking.goals import GoalTracker, GoalType
from .goal_widget import GoalProgressWidget, GoalSetterDialog

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class SessionGoalsPanel(QFrame):
    """Render snapshots without registering callbacks on the goal controller."""

    _MEANINGS = {
        GoalType.EARNINGS: (
            "Earnings count positive observed balance changes. Starting cash is excluded; "
            "spending does not subtract progress. Mission results are not counted twice."
        ),
        GoalType.ACTIVITIES: (
            "Activities count every finished activity in this session, including failed activities."
        ),
        GoalType.TIME: (
            "Time counts whole elapsed session minutes, including paused capture time, "
            "and stops when the session stops."
        ),
    }

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._app = app
        # An unbound preview is local and disabled; never consult the global tracker.
        self._tracker = app.goal_tracker if app is not None else GoalTracker(data_path=None)
        self.setObjectName("session_goals_panel")
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        if app is not None:
            self._timer.start(UI.SESSION_UPDATE_INTERVAL_MS)
        self.refresh()

    @staticmethod
    def _label(text: str, name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _setup_ui(self) -> None:
        self.setStyleSheet("QFrame#session_goals_panel { background: #16213e; border-radius: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = self._label("Session Goal", "session_goal_title")
        title.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        self._set_button = QPushButton("Set Goal")
        self._set_button.setObjectName("session_goal_set")
        self._set_button.clicked.connect(self._set_goal)
        header.addWidget(self._set_button)
        self._clear_button = QPushButton("Clear")
        self._clear_button.setObjectName("session_goal_clear")
        self._clear_button.clicked.connect(self._clear_goal)
        header.addWidget(self._clear_button)
        layout.addLayout(header)

        self._empty_label = self._label("No goal set. Choose a preset or a custom target.", "session_goal_empty")
        self._empty_label.setStyleSheet("color: #AAA; font-size: 12px;")
        layout.addWidget(self._empty_label)
        self._progress_widget = GoalProgressWidget(
            self._tracker, self, show_eta=False, auto_refresh=False
        )
        self._progress_widget._title_label.hide()
        self._progress_widget._goal_name.setObjectName("session_goal_name")
        self._progress_widget._progress_bar.setObjectName("session_goal_progress_bar")
        self._progress_widget._progress_label.setObjectName("session_goal_progress")
        self._progress_widget._remaining_label.setObjectName("session_goal_remaining")
        layout.addWidget(self._progress_widget)

        self._meaning_label = self._label("", "session_goal_meaning")
        self._meaning_label.setStyleSheet("color: #DDD; font-size: 11px;")
        layout.addWidget(self._meaning_label)
        policy = self._label(
            "Your target is remembered. Progress resets on Start, Reset Session and app restart. "
            "Set or Change includes this session's existing totals.",
            "session_goal_policy",
        )
        policy.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(policy)

        error_row = QHBoxLayout()
        self._storage_error = self._label("", "session_goal_storage_error")
        self._storage_error.setStyleSheet("color: #FFB74D; font-size: 11px;")
        error_row.addWidget(self._storage_error, 1)
        self._retry_button = QPushButton("Retry Save")
        self._retry_button.setObjectName("session_goal_retry")
        self._retry_button.clicked.connect(self._retry_save)
        error_row.addWidget(self._retry_button)
        layout.addLayout(error_row)

    def refresh(self) -> None:
        """Refresh app totals before reading the controller's defensive snapshot."""
        if self._app is not None:
            self._app.refresh_session_goal()
        goal = self._tracker.current_goal
        self._progress_widget._update_display()
        # The containing panel owns the labeled, app-aware Clear action.
        self._progress_widget._clear_btn.hide()
        self._progress_widget.setVisible(goal is not None)
        self._empty_label.setVisible(goal is None)
        self._meaning_label.setVisible(goal is not None)
        self._meaning_label.setText(self._MEANINGS[goal.goal_type] if goal else "")
        self._set_button.setText("Change Goal" if goal else "Set Goal")
        self._set_button.setEnabled(self._app is not None)

        error = getattr(self._tracker, "storage_error", None)
        retry = getattr(self._tracker, "needs_save_retry", False)
        self._clear_button.setEnabled(self._app is not None and (goal is not None or bool(error)))
        if error and retry:
            state = (
                "This target is active in this app but was not remembered."
                if goal else "The goal is cleared in this app, but that change was not remembered."
            )
            self._storage_error.setText(f"{state} {error}")
        else:
            self._storage_error.setText(str(error or ""))
        self._storage_error.setVisible(bool(error))
        self._retry_button.setVisible(bool(retry))
        self._retry_button.setEnabled(self._app is not None)

    def _set_goal(self) -> None:
        if self._app is None:
            return
        dialog = GoalSetterDialog(self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_goal:
                goal = dialog.selected_goal
                self._app.set_session_goal(goal.goal_type, goal.target_value, goal.display_name)
                self.refresh()
        finally:
            dialog.deleteLater()

    def _clear_goal(self) -> None:
        if self._app is not None:
            self._app.clear_session_goal()
            self.refresh()

    def _retry_save(self) -> None:
        if self._app is not None:
            self._app.retry_session_goal_save()
            self.refresh()
