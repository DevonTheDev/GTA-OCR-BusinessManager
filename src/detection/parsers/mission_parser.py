"""Conservative, shared mission identity and result parsing for OCR text.

Names and explicit category labels are evidence; ordinary objective vocabulary
is not. These rules use the repository's catalog, without guessing OCR typos.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal, Optional

from ...game.missions import CONTACT_MISSIONS, SECURITY_CONTRACTS, VIP_WORK


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
    """Identity evidence from one screen, without filling gaps from history.

    Candidates are canonical names or enum names for category-only evidence.
    Ambiguous readings retain all competing labels in deterministic order.
    A heist family belongs in mission_type; its explicit phase is independent.
    """

    mission_type: MissionType = MissionType.UNKNOWN
    mission_name: str = ""
    objective: str = ""
    is_active: bool = False
    keywords_found: list[str] = field(default_factory=list)
    raw_text: str = ""
    identity_status: Literal["known_name", "type_only", "ambiguous", "unknown"] = "unknown"
    candidates: tuple[str, ...] = ()
    heist_phase: MissionType = MissionType.UNKNOWN
    outcome: Optional[Literal["complete", "failed", "conflicting"]] = None
    # A scoped result label is independent of identity/phase. Keep its scope
    # even when unqualified, so a template cannot promote it to an activity.
    outcome_scope: Optional[Literal["heist"]] = None

    @property
    def has_mission(self) -> bool:
        """Whether this reading selects an unambiguous mission identity."""
        return self.mission_type != MissionType.UNKNOWN or bool(self.mission_name)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _contains(text: str, phrase: str) -> bool:
    """Match complete words in normalized text, never substrings of words."""
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


# Status banners may wrap across OCR lines. A phrase must start a line or a
# punctuation-delimited segment and finish at its boundary or a payout suffix.
# This avoids treating explanatory text such as "if the mission failed" as a result.
_NAMED_RESULT_PHRASES = {
    "complete": ("mission passed", "job complete", "contract complete"),
    "failed": ("mission failed",),
}
_OUTCOME_PHRASES = {
    "complete": (*_NAMED_RESULT_PHRASES["complete"], "passed"),
    "failed": (
        *_NAMED_RESULT_PHRASES["failed"], "failed", "wasted", "busted", "time ran out",
        "left the area", "abandoned", "product lost", "associate died", "target escaped",
    ),
}
_HEIST_SUCCESS_PHRASE = "heist passed"
_HEIST_SUCCESS = re.compile(
    r"(?:^|[|/:.!])[ \t]*(?P<label>heist\s+passed)(?!\w)"
    r"(?=[ \t]*(?:$|[\r\n|/!.:+$\d-]))",
    re.IGNORECASE | re.MULTILINE,
)


def _is_heist_success(match: re.Match) -> bool:
    # IGNORECASE also equates dotted/dotless I; keep the identity parser's
    # stricter casefold policy without rewriting ordinary result text.
    return _normalize(match["label"]) == _HEIST_SUCCESS_PHRASE


def _named_result_evidence(text: str) -> Iterable[tuple[str, str]]:
    """Yield canonical title/results only from complete segments in one crop."""
    names = {_normalize(name): name for name in MissionParser.MISSION_NAMES}
    titles = "|".join(r"\s+".join(re.escape(word) for word in name.split()) for name in names)
    for outcome, phrases in _NAMED_RESULT_PHRASES.items():
        labels = "|".join(r"\s+".join(phrase.split()) for phrase in phrases)
        pattern = (
            rf"(?:^|[|/:.!])[ \t]*(?:({titles})\s+(?:{labels})|(?:{labels})\s+({titles}))"
            rf"(?=[ \t]*(?:$|[\r\n|/:.!]))"
        )
        for match in re.finditer(pattern, text.casefold(), re.MULTILINE):
            yield names[_normalize(match[1] or match[2])], outcome


def _outcome_evidence(text: str) -> set[str]:
    original = text
    # A wrapped HEIST/PASSED must not fall through to the generic bare PASSED
    # rule. Scope qualification happens after collecting this observation's
    # identities, without joining any independent OCR crops.
    text = _HEIST_SUCCESS.sub(lambda match: "|" if _is_heist_success(match) else match[0], text)
    evidence = {outcome for _, outcome in _named_result_evidence(text)}
    for outcome, phrases in _OUTCOME_PHRASES.items():
        for phrase in phrases:
            words = r"\s+".join(re.escape(word) for word in phrase.split())
            pattern = (
                rf"(?:^|[|/:.!])[ \t]*{words}(?!\w)"
                rf"(?=[ \t]*(?:$|[\r\n|/!.:+$\d-]))"
            )
            if " " not in phrase:
                # A bare status must be an entire label, never the beginning
                # of an objective such as "SUCCESS: Collect the bonus".
                pattern = (
                    rf"(?:^|[|/:.!])[ \t]*{words}[ \t]*[!.]?[ \t]*"
                    rf"(?=$|[\r\n|/])"
                )
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                evidence.add(outcome)
                break
    # OCR can collapse adjacent banners onto one line without punctuation.
    # Preserve contradictory explicit mission results even when neither
    # satisfies the stricter rules required to assert a single result.
    normalized = _normalize(original)
    if _contains(normalized, "mission failed") and any(
        _contains(normalized, phrase)
        for phrase in _NAMED_RESULT_PHRASES["complete"]
    ):
        evidence.update(("complete", "failed"))
    # Only a complete adjacent banner pair may relax the new scoped label's
    # suffix boundary. A heist success mentioned in prose must not suppress an
    # otherwise valid generic failure elsewhere in this crop.
    adjacent = re.finditer(
        r"(?:^|[|/:.!])[ \t]*(heist\s+passed\s+mission\s+failed|"
        r"mission\s+failed\s+heist\s+passed)(?!\w)"
        r"(?=[ \t]*(?:$|[\r\n|/!.:+$\d-]))",
        original, re.IGNORECASE | re.MULTILINE,
    )
    if any(_normalize(match[1]) in ("heist passed mission failed", "mission failed heist passed")
           for match in adjacent):
        evidence.update(("complete", "failed"))
    return evidence


def classify_mission_outcome(text: str) -> Optional[Literal["complete", "failed"]]:
    """Return an explicit result banner, or None for absent/conflicting evidence.

    Rewards, generic congratulations and sub-objective completion are not a
    result for the whole mission. Objective verbs never establish an outcome.
    Both the detector and the parser use this same result policy.
    """
    outcome = MissionParser().parse(text).outcome
    return outcome if outcome in ("complete", "failed") else None


class MissionParser:
    """Identify supported names and explicit categories without forced guesses."""

    # Canonical catalog names are authoritative; supplemental names below were
    # specific named entries in this parser's original MISSION_KEYWORDS.
    MISSION_NAMES = {
        info.name: kind
        for catalog, kind in (
            (CONTACT_MISSIONS, MissionType.CONTACT_MISSION),
            (VIP_WORK, MissionType.VIP_WORK),
            (SECURITY_CONTRACTS, MissionType.SECURITY_CONTRACT),
        )
        for info in catalog.values()
    }
    MISSION_NAMES.update({
        "Asset Recovery": MissionType.VIP_WORK,
        "Executive Search": MissionType.VIP_WORK,
        "Jailbreak": MissionType.MC_CONTRACT,
        "Torched": MissionType.MC_CONTRACT,
        "Fragile Goods": MissionType.MC_CONTRACT,
        "Outrider": MissionType.MC_CONTRACT,
        "Gun Running": MissionType.MC_CONTRACT,
        "Asset Protection": MissionType.SECURITY_CONTRACT,
        "Vehicle Recovery": MissionType.SECURITY_CONTRACT,
        "The Popstar": MissionType.PAYPHONE_HIT,
        "The Tech Entrepreneur": MissionType.PAYPHONE_HIT,
        "The Cofounder": MissionType.PAYPHONE_HIT,
        "Business Battle": MissionType.FREEMODE_EVENT,
        "King of the Castle": MissionType.FREEMODE_EVENT,
        "Hunt the Beast": MissionType.FREEMODE_EVENT,
        "The Big Con": MissionType.CASINO_HEIST,
        "Silent & Sneaky": MissionType.CASINO_HEIST,
        "Data Breaches": MissionType.DOOMSDAY,
    })

    # Only explicit category markers. Generic verbs and shared nouns from the
    # former keyword scores cannot compete against names or assert a category.
    MISSION_KEYWORDS = {
        MissionType.CONTACT_MISSION: ("contact mission",),
        MissionType.VIP_WORK: ("vip work", "vip challenge"),
        MissionType.MC_CONTRACT: ("mc contract", "clubhouse contract"),
        MissionType.SELL_MISSION: ("sell mission",),
        MissionType.RESUPPLY: ("resupply", "supply run"),
        MissionType.SECURITY_CONTRACT: ("security contract",),
        MissionType.PAYPHONE_HIT: ("payphone hit",),
        MissionType.AUTO_SHOP_DELIVERY: (
            "auto shop", "service vehicle", "customer vehicle", "exotic exports",
        ),
        MissionType.NIGHTCLUB_PROMOTION: ("club promotion", "nightclub promotion"),
        MissionType.CAYO_PERICO: ("cayo perico",),
        MissionType.CASINO_HEIST: ("casino heist",),
        MissionType.DOOMSDAY: ("doomsday",),
        MissionType.FREEMODE_EVENT: ("freemode event",),
    }
    HEIST_FAMILIES = {MissionType.CAYO_PERICO, MissionType.CASINO_HEIST, MissionType.DOOMSDAY}
    PHASE_KEYWORDS = {
        MissionType.HEIST_PREP: ("prep", "setup", "preparation"),
        MissionType.HEIST_FINALE: ("finale",),
    }
    OBJECTIVE_VERBS = (
        "go to", "get to", "reach", "find", "locate",
        "steal", "take", "acquire", "collect", "pick up",
        "deliver", "drop off", "bring", "destroy", "eliminate", "kill", "take out",
        "protect", "defend", "escort", "wait", "survive", "escape", "lose",
        "hack", "access", "breach",
    )

    def __init__(self):
        self._last_reading: Optional[MissionReading] = None

    def parse(self, text: str) -> MissionReading:
        """Parse this text only; preserve raw text and the original-case objective."""
        return self.parse_regions((text,))

    def parse_regions(self, texts: Iterable[str]) -> MissionReading:
        """Combine complete evidence from independent OCR crops.

        Wrapping within a crop is allowed; phrases never span crop boundaries.
        Keep the source text and first objective in source order, then resolve
        identity and results before changing the last active reading.
        """
        sources = tuple(text for text in texts if text)
        reading = MissionReading(raw_text="\n".join(sources))
        outcomes = set()
        names = {}
        categories = set()
        phases = set()
        keywords = set()
        for text in sources:
            if any(_is_heist_success(match) for match in _HEIST_SUCCESS.finditer(text)):
                reading.outcome_scope = "heist"
            outcomes.update(_outcome_evidence(text))
            if not reading.objective:
                reading.objective = self._extract_objective(text)
            normalized = _normalize(text)
            region_names = {
                name: kind for name, kind in self.MISSION_NAMES.items()
                if self._contains_name(text, normalized, name)
            }
            region_categories = set()
            keywords.update(_normalize(name) for name in region_names)
            for kind, phrases in self.MISSION_KEYWORDS.items():
                for phrase in phrases:
                    if _contains(normalized, phrase):
                        region_categories.add(kind)
                        keywords.add(phrase)

            region_kinds = region_categories | set(region_names.values())
            for phase, phrases in self.PHASE_KEYWORDS.items():
                for phrase in phrases:
                    # Only a family in this crop can qualify incidental phase
                    # words. Independent labels can combine with another crop's
                    # family; "setup your business" still cannot assert phase.
                    label = rf"^{phrase}\s*(?:$|[:\-])"
                    if (
                        (region_kinds & self.HEIST_FAMILIES and _contains(normalized, phrase))
                        or _contains(normalized, f"heist {phrase}")
                        or any(re.search(label, _normalize(line)) for line in text.splitlines())
                    ):
                        phases.add(phase)
                        keywords.add(phrase)
            names.update(region_names)
            categories.update(region_categories)

        kinds = categories | set(names.values())
        incompatible_phase = bool(phases and kinds and not kinds <= self.HEIST_FAMILIES)
        ambiguous = len(names) > 1 or len(kinds) > 1 or len(phases) > 1 or incompatible_phase
        labels = set(names) | {kind.name for kind in categories - set(names.values())}
        if ambiguous:
            labels.update(phase.name for phase in phases)
            reading.identity_status = "ambiguous"
            reading.candidates = tuple(sorted(labels))
        elif kinds or phases:
            reading.mission_type = next(iter(kinds or phases))
            reading.heist_phase = next(iter(phases), MissionType.UNKNOWN)
            if names:
                reading.identity_status = "known_name"
                reading.mission_name = next(iter(names))
                reading.candidates = (reading.mission_name,)
            else:
                reading.identity_status = "type_only"
                reading.candidates = (reading.mission_type.name,)

        qualified_heist = (
            reading.identity_status in ("known_name", "type_only")
            and (reading.mission_type in self.HEIST_FAMILIES
                 or reading.heist_phase == MissionType.HEIST_FINALE)
            and reading.heist_phase != MissionType.HEIST_PREP
        )
        if reading.outcome_scope == "heist":
            outcomes.add("complete")
        if len(outcomes) > 1:
            reading.outcome = "conflicting"
        elif reading.outcome_scope == "heist" and not qualified_heist:
            reading.outcome = None
        elif "complete" in outcomes:
            reading.outcome = "complete"
        elif "failed" in outcomes:
            reading.outcome = "failed"
        reading.is_active = reading.has_mission and reading.outcome is None and reading.outcome_scope is None

        reading.keywords_found = sorted(keywords)
        if reading.has_mission and reading.is_active:
            self._last_reading = reading
        return reading

    @staticmethod
    def _contains_name(text: str, normalized: str, name: str) -> bool:
        if not _contains(normalized, _normalize(name)):
            return False
        if name == "Blow Up":
            # This catalog title is also an ordinary imperative. Require its
            # end to look like a title, not "blow up the delivery vehicle".
            # An exact title/result segment also identifies it in title-first order.
            return re.search(
                r"(?<!\w)blow\s+up(?=[ \t]*(?:$|[\r\n\"'.:!\-]))", text, re.IGNORECASE
            ) is not None or any(title == name for title, _ in _named_result_evidence(text))
        return True

    def _extract_objective(self, text: str) -> str:
        """Find an imperative while retaining case and joining wrapped lines."""
        normalized_case = " ".join(text.split())
        verbs = "|".join(re.escape(verb) for verb in sorted(self.OBJECTIVE_VERBS, key=len, reverse=True))
        match = re.search(
            rf"(?<!\w)(?:{verbs})\s+[^.!?]+", normalized_case, re.IGNORECASE
        )
        return match.group().strip() if match else ""

    def is_mission_complete(self, text: str) -> bool:
        return classify_mission_outcome(text) == "complete"

    def is_mission_failed(self, text: str) -> bool:
        return classify_mission_outcome(text) == "failed"

    def get_last_reading(self) -> Optional[MissionReading]:
        """Get the last unambiguous active identity, excluding result banners."""
        return self._last_reading
