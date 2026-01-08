"""Parser for extracting mission information from OCR text."""

import re
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum, auto

from ...utils.logging import get_logger


logger = get_logger("parser.mission")


class MissionType(Enum):
    """Types of missions/activities in GTA Online."""

    UNKNOWN = auto()
    CONTACT_MISSION = auto()
    VIP_WORK = auto()
    MC_CONTRACT = auto()
    SELL_MISSION = auto()
    RESUPPLY = auto()
    HEIST_PREP = auto()
    HEIST_FINALE = auto()
    SECURITY_CONTRACT = auto()
    PAYPHONE_HIT = auto()
    AUTO_SHOP_DELIVERY = auto()
    NIGHTCLUB_PROMOTION = auto()
    CASINO_HEIST = auto()
    CAYO_PERICO = auto()
    DOOMSDAY = auto()
    FREEMODE_EVENT = auto()


@dataclass
class MissionReading:
    """Parsed mission information from screen."""

    mission_type: MissionType = MissionType.UNKNOWN
    mission_name: str = ""
    objective: str = ""
    is_active: bool = False
    keywords_found: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def has_mission(self) -> bool:
        """Check if a mission was detected."""
        return self.mission_type != MissionType.UNKNOWN or bool(self.mission_name)


class MissionParser:
    """Parser for GTA mission text."""

    # Keywords that indicate specific mission types
    MISSION_KEYWORDS = {
        MissionType.VIP_WORK: [
            "vip work", "vip challenge", "headhunter", "sightseer",
            "hostile takeover", "asset recovery", "executive search",
        ],
        MissionType.MC_CONTRACT: [
            "mc contract", "clubhouse contract", "jailbreak", "torched",
            "fragile goods", "outrider", "gun running",
        ],
        MissionType.SELL_MISSION: [
            "deliver", "sell", "drop off", "drop-off", "delivery",
            "product", "goods", "stock", "merchandise",
        ],
        MissionType.RESUPPLY: [
            "resupply", "supplies", "steal supplies", "supply run",
            "source", "acquire",
        ],
        MissionType.HEIST_PREP: [
            "prep", "setup", "preparation", "acquire", "steal",
            "scope out", "gather intel",
        ],
        MissionType.HEIST_FINALE: [
            "finale", "the big con", "silent & sneaky", "aggressive",
            "heist", "take", "score",
        ],
        MissionType.SECURITY_CONTRACT: [
            "security contract", "recover valuables", "gang termination",
            "asset protection", "rescue operation", "vehicle recovery",
        ],
        MissionType.PAYPHONE_HIT: [
            "payphone hit", "payphone", "assassination", "eliminate",
            "the popstar", "the tech entrepreneur", "the cofounder",
        ],
        MissionType.AUTO_SHOP_DELIVERY: [
            "auto shop", "service vehicle", "customer vehicle",
            "deliver the vehicle", "exotic exports",
        ],
        MissionType.NIGHTCLUB_PROMOTION: [
            "nightclub", "popularity", "promote", "promotion",
            "club promotion",
        ],
        MissionType.CAYO_PERICO: [
            "cayo perico", "el rubio", "compound", "drainage tunnel",
            "kosatka", "primary target", "secondary target",
        ],
        MissionType.CASINO_HEIST: [
            "casino heist", "vault", "diamond casino", "casino",
            "big con", "silent", "aggressive approach",
        ],
        MissionType.DOOMSDAY: [
            "doomsday", "act 1", "act 2", "act 3", "data breaches",
            "bogdan", "avenger", "facility",
        ],
        MissionType.FREEMODE_EVENT: [
            "freemode event", "business battle", "checkpoints",
            "king of the castle", "hunt the beast",
        ],
    }

    # Common objective verbs
    OBJECTIVE_VERBS = [
        "go to", "get to", "reach", "find", "locate",
        "steal", "take", "acquire", "collect", "pick up",
        "deliver", "drop off", "bring",
        "destroy", "eliminate", "kill", "take out",
        "protect", "defend", "escort",
        "wait", "survive", "escape", "lose",
        "hack", "access", "breach",
    ]

    def __init__(self):
        """Initialize mission parser."""
        self._last_reading: Optional[MissionReading] = None

    def parse(self, text: str) -> MissionReading:
        """Parse mission information from OCR text.

        Args:
            text: Raw OCR text from mission text region

        Returns:
            MissionReading with parsed information
        """
        if not text or not text.strip():
            return MissionReading(raw_text=text)

        text_lower = text.lower()
        reading = MissionReading(raw_text=text, is_active=True)

        # Identify mission type from keywords
        reading.mission_type, reading.keywords_found = self._identify_mission_type(text_lower)

        # Extract mission name (usually in quotes or after specific patterns)
        reading.mission_name = self._extract_mission_name(text)

        # Extract objective
        reading.objective = self._extract_objective(text)

        if reading.has_mission:
            self._last_reading = reading

        return reading

    def _identify_mission_type(self, text_lower: str) -> tuple[MissionType, list[str]]:
        """Identify mission type from keywords.

        Args:
            text_lower: Lowercase text to search

        Returns:
            Tuple of (MissionType, list of keywords found)
        """
        found_keywords = []
        best_match = MissionType.UNKNOWN
        best_score = 0

        for mission_type, keywords in self.MISSION_KEYWORDS.items():
            score = 0
            type_keywords = []

            for keyword in keywords:
                if keyword in text_lower:
                    score += len(keyword)  # Longer matches = higher score
                    type_keywords.append(keyword)

            if score > best_score:
                best_score = score
                best_match = mission_type
                found_keywords = type_keywords

        return best_match, found_keywords

    def _extract_mission_name(self, text: str) -> str:
        """Extract mission name from text.

        Args:
            text: Text to parse

        Returns:
            Extracted mission name or empty string
        """
        # Look for text in quotes
        quote_match = re.search(r'["\']([^"\']+)["\']', text)
        if quote_match:
            return quote_match.group(1).strip()

        # Look for text after common prefixes
        prefix_patterns = [
            r"mission[:\s]+(.+?)(?:\n|$)",
            r"job[:\s]+(.+?)(?:\n|$)",
            r"contract[:\s]+(.+?)(?:\n|$)",
        ]

        for pattern in prefix_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up - take first line only
                name = name.split("\n")[0].strip()
                if name and len(name) < 50:  # Reasonable length
                    return name

        return ""

    def _extract_objective(self, text: str) -> str:
        """Extract objective text.

        Args:
            text: Text to parse

        Returns:
            Extracted objective or empty string
        """
        text_lower = text.lower()

        # Find sentences starting with objective verbs
        for verb in self.OBJECTIVE_VERBS:
            pattern = rf"({verb}\s+.+?)(?:[.\n]|$)"
            match = re.search(pattern, text_lower)
            if match:
                objective = match.group(1).strip()
                # Capitalize first letter
                return objective[0].upper() + objective[1:] if objective else ""

        # Fall back to first line if it looks like an objective
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            first_line = lines[0]
            # Check if it starts with a verb-like word
            if len(first_line) > 5 and len(first_line) < 100:
                return first_line

        return ""

    def is_mission_complete(self, text: str) -> bool:
        """Check if text indicates mission completion.

        Args:
            text: Text to check

        Returns:
            True if mission appears complete
        """
        completion_keywords = [
            "mission passed", "job complete", "passed",
            "success", "completed", "delivered",
            "rp", "cash", "reward", "+$",
        ]

        text_lower = text.lower()
        matches = sum(1 for kw in completion_keywords if kw in text_lower)
        return matches >= 2  # Need multiple indicators

    def is_mission_failed(self, text: str) -> bool:
        """Check if text indicates mission failure.

        Args:
            text: Text to check

        Returns:
            True if mission appears failed
        """
        failure_keywords = [
            "mission failed", "failed", "wasted",
            "busted", "destroyed", "lost",
        ]

        text_lower = text.lower()
        return any(kw in text_lower for kw in failure_keywords)

    def get_last_reading(self) -> Optional[MissionReading]:
        """Get the last parsed mission reading."""
        return self._last_reading
