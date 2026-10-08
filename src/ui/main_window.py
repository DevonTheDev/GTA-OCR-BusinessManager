"""Main application window for GTA Business Manager."""

from dataclasses import dataclass
from html import escape
from queue import Empty, Queue
from threading import Thread
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
from .widgets.history_panel import SessionHistoryPanel
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


def export_detection_sample(sample, destination_directory):
    """Import the detached local writer only when an explicit save is admitted."""
    from ..detection.detection_sample import export_detection_sample as export

    return export(sample, destination_directory)


def _write_detection_sample(sample, destination_directory, results: Queue) -> None:
    """One bounded disk job. This function has no application or Qt access."""
    try:
        results.put(export_detection_sample(sample, destination_directory))
    except Exception:
        # Never pass arbitrary backend exceptions or OCR text to the UI.
        results.put(None)


@dataclass
class _DetectionSampleSaveJob:
    token: object
    sample_id: str
    captured_at: str
    destination_directory: str
    results: Queue
    thread: Optional[Thread] = None


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
        self._sample_dialog = None
        self._sample_export_job = None

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
        file_menu.setToolTipsVisible(True)

        reset_action = QAction("&Reset Session", self)
        reset_action.setShortcut("Ctrl+R")
        reset_action.triggered.connect(self._reset_session)
        file_menu.addAction(reset_action)

        self._sample_action = QAction("Save next detection sample…", self)
        self._sample_action.setObjectName("save_detection_sample")
        self._sample_action.triggered.connect(self._on_detection_sample_action)
        file_menu.addAction(self._sample_action)

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
        self._setup_detection_sample_bar(layout)

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

        self._history_panel = SessionHistoryPanel(self._app, self)
        self._tabs.addTab(self._history_panel, "History")

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
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self._history_panel:
            self._history_panel.refresh()

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

    def _setup_detection_sample_bar(self, parent_layout: QVBoxLayout) -> None:
        """Keep sample readiness local; polling never opens a dialog."""
        from PyQt6.QtWidgets import QPlainTextEdit, QSizePolicy

        bar = self._sample_bar = QWidget(self)
        bar.setObjectName("detection_sample_bar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(3)
        self._sample_status = QLabel("Detection sample: not armed")
        self._sample_status.setObjectName("detection_sample_status")
        self._sample_guidance = QLabel("")
        self._sample_guidance.setObjectName("detection_sample_guidance")
        for label in (self._sample_status, self._sample_guidance):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setMaximumHeight(48)
            layout.addWidget(label)
        self._sample_export_result = QPlainTextEdit()
        self._sample_export_result.setObjectName("detection_sample_export_result")
        self._sample_export_result.setReadOnly(True)
        self._sample_export_result.setMaximumHeight(76)
        self._sample_export_result.hide()
        layout.addWidget(self._sample_export_result)
        parent_layout.addWidget(bar)
        bar.hide()

    def _detection_sample_status(self):
        getter = getattr(self._app, "get_detection_sample_status", None)
        if not callable(getter):
            return None
        status = getter()
        # Older embedders and panel test doubles need not implement this feature.
        if not isinstance(getattr(status, "state", None), str):
            return None
        return status

    def _detection_sample_guidance(self) -> str:
        from .. import hotkeys

        # Do not construct a manager or register a new hook for these instructions.
        manager = hotkeys._hotkey_manager
        binding = None
        if manager is not None and manager.is_running:
            bindings = manager.registered_hotkeys
            if isinstance(bindings, dict):
                binding = bindings.get("toggle_tracking")
        if isinstance(binding, str) and binding:
            return (
                "On one monitor: pause capture in Activities, arm the sample, return to GTA, "
                f"then press {binding} to resume capture. Return here to save."
            )
        return (
            "On one monitor: pause capture in Activities, arm the sample, then return to GTA. "
            "No registered resume shortcut is active; on-screen Resume may capture the "
            "foreground manager. Return here to save."
        )

    def _update_detection_sample_ui(self) -> None:
        self._poll_detection_sample_export()
        guidance = self._detection_sample_guidance()
        self._sample_guidance.setText(guidance)
        # Tooltips have no PlainText switch: escape every displayed character.
        self._sample_action.setToolTip(f"<qt>{escape(guidance)}</qt>")
        self._sample_action.setStatusTip(guidance)
        status = self._detection_sample_status()
        self._sample_bar.setVisible(
            bool(self._sample_export_result.toPlainText())
            or (status is not None and status.state not in {"idle", "unavailable"})
        )
        if status is None:
            self._sample_action.setEnabled(False)
            self._sample_status.setText("Detection samples unavailable")
            return
        state = status.state
        occupied = self._sample_export_job is not None or self._sample_dialog is not None
        eligible = self._app.state.name in {"RUNNING", "PAUSED"}
        if state in {"armed", "collecting"}:
            self._sample_action.setText("Cancel detection sample")
            self._sample_action.setEnabled(not occupied)
        elif state == "ready":
            self._sample_action.setText("Save captured sample…")
            self._sample_action.setEnabled(not occupied)
        elif state == "saving":
            self._sample_action.setText("Saving detection sample…")
            self._sample_action.setEnabled(False)
        else:
            self._sample_action.setText("Save next detection sample…")
            self._sample_action.setEnabled(eligible and not occupied and state != "unavailable")
        identity = f"Sample {status.sample_id}" if status.sample_id else "Detection sample"
        if state == "armed":
            text = f"{identity}: armed for the next normal capture; paused capture waits for Resume."
        elif state == "collecting":
            text = f"{identity}: collecting its admitted capture."
        elif state == "ready":
            text = f"{identity}: captured {status.captured_at}. Ready to save from File."
        elif state == "saving":
            text = f"{identity}: captured {status.captured_at}. Saving locally; this write cannot be cancelled."
        elif state == "saved":
            text = f"{identity}: captured {status.captured_at}. Saved locally."
        elif state == "failed":
            text = f"{identity}: failed. {status.message}"
        elif state == "unavailable":
            text = f"Detection sample unavailable. {status.message}"
        else:
            text = "Detection sample: not armed. File → Save next detection sample…"
        self._sample_status.setText(text)

    def _on_detection_sample_action(self) -> None:
        if self._sample_dialog is not None or self._sample_export_job is not None:
            return
        status = self._detection_sample_status()
        if status is None:
            return
        if status.state in {"armed", "collecting"}:
            self._app.cancel_detection_sample(status.token)
        elif status.state == "ready":
            self._open_detection_sample_save(status)
        elif status.state not in {"saving", "unavailable"} and self._app.state.name in {"RUNNING", "PAUSED"}:
            self._app.request_detection_sample()
        self._update_detection_sample_ui()

    def _open_detection_sample_save(self, status) -> None:
        from PyQt6.QtWidgets import QDialog, QPlainTextEdit, QPushButton

        dialog = QDialog(self)
        dialog.setObjectName("detection_sample_save")
        dialog.setWindowTitle("Save captured detection sample")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.resize(560, 280)
        layout = QVBoxLayout(dialog)
        disclosure = QLabel(
            "Saves the full captured screen, which can include other visible content. "
            "Saved locally; nothing is uploaded. Choose an existing parent folder; "
            "a new sample folder will be created inside it."
        )
        disclosure.setObjectName("detection_sample_disclosure")
        disclosure.setTextFormat(Qt.TextFormat.PlainText)
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        identity = QPlainTextEdit()
        identity.setObjectName("detection_sample_identity")
        identity.setReadOnly(True)
        identity.setPlainText(f"Sample ID: {status.sample_id}\nCaptured at: {status.captured_at}")
        identity.setMaximumHeight(110)
        layout.addWidget(identity)
        error = QLabel("")
        error.setObjectName("detection_sample_save_error")
        error.setTextFormat(Qt.TextFormat.PlainText)
        error.setWordWrap(True)
        error.hide()
        layout.addWidget(error)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel sample")
        cancel.setObjectName("detection_sample_cancel")
        cancel.setDefault(True)
        choose = QPushButton("Choose folder and save")
        choose.setObjectName("detection_sample_save_choose")
        choose.setAutoDefault(False)
        buttons.addStretch()
        buttons.addWidget(choose)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        cancel.clicked.connect(dialog.reject)
        choose.clicked.connect(lambda: self._choose_detection_sample_destination(dialog, status, choose))
        dialog.finished.connect(lambda result: self._finish_detection_sample_dialog(dialog, status.token, result))
        self._sample_dialog = dialog
        dialog.open()
        cancel.setFocus()

    def _finish_detection_sample_dialog(self, dialog, token, result: int) -> None:
        from PyQt6.QtWidgets import QDialog

        if self._sample_dialog is dialog:
            self._sample_dialog = None
        if result != QDialog.DialogCode.Accepted:
            self._app.cancel_detection_sample(token)
        dialog.deleteLater()
        self._update_detection_sample_ui()

    def _choose_detection_sample_destination(self, dialog, status, button) -> None:
        from PyQt6.QtWidgets import QFileDialog

        if self._sample_dialog is not dialog or not button.isEnabled():
            return
        button.setEnabled(False)
        try:
            directory = QFileDialog.getExistingDirectory(
                dialog, "Choose existing parent folder for this sample", "",
                QFileDialog.Option.ShowDirsOnly,
            )
        except Exception:
            if self._sample_dialog is dialog:
                button.setEnabled(True)
                error = dialog.findChild(QLabel, "detection_sample_save_error")
                error.setText("Could not open the folder chooser. Try again or cancel this sample.")
                error.show()
            return
        # The nested dialog can outlive cancellation, Stop or a new run.
        if self._sample_dialog is not dialog:
            return
        if not directory:
            dialog.reject()
            return
        sample = self._app.claim_detection_sample_for_save(status.token)
        if sample is None:
            dialog.reject()
            return
        self._start_detection_sample_export(status.token, sample, directory)
        dialog.accept()

    def _start_detection_sample_export(self, token, sample, directory) -> None:
        results = Queue(maxsize=1)
        job = _DetectionSampleSaveJob(
            token, sample.sample_id, sample.captured_at, directory, results,
        )
        self._sample_export_job = job
        self._sample_export_result.setPlainText(
            f"Saving sample {sample.sample_id}\nCaptured at: {sample.captured_at}\n"
            "A started write cannot be cancelled."
        )
        self._sample_export_result.show()
        try:
            job.thread = Thread(target=_write_detection_sample, args=(sample, directory, results),
                                name="detection-sample-export", daemon=True)
            job.thread.start()
        except Exception:
            from ..detection.detection_sample import DetectionSampleExport

            results.put(DetectionSampleExport(False, None, (), (),
                                               "Could not start the save worker; no files were written."))

    def _poll_detection_sample_export(self) -> None:
        job = self._sample_export_job
        if job is None or (job.thread is not None and job.thread.is_alive()):
            return
        try:
            result = job.results.get_nowait()
        except Empty:
            return
        unknown_output = result is None
        if unknown_output:
            from ..detection.detection_sample import DetectionSampleExport

            result = DetectionSampleExport(False, None, (), (),
                                           "Export outcome unknown; inspect the chosen folder for incomplete output.")
        self._sample_export_job = None
        self._app.complete_detection_sample_save(job.token, result)
        heading = "Saved sample" if result.success else "Sample not saved"
        lines = [f"{heading}: {job.sample_id}", f"Captured at: {job.captured_at}"]
        if result.path:
            lines.append(f"Folder: {result.path}")
        if unknown_output:
            lines.extend((f"Chosen parent: {job.destination_directory}",
                          "Completed and partial files: unknown"))
        elif not result.success:
            lines.extend((f"Completed files: {', '.join(result.completed_files) or 'none'}",
                          f"Partial files: {', '.join(result.partial_files) or 'none reported'}"))
        if result.message:
            lines.append(result.message)
        self._sample_export_result.setPlainText("\n".join(lines))
        self._sample_export_result.show()

    def _setup_update_timer(self) -> None:
        """Setup timer for UI updates."""
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_ui)
        self._update_timer.start(500)  # Update every 500ms

    def _update_ui(self) -> None:
        """Update UI with current data."""
        if not self._app:
            return

        self._update_detection_sample_ui()

        # App-side Stop/reset changes should appear on the normal UI cadence.
        self._business_panel._sync_business_screen_target()

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
