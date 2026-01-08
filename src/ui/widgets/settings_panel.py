"""Settings panel with validation and user feedback."""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QCheckBox,
    QComboBox,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QPushButton,
    QLineEdit,
    QMessageBox,
    QScrollArea,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence

from ...config.settings import get_settings
from ...utils.logging import get_logger

if TYPE_CHECKING:
    from ...app import GTABusinessManager


logger = get_logger("ui.settings")


class SettingsPanel(QWidget):
    """Panel for application settings with validation."""

    def __init__(self, app: "GTABusinessManager" = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._settings = get_settings()
        self._unsaved_changes = False
        self._setup_ui()
        self._load_settings()
        self._connect_change_signals()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Content widget
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header with status
        header_layout = QHBoxLayout()
        header = QLabel("Settings")
        header.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        header_layout.addWidget(header)
        header_layout.addStretch()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        header_layout.addWidget(self._status_label)
        layout.addLayout(header_layout)

        # Group box style
        group_style = """
            QGroupBox {
                color: white;
                font-weight: bold;
                border: 1px solid #0f3460;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 8px;
            }
        """

        # General settings
        general_group = QGroupBox("General")
        general_group.setStyleSheet(group_style)
        general_layout = QVBoxLayout(general_group)

        # Character name
        name_layout = QHBoxLayout()
        name_label = QLabel("Character Name:")
        name_label.setStyleSheet("color: #AAA;")
        name_label.setMinimumWidth(150)
        name_layout.addWidget(name_label)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Default")
        self._name_edit.setMaximumWidth(200)
        self._name_edit.setToolTip("Name for your GTA Online character (for multi-character tracking)")
        name_layout.addWidget(self._name_edit)
        name_layout.addStretch()
        general_layout.addLayout(name_layout)

        # Launch on startup
        self._startup_check = QCheckBox("Launch on Windows startup")
        self._startup_check.setStyleSheet("color: #AAA;")
        self._startup_check.setToolTip("Automatically start the app when Windows starts")
        general_layout.addWidget(self._startup_check)

        # Minimize to tray
        self._tray_check = QCheckBox("Minimize to system tray")
        self._tray_check.setStyleSheet("color: #AAA;")
        self._tray_check.setToolTip("Keep running in system tray when window is closed")
        general_layout.addWidget(self._tray_check)

        layout.addWidget(general_group)

        # Capture settings
        capture_group = QGroupBox("Screen Capture")
        capture_group.setStyleSheet(group_style)
        capture_layout = QVBoxLayout(capture_group)

        # Monitor selection
        monitor_layout = QHBoxLayout()
        monitor_label = QLabel("Monitor:")
        monitor_label.setStyleSheet("color: #AAA;")
        monitor_label.setMinimumWidth(150)
        monitor_layout.addWidget(monitor_label)
        self._monitor_combo = QComboBox()
        self._monitor_combo.addItems(["Primary (0)", "Secondary (1)", "Third (2)"])
        self._monitor_combo.setMaximumWidth(150)
        self._monitor_combo.setToolTip("Which monitor to capture GTA from")
        monitor_layout.addWidget(self._monitor_combo)
        monitor_layout.addStretch()
        capture_layout.addLayout(monitor_layout)

        # Idle FPS
        idle_layout = QHBoxLayout()
        idle_label = QLabel("Idle capture rate:")
        idle_label.setStyleSheet("color: #AAA;")
        idle_label.setMinimumWidth(150)
        idle_layout.addWidget(idle_label)
        self._idle_fps = QDoubleSpinBox()
        self._idle_fps.setRange(0.1, 5.0)
        self._idle_fps.setSingleStep(0.1)
        self._idle_fps.setDecimals(1)
        self._idle_fps.setSuffix(" FPS")
        self._idle_fps.setMaximumWidth(100)
        self._idle_fps.setToolTip("Capture rate when not in a mission (lower = less CPU)")
        idle_layout.addWidget(self._idle_fps)
        idle_layout.addStretch()
        capture_layout.addLayout(idle_layout)

        # Active FPS
        active_layout = QHBoxLayout()
        active_label = QLabel("Active capture rate:")
        active_label.setStyleSheet("color: #AAA;")
        active_label.setMinimumWidth(150)
        active_layout.addWidget(active_label)
        self._active_fps = QDoubleSpinBox()
        self._active_fps.setRange(0.5, 10.0)
        self._active_fps.setSingleStep(0.5)
        self._active_fps.setDecimals(1)
        self._active_fps.setSuffix(" FPS")
        self._active_fps.setMaximumWidth(100)
        self._active_fps.setToolTip("Capture rate during missions (higher = more accurate timing)")
        active_layout.addWidget(self._active_fps)
        active_layout.addStretch()
        capture_layout.addLayout(active_layout)

        layout.addWidget(capture_group)

        # Display settings
        display_group = QGroupBox("Display")
        display_group.setStyleSheet(group_style)
        display_layout = QVBoxLayout(display_group)

        # Display mode
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Display mode:")
        mode_label.setStyleSheet("color: #AAA;")
        mode_label.setMinimumWidth(150)
        mode_layout.addWidget(mode_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Overlay Only", "Window Only", "Both"])
        self._mode_combo.setMaximumWidth(150)
        self._mode_combo.setToolTip("How to show tracking information")
        mode_layout.addWidget(self._mode_combo)
        mode_layout.addStretch()
        display_layout.addLayout(mode_layout)

        # Overlay position
        pos_layout = QHBoxLayout()
        pos_label = QLabel("Overlay position:")
        pos_label.setStyleSheet("color: #AAA;")
        pos_label.setMinimumWidth(150)
        pos_layout.addWidget(pos_label)
        self._pos_combo = QComboBox()
        self._pos_combo.addItems(["Top Left", "Top Right", "Bottom Left", "Bottom Right"])
        self._pos_combo.setMaximumWidth(150)
        self._pos_combo.setToolTip("Where to position the overlay on screen")
        pos_layout.addWidget(self._pos_combo)
        pos_layout.addStretch()
        display_layout.addLayout(pos_layout)

        # Overlay opacity
        opacity_layout = QHBoxLayout()
        opacity_label = QLabel("Overlay opacity:")
        opacity_label.setStyleSheet("color: #AAA;")
        opacity_label.setMinimumWidth(150)
        opacity_layout.addWidget(opacity_label)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(20, 100)
        self._opacity_slider.setValue(85)
        self._opacity_slider.setMaximumWidth(150)
        self._opacity_slider.setToolTip("How transparent the overlay should be")
        opacity_layout.addWidget(self._opacity_slider)
        self._opacity_value = QLabel("85%")
        self._opacity_value.setStyleSheet("color: #AAA;")
        self._opacity_value.setMinimumWidth(40)
        opacity_layout.addWidget(self._opacity_value)
        opacity_layout.addStretch()
        display_layout.addLayout(opacity_layout)

        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_value.setText(f"{v}%")
        )

        layout.addWidget(display_group)

        # Hotkeys settings
        hotkey_group = QGroupBox("Hotkeys")
        hotkey_group.setStyleSheet(group_style)
        hotkey_layout = QVBoxLayout(hotkey_group)

        # Toggle overlay
        overlay_key_layout = QHBoxLayout()
        overlay_key_label = QLabel("Toggle Overlay:")
        overlay_key_label.setStyleSheet("color: #AAA;")
        overlay_key_label.setMinimumWidth(150)
        overlay_key_layout.addWidget(overlay_key_label)
        self._overlay_key = QLineEdit()
        self._overlay_key.setPlaceholderText("ctrl+shift+g")
        self._overlay_key.setMaximumWidth(150)
        self._overlay_key.setToolTip("Keyboard shortcut to show/hide the overlay")
        overlay_key_layout.addWidget(self._overlay_key)
        overlay_key_layout.addStretch()
        hotkey_layout.addLayout(overlay_key_layout)

        # Toggle tracking
        tracking_key_layout = QHBoxLayout()
        tracking_key_label = QLabel("Toggle Tracking:")
        tracking_key_label.setStyleSheet("color: #AAA;")
        tracking_key_label.setMinimumWidth(150)
        tracking_key_layout.addWidget(tracking_key_label)
        self._tracking_key = QLineEdit()
        self._tracking_key.setPlaceholderText("ctrl+shift+t")
        self._tracking_key.setMaximumWidth(150)
        self._tracking_key.setToolTip("Keyboard shortcut to pause/resume tracking")
        tracking_key_layout.addWidget(self._tracking_key)
        tracking_key_layout.addStretch()
        hotkey_layout.addLayout(tracking_key_layout)

        # Show window
        window_key_layout = QHBoxLayout()
        window_key_label = QLabel("Show Window:")
        window_key_label.setStyleSheet("color: #AAA;")
        window_key_label.setMinimumWidth(150)
        window_key_layout.addWidget(window_key_label)
        self._window_key = QLineEdit()
        self._window_key.setPlaceholderText("ctrl+shift+m")
        self._window_key.setMaximumWidth(150)
        self._window_key.setToolTip("Keyboard shortcut to show the main window")
        window_key_layout.addWidget(self._window_key)
        window_key_layout.addStretch()
        hotkey_layout.addLayout(window_key_layout)

        layout.addWidget(hotkey_group)

        # Notifications settings
        notif_group = QGroupBox("Notifications")
        notif_group.setStyleSheet(group_style)
        notif_layout = QVBoxLayout(notif_group)

        self._audio_check = QCheckBox("Enable audio notifications")
        self._audio_check.setStyleSheet("color: #AAA;")
        self._audio_check.setToolTip("Play sounds for important events")
        notif_layout.addWidget(self._audio_check)

        self._desktop_check = QCheckBox("Enable desktop notifications")
        self._desktop_check.setStyleSheet("color: #AAA;")
        self._desktop_check.setToolTip("Show Windows notifications for important events")
        notif_layout.addWidget(self._desktop_check)

        self._money_notif_check = QCheckBox("Notify on large earnings (>$10,000)")
        self._money_notif_check.setStyleSheet("color: #AAA;")
        self._money_notif_check.setToolTip("Show notification when you earn significant money")
        notif_layout.addWidget(self._money_notif_check)

        layout.addWidget(notif_group)

        # Data settings
        data_group = QGroupBox("Data")
        data_group.setStyleSheet(group_style)
        data_layout = QVBoxLayout(data_group)

        # Config path display
        config_layout = QHBoxLayout()
        config_label = QLabel("Config location:")
        config_label.setStyleSheet("color: #AAA;")
        config_layout.addWidget(config_label)
        config_path = QLabel(str(self._settings.config_path))
        config_path.setStyleSheet("color: #666; font-size: 11px;")
        config_path.setWordWrap(True)
        config_layout.addWidget(config_path)
        config_layout.addStretch()
        data_layout.addLayout(config_layout)

        # Export button
        export_btn = QPushButton("Export Session Data...")
        export_btn.clicked.connect(self._export_data)
        export_btn.setMaximumWidth(200)
        data_layout.addWidget(export_btn)

        layout.addWidget(data_group)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._save_btn = QPushButton("Save Settings")
        self._save_btn.clicked.connect(self._save_settings)
        self._save_btn.setEnabled(False)
        button_layout.addWidget(self._save_btn)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_settings)
        button_layout.addWidget(reset_btn)

        layout.addLayout(button_layout)
        layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _connect_change_signals(self):
        """Connect signals to track changes."""
        # Text fields
        self._name_edit.textChanged.connect(self._on_change)
        self._overlay_key.textChanged.connect(self._on_change)
        self._tracking_key.textChanged.connect(self._on_change)
        self._window_key.textChanged.connect(self._on_change)

        # Checkboxes
        self._startup_check.stateChanged.connect(self._on_change)
        self._tray_check.stateChanged.connect(self._on_change)
        self._audio_check.stateChanged.connect(self._on_change)
        self._desktop_check.stateChanged.connect(self._on_change)
        self._money_notif_check.stateChanged.connect(self._on_change)

        # Combo boxes
        self._monitor_combo.currentIndexChanged.connect(self._on_change)
        self._mode_combo.currentIndexChanged.connect(self._on_change)
        self._pos_combo.currentIndexChanged.connect(self._on_change)

        # Spin boxes
        self._idle_fps.valueChanged.connect(self._on_change)
        self._active_fps.valueChanged.connect(self._on_change)

        # Slider
        self._opacity_slider.valueChanged.connect(self._on_change)

    def _on_change(self):
        """Handle any setting change."""
        self._unsaved_changes = True
        self._save_btn.setEnabled(True)
        self._status_label.setText("Unsaved changes")
        self._status_label.setStyleSheet("color: #FF9800; font-size: 12px;")

    def _load_settings(self):
        """Load settings into UI."""
        # Block signals while loading
        self._name_edit.blockSignals(True)
        self._startup_check.blockSignals(True)
        self._tray_check.blockSignals(True)
        self._monitor_combo.blockSignals(True)
        self._idle_fps.blockSignals(True)
        self._active_fps.blockSignals(True)
        self._mode_combo.blockSignals(True)
        self._pos_combo.blockSignals(True)
        self._opacity_slider.blockSignals(True)
        self._audio_check.blockSignals(True)
        self._desktop_check.blockSignals(True)
        self._money_notif_check.blockSignals(True)
        self._overlay_key.blockSignals(True)
        self._tracking_key.blockSignals(True)
        self._window_key.blockSignals(True)

        try:
            self._name_edit.setText(self._settings.get("general.character_name", "Default"))
            self._startup_check.setChecked(self._settings.get("general.launch_on_startup", False))
            self._tray_check.setChecked(self._settings.get("general.minimize_to_tray", True))

            self._monitor_combo.setCurrentIndex(self._settings.get("capture.monitor_index", 0))
            self._idle_fps.setValue(self._settings.get("capture.idle_fps", 0.5))
            self._active_fps.setValue(self._settings.get("capture.active_fps", 2.0))

            mode = self._settings.get("display.mode", "overlay")
            mode_map = {"overlay": 0, "window": 1, "both": 2}
            self._mode_combo.setCurrentIndex(mode_map.get(mode, 0))

            pos = self._settings.get("display.overlay_position", "top-right")
            pos_map = {"top-left": 0, "top-right": 1, "bottom-left": 2, "bottom-right": 3}
            self._pos_combo.setCurrentIndex(pos_map.get(pos, 1))

            self._opacity_slider.setValue(int(self._settings.get("display.overlay_opacity", 0.85) * 100))
            self._opacity_value.setText(f"{self._opacity_slider.value()}%")

            self._audio_check.setChecked(self._settings.get("notifications.audio_enabled", False))
            self._desktop_check.setChecked(self._settings.get("notifications.desktop_notifications", True))
            self._money_notif_check.setChecked(self._settings.get("notifications.money_notification", True))

            # Hotkeys
            self._overlay_key.setText(self._settings.get("hotkeys.toggle_overlay", "ctrl+shift+g"))
            self._tracking_key.setText(self._settings.get("hotkeys.toggle_tracking", "ctrl+shift+t"))
            self._window_key.setText(self._settings.get("hotkeys.show_window", "ctrl+shift+m"))

        finally:
            # Restore signals
            self._name_edit.blockSignals(False)
            self._startup_check.blockSignals(False)
            self._tray_check.blockSignals(False)
            self._monitor_combo.blockSignals(False)
            self._idle_fps.blockSignals(False)
            self._active_fps.blockSignals(False)
            self._mode_combo.blockSignals(False)
            self._pos_combo.blockSignals(False)
            self._opacity_slider.blockSignals(False)
            self._audio_check.blockSignals(False)
            self._desktop_check.blockSignals(False)
            self._money_notif_check.blockSignals(False)
            self._overlay_key.blockSignals(False)
            self._tracking_key.blockSignals(False)
            self._window_key.blockSignals(False)

        self._unsaved_changes = False
        self._save_btn.setEnabled(False)
        self._status_label.setText("")

    def _validate_settings(self) -> tuple[bool, str]:
        """Validate current settings.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate character name
        name = self._name_edit.text().strip()
        if not name:
            return False, "Character name cannot be empty"
        if len(name) > 50:
            return False, "Character name is too long (max 50 characters)"

        # Validate FPS settings
        if self._idle_fps.value() > self._active_fps.value():
            return False, "Idle FPS should be less than or equal to Active FPS"

        # Validate hotkeys (basic check)
        hotkeys = [
            self._overlay_key.text().strip(),
            self._tracking_key.text().strip(),
            self._window_key.text().strip(),
        ]
        non_empty = [h for h in hotkeys if h]
        if len(non_empty) != len(set(non_empty)):
            return False, "Hotkeys must be unique"

        return True, ""

    def _save_settings(self):
        """Save settings from UI with validation."""
        # Validate first
        is_valid, error_msg = self._validate_settings()
        if not is_valid:
            QMessageBox.warning(self, "Invalid Settings", error_msg)
            return

        try:
            self._settings.set("general.character_name", self._name_edit.text().strip() or "Default")
            self._settings.set("general.launch_on_startup", self._startup_check.isChecked())
            self._settings.set("general.minimize_to_tray", self._tray_check.isChecked())

            self._settings.set("capture.monitor_index", self._monitor_combo.currentIndex())
            self._settings.set("capture.idle_fps", self._idle_fps.value())
            self._settings.set("capture.active_fps", self._active_fps.value())

            modes = ["overlay", "window", "both"]
            self._settings.set("display.mode", modes[self._mode_combo.currentIndex()])

            positions = ["top-left", "top-right", "bottom-left", "bottom-right"]
            self._settings.set("display.overlay_position", positions[self._pos_combo.currentIndex()])

            self._settings.set("display.overlay_opacity", self._opacity_slider.value() / 100)

            self._settings.set("notifications.audio_enabled", self._audio_check.isChecked())
            self._settings.set("notifications.desktop_notifications", self._desktop_check.isChecked())
            self._settings.set("notifications.money_notification", self._money_notif_check.isChecked())

            # Hotkeys
            self._settings.set("hotkeys.toggle_overlay", self._overlay_key.text().strip() or "ctrl+shift+g")
            self._settings.set("hotkeys.toggle_tracking", self._tracking_key.text().strip() or "ctrl+shift+t")
            self._settings.set("hotkeys.show_window", self._window_key.text().strip() or "ctrl+shift+m")

            self._unsaved_changes = False
            self._save_btn.setEnabled(False)
            self._status_label.setText("Settings saved!")
            self._status_label.setStyleSheet("color: #4CAF50; font-size: 12px;")

            # Clear status after 3 seconds
            QTimer.singleShot(3000, lambda: self._status_label.setText(""))

            logger.info("Settings saved successfully")

        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")

    def _reset_settings(self):
        """Reset settings to defaults with confirmation."""
        reply = QMessageBox.question(
            self,
            "Reset Settings",
            "Are you sure you want to reset all settings to their default values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._settings.reset_to_defaults()
            self._load_settings()
            self._status_label.setText("Settings reset to defaults")
            self._status_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
            QTimer.singleShot(3000, lambda: self._status_label.setText(""))

    def _export_data(self):
        """Export session data."""
        from PyQt6.QtWidgets import QFileDialog
        import json

        # For now, show a placeholder - full implementation would use repository
        QMessageBox.information(
            self,
            "Export Data",
            "Session data export will be available in a future update.\n\n"
            f"Your data is stored at:\n{self._settings.data_dir}",
        )

    def has_unsaved_changes(self) -> bool:
        """Check if there are unsaved changes."""
        return self._unsaved_changes
