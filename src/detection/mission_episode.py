"""Immutable, capture-local result evidence; not a gameplay run detector.

A new objective is only a conservative observation heuristic. A retry may reuse
old text, and previously unread stale text can still look new. Keep this separate
from the parser's original-case display objective and independent crop contract.
"""

import re
from dataclasses import dataclass
from typing import Iterable

from .parsers.mission_parser import MissionParser, MissionType, _OUTCOME_PHRASES, _HEIST_SUCCESS_PHRASE


OBJECTIVE_LIMIT = 128


@dataclass(frozen=True)
class MissionIdentity:
    name: str = ""
    family: MissionType = MissionType.UNKNOWN
    phase: MissionType = MissionType.UNKNOWN

    @property
    def explicit(self) -> bool:
        return bool(self.name or self.family != MissionType.UNKNOWN or self.phase != MissionType.UNKNOWN)

    def compatible(self, other: "MissionIdentity") -> bool:
        """Allow unresolved axes, but reject every independently known conflict."""
        unknown = MissionType.UNKNOWN
        if self.name and other.name and self.name != other.name:
            return False
        if self.family != unknown and other.family != unknown and self.family != other.family:
            return False
        if self.phase != unknown and other.phase != unknown and self.phase != other.phase:
            return False
        family = other.family if self.family == unknown else self.family
        phase = other.phase if self.phase == unknown else self.phase
        return phase == unknown or family in (*MissionParser.HEIST_FAMILIES, unknown)

    def shares_identity(self, other: "MissionIdentity") -> bool:
        """Compatibility alone does not establish identity for unknown starts."""
        return self.compatible(other) and bool(
            (self.name and self.name == other.name)
            or (self.family != MissionType.UNKNOWN and self.family == other.family)
            or (self.phase != MissionType.UNKNOWN and self.phase == other.phase)
        )


@dataclass(frozen=True)
class ObjectiveEvidence:
    entries: frozenset[str] = frozenset()
    overflow: bool = False

    def include(self, other: "ObjectiveEvidence") -> "ObjectiveEvidence":
        additions = other.entries - self.entries
        capacity = max(0, OBJECTIVE_LIMIT - len(self.entries))
        return ObjectiveEvidence(
            self.entries | frozenset(sorted(additions)[:capacity]),
            self.overflow or other.overflow or len(additions) > capacity,
        )

    def has_new(self, other: "ObjectiveEvidence") -> bool:
        if self.overflow or other.overflow:
            return False
        # Either direction can be a wrapped title, footer or crop fragment.
        # An extension of a previously seen command alone stays uncertain.
        return any(not any(old == entry or old.startswith(entry + " ")
                           or entry.startswith(old + " ") for old in self.entries)
                   for entry in other.entries)


@dataclass(frozen=True)
class TerminalMissionEpisode:
    identity: MissionIdentity
    objectives: ObjectiveEvidence = ObjectiveEvidence()


# Share the parser's catalog rather than inventing additional mission labels.
_LABELS = set(MissionParser.MISSION_NAMES)
_LABELS.add(_HEIST_SUCCESS_PHRASE)
for _labels in (*MissionParser.MISSION_KEYWORDS.values(),
                *MissionParser.PHASE_KEYWORDS.values(), *_OUTCOME_PHRASES.values()):
    _LABELS.update(_labels)
_LABELS.update(f"heist {word}" for words in MissionParser.PHASE_KEYWORDS.values() for word in words)


def _phrases(values):
    return "|".join(r"\s+".join(re.escape(word) for word in phrase.split())
                    for phrase in sorted(values, key=len, reverse=True))


_LABEL = _phrases(_LABELS)
_PREFIX_LABEL = re.compile(rf"^(?:{_LABEL})(?!\w)\s*", re.IGNORECASE)
_SUFFIX_LABEL = re.compile(rf"(?:^|\n)\s*(?:{_LABEL})$", re.IGNORECASE)
_RESULT = _phrases([_HEIST_SUCCESS_PHRASE, *(
    phrase for phrases in _OUTCOME_PHRASES.values() for phrase in phrases
)])
_RESULT_BOUNDARY = re.compile(rf"(?<!\w)(?:{_RESULT})(?!\w)", re.IGNORECASE)
# Reward rows have a value/sign structure, not an open-ended list of labels.
_FOOTER_ROW = re.compile(
    r"^(?:(?:[a-z]+)\s+)?[+$-]\s*\$?\s*\d|^you\b.*\$\s*\d",
    re.IGNORECASE,
)
_EXPLANATION = re.compile(
    r"(?:^|\s)(?:(?:if|when|once|after|before)\s+(?:you|the player)\b"
    r"|you\s+(?:earn(?:ed)?|receive(?:d)?|will|can|must|have)\b)", re.IGNORECASE,
)
_VERBS = _phrases(MissionParser.OBJECTIVE_VERBS)
# Atomic matching prevents a bare "take out" from falling back to "take" + "out".
_IMPERATIVE = re.compile(rf"^(?>{_VERBS})\s+(.+)$", re.IGNORECASE)
_VERB_START = re.compile(rf"^(?:{_VERBS})(?!\w)", re.IGNORECASE)
_NEXT_IMPERATIVE = re.compile(rf"\s+(?=(?:{_VERBS})(?!\w))", re.IGNORECASE)
_INCOMPLETE_OBJECTS = {"a", "an", "the", "to", "any", "all", "every", "each", "some",
                       "your", "their", "this", "that", "these", "those", "for",
                       "from", "at", "in", "on", "with", "into", "out", "off"}


def _complete_command(text: str):
    normalized = " ".join(text.casefold().split())
    match = _IMPERATIVE.fullmatch(normalized)
    return match if match and any(word not in _INCOMPLETE_OBJECTS
                                  for word in match.group(1).split()) else None


def _segments(crop: str):
    """Result and reward boundaries cannot join commands to payout footers."""
    for segment in re.split(r"[|/:.!?;]+", _RESULT_BOUNDARY.sub("|", crop)):
        lines = []
        for line in segment.splitlines():
            pending = "\n".join(lines)
            pending_command = _strip_labels(pending)
            normalized = " ".join(pending_command.split())
            incomplete = _VERB_START.match(normalized) and not _complete_command(pending_command)
            if (_FOOTER_ROW.match(line.strip()) and not _complete_command(line)
                    and not incomplete):
                yield pending
                lines = []
            else:
                lines.append(line)
        yield "\n".join(lines)


def _strip_labels(text: str) -> str:
    text = text.strip()
    while text:
        stripped = _PREFIX_LABEL.sub("", text).strip()
        without_title = _SUFFIX_LABEL.sub("", stripped).strip()
        remainder = _complete_command(without_title)
        # An object may itself be a category label, including when wrapped:
        # "Deliver the / customer vehicle" is still a complete objective.
        if remainder:
            stripped = without_title
        if stripped == text:
            break
        text = stripped
    return text


def objective_evidence(texts: Iterable[str]) -> ObjectiveEvidence:
    """Collect complete imperative segments without joining separate crops.

    Whitespace wrapping and result/title labels do not change an objective.
    Explanatory prose is not a command merely because it contains a verb. A
    leading prose segment is discarded conservatively, even if later lines
    might instead be a separate objective.
    """
    values = set()
    for crop in texts:
        for segment in _segments(crop or ""):
            segment = _strip_labels(segment)
            # A conditional/reward explanation is not a second imperative;
            # dropping it must not make the preceding command look different.
            segment = _strip_labels(_EXPLANATION.split(segment, maxsplit=1)[0])
            normalized = " ".join(segment.casefold().split())
            if not _IMPERATIVE.fullmatch(normalized):
                continue
            # OCR may merge multiple command lines or wrap inside one. Split
            # their verb-led clauses independently of the reported line breaks.
            for command in _NEXT_IMPERATIVE.split(segment):
                command = " ".join(_strip_labels(command).casefold().split())
                if not _complete_command(command):
                    continue
                if command not in values and len(values) == OBJECTIVE_LIMIT:
                    return ObjectiveEvidence(frozenset(values), True)
                values.add(command)
    return ObjectiveEvidence(frozenset(values))
