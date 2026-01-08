"""Global hotkey handling for GTA Business Manager."""

import threading
from typing import Callable, Dict, Optional

from .utils.logging import get_logger
from .config.settings import get_settings


logger = get_logger("hotkeys")


class HotkeyManager:
    """Manages global hotkeys for the application."""

    def __init__(self):
        """Initialize hotkey manager."""
        self._settings = get_settings()
        self._callbacks: Dict[str, Callable] = {}
        self._registered_hotkeys: Dict[str, str] = {}
        self._keyboard_available = False
        self._running = False

        self._check_keyboard()

    def _check_keyboard(self) -> None:
        """Check if keyboard library is available."""
        try:
            import keyboard
            self._keyboard_available = True
            logger.info("Keyboard hotkeys available")
        except ImportError:
            logger.warning("keyboard library not installed - hotkeys disabled")
            self._keyboard_available = False

    def register(self, action: str, callback: Callable) -> bool:
        """Register a callback for an action.

        Args:
            action: Action name (e.g., "toggle_overlay")
            callback: Function to call when hotkey pressed

        Returns:
            True if registered successfully
        """
        if not self._keyboard_available:
            return False

        self._callbacks[action] = callback
        return True

    def start(self) -> bool:
        """Start listening for hotkeys.

        Returns:
            True if started successfully
        """
        if not self._keyboard_available or self._running:
            return False

        try:
            import keyboard

            # Get hotkey bindings from settings
            hotkey_settings = self._settings.get_section("hotkeys")

            for action, hotkey in hotkey_settings.items():
                if action in self._callbacks and hotkey:
                    try:
                        keyboard.add_hotkey(hotkey, self._callbacks[action])
                        self._registered_hotkeys[action] = hotkey
                        logger.debug(f"Registered hotkey: {hotkey} -> {action}")
                    except Exception as e:
                        logger.error(f"Failed to register hotkey {hotkey}: {e}")

            self._running = True
            logger.info(f"Hotkey manager started with {len(self._registered_hotkeys)} hotkeys")
            return True

        except Exception as e:
            logger.error(f"Failed to start hotkey manager: {e}")
            return False

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        if not self._keyboard_available or not self._running:
            return

        try:
            import keyboard

            for action, hotkey in self._registered_hotkeys.items():
                try:
                    keyboard.remove_hotkey(hotkey)
                except Exception as e:
                    logger.debug(f"Could not remove hotkey {hotkey} for {action}: {e}")

            self._registered_hotkeys.clear()
            self._running = False
            logger.info("Hotkey manager stopped")

        except Exception as e:
            logger.error(f"Error stopping hotkey manager: {e}")

    def update_hotkey(self, action: str, new_hotkey: str) -> bool:
        """Update a hotkey binding.

        Args:
            action: Action name
            new_hotkey: New hotkey string

        Returns:
            True if updated successfully
        """
        if not self._keyboard_available:
            return False

        import keyboard

        # Remove old binding
        if action in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(self._registered_hotkeys[action])
            except Exception as e:
                logger.debug(f"Could not remove old hotkey for {action}: {e}")

        # Add new binding
        if action in self._callbacks and new_hotkey:
            try:
                keyboard.add_hotkey(new_hotkey, self._callbacks[action])
                self._registered_hotkeys[action] = new_hotkey
                self._settings.set(f"hotkeys.{action}", new_hotkey)
                logger.debug(f"Updated hotkey: {new_hotkey} -> {action}")
                return True
            except Exception as e:
                logger.error(f"Failed to set hotkey {new_hotkey}: {e}")

        return False

    @property
    def is_available(self) -> bool:
        """Check if hotkeys are available."""
        return self._keyboard_available

    @property
    def is_running(self) -> bool:
        """Check if hotkey listener is running."""
        return self._running

    @property
    def registered_hotkeys(self) -> Dict[str, str]:
        """Get registered hotkeys."""
        return self._registered_hotkeys.copy()


# Global instance
_hotkey_manager: Optional[HotkeyManager] = None


def get_hotkey_manager() -> HotkeyManager:
    """Get the global hotkey manager instance."""
    global _hotkey_manager
    if _hotkey_manager is None:
        _hotkey_manager = HotkeyManager()
    return _hotkey_manager
