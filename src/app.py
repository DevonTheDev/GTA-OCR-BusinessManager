"""Main application orchestrator for GTA Business Manager."""

import copy
import time
import threading
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto

from .config.settings import Settings, get_settings
from .capture.screen_capture import ScreenCapture
from .capture.regions import ScreenRegions
from .detection.ocr_engine import OCREngine
from .detection.state_detector import StateDetector, StateDetectionResult
from .detection.parsers.money_parser import MoneyParser, MoneyReading
from .detection.parsers.timer_parser import TimerParser, TimerReading
from .detection.parsers.mission_parser import MissionParser, MissionReading
from .detection.parsers.business_parser import BusinessParser, BusinessReading
from .game.state_machine import GameStateMachine, GameState, StateTransition
from .game.activities import Activity, ActivityType
from .tracking.session import SessionTracker
from .tracking.activity_tracker import ActivityTracker
from .tracking.analytics import Analytics, EfficiencyMetrics, EarningsBreakdown
from .tracking.cooldowns import CooldownTracker, get_cooldown_tracker, ACTIVITY_COOLDOWNS
from .optimization.optimizer import Optimizer, Recommendation
from .database.repository import Repository, get_repository
from .utils.logging import setup_logging, get_logger
from .utils.performance import PerformanceMonitor


logger = get_logger("app")


class AppState(Enum):
    """Application states."""

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()


@dataclass
class CaptureResult:
    """Result from a capture/detection cycle."""

    timestamp: datetime = field(default_factory=datetime.now)
    money: Optional[MoneyReading] = None
    money_change: int = 0
    game_state: GameState = GameState.UNKNOWN
    state_confidence: float = 0.0
    mission_text: str = ""
    objective_text: str = ""
    timer: Optional[TimerReading] = None
    business: Optional[BusinessReading] = None
    capture_time_ms: float = 0
    ocr_time_ms: float = 0
    total_time_ms: float = 0


@dataclass
class AppData:
    """Current application data state."""

    # Money tracking
    current_money: Optional[int] = None
    session_start_money: Optional[int] = None
    session_earnings: int = 0
    last_money_change: int = 0
    last_money_change_time: Optional[datetime] = None

    # Mission tracking
    current_mission: Optional[str] = None
    mission_start_time: Optional[datetime] = None
    mission_start_money: Optional[int] = None

    # Business states
    business_states: dict = field(default_factory=dict)

    # Statistics
    total_captures: int = 0
    successful_ocr: int = 0

    # Database IDs for persistence
    character_id: Optional[int] = None
    db_session_id: Optional[int] = None


class GTABusinessManager:
    """Main application class that orchestrates all components."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize the business manager.

        Args:
            settings: Settings instance. If None, uses global settings.
        """
        self._settings = settings or get_settings()
        self._state = AppState.STOPPED

        # Core components (initialized lazily)
        self._capture: Optional[ScreenCapture] = None
        self._ocr: Optional[OCREngine] = None
        self._state_machine: Optional[GameStateMachine] = None
        self._state_detector: Optional[StateDetector] = None
        self._perf_monitor: Optional[PerformanceMonitor] = None

        # Parsers
        self._money_parser = MoneyParser()
        self._timer_parser = TimerParser()
        self._mission_parser = MissionParser()
        self._business_parser = BusinessParser()

        # Tracking
        self._session_tracker = SessionTracker()
        self._activity_tracker = ActivityTracker()
        self._optimizer = Optimizer(solo_mode=self._settings.get("optimization.solo_mode", True))
        self._analytics = Analytics()
        self._cooldown_tracker: Optional[CooldownTracker] = None

        # Cached analytics (updated on activity completion)
        self._cached_efficiency: Optional[EfficiencyMetrics] = None
        self._cached_breakdown: Optional[EarningsBreakdown] = None
        self._last_analytics_time: float = 0.0
        self._analytics_min_interval: float = 1.0  # Min seconds between recalculations

        # Database
        self._repository: Optional[Repository] = None

        # Capture thread
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Current data (protected by _data_lock for thread safety)
        self._data = AppData()
        self._data_lock = threading.RLock()
        self._last_capture_result: Optional[CaptureResult] = None

        # Callbacks
        self._on_money_change: List[Callable[[MoneyReading, int], None]] = []
        self._on_state_change: List[Callable[[GameState, GameState], None]] = []
        self._on_capture: List[Callable[[CaptureResult], None]] = []
        self._on_mission_complete: List[Callable[[Activity], None]] = []
        self._on_recommendation: List[Callable[[List[Recommendation]], None]] = []

    def _validate_fps(self, value, default: float, name: str) -> float:
        """Validate FPS setting value.

        Args:
            value: Value to validate
            default: Default value if invalid
            name: Setting name for logging

        Returns:
            Valid FPS value
        """
        try:
            fps = float(value)
            if fps <= 0 or fps > 60:
                logger.warning(f"Invalid {name} value {value}, using {default}")
                return default
            return fps
        except (TypeError, ValueError):
            logger.warning(f"Invalid {name} value {value}, using {default}")
            return default

    def _initialize_components(self) -> None:
        """Initialize all components."""
        logger.info("Initializing components...")

        # Screen capture with validated settings
        monitor_index = self._settings.get("capture.monitor_index", 0)
        if not isinstance(monitor_index, int) or monitor_index < 0:
            logger.warning(f"Invalid monitor_index {monitor_index}, using 0")
            monitor_index = 0
        self._capture = ScreenCapture(monitor_index=monitor_index)

        # Set initial capture rate with validation
        idle_fps = self._settings.get("capture.idle_fps", 0.5)
        idle_fps = self._validate_fps(idle_fps, default=0.5, name="idle_fps")
        self._capture.set_capture_rate(idle_fps)

        # OCR engine
        self._ocr = OCREngine()
        if not self._ocr.is_available:
            logger.warning("OCR not available - detection will be limited")

        # State machine
        self._state_machine = GameStateMachine()
        self._state_machine.add_listener(self._on_game_state_transition)

        # State detector
        self._state_detector = StateDetector(ocr_engine=self._ocr)

        # Performance monitor
        self._perf_monitor = PerformanceMonitor()

        # Cooldown tracker (use data directory for persistence)
        cooldown_path = self._settings.data_dir / "cooldowns.json"
        self._cooldown_tracker = CooldownTracker(data_path=cooldown_path)

        # Initialize database
        self._initialize_database()

        # Start session
        self._session_tracker.start_session(start_money=0)

        logger.info(
            f"Components initialized - "
            f"Resolution: {self._capture.resolution[0]}x{self._capture.resolution[1]}, "
            f"OCR available: {self._ocr.is_available}"
        )

    def _initialize_database(self) -> None:
        """Initialize database and create/load character and session."""
        try:
            self._repository = get_repository()

            # Get or create character
            character_name = self._settings.get("general.character_name", "Default")
            character = self._repository.get_or_create_character(character_name)

            if character:
                self._data.character_id = character.id
                self._repository.set_active_character(character.id)

                # Start database session
                db_session = self._repository.start_session(character, start_money=0)
                if db_session:
                    self._data.db_session_id = db_session.id
                    logger.info(f"Database session started: {db_session.id}")
            else:
                logger.warning("Failed to create character - persistence disabled")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            # Continue without persistence

    def start(self) -> bool:
        """Start the capture and detection loop.

        Returns:
            True if started successfully
        """
        if self._state != AppState.STOPPED:
            logger.warning(f"Cannot start - current state: {self._state.name}")
            return False

        self._state = AppState.STARTING
        logger.info("Starting GTA Business Manager...")

        try:
            self._initialize_components()
            self._stop_event.clear()

            # Start capture thread
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="CaptureThread",
                daemon=True,
            )
            self._capture_thread.start()

            self._state = AppState.RUNNING
            logger.info("GTA Business Manager started")
            return True

        except Exception as e:
            logger.error(f"Failed to start: {e}")
            self._state = AppState.STOPPED
            return False

    def stop(self) -> None:
        """Stop the capture and detection loop."""
        if self._state in (AppState.STOPPED, AppState.STOPPING):
            return

        self._state = AppState.STOPPING
        logger.info("Stopping GTA Business Manager...")

        # Signal thread to stop
        self._stop_event.set()

        # Wait for thread to finish
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5.0)

        # End session tracker
        if self._session_tracker.is_active:
            self._session_tracker.end_session()

        # End database session
        self._end_database_session()

        # Cleanup
        if self._capture:
            self._capture.close()
            self._capture = None

        self._state = AppState.STOPPED
        logger.info("GTA Business Manager stopped")

    def _end_database_session(self) -> None:
        """End the database session and close repository."""
        if self._repository and self._data.db_session_id:
            try:
                end_money = self._data.current_money or 0
                self._repository.end_session(self._data.db_session_id, end_money)
                logger.info(f"Database session {self._data.db_session_id} ended")
            except Exception as e:
                logger.error(f"Failed to end database session: {e}")
            finally:
                self._repository.close()

    def pause(self) -> None:
        """Pause capture and detection."""
        if self._state == AppState.RUNNING:
            self._state = AppState.PAUSED
            logger.info("Capture paused")

    def resume(self) -> None:
        """Resume capture and detection."""
        if self._state == AppState.PAUSED:
            self._state = AppState.RUNNING
            logger.info("Capture resumed")

    def _capture_loop(self) -> None:
        """Main capture and detection loop (runs in thread)."""
        logger.debug("Capture loop started")

        while not self._stop_event.is_set():
            if self._state != AppState.RUNNING:
                time.sleep(0.1)
                continue

            try:
                result = self._do_capture_cycle()
                self._last_capture_result = result

                # Notify listeners
                for callback in self._on_capture:
                    try:
                        callback(result)
                    except Exception as e:
                        logger.error(f"Capture callback error: {e}")

                # Adjust capture rate based on state
                self._adjust_capture_rate(result.game_state)

            except Exception as e:
                logger.error(f"Capture cycle error: {e}")
                time.sleep(1.0)  # Back off on error

        logger.debug("Capture loop ended")

    def _do_capture_cycle(self) -> CaptureResult:
        """Perform one capture and detection cycle."""
        result = CaptureResult()
        total_start = time.perf_counter()

        with self._perf_monitor.time_operation("total"):
            # Capture multiple regions
            with self._perf_monitor.time_operation("capture"):
                full_screen = self._capture.capture_full_screen()
                money_img = self._capture.capture_money_display()
                mission_img = self._capture.capture_mission_text()
                center_img = self._capture.capture_center_prompt()
                timer_img = self._capture.capture_timer()

            self._data.total_captures += 1

            if full_screen is None:
                return result

            # Detect game state
            state_result = self._state_detector.detect(
                full_screen,
                mission_text_image=mission_img,
                center_text_image=center_img,
            )

            result.game_state = state_result.state
            result.state_confidence = state_result.confidence
            result.mission_text = state_result.mission_text
            result.objective_text = state_result.objective_text

            # Update state machine
            if state_result.confidence > 0.6:
                self._state_machine.transition_to(
                    state_result.state,
                    trigger=state_result.reason
                )

            # OCR money display
            if money_img is not None and self._ocr.is_available:
                with self._perf_monitor.time_operation("ocr"):
                    ocr_result = self._ocr.recognize_preprocessed(money_img, invert=True, scale=2.0)

                money_reading = self._money_parser.parse(ocr_result.text)
                if money_reading.has_value and self._money_parser.validate_reading(money_reading):
                    result.money = money_reading
                    result.money_change = self._process_money_change(money_reading)
                    self._data.successful_ocr += 1

            # OCR timer if in mission
            if timer_img is not None and state_result.state in (
                GameState.MISSION_ACTIVE, GameState.SELLING
            ):
                timer_ocr = self._ocr.recognize_preprocessed(timer_img, invert=True, scale=2.0)
                timer_reading = self._timer_parser.parse(timer_ocr.text)
                if timer_reading.has_value:
                    result.timer = timer_reading

            # Handle state-specific processing
            self._process_state(state_result, result)

        # Record timing
        metrics = self._perf_monitor.get_metrics()
        result.capture_time_ms = metrics.avg_capture_ms
        result.ocr_time_ms = metrics.avg_ocr_ms
        result.total_time_ms = (time.perf_counter() - total_start) * 1000

        return result

    def _process_money_change(self, reading: MoneyReading) -> int:
        """Process a money reading and detect changes."""
        current_value = reading.display_value
        change = 0
        prev_money = None

        with self._data_lock:
            # Initialize session start money
            if self._data.session_start_money is None:
                self._data.session_start_money = current_value
                self._session_tracker.update_money(current_value)
                logger.info(f"Session start money: ${current_value:,}")

            # Detect change from last reading
            if self._data.current_money is not None:
                prev_money = self._data.current_money
                change = current_value - prev_money

                if change != 0:
                    self._data.last_money_change = change
                    self._data.last_money_change_time = datetime.now()

                    if change > 0:
                        self._data.session_earnings += change
                        self._session_tracker.update_money(current_value)

            self._data.current_money = current_value

        # Operations that don't need the lock (database, logging, callbacks)
        if change != 0:
            if change > 0:
                self._persist_earning(change, current_value)

            if prev_money is not None:
                logger.info(
                    f"Money change: ${prev_money:,} -> ${current_value:,} "
                    f"({'+' if change >= 0 else ''}{change:,})"
                )

            # Notify listeners
            for callback in self._on_money_change:
                try:
                    callback(reading, change)
                except Exception as e:
                    logger.error(f"Money change callback error: {e}")

        return change

    def _persist_earning(self, amount: int, balance_after: int) -> None:
        """Persist an earning to the database."""
        if not self._repository or not self._data.db_session_id:
            return

        try:
            # Infer source from current game state
            source = ""
            if self._state_machine:
                state = self._state_machine.state
                if state == GameState.MISSION_COMPLETE:
                    source = self._data.current_mission or "Mission"
                elif state == GameState.SELLING:
                    source = "Sell Mission"
                elif state in (GameState.HEIST_FINALE, GameState.HEIST_PREP):
                    source = "Heist"

            self._repository.log_earning(
                session_id=self._data.db_session_id,
                amount=amount,
                source=source,
                balance_after=balance_after,
            )
        except Exception as e:
            logger.error(f"Failed to persist earning: {e}")

    def _persist_activity(
        self,
        activity_type: str,
        activity_name: str,
        earnings: int,
        success: bool,
        duration_seconds: int,
        business_type: str = "",
    ) -> None:
        """Persist a completed activity to the database."""
        if not self._repository or not self._data.db_session_id:
            return

        try:
            self._repository.log_activity(
                session_id=self._data.db_session_id,
                activity_type=activity_type,
                activity_name=activity_name,
                earnings=earnings,
                success=success,
                duration_seconds=duration_seconds,
                business_type=business_type,
            )
        except Exception as e:
            logger.error(f"Failed to persist activity: {e}")

    def _process_state(self, state_result: StateDetectionResult, capture_result: CaptureResult) -> None:
        """Process state-specific logic."""
        state = state_result.state

        # Mission started
        if state == GameState.MISSION_ACTIVE and self._data.mission_start_time is None:
            self._data.mission_start_time = datetime.now()
            self._data.mission_start_money = self._data.current_money
            self._data.current_mission = state_result.mission_text or "Unknown Mission"

            # Determine activity type
            activity_type = self._infer_activity_type(state_result)
            self._activity_tracker.start_activity(
                activity_type=activity_type,
                name=self._data.current_mission,
            )
            logger.info(f"Mission started: {self._data.current_mission}")

        # Sell mission started
        elif state == GameState.SELLING and self._data.mission_start_time is None:
            self._data.mission_start_time = datetime.now()
            self._data.mission_start_money = self._data.current_money

            self._activity_tracker.start_activity(
                activity_type=ActivityType.SELL_MISSION,
                name="Sell Mission",
            )
            logger.info("Sell mission started")

        # Mission complete
        elif state == GameState.MISSION_COMPLETE and self._data.mission_start_time is not None:
            earnings = 0
            if self._data.current_money and self._data.mission_start_money:
                earnings = max(0, self._data.current_money - self._data.mission_start_money)

            # Calculate duration
            duration_seconds = int((datetime.now() - self._data.mission_start_time).total_seconds())

            activity = self._activity_tracker.complete_activity(success=True, earnings=earnings)
            self._session_tracker.record_activity_complete(success=True, earnings=earnings)

            # Persist activity to database
            self._persist_activity(
                activity_type=activity.activity_type.name if activity else "MISSION",
                activity_name=self._data.current_mission or "Unknown",
                earnings=earnings,
                success=True,
                duration_seconds=duration_seconds,
            )

            # Start cooldown for the activity
            if activity:
                self._start_activity_cooldown(activity)

            if activity:
                for callback in self._on_mission_complete:
                    try:
                        callback(activity)
                    except Exception as e:
                        logger.error(f"Mission complete callback error: {e}")

            # Recalculate analytics after activity completion
            self._recalculate_analytics()

            self._reset_mission_state()
            logger.info(f"Mission complete, earnings: ${earnings:,}")

        # Mission failed
        elif state == GameState.MISSION_FAILED and self._data.mission_start_time is not None:
            # Calculate duration
            duration_seconds = int((datetime.now() - self._data.mission_start_time).total_seconds())

            self._activity_tracker.complete_activity(success=False, earnings=0)
            self._session_tracker.record_activity_complete(success=False, earnings=0)

            # Persist failed activity to database
            self._persist_activity(
                activity_type="MISSION",
                activity_name=self._data.current_mission or "Unknown",
                earnings=0,
                success=False,
                duration_seconds=duration_seconds,
            )

            # Recalculate analytics after activity failure
            self._recalculate_analytics()

            self._reset_mission_state()
            logger.info("Mission failed")

        # Business computer - read business stats
        elif state == GameState.BUSINESS_COMPUTER:
            self._process_business_computer()

    def _infer_activity_type(self, state_result: StateDetectionResult) -> ActivityType:
        """Infer activity type from state detection result."""
        text = (state_result.mission_text + " " + state_result.objective_text).lower()

        if any(kw in text for kw in ["headhunter", "sightseer", "vip work", "vip challenge"]):
            return ActivityType.VIP_WORK
        elif any(kw in text for kw in ["deliver", "sell", "drop off"]):
            return ActivityType.SELL_MISSION
        elif any(kw in text for kw in ["heist", "finale"]):
            return ActivityType.HEIST_FINALE
        elif any(kw in text for kw in ["prep", "setup"]):
            return ActivityType.HEIST_PREP
        elif any(kw in text for kw in ["payphone", "assassination"]):
            return ActivityType.PAYPHONE_HIT
        elif any(kw in text for kw in ["security contract"]):
            return ActivityType.SECURITY_CONTRACT

        return ActivityType.CONTACT_MISSION

    def _start_activity_cooldown(self, activity: Activity) -> None:
        """Start cooldown timer for a completed activity.

        Args:
            activity: The completed activity
        """
        if not self._cooldown_tracker:
            return

        # Map activity types and names to cooldown keys
        cooldown_key = None
        display_name = activity.name

        # Check activity type first
        if activity.activity_type == ActivityType.PAYPHONE_HIT:
            cooldown_key = "payphone_hit"
            display_name = "Payphone Hit"
        elif activity.activity_type == ActivityType.VIP_WORK:
            # Check specific VIP work types
            name_lower = activity.name.lower()
            if "headhunter" in name_lower:
                cooldown_key = "headhunter"
                display_name = "Headhunter"
            elif "sightseer" in name_lower:
                cooldown_key = "sightseer"
                display_name = "Sightseer"
            elif "hostile" in name_lower:
                cooldown_key = "hostile_takeover"
                display_name = "Hostile Takeover"
            elif "executive" in name_lower:
                cooldown_key = "executive_search"
                display_name = "Executive Search"
            elif "asset" in name_lower:
                cooldown_key = "asset_recovery"
                display_name = "Asset Recovery"
            elif "piracy" in name_lower:
                cooldown_key = "piracy_prevention"
                display_name = "Piracy Prevention"
        elif activity.activity_type == ActivityType.MC_CONTRACT:
            cooldown_key = "mc_contract"
            display_name = "MC Contract"
        elif activity.activity_type == ActivityType.CLIENT_JOB:
            name_lower = activity.name.lower()
            if "robbery" in name_lower:
                cooldown_key = "robbery_in_progress"
                display_name = "Robbery in Progress"
            elif "data sweep" in name_lower:
                cooldown_key = "data_sweep"
                display_name = "Data Sweep"
            elif "targeted" in name_lower:
                cooldown_key = "targeted_data"
                display_name = "Targeted Data"
            elif "diamond" in name_lower:
                cooldown_key = "diamond_shopping"
                display_name = "Diamond Shopping"

        # Start cooldown if we found a matching key
        if cooldown_key and cooldown_key in ACTIVITY_COOLDOWNS:
            cooldown_seconds = ACTIVITY_COOLDOWNS[cooldown_key]
            if cooldown_seconds > 0:
                self._cooldown_tracker.start_cooldown(
                    cooldown_key,
                    display_name,
                    cooldown_seconds
                )
                logger.info(f"Started cooldown: {display_name} ({cooldown_seconds}s)")

    def _reset_mission_state(self) -> None:
        """Reset mission tracking state."""
        self._data.mission_start_time = None
        self._data.mission_start_money = None
        self._data.current_mission = None

    def _process_business_computer(self) -> None:
        """Process business computer screen to extract stock/supply info."""
        if not self._capture or not self._ocr or not self._ocr.is_available:
            return

        try:
            # Get business regions
            regions = self._capture.regions.get_business_regions()

            # Capture and OCR each region
            text_parts = []
            for region_name, region in regions.items():
                img = self._capture.capture_region(region, wait_for_rate=False)
                if img is not None:
                    ocr_result = self._ocr.recognize_preprocessed(img, invert=True, scale=2.0)
                    if ocr_result.text:
                        text_parts.append(ocr_result.text)

            if not text_parts:
                return

            # Combine and parse
            combined_text = " ".join(text_parts)
            reading = self._business_parser.parse(combined_text)

            if reading.has_data:
                # Convert business type to ID string
                business_id = reading.business_type.name.lower()

                # Update business state
                stock_pct = reading.stock_level or 0
                supply_pct = reading.supply_level or 0
                value = reading.stock_value or 0

                self.update_business_state(business_id, stock_pct, supply_pct, value)

                logger.info(
                    f"Business detected: {reading.business_type.name} - "
                    f"Stock: {stock_pct}%, Supply: {supply_pct}%, Value: ${value:,}"
                )

        except Exception as e:
            logger.error(f"Error processing business computer: {e}")

    def _recalculate_analytics(self, force: bool = False) -> None:
        """Recalculate analytics from current activity data.

        Args:
            force: If True, ignore rate limiting and recalculate immediately
        """
        # Rate limit analytics recalculation to avoid O(n) operations too frequently
        current_time = time.time()
        if not force and (current_time - self._last_analytics_time) < self._analytics_min_interval:
            return

        try:
            activities = self._activity_tracker.get_recent_activities(100)
            session_time = self._session_tracker.duration_seconds

            if activities and session_time > 0:
                self._cached_efficiency = self._analytics.calculate_efficiency(
                    activities, session_time
                )
                self._cached_breakdown = self._analytics.calculate_earnings_breakdown(
                    activities
                )
                self._last_analytics_time = current_time
                logger.debug(
                    f"Analytics updated: {self._cached_efficiency.earnings_per_hour:.0f}/hr, "
                    f"best: {self._cached_efficiency.best_activity_type}"
                )
        except Exception as e:
            logger.error(f"Failed to recalculate analytics: {e}")

    def _adjust_capture_rate(self, state: GameState) -> None:
        """Adjust capture rate based on game state."""
        if state in (GameState.MISSION_ACTIVE, GameState.SELLING):
            fps = self._settings.get("capture.active_fps", 2.0)
            fps = self._validate_fps(fps, default=2.0, name="active_fps")
        elif state == GameState.BUSINESS_COMPUTER:
            fps = self._settings.get("capture.business_fps", 4.0)
            fps = self._validate_fps(fps, default=4.0, name="business_fps")
        else:
            fps = self._settings.get("capture.idle_fps", 0.5)
            fps = self._validate_fps(fps, default=0.5, name="idle_fps")

        self._capture.set_capture_rate(fps)

    def _on_game_state_transition(self, transition: StateTransition) -> None:
        """Handle game state transitions."""
        for callback in self._on_state_change:
            try:
                callback(transition.from_state, transition.to_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")

    # Public API for callbacks

    def on_money_change(self, callback: Callable[[MoneyReading, int], None]) -> None:
        """Register callback for money changes."""
        self._on_money_change.append(callback)

    def on_state_change(self, callback: Callable[[GameState, GameState], None]) -> None:
        """Register callback for game state changes."""
        self._on_state_change.append(callback)

    def on_capture(self, callback: Callable[[CaptureResult], None]) -> None:
        """Register callback for each capture cycle."""
        self._on_capture.append(callback)

    def on_mission_complete(self, callback: Callable[[Activity], None]) -> None:
        """Register callback for mission completion."""
        self._on_mission_complete.append(callback)

    def on_recommendation(self, callback: Callable[[List[Recommendation]], None]) -> None:
        """Register callback for new recommendations."""
        self._on_recommendation.append(callback)

    # Public API for data access

    @property
    def state(self) -> AppState:
        """Get current application state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if capture loop is running."""
        return self._state == AppState.RUNNING

    @property
    def current_money(self) -> Optional[int]:
        """Get last known money value."""
        with self._data_lock:
            return self._data.current_money

    @property
    def session_earnings(self) -> int:
        """Get total earnings this session."""
        with self._data_lock:
            return self._data.session_earnings

    @property
    def session_start_money(self) -> Optional[int]:
        """Get money at session start."""
        with self._data_lock:
            return self._data.session_start_money

    @property
    def game_state(self) -> GameState:
        """Get current game state."""
        if self._state_machine:
            return self._state_machine.state
        return GameState.UNKNOWN

    @property
    def performance_metrics(self):
        """Get performance metrics."""
        if self._perf_monitor:
            return self._perf_monitor.get_metrics()
        return None

    @property
    def last_capture(self) -> Optional[CaptureResult]:
        """Get the last capture result."""
        return self._last_capture_result

    @property
    def session_stats(self):
        """Get session statistics."""
        return self._session_tracker.stats

    @property
    def recent_activities(self) -> List[Activity]:
        """Get recent completed activities."""
        return self._activity_tracker.get_recent_activities(10)

    @property
    def recommendations(self) -> List[Recommendation]:
        """Get current recommendations from optimizer and analytics."""
        # Get optimizer recommendations (business-based)
        optimizer_recs = self._optimizer.get_recommendations(5)

        # Get a thread-safe copy of business states
        with self._data_lock:
            business_states_copy = dict(self._data.business_states)

        # Get analytics recommendations (activity-based insights)
        analytics_recs = []
        try:
            activities = self._activity_tracker.get_recent_activities(100)
            if activities:
                analytics_texts = self._analytics.get_recommendations(
                    activities, business_states_copy
                )
                # Convert analytics text recommendations to Recommendation objects
                for i, text in enumerate(analytics_texts):
                    analytics_recs.append(
                        Recommendation(
                            priority=4,  # Lower priority - informational
                            action=text,
                            reason="Based on your activity history",
                            score=0.4 - (i * 0.05),
                        )
                    )
        except Exception as e:
            logger.debug(f"Failed to get analytics recommendations: {e}")

        # Merge and deduplicate
        all_recs = optimizer_recs + analytics_recs

        # Sort by priority, then by score
        all_recs.sort(key=lambda r: (r.priority, -r.score))

        return all_recs[:7]  # Return top 7 combined recommendations

    @property
    def data(self) -> AppData:
        """Get a snapshot of current app data.

        Returns a deep copy to ensure thread-safety. The returned object
        is safe to read without locks as it won't be modified by the
        capture thread.
        """
        with self._data_lock:
            return copy.deepcopy(self._data)

    @property
    def efficiency_metrics(self) -> Optional[EfficiencyMetrics]:
        """Get calculated efficiency metrics from analytics."""
        # Recalculate if not cached
        if self._cached_efficiency is None:
            self._recalculate_analytics()
        return self._cached_efficiency

    @property
    def earnings_breakdown(self) -> Optional[EarningsBreakdown]:
        """Get earnings breakdown by source."""
        if self._cached_breakdown is None:
            self._recalculate_analytics()
        return self._cached_breakdown

    @property
    def best_activity_type(self) -> Optional[str]:
        """Get the best performing activity type."""
        if self._cached_efficiency:
            return self._cached_efficiency.best_activity_type
        return None

    @property
    def best_activity_rate(self) -> float:
        """Get the earnings rate of the best activity type."""
        if self._cached_efficiency:
            return self._cached_efficiency.best_activity_rate
        return 0.0

    @property
    def cooldown_tracker(self) -> Optional[CooldownTracker]:
        """Get the cooldown tracker instance."""
        return self._cooldown_tracker

    def reset_session(self) -> None:
        """Reset session tracking."""
        with self._data_lock:
            start_money = self._data.current_money or 0
            self._data.session_start_money = self._data.current_money
            self._data.session_earnings = 0

        self._session_tracker.start_session(start_money=start_money)

        # Clear cached analytics
        self._cached_efficiency = None
        self._cached_breakdown = None

        logger.info("Session reset")

    def get_business_state(self, business_id: str) -> Optional[dict]:
        """Get tracked state for a business."""
        with self._data_lock:
            return self._data.business_states.get(business_id)

    def update_business_state(
        self,
        business_id: str,
        stock_percent: int,
        supply_percent: int,
        value: int = 0
    ) -> None:
        """Update business state (from OCR or manual input)."""
        with self._data_lock:
            self._data.business_states[business_id] = {
                "stock": stock_percent,
                "supply": supply_percent,
                "value": value,
                "updated": datetime.now(),
            }
        self._optimizer.update_business_state(business_id, stock_percent, supply_percent, value)
        logger.debug(f"Business {business_id} updated: stock={stock_percent}%, supply={supply_percent}%")
