"""Session statistics panel."""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QGridLayout,
    QPushButton,
    QScrollArea,
)
from PyQt6.QtCore import QTimer, Qt

from ...constants import UI
from ...utils.helpers import format_money, format_money_short, format_time, format_percentage
from .charts import SessionCharts

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class SessionPanel(QWidget):
    """Panel showing current session statistics."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header with reset button
        header_layout = QHBoxLayout()
        header = QLabel("Session Statistics")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        header_layout.addWidget(header)
        header_layout.addStretch()

        reset_btn = QPushButton("Reset Session")
        reset_btn.clicked.connect(self._reset_session)
        header_layout.addWidget(reset_btn)

        layout.addLayout(header_layout)

        # Stats grid
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 20px;
            }
        """)
        stats_grid = QGridLayout(stats_frame)
        stats_grid.setSpacing(20)

        # Row 1: Money stats
        self._add_stat(stats_grid, 0, 0, "Starting Balance", "--", "start_money")
        self._add_stat(stats_grid, 0, 1, "Current Balance", "--", "current_money")
        self._add_stat(stats_grid, 0, 2, "Total Earnings", "--", "earnings")
        self._add_stat(stats_grid, 0, 3, "Earnings/Hour", "--", "rate")

        # Row 2: Activity stats
        self._add_stat(stats_grid, 1, 0, "Session Duration", "--", "duration")
        self._add_stat(stats_grid, 1, 1, "Activities Completed", "--", "activities")
        self._add_stat(stats_grid, 1, 2, "Success Rate", "--", "success_rate")
        self._add_stat(stats_grid, 1, 3, "Avg Per Activity", "--", "avg_earnings")

        # Row 3: Time breakdown
        self._add_stat(stats_grid, 2, 0, "Time in Missions", "--", "mission_time")
        self._add_stat(stats_grid, 2, 1, "Idle Time", "--", "idle_time")
        self._add_stat(stats_grid, 2, 2, "Sells Completed", "--", "sells")
        self._add_stat(stats_grid, 2, 3, "Last Change", "--", "last_change")

        # Row 4: Analytics insights
        self._add_stat(stats_grid, 3, 0, "Best Activity", "--", "best_activity")
        self._add_stat(stats_grid, 3, 1, "Best Rate", "--", "best_rate")
        self._add_stat(stats_grid, 3, 2, "From Missions", "--", "from_missions")
        self._add_stat(stats_grid, 3, 3, "From Sells", "--", "from_sells")

        layout.addWidget(stats_frame)

        # Earnings breakdown
        breakdown_frame = QFrame()
        breakdown_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 20px;
            }
        """)
        breakdown_layout = QVBoxLayout(breakdown_frame)

        breakdown_header = QLabel("Session Timeline")
        breakdown_header.setStyleSheet("color: white; font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        breakdown_layout.addWidget(breakdown_header)

        self._timeline_label = QLabel("No activity recorded yet")
        self._timeline_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._timeline_label.setWordWrap(True)
        breakdown_layout.addWidget(self._timeline_label)

        layout.addWidget(breakdown_frame)

        # Charts section
        self._charts = SessionCharts(app=self._app)
        layout.addWidget(self._charts)

        layout.addStretch()

    def _add_stat(self, grid: QGridLayout, row: int, col: int, label: str, value: str, name: str) -> None:
        """Add a stat display to the grid."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label_widget = QLabel(label)
        label_widget.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(label_widget)

        value_widget = QLabel(value)
        value_widget.setStyleSheet("color: white; font-size: 20px; font-weight: bold;")
        value_widget.setObjectName(f"stat_{name}")
        layout.addWidget(value_widget)

        grid.addWidget(container, row, col)

    def _get_stat_label(self, name: str) -> Optional[QLabel]:
        """Get a stat label by name."""
        return self.findChild(QLabel, f"stat_{name}")

    def _setup_update_timer(self) -> None:
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(UI.SESSION_UPDATE_INTERVAL_MS)

    def _update_display(self) -> None:
        """Update all stat displays."""
        if not self._app:
            return

        stats = self._app.session_stats
        data = self._app.data

        # Money stats
        if data.session_start_money is not None:
            self._get_stat_label("start_money").setText(format_money(data.session_start_money))

        if data.current_money is not None:
            self._get_stat_label("current_money").setText(format_money(data.current_money))

        self._get_stat_label("earnings").setText(f"+{format_money(data.session_earnings)}")
        self._get_stat_label("earnings").setStyleSheet("color: #4CAF50; font-size: 20px; font-weight: bold;")

        # Rate
        if stats and stats.duration_seconds > 60:
            rate = stats.earnings_per_hour
            self._get_stat_label("rate").setText(f"{format_money_short(rate)}/hr")

        # Duration
        if stats:
            self._get_stat_label("duration").setText(format_time(stats.duration_seconds))

        # Activities
        activities = self._app.recent_activities
        total = len(activities)
        self._get_stat_label("activities").setText(str(total))

        # Success rate
        if stats and (stats.missions_passed + stats.missions_failed) > 0:
            rate = stats.mission_success_rate
            self._get_stat_label("success_rate").setText(format_percentage(rate))
            color = "#4CAF50" if rate >= 0.8 else "#FFD700" if rate >= 0.5 else "#F44336"
            self._get_stat_label("success_rate").setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")

        # Average earnings
        if total > 0:
            successful = [a for a in activities if a.success]
            if successful:
                avg = sum(a.earnings for a in successful) // len(successful)
                self._get_stat_label("avg_earnings").setText(format_money_short(avg))

        # Time breakdown
        if stats:
            self._get_stat_label("mission_time").setText(format_time(stats.time_in_missions))
            self._get_stat_label("idle_time").setText(format_time(stats.time_idle))
            self._get_stat_label("sells").setText(str(stats.sells_completed))

        # Last change
        if data.last_money_change != 0:
            sign = "+" if data.last_money_change > 0 else ""
            self._get_stat_label("last_change").setText(f"{sign}{format_money_short(data.last_money_change)}")
            color = "#4CAF50" if data.last_money_change > 0 else "#F44336"
            self._get_stat_label("last_change").setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")

        # Analytics insights
        best_activity = self._app.best_activity_type
        if best_activity:
            # Clean up activity type name for display
            display_name = best_activity.replace("_", " ").title()
            self._get_stat_label("best_activity").setText(display_name)
            self._get_stat_label("best_activity").setStyleSheet(
                "color: #FFD700; font-size: 16px; font-weight: bold;"
            )

        best_rate = self._app.best_activity_rate
        if best_rate > 0:
            self._get_stat_label("best_rate").setText(f"{format_money_short(best_rate)}/hr")
            self._get_stat_label("best_rate").setStyleSheet(
                "color: #4CAF50; font-size: 16px; font-weight: bold;"
            )

        breakdown = self._app.earnings_breakdown
        if breakdown:
            if breakdown.from_missions > 0:
                self._get_stat_label("from_missions").setText(
                    f"+{format_money_short(breakdown.from_missions)}"
                )
                self._get_stat_label("from_missions").setStyleSheet(
                    "color: #4CAF50; font-size: 16px; font-weight: bold;"
                )
            if breakdown.from_sells > 0:
                self._get_stat_label("from_sells").setText(
                    f"+{format_money_short(breakdown.from_sells)}"
                )
                self._get_stat_label("from_sells").setStyleSheet(
                    "color: #4CAF50; font-size: 16px; font-weight: bold;"
                )

    def _reset_session(self) -> None:
        """Reset the session."""
        if self._app:
            self._app.reset_session()

        # Reset charts
        if hasattr(self, "_charts"):
            self._charts.reset()
