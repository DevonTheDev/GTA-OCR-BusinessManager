"""Quick stats widget for overlay and dashboard.

Shows at-a-glance information players care about most:
- Current earnings rate
- Next cooldown expiring
- Business status summaries
- Weekly bonus info
- Goal progress
"""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QGridLayout,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from ...utils.logging import get_logger
from ...utils.helpers import format_money_short

if TYPE_CHECKING:
    from ...app import GTABusinessManager

logger = get_logger("ui.quick_stats")


class MiniStatWidget(QFrame):
    """A compact stat display widget."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 40);
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setStyleSheet("color: #888; font-size: 9px;")
        layout.addWidget(self._label)

        self._value = QLabel("--")
        self._value.setStyleSheet("color: white; font-size: 12px; font-weight: bold;")
        layout.addWidget(self._value)

    def set_value(self, value: str, color: str = "white") -> None:
        """Update the displayed value."""
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")


class CooldownMiniWidget(QFrame):
    """Compact widget showing next cooldown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 40);
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._icon = QLabel("CD")
        self._icon.setStyleSheet("color: #FF9800; font-size: 10px; font-weight: bold;")
        layout.addWidget(self._icon)

        self._name = QLabel("--")
        self._name.setStyleSheet("color: white; font-size: 11px;")
        layout.addWidget(self._name, stretch=1)

        self._time = QLabel("")
        self._time.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
        layout.addWidget(self._time)

    def set_cooldown(self, name: str, remaining: str, is_ready: bool = False) -> None:
        """Update the displayed cooldown."""
        self._name.setText(name)
        if is_ready:
            self._time.setText("READY")
            self._time.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
            self._icon.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        else:
            self._time.setText(remaining)
            self._time.setStyleSheet("color: #FF9800; font-size: 11px; font-weight: bold;")
            self._icon.setStyleSheet("color: #FF9800; font-size: 10px; font-weight: bold;")

    def set_empty(self) -> None:
        """Show no active cooldowns."""
        self._name.setText("No active cooldowns")
        self._time.setText("")
        self._icon.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")


class BonusBadge(QFrame):
    """Widget showing active weekly bonus."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 215, 0, 30);
                border: 1px solid rgba(255, 215, 0, 100);
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        self._multiplier = QLabel("2X")
        self._multiplier.setStyleSheet("color: #FFD700; font-size: 12px; font-weight: bold;")
        layout.addWidget(self._multiplier)

        self._name = QLabel("--")
        self._name.setStyleSheet("color: #FFD700; font-size: 11px;")
        layout.addWidget(self._name, stretch=1)

    def set_bonus(self, name: str, multiplier: str = "2X") -> None:
        """Update the displayed bonus."""
        self._multiplier.setText(multiplier)
        self._name.setText(name)
        self.show()

    def clear(self) -> None:
        """Hide the bonus display."""
        self.hide()


class GoalProgressMini(QFrame):
    """Compact goal progress widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 40);
                border-radius: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(4)

        self._icon = QLabel("GOAL")
        self._icon.setStyleSheet("color: #9C27B0; font-size: 9px; font-weight: bold;")
        header.addWidget(self._icon)

        self._name = QLabel("--")
        self._name.setStyleSheet("color: white; font-size: 11px;")
        header.addWidget(self._name, stretch=1)

        self._percent = QLabel("")
        self._percent.setStyleSheet("color: #9C27B0; font-size: 11px; font-weight: bold;")
        header.addWidget(self._percent)

        layout.addLayout(header)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 20);
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #9C27B0;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self._progress)

    def set_goal(self, name: str, percent: int, remaining: str = "") -> None:
        """Update goal display."""
        self._name.setText(name)
        self._percent.setText(f"{percent}%")
        self._progress.setValue(percent)

        if percent >= 100:
            self._percent.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold;")
            self._progress.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 20);
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background-color: #4CAF50;
                    border-radius: 3px;
                }
            """)
        self.show()

    def clear(self) -> None:
        """Hide goal display."""
        self.hide()


class BusinessMiniStatus(QFrame):
    """Compact business status display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(0, 0, 0, 40);
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._name = QLabel("--")
        self._name.setStyleSheet("color: white; font-size: 11px;")
        layout.addWidget(self._name)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._status, stretch=1)

        self._time = QLabel("")
        self._time.setStyleSheet("color: #4CAF50; font-size: 10px;")
        layout.addWidget(self._time)

    def set_business(
        self,
        name: str,
        stock_pct: int,
        time_to_full: str = "",
        needs_attention: bool = False
    ) -> None:
        """Update business display."""
        self._name.setText(name)
        self._status.setText(f"{stock_pct}%")

        if stock_pct >= 100:
            self._time.setText("FULL")
            self._time.setStyleSheet("color: #F44336; font-size: 10px; font-weight: bold;")
        elif needs_attention:
            self._time.setText("Low supplies!")
            self._time.setStyleSheet("color: #FF9800; font-size: 10px;")
        else:
            self._time.setText(time_to_full)
            self._time.setStyleSheet("color: #888; font-size: 10px;")


class QuickStatsWidget(QWidget):
    """Widget showing quick at-a-glance stats."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Row 1: Key stats
        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)

        self._rate_stat = MiniStatWidget("$/Hour")
        stats_row.addWidget(self._rate_stat)

        self._activities_stat = MiniStatWidget("Activities")
        stats_row.addWidget(self._activities_stat)

        self._time_stat = MiniStatWidget("Session")
        stats_row.addWidget(self._time_stat)

        layout.addLayout(stats_row)

        # Row 2: Cooldown tracker
        self._cooldown_widget = CooldownMiniWidget()
        layout.addWidget(self._cooldown_widget)

        # Row 3: Bonus badge (optional, hidden by default)
        self._bonus_badge = BonusBadge()
        self._bonus_badge.hide()
        layout.addWidget(self._bonus_badge)

        # Row 4: Goal progress (optional, hidden by default)
        self._goal_widget = GoalProgressMini()
        self._goal_widget.hide()
        layout.addWidget(self._goal_widget)

    def _setup_timer(self) -> None:
        """Set up update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(1000)

    def _update_display(self) -> None:
        """Update all displays."""
        if not self._app:
            return

        # Update earnings rate
        stats = self._app.session_stats
        if stats and stats.duration_seconds > 60:
            rate = stats.earnings_per_hour
            self._rate_stat.set_value(format_money_short(rate), "#4CAF50")
        else:
            self._rate_stat.set_value("--")

        # Update activities count
        activities = self._app.recent_activities
        completed = len([a for a in activities if a.success])
        self._activities_stat.set_value(str(completed))

        # Update session time
        if stats:
            hours = stats.duration_seconds // 3600
            mins = (stats.duration_seconds % 3600) // 60
            if hours > 0:
                self._time_stat.set_value(f"{hours}h {mins}m")
            else:
                self._time_stat.set_value(f"{mins}m")

        # Update cooldown
        self._update_cooldown()

    def _update_cooldown(self) -> None:
        """Update cooldown display."""
        if not self._app or not self._app.cooldown_tracker:
            self._cooldown_widget.set_empty()
            return

        active = self._app.cooldown_tracker.get_active_cooldowns()
        if active:
            next_cd = active[0]  # Already sorted by remaining time
            if next_cd.is_expired:
                self._cooldown_widget.set_cooldown(next_cd.display_name, "", is_ready=True)
            else:
                self._cooldown_widget.set_cooldown(
                    next_cd.display_name,
                    next_cd.remaining_formatted,
                    is_ready=False
                )
        else:
            self._cooldown_widget.set_empty()

    def set_bonus(self, name: str, multiplier: str = "2X") -> None:
        """Show an active bonus."""
        self._bonus_badge.set_bonus(name, multiplier)

    def clear_bonus(self) -> None:
        """Hide bonus display."""
        self._bonus_badge.clear()

    def set_goal(self, name: str, percent: int, remaining: str = "") -> None:
        """Show goal progress."""
        self._goal_widget.set_goal(name, percent, remaining)

    def clear_goal(self) -> None:
        """Hide goal display."""
        self._goal_widget.clear()


class ExpandedQuickStats(QWidget):
    """Expanded version of quick stats for dashboard."""

    def __init__(self, app: Optional["GTABusinessManager"] = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        """Set up the expanded UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Section: Session Stats
        session_frame = self._create_section("Session Overview")
        session_layout = QGridLayout(session_frame)
        session_layout.setSpacing(12)

        self._money_stat = MiniStatWidget("Current Balance")
        session_layout.addWidget(self._money_stat, 0, 0)

        self._earnings_stat = MiniStatWidget("Session Earnings")
        session_layout.addWidget(self._earnings_stat, 0, 1)

        self._rate_stat = MiniStatWidget("Earnings Rate")
        session_layout.addWidget(self._rate_stat, 0, 2)

        self._time_stat = MiniStatWidget("Session Time")
        session_layout.addWidget(self._time_stat, 1, 0)

        self._activities_stat = MiniStatWidget("Completed")
        session_layout.addWidget(self._activities_stat, 1, 1)

        self._best_stat = MiniStatWidget("Best Activity")
        session_layout.addWidget(self._best_stat, 1, 2)

        layout.addWidget(session_frame)

        # Section: Active Cooldowns
        cooldown_frame = self._create_section("Cooldowns")
        cooldown_layout = QVBoxLayout(cooldown_frame)
        cooldown_layout.setSpacing(4)

        self._cooldown_widgets = []
        for _ in range(4):
            cw = CooldownMiniWidget()
            self._cooldown_widgets.append(cw)
            cooldown_layout.addWidget(cw)

        layout.addWidget(cooldown_frame)

        # Section: Businesses (if tracked)
        self._business_frame = self._create_section("Business Status")
        self._business_layout = QVBoxLayout(self._business_frame)
        self._business_layout.setSpacing(4)

        self._business_widgets = []
        for _ in range(5):
            bw = BusinessMiniStatus()
            bw.hide()
            self._business_widgets.append(bw)
            self._business_layout.addWidget(bw)

        layout.addWidget(self._business_frame)
        layout.addStretch()

    def _create_section(self, title: str) -> QFrame:
        """Create a section frame with title."""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        # Add title (we'll use the frame's layout for content)
        return frame

    def _setup_timer(self) -> None:
        """Set up update timer."""
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_display)
        self._timer.start(1000)

    def _update_display(self) -> None:
        """Update all displays."""
        if not self._app:
            return

        # Money
        money = self._app.current_money
        if money is not None:
            self._money_stat.set_value(f"${money:,}", "#4CAF50")

        # Earnings
        earnings = self._app.session_earnings
        self._earnings_stat.set_value(f"+${earnings:,}", "#FFD700")

        # Rate and time
        stats = self._app.session_stats
        if stats:
            if stats.duration_seconds > 60:
                rate = stats.earnings_per_hour
                self._rate_stat.set_value(f"{format_money_short(rate)}/hr")

            hours = stats.duration_seconds // 3600
            mins = (stats.duration_seconds % 3600) // 60
            if hours > 0:
                self._time_stat.set_value(f"{hours}h {mins}m")
            else:
                self._time_stat.set_value(f"{mins}m")

        # Activities
        activities = self._app.recent_activities
        completed = len([a for a in activities if a.success])
        failed = len([a for a in activities if not a.success])
        self._activities_stat.set_value(f"{completed} / {completed + failed}")

        # Best activity
        metrics = self._app.efficiency_metrics
        if metrics and metrics.best_activity_type:
            best_name = metrics.best_activity_type.replace("_", " ").title()
            self._best_stat.set_value(best_name[:12])

        # Cooldowns
        self._update_cooldowns()

        # Businesses
        self._update_businesses()

    def _update_cooldowns(self) -> None:
        """Update cooldown displays."""
        if not self._app or not self._app.cooldown_tracker:
            for cw in self._cooldown_widgets:
                cw.hide()
            return

        active = self._app.cooldown_tracker.get_active_cooldowns()

        for i, cw in enumerate(self._cooldown_widgets):
            if i < len(active):
                cd = active[i]
                cw.set_cooldown(
                    cd.display_name,
                    cd.remaining_formatted,
                    is_ready=cd.is_expired
                )
                cw.show()
            else:
                cw.hide()

        # If no cooldowns, show message
        if not active:
            self._cooldown_widgets[0].set_empty()
            self._cooldown_widgets[0].show()

    def _update_businesses(self) -> None:
        """Update business status displays."""
        if not self._app:
            return

        # Get business states from app data
        data = self._app.data
        businesses = data.business_states

        if not businesses:
            self._business_frame.hide()
            return

        self._business_frame.show()

        # Import here to avoid circular import
        from ...game.businesses import BUSINESSES, estimate_time_to_full_formatted

        sorted_businesses = sorted(
            businesses.items(),
            key=lambda x: x[1].get("stock", 0),
            reverse=True
        )

        for i, bw in enumerate(self._business_widgets):
            if i < len(sorted_businesses):
                bid, state = sorted_businesses[i]
                business = BUSINESSES.get(bid)
                if business:
                    stock = state.get("stock", 0)
                    supply = state.get("supply", 0)
                    time_str = estimate_time_to_full_formatted(business, stock)

                    bw.set_business(
                        business.name[:15],
                        stock,
                        time_str,
                        needs_attention=supply <= 20
                    )
                    bw.show()
            else:
                bw.hide()
