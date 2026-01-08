"""System tray icon and menu for GTA Business Manager."""

from typing import Optional, TYPE_CHECKING

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import QTimer, pyqtSignal, QObject

from ..utils.logging import get_logger
from ..utils.helpers import format_money_short

if TYPE_CHECKING:
    from ..app import GTABusinessManager
    from .main_window import MainWindow
    from .overlay import OverlayWindow


logger = get_logger("ui.tray")


class TraySignals(QObject):
    """Signals for thread-safe UI updates."""

    update_tooltip = pyqtSignal(str)
    show_notification = pyqtSignal(str, str)


class SystemTray(QSystemTrayIcon):
    """System tray icon with status and controls."""

    def __init__(
        self,
        app: "GTABusinessManager",
        main_window: Optional["MainWindow"] = None,
        overlay: Optional["OverlayWindow"] = None,
        parent=None
    ):
        super().__init__(parent)
        self._app = app
        self._main_window = main_window
        self._overlay = overlay
        self._signals = TraySignals()

        self._create_icon()
        self._create_menu()

        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_status)
        self._update_timer.start(1000)

        self._signals.update_tooltip.connect(self.setToolTip)
        self._signals.show_notification.connect(self._show_notification)

        self.setToolTip("GTA Business Manager\nStarting...")
        self._app.on_money_change(self._on_money_change)

        logger.info("System tray initialized")

    def _create_icon(self) -> None:
        """Create the tray icon."""
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setBrush(QColor(0, 200, 0))
        painter.setPen(QColor(0, 150, 0))
        painter.drawEllipse(2, 2, 28, 28)

        painter.setPen(QColor(255, 255, 255))
        font = QFont("Arial", 16, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), 0x84, "$")

        painter.end()

        self.setIcon(QIcon(pixmap))

    def _create_menu(self) -> None:
        """Create the context menu."""
        menu = QMenu()

        # Status
        self._status_action = QAction("Status: Starting...", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)

        self._money_action = QAction("Money: --", menu)
        self._money_action.setEnabled(False)
        menu.addAction(self._money_action)

        self._earnings_action = QAction("Session: $0", menu)
        self._earnings_action.setEnabled(False)
        menu.addAction(self._earnings_action)

        menu.addSeparator()

        # Pause/Resume
        self._pause_action = QAction("Pause Tracking", menu)
        self._pause_action.triggered.connect(self._toggle_pause)
        menu.addAction(self._pause_action)

        # Reset session
        reset_action = QAction("Reset Session", menu)
        reset_action.triggered.connect(self._reset_session)
        menu.addAction(reset_action)

        menu.addSeparator()

        # Show dashboard
        show_action = QAction("Show Dashboard", menu)
        show_action.triggered.connect(self._show_dashboard)
        menu.addAction(show_action)

        # Toggle overlay
        if self._overlay:
            self._overlay_action = QAction("Hide Overlay", menu)
            self._overlay_action.triggered.connect(self._toggle_overlay)
            menu.addAction(self._overlay_action)

        menu.addSeparator()

        # Settings
        settings_action = QAction("Settings...", menu)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        # Quit
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _update_status(self) -> None:
        """Update tray icon status."""
        if not self._app:
            return

        # Update status text
        if self._app.is_running:
            self._status_action.setText("Status: Running")
            self._pause_action.setText("Pause Tracking")
        elif self._app.state.name == "PAUSED":
            self._status_action.setText("Status: Paused")
            self._pause_action.setText("Resume Tracking")
        else:
            self._status_action.setText(f"Status: {self._app.state.name}")

        # Update money
        money = self._app.current_money
        if money is not None:
            self._money_action.setText(f"Money: {format_money_short(money)}")
        else:
            self._money_action.setText("Money: --")

        # Update session
        earnings = self._app.session_earnings
        self._earnings_action.setText(f"Session: +{format_money_short(earnings)}")

        # Update overlay action text
        if self._overlay and hasattr(self, '_overlay_action'):
            if self._overlay.isVisible():
                self._overlay_action.setText("Hide Overlay")
            else:
                self._overlay_action.setText("Show Overlay")

        # Update tooltip
        tooltip_lines = [
            "GTA Business Manager",
            f"Status: {'Running' if self._app.is_running else self._app.state.name}",
        ]
        if money is not None:
            tooltip_lines.append(f"Money: {format_money_short(money)}")
        tooltip_lines.append(f"Session: +{format_money_short(earnings)}")

        self.setToolTip("\n".join(tooltip_lines))

    def _on_money_change(self, reading, change: int) -> None:
        """Handle money change event."""
        if change >= 10000:
            self._signals.show_notification.emit(
                "Money Received",
                f"+{format_money_short(change)}"
            )

    def _show_notification(self, title: str, message: str) -> None:
        """Show a system notification."""
        if self.supportsMessages():
            self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3000)

    def _toggle_pause(self) -> None:
        """Toggle pause state."""
        if self._app.is_running:
            self._app.pause()
        else:
            self._app.resume()

    def _reset_session(self) -> None:
        """Reset session tracking."""
        self._app.reset_session()
        self._show_notification("Session Reset", "Session earnings reset")

    def _show_dashboard(self) -> None:
        """Show the main dashboard window."""
        if self._main_window:
            self._main_window.show()
            self._main_window.raise_()
            self._main_window.activateWindow()

    def _toggle_overlay(self) -> None:
        """Toggle overlay visibility."""
        if self._overlay:
            self._overlay.setVisible(not self._overlay.isVisible())

    def _show_settings(self) -> None:
        """Show settings in dashboard."""
        if self._main_window:
            self._main_window.show()
            self._main_window.raise_()
            # Switch to settings tab (index 5)
            if hasattr(self._main_window, '_tabs'):
                self._main_window._tabs.setCurrentIndex(5)

    def _quit(self) -> None:
        """Quit the application."""
        logger.info("Quit requested")
        self._app.stop()
        QApplication.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_dashboard()
