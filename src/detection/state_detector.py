"""Game state detection from screen captures."""

from typing import Optional, Tuple, List
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import cv2

from ..game.state_machine import GameState
from ..utils.logging import get_logger
from .template_matcher import TemplateMatcher
from .ocr_engine import OCREngine


logger = get_logger("detection.state")


@dataclass
class StateDetectionResult:
    """Result of state detection."""

    state: GameState
    confidence: float
    reason: str
    mission_text: str = ""
    objective_text: str = ""
    timer_visible: bool = False
    hud_visible: bool = True


@dataclass
class DetectionContext:
    """Context for detection decisions."""

    last_state: GameState = GameState.UNKNOWN
    last_state_time: datetime = field(default_factory=datetime.now)
    consecutive_same_state: int = 0
    last_mission_text: str = ""
    in_mission_since: Optional[datetime] = None


class StateDetector:
    """Detects current game state from screen captures."""

    # Keywords indicating mission states
    MISSION_ACTIVE_KEYWORDS = [
        "go to", "get to", "reach", "find", "locate", "steal", "take",
        "deliver", "drop off", "destroy", "eliminate", "kill", "protect",
        "defend", "escort", "wait", "survive", "escape", "hack", "collect",
        "pick up", "lose the cops", "lose wanted", "return to", "enter",
        "search", "investigate", "board", "drive", "fly", "land", "follow",
        "photograph", "source", "acquire", "intercept", "retrieve",
    ]

    MISSION_COMPLETE_KEYWORDS = [
        "mission passed", "passed", "job complete", "completed",
        "delivered", "+rp", "+$", "reward", "success", "bonus",
        "well done", "objective complete", "contract complete",
    ]

    MISSION_FAILED_KEYWORDS = [
        "mission failed", "failed", "wasted", "busted",
        "destroyed", "time ran out", "left the area", "abandoned",
        "product lost", "associate died", "target escaped",
    ]

    SELL_MISSION_KEYWORDS = [
        "deliver the product", "deliver the goods", "drop off",
        "delivery vehicle", "sell mission", "product value",
        "deliver all", "remaining deliveries", "drop-off",
        "customer", "buyer", "deliveries remaining", "bonus",
    ]

    VIP_WORK_KEYWORDS = [
        "vip work", "vip challenge", "headhunter", "sightseer",
        "hostile takeover", "executive search", "asset recovery",
        "ceo work", "special cargo", "vehicle cargo", "import",
        "export", "source vehicle", "targets remaining",
    ]

    HEIST_KEYWORDS = [
        "heist", "finale", "setup", "prep", "scope", "cayo perico",
        "diamond casino", "doomsday", "pacific standard", "humane labs",
        "series a", "prison break", "fleeca", "apartment heist",
        "planning board", "support crew", "take", "cut",
    ]

    BUSINESS_KEYWORDS = [
        "stock", "supplies", "product", "value", "production",
        "staff", "equipment", "security", "sell stock",
        "cocaine", "meth", "cash", "weed", "documents",
        "bunker", "nightclub", "warehouse", "acid lab",
        "popularity", "safe", "agency", "payphone",
    ]

    MC_KEYWORDS = [
        "mc business", "mc contract", "clubhouse", "president",
        "road captain", "sergeant at arms", "enforcer",
    ]

    AGENCY_KEYWORDS = [
        "security contract", "payphone hit", "dre", "short trip",
        "vip contract", "agency safe", "imani tech",
    ]

    AUTO_SHOP_KEYWORDS = [
        "auto shop", "customer vehicle", "service", "exotic export",
        "contract", "union depository", "data", "prison",
    ]

    NIGHTCLUB_KEYWORDS = [
        "nightclub", "popularity", "dj", "tony", "warehouse",
        "technician", "goods", "promote",
    ]

    def __init__(
        self,
        template_matcher: Optional[TemplateMatcher] = None,
        ocr_engine: Optional[OCREngine] = None,
    ):
        """Initialize state detector.

        Args:
            template_matcher: Template matcher for UI detection
            ocr_engine: OCR engine for text detection
        """
        self._templates = template_matcher or TemplateMatcher()
        self._ocr = ocr_engine or OCREngine()
        self._context = DetectionContext()

    def detect(
        self,
        image: np.ndarray,
        mission_text_image: Optional[np.ndarray] = None,
        center_text_image: Optional[np.ndarray] = None,
    ) -> StateDetectionResult:
        """Detect current game state from screen capture.

        Args:
            image: Full screen capture (BGR)
            mission_text_image: Optional cropped mission text region
            center_text_image: Optional cropped center screen region

        Returns:
            StateDetectionResult with detected state
        """
        height, width = image.shape[:2]

        # Layer 1: Quick visual checks
        quick_result = self._quick_state_check(image)

        # Layer 2: OCR-based detection if we have the regions
        ocr_result = None
        if mission_text_image is not None or center_text_image is not None:
            ocr_result = self._ocr_state_check(mission_text_image, center_text_image)

        # Layer 3: Template matching
        template_result = self._check_templates(image)

        # Combine results with priority
        final_result = self._combine_results(quick_result, ocr_result, template_result)

        # Update context
        self._update_context(final_result)

        return final_result

    def _quick_state_check(self, image: np.ndarray) -> StateDetectionResult:
        """Perform quick color/pattern-based state checks."""
        height, width = image.shape[:2]

        # Check HUD visibility first
        hud_visible = self._is_hud_visible(image)

        # Check for loading screen (mostly black)
        center_region = image[
            height // 4 : 3 * height // 4,
            width // 4 : 3 * width // 4,
        ]
        avg_brightness = np.mean(center_region)

        if avg_brightness < 15:
            return StateDetectionResult(
                state=GameState.LOADING,
                confidence=0.9,
                reason="Screen mostly black - loading",
                hud_visible=False,
            )

        # Check for cutscene (black bars at top/bottom, or very dark with some content)
        top_bar = image[:int(height * 0.1), :]
        bottom_bar = image[int(height * 0.9):, :]
        if np.mean(top_bar) < 10 and np.mean(bottom_bar) < 10 and avg_brightness > 20:
            return StateDetectionResult(
                state=GameState.CUTSCENE,
                confidence=0.75,
                reason="Black bars detected - cutscene",
                hud_visible=False,
            )

        # Convert to HSV for color detection
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        # Check for mission passed (yellow/gold banner at top)
        top_region = image[: int(height * 0.2), :]
        top_hsv = cv2.cvtColor(top_region, cv2.COLOR_BGR2HSV)

        # Yellow/gold range (GTA mission passed color)
        yellow_mask = cv2.inRange(top_hsv, (18, 80, 150), (35, 255, 255))
        yellow_ratio = np.sum(yellow_mask > 0) / yellow_mask.size

        if yellow_ratio > 0.03:
            return StateDetectionResult(
                state=GameState.MISSION_COMPLETE,
                confidence=0.8,
                reason=f"Yellow banner detected ({yellow_ratio:.1%})",
                hud_visible=hud_visible,
            )

        # Check for mission failed (red wasted/failed screen)
        red_mask = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))
        red_mask2 = cv2.inRange(hsv, (170, 100, 100), (180, 255, 255))
        red_combined = cv2.bitwise_or(red_mask, red_mask2)
        red_ratio = np.sum(red_combined > 0) / red_combined.size

        if red_ratio > 0.02:
            return StateDetectionResult(
                state=GameState.MISSION_FAILED,
                confidence=0.7,
                reason=f"Red elements detected ({red_ratio:.1%})",
                hud_visible=hud_visible,
            )

        # Check for menu (dark overlay with specific patterns)
        if self._is_menu_open(image):
            return StateDetectionResult(
                state=GameState.MENU,
                confidence=0.7,
                reason="Menu overlay detected",
                hud_visible=False,
            )

        # Check for phone open (right side of screen has phone UI)
        phone_region = image[int(height * 0.2):int(height * 0.8), int(width * 0.65):]
        phone_brightness = np.mean(phone_region)
        if phone_brightness > 100 and not hud_visible:
            return StateDetectionResult(
                state=GameState.PHONE,
                confidence=0.6,
                reason="Phone UI detected",
                hud_visible=False,
            )

        # Check for mission objective (white text at top center)
        mission_region = image[int(height * 0.02):int(height * 0.12), int(width * 0.25):int(width * 0.75)]
        mission_brightness = np.mean(mission_region)

        # Check for timer region (indicates active mission)
        timer_region = image[int(height * 0.85):, int(width * 0.8):]
        timer_visible = self._detect_timer_present(timer_region)

        if timer_visible or mission_brightness > 80:
            return StateDetectionResult(
                state=GameState.MISSION_ACTIVE,
                confidence=0.6,
                reason="Mission indicators visible",
                hud_visible=hud_visible,
                timer_visible=timer_visible,
            )

        # Default to idle if HUD is visible
        if hud_visible:
            return StateDetectionResult(
                state=GameState.IDLE,
                confidence=0.5,
                reason="HUD visible, no mission indicators",
                hud_visible=True,
            )

        return StateDetectionResult(
            state=GameState.UNKNOWN,
            confidence=0.0,
            reason="No clear indicators",
            hud_visible=hud_visible,
        )

    def _ocr_state_check(
        self,
        mission_text_image: Optional[np.ndarray],
        center_text_image: Optional[np.ndarray],
    ) -> Optional[StateDetectionResult]:
        """Check state using OCR on text regions."""
        if not self._ocr.is_available:
            return None

        combined_text = ""
        mission_text = ""
        center_text = ""

        # OCR the mission text region
        if mission_text_image is not None:
            result = self._ocr.recognize_preprocessed(mission_text_image, invert=True, scale=2.0)
            mission_text = result.text.lower()
            combined_text += mission_text + " "

        # OCR the center region
        if center_text_image is not None:
            result = self._ocr.recognize_preprocessed(center_text_image, invert=True, scale=2.0)
            center_text = result.text.lower()
            combined_text += center_text

        if not combined_text.strip():
            return None

        # Check for mission complete
        if any(kw in combined_text for kw in self.MISSION_COMPLETE_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_COMPLETE,
                confidence=0.85,
                reason="Mission complete text detected",
                mission_text=mission_text,
            )

        # Check for mission failed
        if any(kw in combined_text for kw in self.MISSION_FAILED_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_FAILED,
                confidence=0.85,
                reason="Mission failed text detected",
                mission_text=mission_text,
            )

        # Check for sell mission
        if any(kw in combined_text for kw in self.SELL_MISSION_KEYWORDS):
            return StateDetectionResult(
                state=GameState.SELLING,
                confidence=0.8,
                reason="Sell mission text detected",
                mission_text=mission_text,
                objective_text=center_text,
            )

        # Check for VIP work
        if any(kw in combined_text for kw in self.VIP_WORK_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_ACTIVE,
                confidence=0.8,
                reason="VIP work text detected",
                mission_text=mission_text,
            )

        # Check for heist
        if any(kw in combined_text for kw in self.HEIST_KEYWORDS):
            # Determine if it's a prep or finale
            if any(kw in combined_text for kw in ["finale", "take", "cut"]):
                return StateDetectionResult(
                    state=GameState.HEIST_FINALE,
                    confidence=0.8,
                    reason="Heist finale text detected",
                    mission_text=mission_text,
                )
            elif any(kw in combined_text for kw in ["prep", "setup", "scope"]):
                return StateDetectionResult(
                    state=GameState.HEIST_PREP,
                    confidence=0.8,
                    reason="Heist prep text detected",
                    mission_text=mission_text,
                )
            else:
                return StateDetectionResult(
                    state=GameState.MISSION_ACTIVE,
                    confidence=0.75,
                    reason="Heist-related text detected",
                    mission_text=mission_text,
                )

        # Check for agency work
        if any(kw in combined_text for kw in self.AGENCY_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_ACTIVE,
                confidence=0.8,
                reason="Agency contract text detected",
                mission_text=mission_text,
            )

        # Check for auto shop
        if any(kw in combined_text for kw in self.AUTO_SHOP_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_ACTIVE,
                confidence=0.75,
                reason="Auto shop contract text detected",
                mission_text=mission_text,
            )

        # Check for business computer
        if any(kw in combined_text for kw in self.BUSINESS_KEYWORDS):
            return StateDetectionResult(
                state=GameState.BUSINESS_COMPUTER,
                confidence=0.75,
                reason="Business UI text detected",
            )

        # Check for active mission
        if any(kw in combined_text for kw in self.MISSION_ACTIVE_KEYWORDS):
            return StateDetectionResult(
                state=GameState.MISSION_ACTIVE,
                confidence=0.7,
                reason="Mission objective text detected",
                mission_text=mission_text,
                objective_text=center_text,
            )

        return None

    def _check_templates(self, image: np.ndarray) -> Optional[StateDetectionResult]:
        """Check for known UI templates."""
        # Check for mission banners
        mission_templates = ["mission_banner", "mission_passed", "mission_failed"]
        match = self._templates.match_any(image, mission_templates)

        if match and match.matched:
            if "passed" in match.template_name:
                state = GameState.MISSION_COMPLETE
            elif "failed" in match.template_name:
                state = GameState.MISSION_FAILED
            else:
                state = GameState.MISSION_ACTIVE

            return StateDetectionResult(
                state=state,
                confidence=match.confidence,
                reason=f"Matched template: {match.template_name}",
            )

        # Check for business computer
        business_templates = ["business_laptop", "business_computer", "mc_laptop", "bunker_laptop"]
        match = self._templates.match_any(image, business_templates)

        if match and match.matched:
            return StateDetectionResult(
                state=GameState.BUSINESS_COMPUTER,
                confidence=match.confidence,
                reason=f"Matched template: {match.template_name}",
            )

        return None

    def _combine_results(
        self,
        quick: StateDetectionResult,
        ocr: Optional[StateDetectionResult],
        template: Optional[StateDetectionResult],
    ) -> StateDetectionResult:
        """Combine detection results from multiple sources."""
        candidates = [quick]
        if ocr:
            candidates.append(ocr)
        if template:
            candidates.append(template)

        # Sort by confidence
        candidates.sort(key=lambda r: r.confidence, reverse=True)

        best = candidates[0]

        # If OCR detected something specific, prefer it
        if ocr and ocr.confidence > 0.7:
            best = ocr

        # Template matches are very reliable
        if template and template.confidence > 0.85:
            best = template

        # Contextual adjustments
        if self._context.last_state == GameState.MISSION_ACTIVE:
            # If we were in mission, stay in mission unless clear evidence otherwise
            if best.state == GameState.IDLE and best.confidence < 0.7:
                return StateDetectionResult(
                    state=GameState.MISSION_ACTIVE,
                    confidence=0.6,
                    reason="Maintaining mission state",
                    mission_text=best.mission_text,
                    hud_visible=best.hud_visible,
                )

        return best

    def _update_context(self, result: StateDetectionResult) -> None:
        """Update detection context."""
        now = datetime.now()

        if result.state == self._context.last_state:
            self._context.consecutive_same_state += 1
        else:
            self._context.consecutive_same_state = 0

            # Track mission start
            if result.state == GameState.MISSION_ACTIVE and self._context.last_state != GameState.MISSION_ACTIVE:
                self._context.in_mission_since = now
            elif result.state not in (GameState.MISSION_ACTIVE, GameState.LOADING, GameState.CUTSCENE):
                self._context.in_mission_since = None

        self._context.last_state = result.state
        self._context.last_state_time = now

        if result.mission_text:
            self._context.last_mission_text = result.mission_text

    def _is_hud_visible(self, image: np.ndarray) -> bool:
        """Check if the game HUD is visible."""
        height, width = image.shape[:2]

        # Check money display region (top-right)
        money_region = image[int(height * 0.01):int(height * 0.06), int(width * 0.78):]

        # HUD text is bright white on dark/transparent background
        # Check for high contrast white pixels
        gray = cv2.cvtColor(money_region, cv2.COLOR_BGR2GRAY)
        white_pixels = np.sum(gray > 200)
        total_pixels = gray.size

        return (white_pixels / total_pixels) > 0.05

    def _is_menu_open(self, image: np.ndarray) -> bool:
        """Check if a menu is open."""
        height, width = image.shape[:2]
        center = image[
            height // 4 : 3 * height // 4,
            width // 4 : 3 * width // 4,
        ]

        # Menus have uniform dark overlay
        gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
        std_dev = np.std(gray)
        avg = np.mean(gray)

        return avg < 60 and std_dev < 40

    def _detect_timer_present(self, region: np.ndarray) -> bool:
        """Detect if a timer is visible in the region."""
        if region.size == 0:
            return False

        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

        # Timers have specific digit patterns - white text
        white_pixels = np.sum(gray > 200)
        total_pixels = gray.size

        # Timer region should have some white text
        return (white_pixels / total_pixels) > 0.02

    @property
    def context(self) -> DetectionContext:
        """Get current detection context."""
        return self._context

    def reset_context(self) -> None:
        """Reset detection context."""
        self._context = DetectionContext()
