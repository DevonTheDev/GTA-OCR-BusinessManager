"""Weekly bonus management panel.

Lets players easily set which 2x/3x bonuses are active this week.
"""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QGridLayout,
    QCheckBox,
    QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ...game.weekly_bonuses import (
    WeeklyBonusTracker,
    BONUS_PRESETS,
    BonusCategory,
    get_weekly_bonus_tracker,
)
from ...utils.logging import get_logger

logger = get_logger("ui.bonus_panel")


class BonusToggleButton(QFrame):
    """A toggle button for a bonus preset."""

    toggled = pyqtSignal(str, bool)  # preset_key, is_active

    def __init__(self, preset_key: str, bonus, parent=None):
        super().__init__(parent)
        self._preset_key = preset_key
        self._bonus = bonus
        self._is_active = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui()
        self._update_style()

    def _setup_ui(self) -> None:
        """Set up the button UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Multiplier badge
        self._multiplier = QLabel(self._bonus.multiplier_text)
        self._multiplier.setFixedWidth(30)
        self._multiplier.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._multiplier)

        # Name and description
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self._name = QLabel(self._bonus.name)
        self._name.setStyleSheet("font-weight: bold;")
        text_layout.addWidget(self._name)

        self._desc = QLabel(self._bonus.description)
        self._desc.setStyleSheet("font-size: 10px;")
        self._desc.setWordWrap(True)
        text_layout.addWidget(self._desc)

        layout.addLayout(text_layout, stretch=1)

        # Check indicator
        self._check = QLabel("")
        self._check.setFixedWidth(20)
        layout.addWidget(self._check)

    def _update_style(self) -> None:
        """Update button style based on active state."""
        if self._is_active:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 215, 0, 30);
                    border: 2px solid #FFD700;
                    border-radius: 8px;
                }
                QLabel {
                    color: white;
                }
            """)
            self._multiplier.setStyleSheet(
                "color: #FFD700; font-weight: bold; font-size: 14px;"
            )
            self._check.setText("ON")
            self._check.setStyleSheet("color: #4CAF50; font-weight: bold;")
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 255, 255, 5);
                    border: 1px solid rgba(255, 255, 255, 20);
                    border-radius: 8px;
                }
                QFrame:hover {
                    background-color: rgba(255, 255, 255, 10);
                    border: 1px solid rgba(255, 215, 0, 50);
                }
                QLabel {
                    color: #AAA;
                }
            """)
            self._multiplier.setStyleSheet(
                "color: #666; font-weight: bold; font-size: 14px;"
            )
            self._check.setText("")

    def mousePressEvent(self, event) -> None:
        """Handle click to toggle."""
        self._is_active = not self._is_active
        self._update_style()
        self.toggled.emit(self._preset_key, self._is_active)

    def set_active(self, active: bool) -> None:
        """Set active state without emitting signal."""
        self._is_active = active
        self._update_style()

    @property
    def is_active(self) -> bool:
        """Get active state."""
        return self._is_active


class BonusCategoryGroup(QGroupBox):
    """Group of bonus toggles for a category."""

    bonus_toggled = pyqtSignal(str, bool)

    def __init__(self, category_name: str, parent=None):
        super().__init__(category_name, parent)
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                color: #FFD700;
                border: 1px solid rgba(255, 215, 0, 30);
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(6)
        self._buttons: dict[str, BonusToggleButton] = {}

    def add_bonus(self, preset_key: str, bonus) -> None:
        """Add a bonus toggle to this group."""
        button = BonusToggleButton(preset_key, bonus)
        button.toggled.connect(self._on_toggled)
        self._buttons[preset_key] = button
        self._layout.addWidget(button)

    def _on_toggled(self, preset_key: str, is_active: bool) -> None:
        """Handle bonus toggle."""
        self.bonus_toggled.emit(preset_key, is_active)

    def set_active(self, preset_key: str, active: bool) -> None:
        """Set active state for a bonus."""
        if preset_key in self._buttons:
            self._buttons[preset_key].set_active(active)


class WeeklyBonusPanel(QWidget):
    """Panel for managing weekly bonuses."""

    def __init__(self, tracker: Optional[WeeklyBonusTracker] = None, parent=None):
        super().__init__(parent)
        self._tracker = tracker or get_weekly_bonus_tracker()
        self._groups: dict[str, BonusCategoryGroup] = {}
        self._setup_ui()
        self._load_current_bonuses()

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Header
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 215, 0, 20);
                border-radius: 8px;
                padding: 12px;
            }
        """)
        header_layout = QVBoxLayout(header)
        header_layout.setSpacing(4)

        title = QLabel("This Week's Bonuses")
        title.setStyleSheet("color: #FFD700; font-size: 16px; font-weight: bold;")
        header_layout.addWidget(title)

        subtitle = QLabel(
            "Select active 2x/3x bonuses to get better recommendations"
        )
        subtitle.setStyleSheet("color: #AAA; font-size: 11px;")
        subtitle.setWordWrap(True)
        header_layout.addWidget(subtitle)

        # Reset timer
        self._reset_label = QLabel("")
        self._reset_label.setStyleSheet("color: #666; font-size: 10px;")
        header_layout.addWidget(self._reset_label)

        layout.addWidget(header)

        # Quick actions
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        clear_btn = QPushButton("Clear All")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(244, 67, 54, 30);
                border: 1px solid #F44336;
                border-radius: 4px;
                color: #F44336;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: rgba(244, 67, 54, 50);
            }
        """)
        clear_btn.clicked.connect(self._clear_all)
        actions_layout.addWidget(clear_btn)

        actions_layout.addStretch()

        count_label = QLabel("")
        count_label.setStyleSheet("color: #666; font-size: 11px;")
        self._count_label = count_label
        actions_layout.addWidget(count_label)

        layout.addLayout(actions_layout)

        # Scrollable bonus list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
        """)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(12)

        # Organize presets by category
        categories = {
            "Business Sales": BonusCategory.BUSINESS_SELL,
            "Heists": BonusCategory.HEIST,
            "VIP/CEO Work": BonusCategory.VIP_WORK,
            "Missions": BonusCategory.MISSION,
            "Freemode": BonusCategory.FREEMODE,
        }

        for cat_name, cat_enum in categories.items():
            group = BonusCategoryGroup(cat_name)
            group.bonus_toggled.connect(self._on_bonus_toggled)

            # Add matching presets
            for key, bonus in BONUS_PRESETS.items():
                if bonus.category == cat_enum:
                    group.add_bonus(key, bonus)

            self._groups[cat_name] = group
            scroll_layout.addWidget(group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        self._update_count()

    def _load_current_bonuses(self) -> None:
        """Load and display currently active bonuses."""
        for bonus in self._tracker.active_bonuses:
            # Find preset key for this bonus
            for key, preset in BONUS_PRESETS.items():
                if preset.name == bonus.name:
                    # Find the right group
                    for group in self._groups.values():
                        group.set_active(key, True)
                    break

        self._update_count()
        self._update_reset_timer()

    def _on_bonus_toggled(self, preset_key: str, is_active: bool) -> None:
        """Handle bonus toggle."""
        if is_active:
            self._tracker.add_preset(preset_key)
        else:
            bonus = BONUS_PRESETS.get(preset_key)
            if bonus:
                self._tracker.remove_bonus(bonus.name)

        self._update_count()
        logger.info(f"Bonus {'activated' if is_active else 'deactivated'}: {preset_key}")

    def _clear_all(self) -> None:
        """Clear all active bonuses."""
        self._tracker.clear_all()

        # Update all buttons
        for group in self._groups.values():
            for key in BONUS_PRESETS.keys():
                group.set_active(key, False)

        self._update_count()
        logger.info("Cleared all weekly bonuses")

    def _update_count(self) -> None:
        """Update the active bonus count."""
        count = len(self._tracker.active_bonuses)
        if count == 0:
            self._count_label.setText("No bonuses active")
        elif count == 1:
            self._count_label.setText("1 bonus active")
        else:
            self._count_label.setText(f"{count} bonuses active")

    def _update_reset_timer(self) -> None:
        """Update the reset timer display."""
        time_str = self._tracker.time_until_reset_formatted
        if time_str and time_str != "Unknown":
            self._reset_label.setText(f"Resets in: {time_str}")
        else:
            self._reset_label.setText("Set bonuses each Thursday")

    def refresh(self) -> None:
        """Refresh the display."""
        self._load_current_bonuses()


class CompactBonusDisplay(QFrame):
    """Compact display of active bonuses for dashboard."""

    def __init__(self, tracker: Optional[WeeklyBonusTracker] = None, parent=None):
        super().__init__(parent)
        self._tracker = tracker or get_weekly_bonus_tracker()

        self.setStyleSheet("""
            QFrame {
                background-color: #16213e;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the compact display."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("Active Bonuses")
        title.setStyleSheet("color: #FFD700; font-size: 12px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self._count = QLabel("")
        self._count.setStyleSheet("color: #666; font-size: 10px;")
        header.addWidget(self._count)
        layout.addLayout(header)

        # Bonus list (max 3)
        self._bonus_labels = []
        for _ in range(3):
            label = QLabel("")
            label.setStyleSheet("color: white; font-size: 11px;")
            label.hide()
            self._bonus_labels.append(label)
            layout.addWidget(label)

        self._more_label = QLabel("")
        self._more_label.setStyleSheet("color: #666; font-size: 10px;")
        self._more_label.hide()
        layout.addWidget(self._more_label)

        self._empty_label = QLabel("No bonuses set - click to add")
        self._empty_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._empty_label)

    def update_display(self) -> None:
        """Update the bonus display."""
        bonuses = self._tracker.active_bonuses

        if not bonuses:
            self._empty_label.show()
            self._count.setText("")
            for label in self._bonus_labels:
                label.hide()
            self._more_label.hide()
            return

        self._empty_label.hide()
        self._count.setText(f"{len(bonuses)} active")

        # Show up to 3 bonuses
        for i, label in enumerate(self._bonus_labels):
            if i < len(bonuses):
                bonus = bonuses[i]
                label.setText(f"{bonus.multiplier_text} {bonus.name}")
                label.setStyleSheet("color: #FFD700; font-size: 11px;")
                label.show()
            else:
                label.hide()

        # Show "more" if needed
        if len(bonuses) > 3:
            self._more_label.setText(f"+{len(bonuses) - 3} more")
            self._more_label.show()
        else:
            self._more_label.hide()
