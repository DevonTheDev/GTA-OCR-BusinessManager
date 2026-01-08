"""Transparent overlay window for GTA Business Manager."""

from typing import TYPE_CHECKING, Optional
from enum import Enum, auto

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QFont, QMouseEvent

from ..utils.logging import get_logger
from ..utils.helpers import format_money_short, format_time_short, format_time
from .widgets.cooldown_widget import CompactCooldownWidget

if TYPE_CHECKING:
    from ..app import GTABusinessManager


logger = get_logger("ui.overlay")


class OverlaySize(Enum):
    """Overlay size modes."""
    COMPACT = auto()  # Just money and rate
    NORMAL = auto()   # Money, rate, state, recommendation
    EXPANDED = auto() # All info including cooldowns, goal, bonus


class OverlayWindow(QWidget):
    """Transparent overlay window showing key information."""

    def __init__(self, app: "GTABusinessManager", parent=None):
        """Initialize overlay window."""
        super().__init__(parent)
        self._app = app
        self._drag_position: Optional[QPoint] = None
        self._is_locked = False
        self._size_mode = OverlaySize.NORMAL
        self._goal_tracker = None
        self._bonus_tracker = None

        self._setup_window()
        self._setup_ui()
        self._setup_update_timer()

        logger.info("Overlay window initialized")

    def _setup_window(self) -> None:
        """Setup window properties for overlay."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput if self._is_locked else Qt.WindowType.Widget
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._update_size()
        self._position_overlay()

    def _update_size(self) -> None:
        """Update overlay size based on mode."""
        sizes = {
            OverlaySize.COMPACT: (240, 100),
            OverlaySize.NORMAL: (280, 200),
            OverlaySize.EXPANDED: (300, 280),
        }
        width, height = sizes.get(self._size_mode, (280, 200))
        self.setFixedSize(width, height)

    def _position_overlay(self, position: str = "top-right") -> None:
        """Position the overlay on screen."""
        from PyQt6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if not screen:
            return

        geometry = screen.availableGeometry()
        margin = 20

        positions = {
            "top-left": (margin, margin),
            "top-right": (geometry.width() - self.width() - margin, margin),
            "bottom-left": (margin, geometry.height() - self.height() - margin),
            "bottom-right": (geometry.width() - self.width() - margin, geometry.height() - self.height() - margin),
        }

        x, y = positions.get(position, positions["top-right"])
        self.move(x, y)

    def _setup_ui(self) -> None:
        """Setup the overlay UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main container with semi-transparent background
        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background-color: rgba(26, 26, 46, 220);
                border-radius: 10px;
                border: 1px solid rgba(15, 52, 96, 200);
            }
            QLabel {
                color: white;
                background: transparent;
            }
        """)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(6)

        # Header row
        header_layout = QHBoxLayout()
        title = QLabel("GTA Manager")
        title.setStyleSheet("color: #AAA; font-size: 10px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self._state_badge = QLabel("IDLE")
        self._state_badge.setStyleSheet(
            "color: #AAA; font-size: 9px; background-color: rgba(0,0,0,50); "
            "padding: 2px 6px; border-radius: 3px;"
        )
        header_layout.addWidget(self._state_badge)
        container_layout.addLayout(header_layout)

        # Money display
        self._money_label = QLabel("$--")
        self._money_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        self._money_label.setStyleSheet("color: #4CAF50;")
        container_layout.addWidget(self._money_label)

        # Session earnings
        session_layout = QHBoxLayout()
        session_icon = QLabel("Session:")
        session_icon.setStyleSheet("color: #AAA; font-size: 11px;")
        session_layout.addWidget(session_icon)

        self._session_label = QLabel("+$0")
        self._session_label.setStyleSheet("color: #FFD700; font-size: 14px; font-weight: bold;")
        session_layout.addWidget(self._session_label)
        session_layout.addStretch()

        self._rate_label = QLabel("")
        self._rate_label.setStyleSheet("color: #666; font-size: 10px;")
        session_layout.addWidget(self._rate_label)
        container_layout.addLayout(session_layout)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(255,255,255,20);")
        container_layout.addWidget(divider)

        # Current activity
        self._activity_label = QLabel("Idle")
        self._activity_label.setStyleSheet("color: white; font-size: 12px;")
        container_layout.addWidget(self._activity_label)

        # Timer (if in mission)
        self._timer_label = QLabel("")
        self._timer_label.setStyleSheet("color: #4CAF50; font-size: 11px;")
        container_layout.addWidget(self._timer_label)

        # Cooldowns (compact display)
        self._cooldown_widget = CompactCooldownWidget(max_display=2)
        container_layout.addWidget(self._cooldown_widget)

        # Goal progress (hidden by default)
        self._goal_frame = QFrame()
        self._goal_frame.setStyleSheet("background: transparent;")
        goal_layout = QVBoxLayout(self._goal_frame)
        goal_layout.setContentsMargins(0, 4, 0, 0)
        goal_layout.setSpacing(2)

        goal_header = QHBoxLayout()
        self._goal_name_label = QLabel("")
        self._goal_name_label.setStyleSheet("color: #9C27B0; font-size: 10px;")
        goal_header.addWidget(self._goal_name_label)
        goal_header.addStretch()
        self._goal_percent_label = QLabel("")
        self._goal_percent_label.setStyleSheet("color: #9C27B0; font-size: 10px; font-weight: bold;")
        goal_header.addWidget(self._goal_percent_label)
        goal_layout.addLayout(goal_header)

        self._goal_progress = QProgressBar()
        self._goal_progress.setFixedHeight(6)
        self._goal_progress.setTextVisible(False)
        self._goal_progress.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 20);
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #9C27B0;
                border-radius: 3px;
            }
        """)
        goal_layout.addWidget(self._goal_progress)
        self._goal_frame.hide()
        container_layout.addWidget(self._goal_frame)

        # Bonus badge (hidden by default)
        self._bonus_frame = QFrame()
        self._bonus_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 215, 0, 25);
                border: 1px solid rgba(255, 215, 0, 80);
                border-radius: 4px;
            }
        """)
        bonus_layout = QHBoxLayout(self._bonus_frame)
        bonus_layout.setContentsMargins(6, 3, 6, 3)
        bonus_layout.setSpacing(4)

        self._bonus_multiplier = QLabel("2X")
        self._bonus_multiplier.setStyleSheet("color: #FFD700; font-size: 11px; font-weight: bold;")
        bonus_layout.addWidget(self._bonus_multiplier)

        self._bonus_name = QLabel("")
        self._bonus_name.setStyleSheet("color: #FFD700; font-size: 10px;")
        bonus_layout.addWidget(self._bonus_name, stretch=1)
        self._bonus_frame.hide()
        container_layout.addWidget(self._bonus_frame)

        # Recommendation
        self._recommendation_label = QLabel("")
        self._recommendation_label.setStyleSheet("color: #FFD700; font-size: 10px;")
        self._recommendation_label.setWordWrap(True)
        container_layout.addWidget(self._recommendation_label)

        layout.addWidget(container)

    def _setup_update_timer(self) -> None:
        """Setup timer for UI updates."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_ui)
        self._update_timer.start(500)

    def _update_ui(self) -> None:
        """Update overlay with current data."""
        if not self._app:
            return

        # Update money
        money = self._app.current_money
        if money is not None:
            self._money_label.setText(f"${money:,}")
        else:
            self._money_label.setText("$--")

        # Update session
        earnings = self._app.session_earnings
        self._session_label.setText(f"+{format_money_short(earnings)}")

        # Update rate
        stats = self._app.session_stats
        if stats and stats.duration_seconds > 60:
            rate = stats.earnings_per_hour
            self._rate_label.setText(f"({format_money_short(rate)}/hr)")

        # Update state badge
        state = self._app.game_state
        state_text = state.name.replace("_", " ")
        state_colors = {
            "IDLE": "#AAA",
            "MISSION_ACTIVE": "#4CAF50",
            "SELLING": "#FF9800",
            "MISSION_COMPLETE": "#4CAF50",
            "MISSION_FAILED": "#F44336",
            "LOADING": "#2196F3",
        }
        color = state_colors.get(state.name, "#AAA")
        self._state_badge.setText(state_text)
        self._state_badge.setStyleSheet(
            f"color: {color}; font-size: 9px; background-color: rgba(0,0,0,50); "
            "padding: 2px 6px; border-radius: 3px;"
        )

        # Update activity
        last = self._app.last_capture
        if last and last.timer and last.timer.has_value:
            self._activity_label.setText(f"{state_text}")
            self._timer_label.setText(f"Timer: {last.timer.formatted}")
            self._timer_label.show()
        else:
            self._activity_label.setText(state_text)
            self._timer_label.hide()

        # Update recommendation
        recommendations = self._app.recommendations
        if recommendations:
            rec = recommendations[0]
            self._recommendation_label.setText(f"Next: {rec.action}")
            self._recommendation_label.show()
        else:
            self._recommendation_label.hide()

        # Update goal if tracker is set
        self._update_goal()

        # Update bonus if tracker is set
        self._update_bonus()

    def _update_goal(self) -> None:
        """Update goal progress display."""
        if not self._goal_tracker:
            return

        goal = self._goal_tracker.current_goal
        if goal:
            self._goal_name_label.setText(goal.display_name)
            self._goal_percent_label.setText(f"{goal.progress_percent}%")
            self._goal_progress.setValue(goal.progress_percent)

            if goal.is_complete:
                self._goal_percent_label.setStyleSheet(
                    "color: #4CAF50; font-size: 10px; font-weight: bold;"
                )
                self._goal_progress.setStyleSheet("""
                    QProgressBar {
                        background-color: rgba(255, 255, 255, 20);
                        border-radius: 3px;
                    }
                    QProgressBar::chunk {
                        background-color: #4CAF50;
                        border-radius: 3px;
                    }
                """)

            self._goal_frame.show()
        else:
            self._goal_frame.hide()

    def _update_bonus(self) -> None:
        """Update bonus badge display."""
        if not self._bonus_tracker or not self._bonus_tracker.has_bonuses:
            self._bonus_frame.hide()
            return

        bonuses = self._bonus_tracker.active_bonuses
        if bonuses:
            # Show first bonus
            bonus = bonuses[0]
            self._bonus_multiplier.setText(bonus.multiplier_text)
            name = bonus.name
            if len(bonuses) > 1:
                name += f" +{len(bonuses) - 1} more"
            self._bonus_name.setText(name)
            self._bonus_frame.show()
        else:
            self._bonus_frame.hide()

    def set_goal_tracker(self, tracker) -> None:
        """Set the goal tracker to display progress.

        Args:
            tracker: GoalTracker instance
        """
        self._goal_tracker = tracker
        self._update_size_for_content()

    def set_bonus_tracker(self, tracker) -> None:
        """Set the weekly bonus tracker.

        Args:
            tracker: WeeklyBonusTracker instance
        """
        self._bonus_tracker = tracker
        self._update_size_for_content()

    def _update_size_for_content(self) -> None:
        """Adjust size based on visible content."""
        # Calculate needed height
        base_height = 180
        if self._goal_tracker and self._goal_tracker.has_goal:
            base_height += 35
        if self._bonus_tracker and self._bonus_tracker.has_bonuses:
            base_height += 30
        self.setFixedSize(280, base_height)

    def set_size_mode(self, mode: OverlaySize) -> None:
        """Change overlay size mode.

        Args:
            mode: New size mode
        """
        self._size_mode = mode
        self._update_size()

    def cycle_size_mode(self) -> None:
        """Cycle through size modes."""
        modes = list(OverlaySize)
        current_idx = modes.index(self._size_mode)
        next_idx = (current_idx + 1) % len(modes)
        self.set_size_mode(modes[next_idx])

    def set_position(self, position: str) -> None:
        """Change overlay position."""
        self._position_overlay(position)

    def set_opacity(self, opacity: float) -> None:
        """Set overlay opacity."""
        self.setWindowOpacity(opacity)

    def set_locked(self, locked: bool) -> None:
        """Lock/unlock the overlay (pass-through clicks when locked)."""
        self._is_locked = locked
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if locked:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.show()

    # Make overlay draggable
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_locked:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_position and not self._is_locked:
            self.move(event.globalPosition().toPoint() - self._drag_position)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_position = None
