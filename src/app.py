"""Main application orchestrator for GTA Business Manager."""

import copy
import math
import re
import time
import threading
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto

from .config.settings import Settings, get_settings
from .capture.screen_capture import ScreenCapture
from .capture.regions import ScreenRegions
from .detection.ocr_engine import OCREngine
from .detection.state_detector import StateDetector, StateDetectionResult
from .detection.parsers.money_parser import MoneyParser, MoneyReading
from .detection.parsers.timer_parser import TimerParser, TimerReading
from .detection.parsers.mission_parser import MissionParser, MissionReading, MissionType
from .detection.parsers.business_parser import BusinessParser, BusinessReading, BusinessType
from .game.state_machine import GameStateMachine, GameState, StateTransition
from .game.activities import Activity, ActivityType
from .game.businesses import BUSINESSES
from .game.business_readings import normalize_live_business_reading
from .game.live_business_snapshot import (
    LiveBusinessReadingSnapshot, create_live_business_reading_snapshot,
)
from .tracking.session import SessionTracker
from .tracking.session_goals import SessionGoalController
from .tracking.goals import GoalType, SessionGoal
from .tracking.activity_tracker import ActivityTracker
from .tracking.analytics import Analytics, EfficiencyMetrics, EarningsBreakdown
from .tracking.cooldowns import CooldownTracker, ACTIVITY_COOLDOWNS
from .optimization.optimizer import Optimizer, Recommendation
from .optimization.recommendation_snoozes import RecommendationSnapshot, RecommendationSnoozes
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
    mission: Optional[MissionReading] = None
    activity_name: str = ""
    activity_type: Optional[ActivityType] = None
    activity_identity_status: str = "unknown"
    banner_text: str = ""


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
    mission_identity_status: str = "unknown"
    mission_identity_type: MissionType = MissionType.UNKNOWN
    mission_heist_phase: MissionType = MissionType.UNKNOWN

    # Business states
    business_states: dict = field(default_factory=dict)

    # Statistics
    total_captures: int = 0
    successful_ocr: int = 0

    # Database IDs for persistence
    character_id: Optional[int] = None
    db_session_id: Optional[int] = None
    # Original OCR baseline for the DB session, independent of statistics resets.
    db_start_money: Optional[int] = None


class GTABusinessManager:
    """Main application class that orchestrates all components."""

    def __init__(
        self, settings: Optional[Settings] = None, *,
        recommendation_clock: Callable[[], float] = time.monotonic,
    ):
        """Initialize the business manager.

        Args:
            settings: Settings instance. If None, uses global settings.
            recommendation_clock: Monotonic clock for temporary recommendation snoozes.
        """
        self._settings = settings or get_settings()
        self._state = AppState.STOPPED
        self._lifecycle_lock = threading.RLock()

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
        # Presentation preferences belong to this manager, across capture runs
        # and statistics resets, and never enter settings or stored history.
        self._recommendation_snoozes = RecommendationSnoozes(clock=recommendation_clock)
        # Reminders belong to the app, including while capture is stopped.
        self._cooldown_tracker = CooldownTracker(
            data_path=self._settings.data_dir / "cooldowns.json"
        )

        # Cached analytics (updated on activity completion and throttled reads)
        self._analytics_lock = threading.Lock()
        self._cached_efficiency: Optional[EfficiencyMetrics] = None
        self._cached_breakdown: Optional[EarningsBreakdown] = None
        self._last_analytics_time: Optional[float] = None
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
        # Live assignment is independent of saved businesses and manual history.
        self._business_screen_target: Optional[str] = None
        self._business_screen_generation = 0

        # Remember the target only; progress belongs to the current statistics
        # object and must never be restored into another capture run.
        self._goal_controller = SessionGoalController(
            self._settings.data_dir / "session_goal_target.json"
        )

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
            if not math.isfinite(fps) or fps <= 0 or fps > 60:
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
        """Start a fresh session only after the previous worker has finished."""
        with self._lifecycle_lock:
            if self._state != AppState.STOPPED or (
                self._capture_thread and self._capture_thread.is_alive()
            ):
                logger.warning(f"Cannot start - current state: {self._state.name}")
                return False

            self._state = AppState.STARTING
            logger.info("Starting GTA Business Manager...")
            try:
                # Business observations and completed activity history outlive a
                # capture run. Money, mission state and database IDs do not.
                with self._data_lock:
                    self._data = AppData(business_states=self._data.business_states)
                    self._last_capture_result = None
                self._money_parser = MoneyParser()
                self._activity_tracker.cancel_activity()
                self._invalidate_analytics()

                self._initialize_components()
                with self._data_lock:
                    self._refresh_session_goal_locked(force=True)
                self._stop_event.clear()
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
                self._state = AppState.STOPPING
                self._stop_event.set()
                if not self._capture_thread or not self._capture_thread.is_alive():
                    self._finish_stop(self._capture_thread)
                return False

    def stop(self) -> None:
        """Signal shutdown; a timed-out worker retains its resources until exit."""
        with self._lifecycle_lock:
            with self._data_lock:
                self._business_screen_target = None
                # Stop also retires Automatic batches and stopped preselection.
                self._business_screen_generation += 1
                self._stop_event.set()
            if self._state == AppState.STOPPED:
                return
            self._state = AppState.STOPPING
            worker = self._capture_thread

        # Never hold the lifecycle lock while joining: the worker's finally
        # block needs that lock to finish. Callbacks may stop their own worker.
        if worker is threading.current_thread():
            return
        if worker and worker.is_alive():
            worker.join(timeout=5.0)
            if worker.is_alive():
                logger.warning("Capture is still stopping; resources remain open until it exits")
                return
        self._finish_stop(worker)

    def _finish_stop(self, worker: Optional[threading.Thread]) -> None:
        """Finalize one run exactly once, never a newer run started meanwhile."""
        with self._lifecycle_lock:
            if worker is not self._capture_thread or self._state == AppState.STOPPED:
                return
            self._state = AppState.STOPPING
            self._stop_event.set()
            try:
                if self._session_tracker.is_active:
                    self._session_tracker.end_session()
            except Exception as e:
                logger.error(f"Failed to end session tracker: {e}")
            try:
                with self._data_lock:
                    self._refresh_session_goal_locked(force=True)
            except Exception as e:
                logger.error(f"Failed to finalize session goal progress: {e}")
            try:
                self._end_database_session()
            except Exception as e:
                logger.error(f"Failed to close database resources: {e}")
            capture, self._capture = self._capture, None
            if capture:
                try:
                    capture.close()
                except Exception as e:
                    logger.error(f"Failed to close capture resources: {e}")
            self._capture_thread = None
            self._state = AppState.STOPPED
            logger.info("GTA Business Manager stopped")

    def _end_database_session(self) -> None:
        """Atomically finalize observed balances, then close the repository."""
        repository = self._repository
        with self._data_lock:
            session_id = self._data.db_session_id
            end_money = self._data.current_money or 0
            start_money = self._data.db_start_money
        if repository and session_id:
            try:
                if repository.end_session(session_id, end_money, start_money=start_money):
                    logger.info(f"Database session {session_id} ended")
                else:
                    logger.warning(f"Failed to finalize database session {session_id}")
            except Exception as e:
                logger.error(f"Failed to end database session: {e}")
            finally:
                repository.close()

    def pause(self) -> None:
        """Pause capture and detection."""
        with self._lifecycle_lock:
            if self._state == AppState.RUNNING:
                self._state = AppState.PAUSED
                logger.info("Capture paused")

    def resume(self) -> None:
        """Resume capture and detection."""
        with self._lifecycle_lock:
            if self._state == AppState.PAUSED:
                self._state = AppState.RUNNING
                logger.info("Capture resumed")

    def _capture_loop(self) -> None:
        """Main capture loop; its finally block owns deferred resource cleanup."""
        worker = threading.current_thread()
        logger.debug("Capture loop started")
        try:
            while not self._stop_event.is_set():
                if self._state != AppState.RUNNING:
                    self._stop_event.wait(0.1)
                    continue
                try:
                    result = self._do_capture_cycle()
                    self._last_capture_result = result
                    for callback in self._on_capture:
                        if self._stop_event.is_set():
                            break
                        try:
                            callback(result)
                        except Exception as e:
                            logger.error(f"Capture callback error: {e}")
                    if not self._stop_event.is_set():
                        self._adjust_capture_rate(result.game_state)
                except Exception as e:
                    logger.error(f"Capture cycle error: {e}")
                    self._stop_event.wait(1.0)
        finally:
            self._finish_stop(worker)
            logger.debug("Capture loop ended")

    def _do_capture_cycle(self) -> CaptureResult:
        """Perform one capture and detection cycle."""
        result = CaptureResult()
        total_start = time.perf_counter()

        with self._perf_monitor.time_operation("total"):
            # Capture multiple regions
            with self._perf_monitor.time_operation("capture"):
                # Visual checks and all HUD text share one captured screenshot.
                regions = self._capture.regions
                images = self._capture.capture_multiple_regions([
                    regions.full_screen,
                    regions.money_display,
                    regions.mission_text,
                    regions.center_prompt,
                    regions.timer_bottom_right,
                    regions.mission_banner,
                ])
                full_screen, money_img, mission_img, center_img, timer_img, banner_img = (
                    images[index] for index in range(6)
                )

            self._data.total_captures += 1

            if full_screen is None:
                return result

            # Detect game state
            state_result = self._state_detector.detect(
                full_screen,
                mission_text_image=mission_img,
                center_text_image=center_img,
                mission_banner_image=banner_img,
            )

            result.game_state = state_result.state
            result.state_confidence = state_result.confidence
            result.mission_text = state_result.mission_text
            result.objective_text = state_result.objective_text
            result.banner_text = getattr(state_result, "banner_text", "")
            result.mission = getattr(state_result, "mission", None)

            # Update state machine
            if self._confident_detection(state_result):
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
                GameState.MISSION_ACTIVE, GameState.SELLING,
                GameState.HEIST_PREP, GameState.HEIST_FINALE,
            ):
                timer_ocr = self._ocr.recognize_preprocessed(timer_img, invert=True, scale=2.0)
                timer_reading = self._timer_parser.parse(timer_ocr.text)
                if timer_reading.has_value:
                    result.timer = timer_reading

            # Handle state-specific processing
            self._process_state(state_result, result)
            current_activity = self._activity_tracker.current_activity
            if current_activity is not None:
                result.activity_name = current_activity.name
                result.activity_type = current_activity.activity_type
                result.activity_identity_status = self._data.mission_identity_status

        # Record timing
        metrics = self._perf_monitor.get_metrics()
        result.capture_time_ms = metrics.avg_capture_ms
        result.ocr_time_ms = metrics.avg_ocr_ms
        result.total_time_ms = (time.perf_counter() - total_start) * 1000

        return result

    def _process_money_change(self, reading: MoneyReading) -> int:
        """Process a money reading and detect changes."""
        if not reading.has_value:
            return 0

        current_value = reading.display_value
        change = 0
        prev_money = None
        initial_balance = False

        with self._data_lock:
            if self._data.db_start_money is None:
                self._data.db_start_money = current_value
            # Initialize session start money
            if self._data.session_start_money is None:
                self._data.session_start_money = current_value
                self._session_tracker.set_money_baseline(current_value)
                initial_balance = True
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

            # Spending must advance the tracker's balance too, so later income
            # is measured against the latest observation rather than an old high.
            self._session_tracker.update_money(current_value)
            self._data.current_money = current_value

        # Operations that don't need the lock (database, logging, callbacks)
        if initial_balance and self._repository and self._data.db_session_id:
            try:
                if not self._repository.set_session_start_money(
                    self._data.db_session_id, current_value
                ):
                    logger.warning("Failed to persist session opening balance")
            except Exception as e:
                logger.error(f"Failed to persist session opening balance: {e}")

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
        if (state in (GameState.MISSION_ACTIVE, GameState.HEIST_PREP, GameState.HEIST_FINALE,
                      GameState.SELLING, GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
                and not self._confident_detection(state_result)):
            return
        if state in (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED):
            reading = self._mission_reading(state_result)
            capture_result.mission = reading
            expected = "complete" if state == GameState.MISSION_COMPLETE else "failed"
            if reading.outcome not in (None, expected):
                return
            if self._data.mission_start_time is not None:
                # A result banner can be the first readable title. Refine an
                # existing compatible activity before recording its result,
                # without creating a new activity from the banner itself.
                self._refine_mission_identity(state_result, reading, allow_result=True)

        # A weak visual-only frame is not enough to start or refine an activity.
        # Use the same strict threshold as the game-state transition above.
        if state in (GameState.MISSION_ACTIVE, GameState.HEIST_PREP,
                     GameState.HEIST_FINALE, GameState.SELLING):
            reading = self._mission_reading(state_result)
            capture_result.mission = reading
            if reading.outcome is not None:
                return
            if self._data.mission_start_time is None:
                self._data.mission_start_time = datetime.now()
                self._data.mission_start_money = self._data.current_money
                activity_type = self._infer_activity_type(state_result, reading)
                self._data.current_mission = self._mission_display_name(state_result, reading)
                self._data.mission_identity_status = self._identity_status(state_result, reading)
                self._data.mission_identity_type = self._identity_family(reading)
                self._data.mission_heist_phase = self._identity_phase(state_result, reading)
                self._activity_tracker.start_activity(
                    activity_type=activity_type, name=self._data.current_mission,
                )
                logger.info("Mission started: %s", self._data.current_mission)
            else:
                self._refine_mission_identity(state_result, reading)

        # Mission complete
        elif state == GameState.MISSION_COMPLETE and self._data.mission_start_time is not None:
            earnings = 0
            if (self._data.current_money is not None
                    and self._data.mission_start_money is not None):
                earnings = max(0, self._data.current_money - self._data.mission_start_money)

            # Calculate duration
            duration_seconds = int((datetime.now() - self._data.mission_start_time).total_seconds())

            activity = self._activity_tracker.complete_activity(success=True, earnings=earnings)
            self._session_tracker.record_activity_complete(
                success=True, earnings=earnings,
                is_sell=activity is not None and activity.activity_type == ActivityType.SELL_MISSION,
            )

            # Persist activity to database
            self._persist_activity(
                activity_type=activity.activity_type.name if activity else "MISSION",
                activity_name=(
                    activity.name if activity and activity.name
                    else self._data.current_mission or "Unknown"
                ),
                earnings=earnings,
                success=True,
                duration_seconds=duration_seconds,
            )

            # Start cooldown for the activity
            if activity:
                self._start_activity_cooldown(activity)

            # Consume this result before external listeners can process another
            # state or start a new mission. Do not reset their new state afterward.
            self._reset_mission_state()

            if activity:
                for callback in tuple(self._on_mission_complete):
                    try:
                        callback(activity)
                    except Exception as e:
                        logger.error(f"Mission complete callback error: {e}")

            # Recalculate analytics after activity completion
            self._recalculate_analytics()

            logger.info(f"Mission complete, earnings: ${earnings:,}")

        # Mission failed
        elif state == GameState.MISSION_FAILED and self._data.mission_start_time is not None:
            # Calculate duration
            duration_seconds = int((datetime.now() - self._data.mission_start_time).total_seconds())

            activity = self._activity_tracker.complete_activity(success=False, earnings=0)
            self._session_tracker.record_activity_complete(success=False, earnings=0)

            # Persist failed activity to database
            self._persist_activity(
                activity_type=activity.activity_type.name if activity else "MISSION",
                activity_name=(
                    activity.name if activity and activity.name
                    else self._data.current_mission or "Unknown"
                ),
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

    @staticmethod
    def _confident_detection(state_result: StateDetectionResult) -> bool:
        confidence = state_result.confidence
        return (isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                and math.isfinite(confidence) and confidence > 0.6)

    def _mission_reading(self, state_result: StateDetectionResult) -> MissionReading:
        reading = getattr(state_result, "mission", None)
        if reading is None:
            reading = self._mission_parser.parse("\n".join(
                text for text in (state_result.mission_text, state_result.objective_text,
                                  getattr(state_result, "banner_text", "")) if text
            ))
        return reading

    @staticmethod
    def _mission_display_name(state_result, reading):
        if reading.identity_status == "known_name":
            return reading.mission_name
        return (state_result.mission_text or state_result.objective_text
                or getattr(state_result, "banner_text", "")
                or ("Sell Mission" if state_result.state == GameState.SELLING else "Unknown Mission"))

    @staticmethod
    def _identity_status(state_result, reading):
        if reading.identity_status in ("known_name", "type_only", "ambiguous"):
            return reading.identity_status
        if state_result.state in (GameState.HEIST_PREP, GameState.HEIST_FINALE):
            return "type_only"
        return "unknown"

    @staticmethod
    def _identity_family(reading):
        if (reading.identity_status not in ("known_name", "type_only")
                or reading.mission_type in (MissionType.HEIST_PREP, MissionType.HEIST_FINALE)):
            return MissionType.UNKNOWN
        return reading.mission_type

    @staticmethod
    def _identity_phase(state_result, reading):
        if state_result.state == GameState.HEIST_PREP:
            return MissionType.HEIST_PREP
        if state_result.state == GameState.HEIST_FINALE:
            return MissionType.HEIST_FINALE
        return reading.heist_phase

    def _refine_mission_identity(self, state_result, reading, *, allow_result=False):
        """Fill unresolved identity axes without restarting time, money or activity."""
        current = self._activity_tracker.current_activity
        status = self._data.mission_identity_status
        expected_result = {
            GameState.MISSION_COMPLETE: "complete", GameState.MISSION_FAILED: "failed",
        }.get(state_result.state)
        outcome_allowed = reading.outcome is None or (
            allow_result and expected_result is not None and reading.outcome == expected_result
        )
        if (current is None or reading.identity_status not in ("known_name", "type_only")
                or not outcome_allowed):
            return
        family, phase = self._data.mission_identity_type, self._data.mission_heist_phase
        new_family = self._identity_family(reading)
        new_phase = self._identity_phase(state_result, reading)
        unknown = MissionType.UNKNOWN
        if family != unknown and new_family != unknown and family != new_family:
            return
        if phase != unknown and new_phase != unknown and phase != new_phase:
            return
        resolved_family = new_family if family == unknown else family
        resolved_phase = new_phase if phase == unknown else phase
        heist_families = (MissionType.CAYO_PERICO, MissionType.CASINO_HEIST, MissionType.DOOMSDAY)
        if resolved_phase != unknown and resolved_family not in (*heist_families, unknown):
            return
        if (status == "known_name" and reading.identity_status == "known_name"
                and reading.mission_name != current.name):
            return
        improves_family = family == unknown and new_family != unknown
        improves_phase = phase == unknown and new_phase != unknown
        improves_name = status != "known_name" and reading.identity_status == "known_name"
        if not (improves_family or improves_phase or improves_name):
            return
        kind = self._activity_kind(resolved_family, resolved_phase)
        if kind == ActivityType.UNKNOWN:
            return
        if improves_name:
            current.name = reading.mission_name
        elif status != "known_name" and (improves_family or status in ("unknown", "ambiguous")):
            current.name = self._mission_display_name(state_result, reading)
        current.activity_type = kind
        self._data.current_mission = current.name
        self._data.mission_identity_type = resolved_family
        self._data.mission_heist_phase = resolved_phase
        self._data.mission_identity_status = "known_name" if status == "known_name" or improves_name else "type_only"
        logger.info("Mission identity resolved: %s (%s)", current.name, kind.name)

    @staticmethod
    def _activity_kind(mission_type, phase=MissionType.UNKNOWN):
        if phase == MissionType.HEIST_PREP:
            return ActivityType.HEIST_PREP
        if phase == MissionType.HEIST_FINALE:
            return ActivityType.HEIST_FINALE
        types = {
            MissionType.CONTACT_MISSION: ActivityType.CONTACT_MISSION,
            MissionType.VIP_WORK: ActivityType.VIP_WORK,
            MissionType.MC_CONTRACT: ActivityType.MC_CONTRACT,
            MissionType.SELL_MISSION: ActivityType.SELL_MISSION,
            MissionType.RESUPPLY: ActivityType.RESUPPLY_MISSION,
            MissionType.HEIST_PREP: ActivityType.HEIST_PREP,
            MissionType.HEIST_FINALE: ActivityType.HEIST_FINALE,
            MissionType.SECURITY_CONTRACT: ActivityType.SECURITY_CONTRACT,
            MissionType.PAYPHONE_HIT: ActivityType.PAYPHONE_HIT,
            MissionType.AUTO_SHOP_DELIVERY: ActivityType.AUTO_SHOP_DELIVERY,
            MissionType.CAYO_PERICO: ActivityType.CAYO_PERICO,
            MissionType.CASINO_HEIST: ActivityType.CASINO_HEIST,
            MissionType.DOOMSDAY: ActivityType.DOOMSDAY_HEIST,
            MissionType.FREEMODE_EVENT: ActivityType.FREEMODE_EVENT,
            MissionType.NIGHTCLUB_PROMOTION: ActivityType.NIGHTCLUB_PROMOTION,
        }
        return types.get(mission_type, ActivityType.UNKNOWN)

    def _infer_activity_type(self, state_result: StateDetectionResult,
                             reading: Optional[MissionReading] = None) -> ActivityType:
        """Resolve shared evidence instead of selecting the first matching word."""
        reading = reading if reading is not None else self._mission_reading(state_result)
        if reading.identity_status == "ambiguous":
            return ActivityType.UNKNOWN
        phase = self._identity_phase(state_result, reading)
        if phase != MissionType.UNKNOWN:
            return self._activity_kind(reading.mission_type, phase)
        if reading.identity_status in ("known_name", "type_only"):
            return self._activity_kind(reading.mission_type)
        if state_result.state == GameState.SELLING:
            return ActivityType.SELL_MISSION
        # Preserve the established generic delivery category as unresolved. A
        # later specific name can improve it; ordinary 'deliver' is insufficient.
        text = " ".join(" ".join((state_result.mission_text, state_result.objective_text,
                                  getattr(state_result, "banner_text", ""))).casefold().split())
        if re.search(r"(?<!\w)deliver (?:the )?(?:goods|product)(?!\w)", text):
            return ActivityType.SELL_MISSION
        return ActivityType.UNKNOWN

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
        self._data.mission_identity_status = "unknown"
        self._data.mission_identity_type = MissionType.UNKNOWN
        self._data.mission_heist_phase = MissionType.UNKNOWN

    def _process_business_computer(self) -> None:
        """Process business computer screen to extract stock/supply info."""
        if not self._capture or not self._ocr or not self._ocr.is_available:
            return

        try:
            with self._data_lock:
                if self._stop_event.is_set():
                    return
                target = self._business_screen_target
                generation = self._business_screen_generation

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

            # OCR can be slow. Retire stale batches before parsing can update its
            # last-reading cache, and keep both published caches in this section.
            combined_text = " ".join(text_parts)
            with self._data_lock:
                if self._stop_event.is_set() or generation != self._business_screen_generation:
                    return
                hint = BusinessType[target.upper()] if target is not None else None
                reading = self._business_parser.parse(combined_text, business_hint=hint)

                if not reading.has_data:
                    return
                # Convert business type to ID string
                business_id = reading.business_type.name.lower()
                if business_id not in BUSINESSES:
                    return

                # Missing fields remain unknown. A supply-only screen may have
                # no applicable observations for a business without supplies.
                try:
                    stock_pct, supply_pct, value = normalize_live_business_reading(
                        business_id, reading.stock_level, reading.supply_level,
                        reading.stock_value,
                    )
                except ValueError:
                    return

                self.update_business_state(
                    business_id, stock_pct, supply_pct, value,
                    identity_source="selected_target" if target is not None else "ocr_text",
                )

            description = (
                "Business assigned to selected target" if target is not None else "Business detected"
            )
            stock_text = f"{stock_pct}%" if stock_pct is not None else "unknown"
            supply_text = f"{supply_pct}%" if supply_pct is not None else "unknown"
            value_text = f"${value:,}" if value is not None else "unknown"
            logger.info(
                f"{description}: {reading.business_type.name} - "
                f"Stock: {stock_text}, Supply: {supply_text}, Value: {value_text}"
            )

        except Exception as e:
            logger.error(f"Error processing business computer: {e}")

    def _invalidate_analytics(self) -> None:
        """Clear cached results and allow the next read to refresh immediately."""
        with self._analytics_lock:
            self._cached_efficiency = None
            self._cached_breakdown = None
            self._last_analytics_time = None

    def _recalculate_analytics(self, force: bool = False) -> None:
        """Refresh the cache once per elapsed-time interval, unless forced."""
        with self._analytics_lock:
            current_time = time.monotonic()
            if (
                not force
                and self._last_analytics_time is not None
                and current_time - self._last_analytics_time < self._analytics_min_interval
            ):
                return

            try:
                activities = self._activity_tracker.get_recent_activities(100)
                session_time = self._session_tracker.duration_seconds
                if activities and session_time > 0:
                    # Publish only after both calculations succeed, retaining the
                    # prior snapshot if either raises.
                    efficiency = self._analytics.calculate_efficiency(activities, session_time)
                    breakdown = self._analytics.calculate_earnings_breakdown(activities)
                    self._cached_efficiency = efficiency
                    self._cached_breakdown = breakdown
                    logger.debug(
                        f"Analytics updated: {efficiency.earnings_per_hour:.0f}/hr, "
                        f"best: {efficiency.best_activity_type}"
                    )
                else:
                    self._cached_efficiency = None
                    self._cached_breakdown = None
            except Exception as e:
                logger.error(f"Failed to recalculate analytics: {e}")
            finally:
                # Empty and failed attempts also consume the retry budget.
                self._last_analytics_time = time.monotonic()

    def _adjust_capture_rate(self, state: GameState) -> None:
        """Adjust capture rate based on game state."""
        if state in (GameState.MISSION_ACTIVE, GameState.SELLING,
                     GameState.HEIST_PREP, GameState.HEIST_FINALE):
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
    def history_repository(self) -> Repository:
        """Access recorded history without starting capture or a new session."""
        with self._lifecycle_lock:
            if self._repository is None:
                self._repository = get_repository()
            return self._repository

    @property
    def session_stats(self):
        """Get session statistics."""
        return self._session_tracker.stats

    @property
    def goal_tracker(self) -> SessionGoalController:
        """App-owned target controller shared by the Session card and overlay."""
        return self._goal_controller

    def _refresh_session_goal_locked(self, *, force: bool = False) -> Optional[SessionGoal]:
        """Sample goal inputs under the app data lock, then its controller lock."""
        if not self._goal_controller.has_goal:
            return None
        if not force and self._state in (AppState.STARTING, AppState.STOPPING):
            return self._goal_controller.current_goal
        stats = self._session_tracker.stats
        self._goal_controller.sync(
            stats,
            earnings=max(0, self._data.session_earnings),
            activities=max(0, stats.activities_completed) if stats else 0,
            minutes=max(0, int(stats.duration_seconds // 60)) if stats else 0,
        )
        return self._goal_controller.current_goal

    def refresh_session_goal(self) -> Optional[SessionGoal]:
        """Refresh absolute progress without persisting frequently changing totals."""
        with self._data_lock:
            return self._refresh_session_goal_locked()

    def set_session_goal(
        self, goal_type: GoalType, target: int, display_name: str = "",
    ) -> Optional[SessionGoal]:
        """Choose a remembered target and include this statistics period's totals."""
        with self._data_lock:
            self._refresh_session_goal_locked()
            self._goal_controller.set_goal(goal_type, target, display_name)
            return self._refresh_session_goal_locked()

    def clear_session_goal(self) -> None:
        """Clear the remembered target and its current in-memory progress."""
        with self._data_lock:
            self._goal_controller.clear_goal()

    def retry_session_goal_save(self) -> bool:
        """Retry a target save explicitly; goal progress is never written."""
        with self._data_lock:
            return self._goal_controller.retry_save()

    @property
    def recent_activities(self) -> List[Activity]:
        """Get recent completed activities."""
        return self._activity_tracker.get_recent_activities(10)

    @property
    def recommendations(self) -> List[Recommendation]:
        """Get the visible recommendations shared by every app consumer."""
        return list(self.recommendation_snapshot.visible)

    @property
    def recommendation_snapshot(self) -> RecommendationSnapshot:
        """Filter a complete candidate view against one snooze-time sample.

        Candidate collection and snooze sampling are separate snapshots, not a
        transaction across gameplay and presentation state. No app data lock is
        held while entering the snooze registry.
        """
        candidates = self._collect_recommendation_candidates()
        snoozed = self._recommendation_snoozes.active_ids()
        visible = [rec for rec in candidates if self._recommendation_id(rec) not in snoozed]
        return RecommendationSnapshot(
            visible=tuple(visible[:7]),
            total_candidates=len(candidates),
            hidden_count=len(candidates) - len(visible),
            snoozed_count=len(snoozed),
        )

    def snooze_recommendation(self, recommendation_id: str) -> bool:
        """Snooze a currently generated ID, including candidates below the display limit."""
        if not isinstance(recommendation_id, str) or not recommendation_id.strip():
            return False
        candidates = self._collect_recommendation_candidates()
        if not any(self._recommendation_id(rec) == recommendation_id for rec in candidates):
            return False
        return self._recommendation_snoozes.snooze(recommendation_id)

    def restore_snoozed_recommendations(self) -> None:
        """Clear all manager-owned snoozes without changing their source state."""
        self._recommendation_snoozes.restore_all()

    @staticmethod
    def _recommendation_id(recommendation: Recommendation) -> Optional[str]:
        """Legacy or malformed ID-less records remain visible and unsnoozable."""
        identifier = getattr(recommendation, "recommendation_id", None)
        return identifier if isinstance(identifier, str) and identifier.strip() else None

    def _collect_recommendation_candidates(self) -> List[Recommendation]:
        """Merge and rank every eligible candidate before deduplication and filtering."""
        # Observe both live caches before a concurrent update or clear can change
        # either one. Activity-history recommendations use this captured view.
        with self._data_lock:
            optimizer_recs = self._optimizer.get_recommendations(limit=None)
            business_states_copy = dict(self._data.business_states)

        # Get analytics recommendations (activity-based insights)
        analytics_recs = []
        try:
            activities = self._activity_tracker.get_recent_activities(100)
            if activities:
                analytics_insights = self._analytics.get_recommendation_insights(
                    activities, business_states_copy
                )
                for i, insight in enumerate(analytics_insights):
                    analytics_recs.append(
                        Recommendation(
                            priority=4,  # Lower priority - informational
                            action=insight.text,
                            reason="Based on your activity history",
                            score=0.4 - (i * 0.05),
                            recommendation_id=insight.recommendation_id,
                        )
                    )
        except Exception as e:
            logger.debug(f"Failed to get analytics recommendations: {e}")

        # Rank first so each identity retains its highest-priority representation.
        all_recs = optimizer_recs + analytics_recs
        all_recs.sort(key=lambda r: (r.priority, -r.score))
        seen_ids = set()
        candidates = []
        for recommendation in all_recs:
            identifier = self._recommendation_id(recommendation)
            if identifier is not None:
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
            candidates.append(recommendation)
        return candidates

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
        self._recalculate_analytics()
        return self._cached_efficiency

    @property
    def earnings_breakdown(self) -> Optional[EarningsBreakdown]:
        """Get earnings breakdown by source."""
        self._recalculate_analytics()
        return self._cached_breakdown

    @property
    def best_activity_type(self) -> Optional[str]:
        """Get the best performing activity type."""
        self._recalculate_analytics()
        if self._cached_efficiency:
            return self._cached_efficiency.best_activity_type
        return None

    @property
    def best_activity_rate(self) -> float:
        """Get the earnings rate of the best activity type."""
        self._recalculate_analytics()
        if self._cached_efficiency:
            return self._cached_efficiency.best_activity_rate
        return 0.0

    @property
    def cooldown_tracker(self) -> CooldownTracker:
        """Get the app-owned reminder tracker, available before capture starts."""
        return self._cooldown_tracker

    def reset_session(self) -> None:
        """Reset session tracking."""
        with self._lifecycle_lock:
            with self._data_lock:
                self._refresh_session_goal_locked(force=True)
                start_money = self._data.current_money or 0
                self._data.session_start_money = self._data.current_money
                self._data.session_earnings = 0
                self._session_tracker.start_session(start_money=start_money)
                self._refresh_session_goal_locked(force=True)

        self._invalidate_analytics()

        logger.info("Session reset")

    @property
    def business_screen_target(self) -> Optional[str]:
        """Get the live screen assignment, or None for automatic text identity."""
        with self._data_lock:
            return self._business_screen_target

    def set_business_screen_target(self, business_id: Optional[str]) -> None:
        """Assign future labeled OCR readings to a catalog business, without saving."""
        if business_id is not None and (
            not isinstance(business_id, str)
            or business_id not in BUSINESSES
            or business_id.upper() not in BusinessType.__members__
        ):
            raise ValueError("Business screen target must be None or a supported business ID")
        with self._data_lock:
            if business_id == self._business_screen_target:
                return
            self._business_screen_target = business_id
            self._business_screen_generation += 1

    def get_business_state(self, business_id: str) -> Optional[dict]:
        """Get tracked state for a business."""
        with self._data_lock:
            return self._data.business_states.get(business_id)

    def get_live_business_reading_snapshot(
        self, business_id: str,
    ) -> Optional[LiveBusinessReadingSnapshot]:
        """Capture validated raw observations for a detached, read-only preview."""
        if type(business_id) is not str or business_id not in BUSINESSES:
            raise ValueError("Choose a known business")
        with self._data_lock:
            state = self._data.business_states.get(business_id)
            if state is None:
                return None
            if not isinstance(state, dict):
                raise ValueError("Live business reading is unavailable")
            return create_live_business_reading_snapshot(
                business_id,
                stock_percent=state.get("stock"),
                supply_percent=state.get("supply"),
                stock_value=state.get("value"),
                updated_at=state.get("updated"),
                identity_source=state.get("identity_source"),
                captured_at=datetime.now(timezone.utc),
            )

    def get_live_business_reading_snapshots(self) -> tuple[LiveBusinessReadingSnapshot, ...]:
        """Capture all available raw readings in catalog order under one lock.

        One UTC read time belongs to the entire capture. A malformed catalog
        reading aborts the capture, so callers never receive a partial batch.
        """
        with self._data_lock:
            captured_at = datetime.now(timezone.utc)
            snapshots = []
            for business_id in BUSINESSES:
                state = self._data.business_states.get(business_id)
                if state is None:
                    continue
                if not isinstance(state, dict):
                    raise ValueError("Live business reading is unavailable")
                snapshots.append(create_live_business_reading_snapshot(
                    business_id,
                    stock_percent=state.get("stock"),
                    supply_percent=state.get("supply"),
                    stock_value=state.get("value"),
                    updated_at=state.get("updated"),
                    identity_source=state.get("identity_source"),
                    captured_at=captured_at,
                ))
            return tuple(snapshots)

    def clear_business_readings(self) -> None:
        """Forget live observations and pending OCR, retaining the target and saved data.

        Capture is not paused: a batch started after this operation can publish a
        fresh reading. Session statistics, reminders and saved check-ins remain
        independent of these in-memory business observations.
        """
        with self._data_lock:
            # Even an empty cache can have an OCR batch waiting to publish.
            self._business_screen_generation += 1
            self._data.business_states.clear()
            self._business_parser.clear_readings()
            self._optimizer.clear_business_states()
        logger.info("Live business readings cleared")

    def set_manual_business_reading(
        self,
        business_id: str,
        stock_percent: Optional[int] = None,
        supply_percent: Optional[int] = None,
        value: Optional[int] = None,
    ) -> None:
        """Replace one live reading, preserving unknown fields and retiring old OCR.

        This observation is independent of the screen target and saved history.
        Capture remains available to replace it with a newly started OCR batch.
        Invalid input or an optimizer preparation failure changes no live state.
        """
        stock_percent, supply_percent, value = normalize_live_business_reading(
            business_id, stock_percent, supply_percent, value, manual=True,
        )
        business_type = BusinessType[business_id.upper()]
        with self._data_lock:
            state = {
                "stock": stock_percent,
                "supply": supply_percent,
                "value": value,
                "updated": datetime.now(),
                "identity_source": "manual_entry",
            }
            # Prepare the optimizer's replacement before changing app state or
            # retiring OCR, so validation/calculation failures are atomic too.
            self._optimizer.update_business_state(business_id, stock_percent, supply_percent, value)
            self._data.business_states[business_id] = state
            self._business_screen_generation += 1
            self._business_parser.clear_reading(business_type)
        logger.info(f"Manual live reading entered for {business_id}")

    def update_business_state(
        self,
        business_id: str,
        stock_percent: Optional[int] = None,
        supply_percent: Optional[int] = None,
        value: Optional[int] = None,
        *,
        identity_source: Optional[str] = None,
    ) -> None:
        """Publish a validated live observation without retiring an OCR batch."""
        if identity_source not in (None, "ocr_text", "selected_target", "manual_entry"):
            raise ValueError("Unknown business identity source")
        stock_percent, supply_percent, value = normalize_live_business_reading(
            business_id, stock_percent, supply_percent, value,
        )
        with self._data_lock:
            state = {
                "stock": stock_percent,
                "supply": supply_percent,
                "value": value,
                "updated": datetime.now(),
            }
            if identity_source is not None:
                state["identity_source"] = identity_source
            self._optimizer.update_business_state(business_id, stock_percent, supply_percent, value)
            self._data.business_states[business_id] = state
        logger.debug(f"Business {business_id} updated: stock={stock_percent}%, supply={supply_percent}%")
