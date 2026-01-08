"""Dashboard widget for GTA Business Manager."""

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGridLayout,
    QFrame,
    QProgressBar,
)
from PyQt6.QtCore import QTimer, Qt

from ...utils.helpers import format_money, format_money_short, format_time

if TYPE_CHECKING:
    from ...app import GTABusinessManager


class StatCard(QFrame):
    """A card widget displaying a statistic."""

    def __init__(self, title: str, value: str = "--", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 16px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(self._title_label)

        self._value_label = QLabel(value)
        self._value_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        layout.addWidget(self._value_label)

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self._subtitle_label)

    def set_value(self, value: str, color: str = "white") -> None:
        """Update the value."""
        self._value_label.setText(value)
        self._value_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: bold;")

    def set_subtitle(self, text: str) -> None:
        """Update the subtitle."""
        self._subtitle_label.setText(text)


class ActivityCard(QFrame):
    """Card showing current activity status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 16px;
            }
        """)

        layout = QVBoxLayout(self)

        header = QLabel("Current Activity")
        header.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(header)

        self._activity_label = QLabel("Idle")
        self._activity_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        layout.addWidget(self._activity_label)

        self._timer_label = QLabel("")
        self._timer_label.setStyleSheet("color: #4CAF50; font-size: 14px;")
        layout.addWidget(self._timer_label)

        self._objective_label = QLabel("")
        self._objective_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._objective_label.setWordWrap(True)
        layout.addWidget(self._objective_label)

    def set_activity(self, name: str, timer: str = "", objective: str = "") -> None:
        """Update activity display."""
        self._activity_label.setText(name or "Idle")
        self._timer_label.setText(timer)
        self._objective_label.setText(objective)


class RecommendationCard(QFrame):
    """Card showing top recommendation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                border-left: 4px solid #FFD700;
                padding: 16px;
            }
        """)

        layout = QVBoxLayout(self)

        header = QLabel("Next Recommended Action")
        header.setStyleSheet("color: #FFD700; font-size: 11px;")
        layout.addWidget(header)

        self._action_label = QLabel("No recommendations yet")
        self._action_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        self._action_label.setWordWrap(True)
        layout.addWidget(self._action_label)

        self._reason_label = QLabel("")
        self._reason_label.setStyleSheet("color: #AAA; font-size: 12px;")
        self._reason_label.setWordWrap(True)
        layout.addWidget(self._reason_label)

        self._value_label = QLabel("")
        self._value_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        layout.addWidget(self._value_label)

    def set_recommendation(self, action: str, reason: str = "", value: int = 0) -> None:
        """Update recommendation display."""
        self._action_label.setText(action or "No recommendations yet")
        self._reason_label.setText(reason)
        if value > 0:
            self._value_label.setText(f"Est. value: {format_money_short(value)}")
        else:
            self._value_label.setText("")


class DashboardWidget(QWidget):
    """Main dashboard showing overview of all data."""

    def __init__(self, app: "GTABusinessManager" = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_update_timer()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Top row - key stats
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        self._money_card = StatCard("Current Balance")
        stats_layout.addWidget(self._money_card)

        self._session_card = StatCard("Session Earnings")
        stats_layout.addWidget(self._session_card)

        self._activities_card = StatCard("Activities Completed")
        stats_layout.addWidget(self._activities_card)

        self._time_card = StatCard("Session Duration")
        stats_layout.addWidget(self._time_card)

        layout.addLayout(stats_layout)

        # Middle row - activity and recommendation
        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(16)

        self._activity_card = ActivityCard()
        middle_layout.addWidget(self._activity_card, stretch=1)

        self._recommendation_card = RecommendationCard()
        middle_layout.addWidget(self._recommendation_card, stretch=1)

        layout.addLayout(middle_layout)

        # Bottom section - recent activity list
        recent_frame = QFrame()
        recent_frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 16px;
            }
        """)
        recent_layout = QVBoxLayout(recent_frame)

        recent_header = QLabel("Recent Activity")
        recent_header.setStyleSheet("color: #AAA; font-size: 11px; margin-bottom: 8px;")
        recent_layout.addWidget(recent_header)

        self._recent_list = QVBoxLayout()
        self._recent_list.setSpacing(4)
        recent_layout.addLayout(self._recent_list)

        # Placeholder items
        for i in range(5):
            item = QLabel("--")
            item.setStyleSheet("color: #666; font-size: 12px; padding: 4px 0;")
            self._recent_list.addWidget(item)

        recent_layout.addStretch()
        layout.addWidget(recent_frame)

    def _setup_update_timer(self):
        """Setup update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(1000)

    def _update_display(self):
        """Update all dashboard displays."""
        if not self._app:
            return

        # Update money card
        money = self._app.current_money
        if money is not None:
            self._money_card.set_value(format_money(money), "#4CAF50")
        else:
            self._money_card.set_value("--")

        # Update session card
        earnings = self._app.session_earnings
        self._session_card.set_value(f"+{format_money(earnings)}", "#FFD700")

        # Update activities card
        activities = self._app.recent_activities
        self._activities_card.set_value(str(len(activities)))

        # Update time card
        stats = self._app.session_stats
        if stats:
            self._time_card.set_value(format_time(stats.duration_seconds))
            rate = stats.earnings_per_hour
            if rate > 0:
                self._time_card.set_subtitle(f"{format_money_short(rate)}/hr")

        # Update activity card
        last_capture = self._app.last_capture
        if last_capture:
            state = last_capture.game_state.name.replace("_", " ").title()
            timer = ""
            if last_capture.timer and last_capture.timer.has_value:
                timer = last_capture.timer.formatted

            self._activity_card.set_activity(
                state,
                timer=timer,
                objective=last_capture.objective_text
            )

        # Update recommendation
        recommendations = self._app.recommendations
        if recommendations:
            rec = recommendations[0]
            self._recommendation_card.set_recommendation(
                rec.action,
                rec.reason,
                rec.estimated_value
            )
        else:
            self._recommendation_card.set_recommendation("No recommendations yet")

        # Update recent activity list
        self._update_recent_list(activities)

    def _update_recent_list(self, activities):
        """Update the recent activities list."""
        # Get the label widgets
        for i in range(self._recent_list.count()):
            label = self._recent_list.itemAt(i).widget()
            if isinstance(label, QLabel):
                if i < len(activities):
                    activity = activities[i]
                    status = "Passed" if activity.success else "Failed"
                    color = "#4CAF50" if activity.success else "#F44336"
                    text = f"{activity.name or activity.activity_type.name} - {status}"
                    if activity.earnings > 0:
                        text += f" (+{format_money_short(activity.earnings)})"
                    label.setText(text)
                    label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 4px 0;")
                else:
                    label.setText("--")
                    label.setStyleSheet("color: #666; font-size: 12px; padding: 4px 0;")
