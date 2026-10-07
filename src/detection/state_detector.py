"""Game state detection from screen captures."""

from typing import Optional, Tuple, List
from dataclasses import dataclass, field, replace
from datetime import datetime
import re

import numpy as np
import cv2

from ..game.state_machine import GameState
from ..utils.logging import get_logger
from .template_matcher import TemplateMatcher
from .ocr_engine import OCREngine
from .mission_episode import objective_evidence
from .parsers.mission_parser import MissionParser, MissionReading, MissionType


logger = get_logger("detection.state")

# Only this fixed category marker may leave the footer admission boundary.
VIP_STATUS_MARKER = "VIP WORK"
_VIP_STATUS_ROW = re.compile(
    r"[ \t]*VIP[ \t]+WORK[ \t]+END(?:[ \t]+[0-9][0-9:.]{0,15})?[ \t]*",
    re.IGNORECASE | re.ASCII,
)


def is_qualified_result_header(reading: MissionReading) -> bool:
    """Require the independent header to prove both heist success and family."""
    return (
        reading.outcome_scope == "heist" and reading.outcome == "complete"
        and reading.identity_status in ("known_name", "type_only")
        and reading.mission_type in MissionParser.HEIST_FAMILIES
        and reading.heist_phase != MissionType.HEIST_PREP
    )


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
    mission: Optional[MissionReading] = None
    banner_text: str = ""
    bottom_objective_text: str = ""
    bottom_objective_command: str = ""
    result_header_text: str = ""
    result_header_evidence: str = ""
    vip_status_text: str = ""
    vip_status_evidence: str = ""


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
        self._mission_parser = MissionParser()

    def detect(
        self,
        image: np.ndarray,
        mission_text_image: Optional[np.ndarray] = None,
        center_text_image: Optional[np.ndarray] = None,
        mission_banner_image: Optional[np.ndarray] = None,
        bottom_objective_image: Optional[np.ndarray] = None,
        result_header_image: Optional[np.ndarray] = None,
        vip_status_image: Optional[np.ndarray] = None,
    ) -> StateDetectionResult:
        """Detect current game state from screen capture.

        Args:
            image: Full screen capture (BGR)
            mission_text_image: Optional cropped mission text region
            center_text_image: Optional cropped center screen region
            mission_banner_image: Optional cropped mission name/result banner
            bottom_objective_image: Optional independent bottom objective crop
            result_header_image: Optional independent result-only heist header
            vip_status_image: Optional independent identity-only VIP status crop

        Returns:
            StateDetectionResult with detected state
        """
        height, width = image.shape[:2]

        # Layer 1: Quick visual checks
        quick_result = self._quick_state_check(image)

        # Layer 2: OCR-based detection if we have the regions
        ocr_result = None
        if any(region is not None for region in (mission_text_image, center_text_image, mission_banner_image)):
            ocr_result = self._ocr_state_check(mission_text_image, center_text_image, mission_banner_image)

        # Layer 3: Template matching
        template_result = self._check_templates(image)

        # Combine results with priority
        final_result = self._combine_results(quick_result, ocr_result, template_result)

        # First preserve the existing decision and its source ownership. Bottom
        # text cannot displace a business screen or lend identity to a result.
        main_reading = final_result.mission
        protected = main_reading is not None and (
            main_reading.outcome is not None or main_reading.outcome_scope is not None
            or main_reading.identity_status == "ambiguous"
        )
        if (bottom_objective_image is not None and self._ocr.is_available
                and not protected
                and final_result.state not in (GameState.BUSINESS_COMPUTER,
                                               GameState.MISSION_COMPLETE,
                                               GameState.MISSION_FAILED)):
            raw = self._ocr.recognize_preprocessed(
                bottom_objective_image, threshold=False, invert=True, scale=2.0,
            ).text
            final_result = replace(final_result, bottom_objective_text=raw)
            command = self._bottom_objective_command(raw)
            if command:
                reading = self._mission_parser.parse_regions((
                    final_result.mission_text, final_result.objective_text,
                    final_result.banner_text, command,
                ))
                if reading.identity_status == "ambiguous":
                    # A strong visual/template cue cannot override conflicting
                    # independent identities or start/refine an activity.
                    final_result = replace(
                        final_result, state=GameState.UNKNOWN, confidence=0.0,
                        reason="Bottom objective conflicts with primary mission identity",
                        mission=reading,
                    )
                elif (reading.identity_status in ("known_name", "type_only")
                      and reading.mission_type not in (MissionType.UNKNOWN, MissionType.HEIST_PREP,
                                                       MissionType.HEIST_FINALE)):
                    if reading.heist_phase == MissionType.HEIST_PREP:
                        state = GameState.HEIST_PREP
                    elif reading.heist_phase == MissionType.HEIST_FINALE:
                        state = GameState.HEIST_FINALE
                    elif reading.mission_type == MissionType.SELL_MISSION:
                        state = GameState.SELLING
                    else:
                        state = GameState.MISSION_ACTIVE
                    objective_result = replace(
                        final_result, state=state, confidence=0.8,
                        reason="Complete bottom objective supplies specific mission identity",
                        mission=reading, bottom_objective_command=command,
                    )
                    final_result = self._combine_results(
                        quick_result, objective_result, template_result,
                    )

        # A stronger generic template may win the primary combination. It must
        # not let the footer override independently observed business fields.
        if ocr_result is None or ocr_result.state != GameState.BUSINESS_COMPUTER:
            final_result = self._vip_status_observation(final_result, vip_status_image)
        # Keep this last: an unqualified HEIST PASSED must veto status activity.
        final_result = self._result_header_observation(final_result, result_header_image)

        # Update context exactly once, after every independent source is resolved.
        self._update_context(final_result)

        return final_result

    @staticmethod
    def _vip_status_marker(raw: str) -> str:
        """Accept a complete bounded row; its numeric suffix has no semantics.

        Keep the raw OCR separately. Never repair spelling, concatenate lines,
        extract a title/command, or interpret the suffix as a timer or result.
        """
        if len(raw) > 512:
            return ""
        try:
            if len(raw.encode("utf-8")) > 512:
                return ""
        except UnicodeEncodeError:
            return ""
        lines = raw.splitlines()
        if len(lines) > 8 or any(len(line.encode("utf-8")) > 128 for line in lines):
            return ""
        return VIP_STATUS_MARKER if any(_VIP_STATUS_ROW.fullmatch(line) for line in lines) else ""

    def _vip_status_observation(self, result, image):
        """Enrich only unprotected identity using the separately admitted marker."""
        reading = result.mission
        protected = reading is not None and (
            reading.outcome is not None or reading.outcome_scope is not None
            or reading.identity_status == "ambiguous"
        )
        if (image is None or not self._ocr.is_available or protected
                or result.state in (GameState.BUSINESS_COMPUTER, GameState.MISSION_COMPLETE,
                                    GameState.MISSION_FAILED)):
            return result
        raw = self._ocr.recognize_preprocessed(
            image, threshold=False, invert=True, scale=2.0,
        ).text
        marker = self._vip_status_marker(raw)
        result = replace(result, vip_status_text=raw, vip_status_evidence=marker)
        if not marker:
            return result
        reading = self._mission_parser.parse_regions((
            result.mission_text, result.objective_text, result.banner_text,
            result.bottom_objective_command, marker,
        ))
        if reading.identity_status == "ambiguous":
            return replace(result, state=GameState.UNKNOWN, confidence=0.0,
                           reason="VIP status conflicts with independent mission identity", mission=reading)
        return replace(result, state=GameState.MISSION_ACTIVE, confidence=0.8,
                       reason="Complete VIP status row supplies category identity", mission=reading)

    def _result_header_observation(self, result, image):
        """Admit only a self-qualified result, retaining primary source priority."""
        reading = result.mission
        protected = reading is not None and (
            reading.outcome is not None or reading.outcome_scope is not None
            or reading.identity_status == "ambiguous"
        )
        if (image is None or not self._ocr.is_available or protected
                or result.state in (GameState.BUSINESS_COMPUTER, GameState.MISSION_COMPLETE,
                                    GameState.MISSION_FAILED)):
            return result
        raw = self._ocr.recognize_preprocessed(
            image, threshold=False, invert=False, scale=2.0,
        ).text
        result = replace(result, result_header_text=raw)
        header = self._mission_parser.parse(raw)
        if header.outcome_scope != "heist":
            return result
        if not is_qualified_result_header(header):
            return replace(result, state=GameState.UNKNOWN, confidence=0.0,
                           reason="Heist result header lacks compatible independent identity")
        merged = self._mission_parser.parse_regions((
            result.mission_text, result.objective_text, result.banner_text,
            result.bottom_objective_command, result.vip_status_evidence, raw,
        ))
        if not is_qualified_result_header(merged):
            return replace(result, state=GameState.UNKNOWN, confidence=0.0,
                           reason="Result header conflicts with primary mission evidence", mission=merged)
        return replace(result, state=GameState.MISSION_COMPLETE, confidence=0.85,
                       reason="Independent family-qualified heist result header", mission=merged,
                       result_header_evidence=raw)

    def _bottom_objective_command(self, raw: str) -> str:
        """Admit one complete family-qualified command, never extracted scraps.

        Numeric/currency text and observed standalone result-footer markers are
        vetoes only. This bounded check cannot recover tokens omitted by OCR.
        """
        if any(char.isdigit() or char in "$€£¥" for char in raw):
            return ""
        if any(line.strip().casefold().rstrip(".!?").strip() in {"rp", "platinum", "continue"}
               for line in raw.splitlines()):
            return ""
        commands = objective_evidence((raw,)).entries
        command = " ".join(raw.split()).rstrip(".!?").strip()
        if len(commands) != 1 or command.casefold() not in commands:
            return ""
        # The admitted, normalized source must retain its own identity too:
        # folding a newline can remove a catalog title's required boundary.
        for text in dict.fromkeys((raw, command)):
            reading = self._mission_parser.parse(text)
            if (reading.outcome is not None or reading.outcome_scope is not None
                    or reading.identity_status not in ("known_name", "type_only")
                    or reading.mission_type in (MissionType.UNKNOWN, MissionType.HEIST_PREP,
                                               MissionType.HEIST_FINALE)):
                return ""
        return command

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
        mission_banner_image: Optional[np.ndarray] = None,
    ) -> Optional[StateDetectionResult]:
        """Check state using OCR on text regions."""
        if not self._ocr.is_available:
            return None

        # Keep all original crops; identity and objectives must survive whichever
        # state classifier wins. Windows OCR does not report a confidence score.
        mission_text = ""
        center_text = ""
        banner_text = ""
        if mission_text_image is not None:
            mission_text = self._ocr.recognize_preprocessed(
                mission_text_image, invert=True, scale=2.0,
            ).text
        if center_text_image is not None:
            center_text = self._ocr.recognize_preprocessed(
                center_text_image, invert=True, scale=2.0,
            ).text
        if mission_banner_image is not None:
            banner_text = self._ocr.recognize_preprocessed(
                mission_banner_image, invert=True, scale=2.0,
            ).text
        text_regions = (mission_text, center_text, banner_text)
        if not any(text.strip() for text in text_regions):
            return None
        reading = self._mission_parser.parse_regions(text_regions)

        def detected(state, confidence, reason):
            return StateDetectionResult(
                state=state, confidence=confidence, reason=reason,
                mission_text=mission_text, objective_text=center_text, mission=reading,
                banner_text=banner_text,
            )

        # Explicit status text can finish an activity; bonus/reward/objective
        # vocabulary alone cannot. These remain heuristic state scores, not OCR
        # confidence or calibrated gameplay accuracy.
        outcome = reading.outcome
        if reading.outcome_scope == "heist" and outcome is None:
            return detected(GameState.UNKNOWN, 0.0, "Heist result lacks compatible identity evidence")
        if outcome in ("complete", "failed"):
            return detected(
                GameState.MISSION_COMPLETE if outcome == "complete" else GameState.MISSION_FAILED,
                0.85, "Explicit mission result text detected",
            )
        if reading.outcome == "conflicting":
            return detected(GameState.UNKNOWN, 0.0, "Conflicting mission result text")
        if reading.identity_status == "ambiguous":
            return detected(GameState.UNKNOWN, 0.0, "Conflicting mission identity text")
        if reading.identity_status in ("known_name", "type_only"):
            if reading.heist_phase == MissionType.HEIST_PREP or reading.mission_type == MissionType.HEIST_PREP:
                state = GameState.HEIST_PREP
            elif reading.heist_phase == MissionType.HEIST_FINALE or reading.mission_type == MissionType.HEIST_FINALE:
                state = GameState.HEIST_FINALE
            elif reading.mission_type == MissionType.SELL_MISSION:
                state = GameState.SELLING
            else:
                state = GameState.MISSION_ACTIVE
            return detected(state, 0.8, "Specific mission identity text detected")

        def contains(keywords):
            return any(re.search(r"(?<!\w)" + re.escape(keyword).replace(r"\ ", r"\s+")
                                 + r"(?!\w)", text, re.IGNORECASE)
                       for text in text_regions for keyword in keywords)

        # A generic objective may establish activity without establishing its
        # identity. Do not confuse 'customer vehicle' with selling, or substrings
        # such as 'cut' in 'executive' with a heist finale.
        if contains(("deliver the product", "deliver the goods", "sell mission",
                     "remaining deliveries", "deliveries remaining")):
            return detected(GameState.SELLING, 0.75, "Delivery objective text detected")
        # A result table's "Take" label is not an imperative objective. Keep
        # other existing generic cues, but admit take-only text only when one
        # crop supplies a complete Take/Take out command. An unrelated command
        # cannot validate it, and objective_evidence never stitches crops.
        other_keywords = [keyword for keyword in self.MISSION_ACTIVE_KEYWORDS if keyword != "take"]
        if contains(other_keywords) or (contains(("take",)) and any(
                command.startswith("take ") for command in objective_evidence(text_regions).entries)):
            return detected(GameState.MISSION_ACTIVE, 0.7, "Mission objective text detected; identity unresolved")
        if contains(self.BUSINESS_KEYWORDS):
            return detected(GameState.BUSINESS_COMPUTER, 0.75, "Business UI text detected")
        return detected(GameState.UNKNOWN, 0.0, "No specific mission text evidence")

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
        outcomes = (GameState.MISSION_COMPLETE, GameState.MISSION_FAILED)
        if ocr is not None and ocr.mission is not None:
            if ocr.mission.outcome_scope == "heist" and ocr.mission.outcome is None:
                return ocr
            if ocr.mission.outcome == "conflicting":
                return ocr
            if ocr.mission.outcome in ("complete", "failed"):
                if template is not None and template.state in outcomes and template.state != ocr.state:
                    return replace(ocr, state=GameState.UNKNOWN, confidence=0.0,
                                   reason="Text and template mission results disagree")
                # A generic mission-banner template cannot turn a result screen
                # into a fresh active mission. Explicit result text wins here.
                return ocr
        if quick.state in outcomes:
            # Yellow/red scenery is only a visual cue, not proof of a result.
            # OCR or an actual result-template match must corroborate it.
            quick = replace(quick, state=GameState.UNKNOWN, confidence=0.0,
                            reason="Visual result cue lacks text/template confirmation")
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
                    mission_text=ocr.mission_text if ocr is not None else best.mission_text,
                    objective_text=ocr.objective_text if ocr is not None else best.objective_text,
                    mission=ocr.mission if ocr is not None else best.mission,
                    banner_text=ocr.banner_text if ocr is not None else best.banner_text,
                    bottom_objective_text=(ocr.bottom_objective_text if ocr is not None
                                           else best.bottom_objective_text),
                    bottom_objective_command=(ocr.bottom_objective_command if ocr is not None
                                              else best.bottom_objective_command),
                    result_header_text=(ocr.result_header_text if ocr is not None
                                        else best.result_header_text),
                    result_header_evidence=(ocr.result_header_evidence if ocr is not None
                                            else best.result_header_evidence),
                    vip_status_text=(ocr.vip_status_text if ocr is not None else best.vip_status_text),
                    vip_status_evidence=(ocr.vip_status_evidence if ocr is not None
                                         else best.vip_status_evidence),
                    hud_visible=best.hud_visible,
                )

        if ocr is not None:
            best = replace(best, mission_text=ocr.mission_text,
                           objective_text=ocr.objective_text, mission=ocr.mission,
                           banner_text=ocr.banner_text,
                           bottom_objective_text=ocr.bottom_objective_text,
                           bottom_objective_command=ocr.bottom_objective_command,
                           result_header_text=ocr.result_header_text,
                           result_header_evidence=ocr.result_header_evidence,
                           vip_status_text=ocr.vip_status_text,
                           vip_status_evidence=ocr.vip_status_evidence)
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
