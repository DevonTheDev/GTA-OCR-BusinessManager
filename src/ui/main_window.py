"""Main application window for GTA Business Manager."""

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QStatusBar,
    QLabel,
    QMenuBar,
    QMenu,
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction

from .widgets.dashboard import DashboardWidget
from .widgets.business_panel import BusinessPanel
from .widgets.activity_panel import ActivityPanel
from .widgets.session_panel import SessionPanel
from .widgets.recommendations import RecommendationsPanel
from .widgets.settings_panel import SettingsPanel
from .styles.dark_theme import DarkTheme
from ..utils.logging import get_logger
from ..utils.helpers import format_money_short
from .. import __version__

if TYPE_CHECKING:
    from ..app import GTABusinessManager
    from .overlay import OverlayWindow


logger = get_logger("ui.main_window")


class MainWindow(QMainWindow):
    """Main dashboard window."""

    def __init__(self, app: "GTABusinessManager", overlay: Optional["OverlayWindow"] = None, parent=None):
        """Initialize main window.

        Args:
            app: Main application instance
            overlay: Optional overlay window reference
            parent: Parent widget
        """
        super().__init__(parent)
        self._app = app
        self._overlay = overlay

        self.setWindowTitle(f"GTA Business Manager v{__version__}")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)

        # Apply dark theme
        self.setStyleSheet(DarkTheme.get_stylesheet())

        self._setup_menu()
        self._setup_ui()
        self._setup_status_bar()
        self._setup_update_timer()

        # Connect to app events
        self._app.on_state_change(self._on_game_state_change)

        logger.info("Main window initialized")

    def _setup_menu(self) -> None:
        """Setup the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        reset_action = QAction("&Reset Session", self)
        reset_action.setShortcut("Ctrl+R")
        reset_action.triggered.connect(self._reset_session)
        file_menu.addAction(reset_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        overlay_action = QAction("Toggle &Overlay", self)
        overlay_action.setShortcut("Ctrl+O")
        overlay_action.triggered.connect(self._toggle_overlay)
        view_menu.addAction(overlay_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_ui(self) -> None:
        """Setup the main UI."""
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top info bar
        self._setup_info_bar(layout)

        # Tab widget for different views
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs)

        # Dashboard tab (overview)
        self._dashboard = DashboardWidget(self._app, self)
        self._tabs.addTab(self._dashboard, "Dashboard")

        # Session tab
        self._session_panel = SessionPanel(self._app, self)
        self._tabs.addTab(self._session_panel, "Session")

        # Businesses tab
        self._business_panel = BusinessPanel(self._app, self)
        self._tabs.addTab(self._business_panel, "Businesses")

        # Activities tab
        self._activity_panel = ActivityPanel(self._app, self)
        self._tabs.addTab(self._activity_panel, "Activities")

        # Recommendations tab
        self._recommendations = RecommendationsPanel(self._app, self)
        self._tabs.addTab(self._recommendations, "Recommendations")

        # Settings tab
        self._settings_panel = SettingsPanel(self._app, self)
        self._tabs.addTab(self._settings_panel, "Settings")

    def _setup_info_bar(self, parent_layout: QVBoxLayout) -> None:
        """Setup the top information bar."""
        bar = QWidget()
        bar.setStyleSheet("background-color: #0f3460; padding: 8px;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 8, 16, 8)

        # Status indicator
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #4CAF50; font-size: 16px;")
        bar_layout.addWidget(self._status_dot)

        self._status_label = QLabel("Running")
        self._status_label.setStyleSheet("color: white; font-weight: bold;")
        bar_layout.addWidget(self._status_label)

        bar_layout.addSpacing(30)

        # Money display
        money_icon = QLabel("$")
        money_icon.setStyleSheet("color: #4CAF50; font-size: 18px; font-weight: bold;")
        bar_layout.addWidget(money_icon)

        self._money_label = QLabel("--")
        self._money_label.setStyleSheet("color: #4CAF50; font-size: 18px; font-weight: bold;")
        bar_layout.addWidget(self._money_label)

        bar_layout.addSpacing(30)

        # Session earnings
        session_icon = QLabel("+")
        session_icon.setStyleSheet("color: #FFD700; font-size: 14px;")
        bar_layout.addWidget(session_icon)

        self._session_label = QLabel("$0")
        self._session_label.setStyleSheet("color: #FFD700; font-size: 14px;")
        bar_layout.addWidget(self._session_label)

        bar_layout.addStretch()

        # Game state
        self._state_label = QLabel("IDLE")
        self._state_label.setStyleSheet(
            "color: #AAA; font-size: 12px; background-color: #1a1a2e; "
            "padding: 4px 12px; border-radius: 4px;"
        )
        bar_layout.addWidget(self._state_label)

        parent_layout.addWidget(bar)

    def _setup_status_bar(self) -> None:
        """Setup the status bar."""
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # Performance info on right side
        self._perf_label = QLabel("")
        self._status_bar.addPermanentWidget(self._perf_label)

        self._status_bar.showMessage("Ready")

    def _setup_update_timer(self) -> None:
        """Setup timer for UI updates."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_ui)
        self._update_timer.start(500)  # Update every 500ms

    def _update_ui(self) -> None:
        """Update UI with current data."""
        if not self._app:
            return

        # Update status
        if self._app.is_running:
            self._status_dot.setStyleSheet("color: #4CAF50; font-size: 16px;")
            self._status_label.setText("Running")
        elif self._app.state.name == "PAUSED":
            self._status_dot.setStyleSheet("color: #FFD700; font-size: 16px;")
            self._status_label.setText("Paused")
        else:
            self._status_dot.setStyleSheet("color: #AAA; font-size: 16px;")
            self._status_label.setText(self._app.state.name)

        # Update money display
        money = self._app.current_money
        if money is not None:
            self._money_label.setText(format_money_short(money))
        else:
            self._money_label.setText("--")

        # Update session earnings
        earnings = self._app.session_earnings
        self._session_label.setText(f"+{format_money_short(earnings)}")

        # Update game state
        state = self._app.game_state.name.replace("_", " ")
        self._state_label.setText(state)

        # Update performance info
        metrics = self._app.performance_metrics
        if metrics:
            self._perf_label.setText(
                f"FPS: {metrics.captures_per_second:.1f} | "
                f"CPU: {metrics.cpu_percent:.1f}% | "
                f"RAM: {metrics.memory_mb:.0f}MB"
            )

    def _on_game_state_change(self, from_state, to_state) -> None:
        """Handle game state changes."""
        # Update state label with color
        state_colors = {
            "IDLE": "#AAA",
            "MISSION_ACTIVE": "#4CAF50",
            "SELLING": "#FF9800",
            "MISSION_COMPLETE": "#4CAF50",
            "MISSION_FAILED": "#F44336",
            "LOADING": "#2196F3",
        }
        color = state_colors.get(to_state.name, "#AAA")
        self._state_label.setStyleSheet(
            f"color: {color}; font-size: 12px; background-color: #1a1a2e; "
            "padding: 4px 12px; border-radius: 4px;"
        )

    def _reset_session(self) -> None:
        """Reset the current session."""
        self._app.reset_session()
        self._status_bar.showMessage("Session reset", 3000)

    def _toggle_overlay(self) -> None:
        """Toggle the overlay window."""
        if self._overlay:
            if self._overlay.isVisible():
                self._overlay.hide()
                self._status_bar.showMessage("Overlay hidden", 2000)
            else:
                self._overlay.show()
                self._status_bar.showMessage("Overlay shown", 2000)
        else:
            self._status_bar.showMessage("No overlay available", 2000)

    def set_overlay(self, overlay: "OverlayWindow") -> None:
        """Set the overlay window reference.

        Args:
            overlay: The overlay window instance
        """
        self._overlay = overlay

    def _show_about(self) -> None:
        """Show about dialog."""
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.about(
            self,
            "About GTA Business Manager",
            f"GTA Business Manager v{__version__}\n\n"
            "A screen capture and OCR-based gameplay tracker\n"
            "for GTA Online.\n\n"
            "Monitor your money, track activities, and get\n"
            "optimized workflow recommendations.\n\n"
            "This tool uses only screen capture and OCR.\n"
            "It does not modify any game files."
        )

    def closeEvent(self, event) -> None:
        """Handle window close."""
        # Just hide instead of closing (tray keeps running)
        event.ignore()
        self.hide()
