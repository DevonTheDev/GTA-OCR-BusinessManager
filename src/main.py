"""Entry point for GTA Business Manager."""

import atexit
import sys
import signal
import threading
from typing import Optional

from .config.settings import get_settings
from .utils.logging import setup_logging, get_logger
from .app import GTABusinessManager


# Global app reference for cleanup handlers
_app_instance: Optional[GTABusinessManager] = None
_cleanup_done = threading.Event()


def _cleanup() -> None:
    """Cleanup handler called on exit.

    Ensures app.stop() is called even on unexpected exits.
    """
    global _app_instance
    if _cleanup_done.is_set():
        return

    _cleanup_done.set()

    if _app_instance is not None:
        logger = get_logger("main")
        try:
            if _app_instance.is_running:
                logger.info("Performing cleanup shutdown...")
                _app_instance.stop()
                logger.info("Cleanup complete")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


def _exception_handler(exc_type, exc_value, exc_tb):
    """Handle uncaught exceptions."""
    logger = get_logger("main")
    if issubclass(exc_type, KeyboardInterrupt):
        # Normal exit on Ctrl+C
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    logger.critical(
        "Uncaught exception",
        exc_info=(exc_type, exc_value, exc_tb)
    )
    _cleanup()


# Register cleanup on atexit
atexit.register(_cleanup)

# Install exception handler
sys.excepthook = _exception_handler


def main(args: Optional[list[str]] = None) -> int:
    """Main entry point."""
    if args is None:
        args = sys.argv[1:]

    # Parse arguments
    debug_mode = "--debug" in args
    no_gui = "--no-gui" in args
    console_only = "--console" in args
    no_overlay = "--no-overlay" in args

    # Setup logging
    settings = get_settings()
    log_level = 10 if debug_mode else 20
    setup_logging(level=log_level)

    logger = get_logger("main")
    logger.info("GTA Business Manager starting...")
    logger.info(f"Config path: {settings.config_path}")

    # Check dependencies
    if not _check_dependencies():
        logger.error("Missing required dependencies")
        return 1

    # Create application
    global _app_instance
    app = GTABusinessManager(settings)
    _app_instance = app

    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info("Shutdown signal received")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if console_only or no_gui:
        return _run_console_mode(app, debug_mode)
    else:
        return _run_gui_mode(app, debug_mode, show_overlay=not no_overlay)


def _check_dependencies() -> bool:
    """Check if required dependencies are available."""
    logger = get_logger("main")
    all_ok = True

    deps = [
        ("mss", "mss"),
        ("cv2", "opencv-python"),
        ("PIL", "Pillow"),
        ("numpy", "numpy"),
        ("yaml", "PyYAML"),
        ("sqlalchemy", "SQLAlchemy"),
    ]

    for module, package in deps:
        try:
            __import__(module)
            logger.debug(f"{module}: OK")
        except ImportError:
            logger.error(f"{package} not installed")
            all_ok = False

    # Optional but recommended
    try:
        import winocr
        logger.debug("winocr: OK")
    except ImportError:
        logger.warning("winocr not installed - OCR features limited")

    try:
        from PyQt6 import QtWidgets
        logger.debug("PyQt6: OK")
    except ImportError:
        logger.warning("PyQt6 not installed - GUI unavailable")

    try:
        import keyboard
        logger.debug("keyboard: OK")
    except ImportError:
        logger.warning("keyboard not installed - hotkeys disabled")

    return all_ok


def _run_console_mode(app: GTABusinessManager, debug: bool) -> int:
    """Run in console-only mode."""
    logger = get_logger("main")
    logger.info("Running in console mode")

    def on_capture(result):
        if result.money and result.money.has_value:
            money = result.money.display_value
            earnings = app.session_earnings
            state = result.game_state.name
            print(
                f"\rMoney: ${money:,} | Session: +${earnings:,} | State: {state} | "
                f"OCR: {result.ocr_time_ms:.0f}ms",
                end="",
                flush=True,
            )

    app.on_capture(on_capture)

    if not app.start():
        logger.error("Failed to start application")
        return 1

    logger.info("Press Ctrl+C to stop")

    try:
        import time
        while app.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    app.stop()
    print()
    logger.info("Application stopped")
    return 0


def _run_gui_mode(app: GTABusinessManager, debug: bool, show_overlay: bool = True) -> int:
    """Run with GUI."""
    logger = get_logger("main")

    try:
        from PyQt6.QtWidgets import QApplication
        from .ui.system_tray import SystemTray
        from .ui.main_window import MainWindow
        from .ui.overlay import OverlayWindow
        from .ui.styles.dark_theme import DarkTheme
    except ImportError:
        logger.error("PyQt6 not available")
        return _run_console_mode(app, debug)

    logger.info("Starting GUI mode")

    # Create Qt application
    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setStyle("Fusion")

    # Create overlay first
    overlay = None
    if show_overlay:
        overlay = OverlayWindow(app)
        settings = get_settings()
        overlay.set_position(settings.get("display.overlay_position", "top-right"))
        overlay.set_opacity(settings.get("display.overlay_opacity", 0.85))

    # Create main window with overlay reference
    main_window = MainWindow(app, overlay=overlay)

    # Create system tray
    tray = SystemTray(app, main_window=main_window, overlay=overlay)
    tray.show()

    # Setup hotkeys
    _setup_hotkeys(app, main_window, overlay, tray)

    # Show based on settings
    display_mode = get_settings().get("display.mode", "overlay")
    if display_mode == "window":
        main_window.show()
    elif display_mode == "both":
        main_window.show()
        if overlay:
            overlay.show()
    elif overlay:
        overlay.show()

    # Start capture
    app.start()

    # Run Qt event loop
    exit_code = qt_app.exec()

    # Cleanup
    app.stop()

    return exit_code


def _setup_hotkeys(app, main_window, overlay, tray):
    """Setup global hotkeys."""
    try:
        from .hotkeys import get_hotkey_manager
    except ImportError:
        return

    hotkeys = get_hotkey_manager()
    if not hotkeys.is_available:
        return

    def toggle_overlay():
        if overlay:
            overlay.setVisible(not overlay.isVisible())

    def toggle_tracking():
        if app.is_running:
            app.pause()
        else:
            app.resume()

    def show_window():
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()

    hotkeys.register("toggle_overlay", toggle_overlay)
    hotkeys.register("toggle_tracking", toggle_tracking)
    hotkeys.register("show_window", show_window)
    hotkeys.start()


if __name__ == "__main__":
    sys.exit(main())
