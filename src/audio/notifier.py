"""Audio notification system for GTA Business Manager."""

from datetime import datetime, timezone
from typing import Dict, Optional, Callable
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass
import threading
import winsound

from ..constants import NOTIFICATION
from ..utils.logging import get_logger


logger = get_logger("audio")


class NotificationType(Enum):
    """Types of notifications."""
    # Money events
    MONEY_SMALL = auto()  # < $50K
    MONEY_MEDIUM = auto()  # $50K - $200K
    MONEY_LARGE = auto()  # $200K - $1M
    MONEY_HUGE = auto()  # > $1M

    # Activity events
    MISSION_PASSED = auto()
    MISSION_FAILED = auto()
    COOLDOWN_READY = auto()
    ACTIVITY_AVAILABLE = auto()

    # Business events
    BUSINESS_READY = auto()  # Business full
    SUPPLIES_LOW = auto()
    SUPPLIES_EMPTY = auto()
    NIGHTCLUB_SAFE_FULL = auto()

    # Goal events
    GOAL_PROGRESS = auto()  # Milestone reached
    GOAL_COMPLETE = auto()

    # Alerts
    AFK_WARNING = auto()
    SESSION_MILESTONE = auto()  # E.g., 1 hour played


@dataclass
class NotificationEvent:
    """Represents a notification event."""
    type: NotificationType
    title: str
    message: str
    value: int = 0  # Associated money value if any


class AudioNotifier:
    """Handles audio notifications with rate limiting and Windows sounds."""

    # Windows system sounds (always available)
    SYSTEM_SOUNDS = {
        NotificationType.MONEY_SMALL: ("SystemAsterisk", 1),
        NotificationType.MONEY_MEDIUM: ("SystemExclamation", 1),
        NotificationType.MONEY_LARGE: ("SystemHand", 1),
        NotificationType.MONEY_HUGE: ("SystemHand", 2),
        NotificationType.MISSION_PASSED: ("SystemAsterisk", 1),
        NotificationType.MISSION_FAILED: ("SystemHand", 1),
        NotificationType.COOLDOWN_READY: ("SystemExclamation", 1),
        NotificationType.BUSINESS_READY: ("SystemExclamation", 1),
        NotificationType.SUPPLIES_EMPTY: ("SystemHand", 1),
        NotificationType.GOAL_COMPLETE: ("SystemAsterisk", 2),
        NotificationType.GOAL_PROGRESS: ("SystemAsterisk", 1),
        NotificationType.AFK_WARNING: ("SystemQuestion", 1),
    }

    def __init__(self, sounds_dir: Optional[Path] = None, enabled: bool = False):
        """Initialize audio notifier.

        Args:
            sounds_dir: Directory containing sound files
            enabled: Whether audio is enabled
        """
        self._sounds_dir = sounds_dir
        self._enabled = enabled
        self._volume = NOTIFICATION.DEFAULT_VOLUME
        self._playsound_available = self._check_playsound()
        self._use_system_sounds = True  # Use Windows sounds as fallback
        self._last_played: Dict[str, datetime] = {}
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[NotificationEvent], None]] = []

        # Notification preferences (which types to play)
        self._enabled_types: set[NotificationType] = set(NotificationType)

    def _check_playsound(self) -> bool:
        """Check if playsound is available."""
        try:
            import playsound
            return True
        except ImportError:
            logger.info("playsound not installed - audio notifications disabled")
            return False

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable audio notifications.

        Args:
            enabled: Whether to enable
        """
        self._enabled = enabled

    def set_volume(self, volume: float) -> None:
        """Set notification volume.

        Args:
            volume: Volume level (0.0-1.0)
        """
        self._volume = max(0.0, min(NOTIFICATION.MAX_VOLUME, volume))

    def _can_play(self, notification_key: str, cooldown: float = None) -> bool:
        """Check if a notification can be played based on rate limiting.

        Args:
            notification_key: Unique key for this notification type
            cooldown: Cooldown period in seconds (default from constants)

        Returns:
            True if notification can be played
        """
        if cooldown is None:
            cooldown = NOTIFICATION.COOLDOWN_SAME_TYPE

        now = datetime.now(timezone.utc)

        with self._lock:
            last_time = self._last_played.get(notification_key)
            if last_time is not None:
                elapsed = (now - last_time).total_seconds()
                if elapsed < cooldown:
                    logger.debug(
                        f"Rate limited notification '{notification_key}' "
                        f"(cooldown: {cooldown - elapsed:.1f}s remaining)"
                    )
                    return False

            self._last_played[notification_key] = now
            return True

    def play(self, sound_name: str) -> None:
        """Play a notification sound.

        Args:
            sound_name: Name of sound to play (without extension)
        """
        if not self._enabled or not self._playsound_available:
            return

        if not self._sounds_dir:
            return

        # Try common extensions
        for ext in [".wav", ".mp3"]:
            sound_path = self._sounds_dir / f"{sound_name}{ext}"
            if sound_path.exists():
                self._play_async(str(sound_path))
                return

        logger.warning(f"Sound not found: {sound_name}")

    def _play_async(self, path: str) -> None:
        """Play sound in background thread.

        Args:
            path: Path to sound file
        """
        def play_sound():
            try:
                import playsound
                playsound.playsound(path, block=True)
            except Exception as e:
                logger.error(f"Failed to play sound: {e}")

        thread = threading.Thread(target=play_sound, daemon=True)
        thread.start()

    def notify_business_ready(self, business_name: str) -> None:
        """Play notification for business ready to sell.

        Args:
            business_name: Name of the business
        """
        notification_key = f"business_ready_{business_name}"
        if self._can_play(notification_key):
            self.play("business_ready")
            logger.debug(f"Audio notification: {business_name} ready")

    def notify_supplies_low(self, business_name: str) -> None:
        """Play notification for low supplies.

        Args:
            business_name: Name of the business
        """
        notification_key = f"supplies_low_{business_name}"
        if self._can_play(notification_key):
            self.play("supplies_low")
            logger.debug(f"Audio notification: {business_name} supplies low")

    def notify_money_received(self, amount: int) -> None:
        """Play notification for money received.

        Args:
            amount: Amount received
        """
        # Shorter cooldown for money notifications
        cooldown = NOTIFICATION.MIN_NOTIFICATION_INTERVAL
        if amount >= 100000:
            if self._can_play("big_money", cooldown):
                self.play("big_money")
        elif amount >= 10000:
            if self._can_play("money_received", cooldown):
                self.play("money_received")

    def notify_mission_complete(self, success: bool) -> None:
        """Play notification for mission completion.

        Args:
            success: Whether mission succeeded
        """
        # Shorter cooldown for mission notifications
        cooldown = NOTIFICATION.MIN_NOTIFICATION_INTERVAL
        if success:
            if self._can_play("mission_passed", cooldown):
                self.play("mission_passed")
        else:
            if self._can_play("mission_failed", cooldown):
                self.play("mission_failed")

    def notify_cooldown_ready(self, activity_name: str) -> None:
        """Play notification when a cooldown expires.

        Args:
            activity_name: Name of the activity
        """
        notification_key = f"cooldown_ready_{activity_name}"
        if self._can_play(notification_key):
            self._emit_notification(
                NotificationType.COOLDOWN_READY,
                "Cooldown Ready",
                f"{activity_name} is ready!",
            )
            self.play("cooldown_ready")
            logger.debug(f"Audio notification: {activity_name} cooldown ready")

    def notify_goal_progress(self, goal_name: str, percent: int) -> None:
        """Play notification for goal milestone.

        Args:
            goal_name: Name of the goal
            percent: Progress percentage
        """
        notification_key = f"goal_progress_{percent // 25}"  # Every 25%
        if self._can_play(notification_key, cooldown=60):
            self._emit_notification(
                NotificationType.GOAL_PROGRESS,
                "Goal Progress",
                f"{goal_name}: {percent}% complete",
            )
            self.play("goal_progress")
            logger.debug(f"Audio notification: {goal_name} at {percent}%")

    def notify_goal_complete(self, goal_name: str) -> None:
        """Play notification when a goal is completed.

        Args:
            goal_name: Name of the goal
        """
        if self._can_play("goal_complete", cooldown=5):
            self._emit_notification(
                NotificationType.GOAL_COMPLETE,
                "Goal Complete!",
                f"You achieved: {goal_name}",
            )
            self.play("goal_complete")
            logger.debug(f"Audio notification: Goal complete - {goal_name}")

    def notify_nightclub_safe(self, current: int, max_val: int) -> None:
        """Play notification when nightclub safe is filling.

        Args:
            current: Current safe value
            max_val: Maximum safe value
        """
        percent = (current / max_val * 100) if max_val > 0 else 0
        if percent >= 90:
            if self._can_play("nightclub_safe_full", cooldown=300):
                self._emit_notification(
                    NotificationType.NIGHTCLUB_SAFE_FULL,
                    "Nightclub Safe",
                    f"Safe nearly full: ${current:,}",
                    value=current,
                )
                self.play("nightclub_safe")
                logger.debug(f"Audio notification: Nightclub safe at {percent:.0f}%")

    def notify_session_milestone(self, hours: int, earnings: int) -> None:
        """Play notification for session time milestone.

        Args:
            hours: Hours played
            earnings: Total session earnings
        """
        notification_key = f"session_hour_{hours}"
        if self._can_play(notification_key, cooldown=3600):
            self._emit_notification(
                NotificationType.SESSION_MILESTONE,
                f"{hours} Hour{'s' if hours > 1 else ''} Played",
                f"Session earnings: ${earnings:,}",
                value=earnings,
            )
            self._play_system_sound(NotificationType.SESSION_MILESTONE)
            logger.debug(f"Audio notification: {hours} hour milestone")

    def notify_afk_warning(self, idle_minutes: int) -> None:
        """Play warning when player might be AFK.

        Args:
            idle_minutes: Minutes of inactivity
        """
        if self._can_play("afk_warning", cooldown=300):
            self._emit_notification(
                NotificationType.AFK_WARNING,
                "AFK Warning",
                f"No activity detected for {idle_minutes} minutes",
            )
            self._play_system_sound(NotificationType.AFK_WARNING)
            logger.debug(f"Audio notification: AFK warning ({idle_minutes}m)")

    def _emit_notification(
        self,
        ntype: NotificationType,
        title: str,
        message: str,
        value: int = 0,
    ) -> None:
        """Emit a notification event to callbacks.

        Args:
            ntype: Notification type
            title: Notification title
            message: Notification message
            value: Associated value
        """
        event = NotificationEvent(
            type=ntype,
            title=title,
            message=message,
            value=value,
        )

        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Notification callback error: {e}")

    def _play_system_sound(self, ntype: NotificationType) -> None:
        """Play a Windows system sound.

        Args:
            ntype: Notification type to get sound for
        """
        if not self._enabled or not self._use_system_sounds:
            return

        if ntype not in self._enabled_types:
            return

        sound_info = self.SYSTEM_SOUNDS.get(ntype)
        if not sound_info:
            return

        sound_name, repeat = sound_info

        def play():
            try:
                for _ in range(repeat):
                    winsound.PlaySound(
                        sound_name,
                        winsound.SND_ALIAS | winsound.SND_ASYNC
                    )
            except Exception as e:
                logger.debug(f"System sound failed: {e}")

        thread = threading.Thread(target=play, daemon=True)
        thread.start()

    def on_notification(self, callback: Callable[[NotificationEvent], None]) -> None:
        """Register a callback for notification events.

        Args:
            callback: Function to call with NotificationEvent
        """
        self._callbacks.append(callback)

    def set_notification_enabled(self, ntype: NotificationType, enabled: bool) -> None:
        """Enable or disable a specific notification type.

        Args:
            ntype: Notification type
            enabled: Whether to enable
        """
        if enabled:
            self._enabled_types.add(ntype)
        else:
            self._enabled_types.discard(ntype)

    def set_use_system_sounds(self, enabled: bool) -> None:
        """Enable or disable Windows system sounds as fallback.

        Args:
            enabled: Whether to use system sounds
        """
        self._use_system_sounds = enabled

    @property
    def is_available(self) -> bool:
        """Check if audio is available."""
        return self._playsound_available or self._use_system_sounds

    @property
    def is_enabled(self) -> bool:
        """Check if audio is enabled."""
        return self._enabled and (self._playsound_available or self._use_system_sounds)
