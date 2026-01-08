"""Goal tracking widget for displaying session goals."""

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QProgressBar, QPushButton, QComboBox, QSpinBox,
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont

from ...tracking.goals import GoalTracker, GoalType, SessionGoal, PRESET_GOALS, get_goal_tracker
from ...utils.logging import get_logger


logger = get_logger("ui.goal_widget")


class GoalProgressWidget(QFrame):
    """Widget displaying current goal progress."""

    goal_cleared = pyqtSignal()

    def __init__(
        self,
        tracker: Optional[GoalTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize goal progress widget.

        Args:
            tracker: Goal tracker to use (uses global if None)
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_goal_tracker()

        self._setup_ui()
        self._setup_update_timer()
        self._update_display()

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 60, 180);
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header with goal name
        header_layout = QHBoxLayout()

        self._title_label = QLabel("Session Goal")
        self._title_label.setStyleSheet("color: #AAA; font-size: 10px;")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        self._clear_btn = QPushButton("X")
        self._clear_btn.setFixedSize(20, 20)
        self._clear_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 100, 100, 100);
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 100, 100, 180);
            }
        """)
        self._clear_btn.clicked.connect(self._on_clear)
        header_layout.addWidget(self._clear_btn)

        layout.addLayout(header_layout)

        # Goal name
        self._goal_name = QLabel("No Goal Set")
        self._goal_name.setStyleSheet("color: white; font-size: 13px; font-weight: bold;")
        layout.addWidget(self._goal_name)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(12)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 30);
                border: none;
                border-radius: 6px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #4CAF50, stop: 1 #8BC34A
                );
                border-radius: 6px;
            }
        """)
        layout.addWidget(self._progress_bar)

        # Progress text row
        progress_layout = QHBoxLayout()

        self._progress_label = QLabel("0%")
        self._progress_label.setStyleSheet("color: #4CAF50; font-size: 14px; font-weight: bold;")
        progress_layout.addWidget(self._progress_label)

        progress_layout.addStretch()

        self._remaining_label = QLabel("")
        self._remaining_label.setStyleSheet("color: #888; font-size: 11px;")
        progress_layout.addWidget(self._remaining_label)

        layout.addLayout(progress_layout)

        # ETA row (hidden when no goal)
        self._eta_label = QLabel("")
        self._eta_label.setStyleSheet("color: #666; font-size: 10px; font-style: italic;")
        layout.addWidget(self._eta_label)

        # No goal state
        self._no_goal_label = QLabel("Click to set a goal")
        self._no_goal_label.setStyleSheet("color: #666; font-size: 11px; font-style: italic;")
        self._no_goal_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._no_goal_label)

    def _setup_update_timer(self) -> None:
        """Setup timer for updating display."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.start(1000)  # Update every second

    def _update_display(self) -> None:
        """Update the display with current goal state."""
        goal = self._tracker.current_goal

        if goal is None:
            self._show_no_goal()
            return

        self._show_goal(goal)

    def _show_no_goal(self) -> None:
        """Show no goal state."""
        self._goal_name.hide()
        self._progress_bar.hide()
        self._progress_label.hide()
        self._remaining_label.hide()
        self._eta_label.hide()
        self._clear_btn.hide()
        self._no_goal_label.show()

    def _show_goal(self, goal: SessionGoal) -> None:
        """Show goal progress."""
        self._no_goal_label.hide()
        self._goal_name.show()
        self._progress_bar.show()
        self._progress_label.show()
        self._remaining_label.show()
        self._clear_btn.show()

        # Update values
        self._goal_name.setText(goal.display_name)
        self._progress_bar.setValue(goal.progress_percent)
        self._progress_label.setText(f"{goal.progress_percent}%")
        self._remaining_label.setText(goal.remaining_formatted)

        # Update colors based on progress
        if goal.is_complete:
            self._progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 30);
                    border: none;
                    border-radius: 6px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 0,
                        stop: 0 #FFD700, stop: 1 #FFC107
                    );
                    border-radius: 6px;
                }
            """)
            self._progress_label.setStyleSheet("color: #FFD700; font-size: 14px; font-weight: bold;")
            self._eta_label.setText("Goal Complete!")
            self._eta_label.show()
        else:
            # Show ETA if available
            eta = goal.estimated_completion_time
            if eta:
                hours = int(eta.total_seconds() // 3600)
                minutes = int((eta.total_seconds() % 3600) // 60)
                if hours > 0:
                    self._eta_label.setText(f"ETA: {hours}h {minutes}m at current rate")
                else:
                    self._eta_label.setText(f"ETA: {minutes}m at current rate")
                self._eta_label.show()
            else:
                self._eta_label.hide()

    def _on_clear(self) -> None:
        """Handle clear button click."""
        self._tracker.clear_goal()
        self._update_display()
        self.goal_cleared.emit()

    def update_progress(self, earnings: int = 0, activities: int = 0, minutes: int = 0) -> bool:
        """Update goal progress.

        Args:
            earnings: Current session earnings
            activities: Current activity count
            minutes: Current session minutes

        Returns:
            True if goal was just completed
        """
        completed = False

        if self._tracker.current_goal:
            goal_type = self._tracker.current_goal.goal_type
            if goal_type == GoalType.EARNINGS:
                completed = self._tracker.update_earnings(earnings)
            elif goal_type == GoalType.ACTIVITIES:
                completed = self._tracker.update_activities(activities)
            elif goal_type == GoalType.TIME:
                completed = self._tracker.update_time(minutes)

        self._update_display()
        return completed


class GoalSetterDialog(QDialog):
    """Dialog for setting a new session goal."""

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize goal setter dialog."""
        super().__init__(parent)
        self.setWindowTitle("Set Session Goal")
        self.setModal(True)
        self.setMinimumWidth(350)

        self._selected_goal: Optional[SessionGoal] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)

        # Presets section
        presets_group = QGroupBox("Quick Presets")
        presets_layout = QVBoxLayout(presets_group)

        # Earnings presets
        earnings_layout = QHBoxLayout()
        earnings_layout.addWidget(QLabel("Earnings:"))
        for name, goal in PRESET_GOALS.items():
            if goal.goal_type == GoalType.EARNINGS:
                btn = QPushButton(goal.display_name.replace("Earn ", "").replace(" (", "\n("))
                btn.setFixedWidth(90)
                btn.clicked.connect(lambda checked, n=name: self._select_preset(n))
                earnings_layout.addWidget(btn)
        presets_layout.addLayout(earnings_layout)

        # Activities presets
        activities_layout = QHBoxLayout()
        activities_layout.addWidget(QLabel("Activities:"))
        for name, goal in PRESET_GOALS.items():
            if goal.goal_type == GoalType.ACTIVITIES:
                btn = QPushButton(goal.display_name)
                btn.setFixedWidth(90)
                btn.clicked.connect(lambda checked, n=name: self._select_preset(n))
                activities_layout.addWidget(btn)
        presets_layout.addLayout(activities_layout)

        # Time presets
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Time:"))
        for name, goal in PRESET_GOALS.items():
            if goal.goal_type == GoalType.TIME:
                btn = QPushButton(goal.display_name.replace(" Session", ""))
                btn.setFixedWidth(90)
                btn.clicked.connect(lambda checked, n=name: self._select_preset(n))
                time_layout.addWidget(btn)
        presets_layout.addLayout(time_layout)

        layout.addWidget(presets_group)

        # Custom goal section
        custom_group = QGroupBox("Custom Goal")
        custom_layout = QFormLayout(custom_group)

        self._type_combo = QComboBox()
        self._type_combo.addItem("Earnings ($)", GoalType.EARNINGS)
        self._type_combo.addItem("Activities", GoalType.ACTIVITIES)
        self._type_combo.addItem("Time (minutes)", GoalType.TIME)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        custom_layout.addRow("Goal Type:", self._type_combo)

        self._value_spin = QSpinBox()
        self._value_spin.setRange(1, 100_000_000)
        self._value_spin.setValue(1_000_000)
        self._value_spin.setSingleStep(100_000)
        custom_layout.addRow("Target:", self._value_spin)

        self._custom_btn = QPushButton("Set Custom Goal")
        self._custom_btn.clicked.connect(self._set_custom_goal)
        custom_layout.addRow(self._custom_btn)

        layout.addWidget(custom_group)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_type_changed(self, index: int) -> None:
        """Handle goal type change."""
        goal_type = self._type_combo.currentData()

        if goal_type == GoalType.EARNINGS:
            self._value_spin.setRange(1000, 100_000_000)
            self._value_spin.setValue(1_000_000)
            self._value_spin.setSingleStep(100_000)
        elif goal_type == GoalType.ACTIVITIES:
            self._value_spin.setRange(1, 1000)
            self._value_spin.setValue(10)
            self._value_spin.setSingleStep(5)
        elif goal_type == GoalType.TIME:
            self._value_spin.setRange(1, 1440)  # 24 hours
            self._value_spin.setValue(60)
            self._value_spin.setSingleStep(15)

    def _select_preset(self, preset_name: str) -> None:
        """Select a preset goal."""
        preset = PRESET_GOALS.get(preset_name)
        if preset:
            self._selected_goal = SessionGoal(
                preset.goal_type,
                preset.target_value,
                preset.display_name,
            )
            self.accept()

    def _set_custom_goal(self) -> None:
        """Set a custom goal."""
        goal_type = self._type_combo.currentData()
        value = self._value_spin.value()

        self._selected_goal = SessionGoal(goal_type, value)
        self.accept()

    @property
    def selected_goal(self) -> Optional[SessionGoal]:
        """Get the selected goal."""
        return self._selected_goal


class GoalWidget(QWidget):
    """Full goal widget with setter and progress display."""

    goal_completed = pyqtSignal(SessionGoal)

    def __init__(
        self,
        tracker: Optional[GoalTracker] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize goal widget.

        Args:
            tracker: Goal tracker to use
            parent: Parent widget
        """
        super().__init__(parent)
        self._tracker = tracker or get_goal_tracker()

        self._setup_ui()

        # Connect goal complete callback
        self._tracker.on_goal_complete(self._on_goal_complete)

    def _setup_ui(self) -> None:
        """Setup the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Progress widget
        self._progress_widget = GoalProgressWidget(self._tracker)
        self._progress_widget.mousePressEvent = self._on_widget_clicked
        self._progress_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self._progress_widget)

    def _on_widget_clicked(self, event) -> None:
        """Handle click on progress widget to set goal."""
        if not self._tracker.has_goal:
            self._show_goal_dialog()

    def _show_goal_dialog(self) -> None:
        """Show goal setter dialog."""
        dialog = GoalSetterDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_goal:
            goal = dialog.selected_goal
            self._tracker.set_goal(
                goal.goal_type,
                goal.target_value,
                goal.display_name,
            )

    def _on_goal_complete(self, goal: SessionGoal) -> None:
        """Handle goal completion."""
        self.goal_completed.emit(goal)

    def update_progress(self, earnings: int = 0, activities: int = 0, minutes: int = 0) -> bool:
        """Update goal progress.

        Args:
            earnings: Current session earnings
            activities: Current activity count
            minutes: Current session minutes

        Returns:
            True if goal was just completed
        """
        return self._progress_widget.update_progress(earnings, activities, minutes)

    def set_goal(self, goal_type: GoalType, target: int, name: str = "") -> None:
        """Set a new goal programmatically.

        Args:
            goal_type: Type of goal
            target: Target value
            name: Optional display name
        """
        self._tracker.set_goal(goal_type, target, name)
