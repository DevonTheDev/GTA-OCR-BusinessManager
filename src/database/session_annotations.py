"""Bounded, immutable saved-session context and its validation contract."""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata


class SessionAnnotationUnavailable(Exception):
    """The chosen completed session or its character is no longer available."""

    def __init__(self):
        super().__init__("This completed session is no longer available.")


class InvalidSessionAnnotation(Exception):
    """Stored annotation data is invalid and must never become an empty draft."""

    def __init__(self):
        super().__init__("Saved session notes are unavailable because their data is invalid.")


class SessionAnnotationConflict(Exception):
    """An editor's expected revision no longer matches the saved annotation."""

    def __init__(self, current: "SessionAnnotation | None"):
        self.current = current
        super().__init__("Session notes changed since they were loaded. Reload before saving.")


def validate_annotation_id(session_id: int) -> None:
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
        raise ValueError("session_id must be a positive integer")


def validate_annotation_revision(revision: int) -> None:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")


def _text(value: str, name: str, *, single_line: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError(f"{name} must contain valid Unicode text") from None
    if "\0" in value:
        raise ValueError(f"{name} cannot contain NUL characters")
    if single_line and any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value):
        raise ValueError(f"{name} must be a single line without control characters")
    return value


def normalize_annotation(label: str, tags: tuple[str, ...] | list[str], note: str
                         ) -> tuple[str, tuple[str, ...], str]:
    """Normalize display values; lengths are Unicode code points, not UTF-16 units."""
    label = _text(label, "Label", single_line=True).strip()
    if len(label) > 80:
        raise ValueError("Label must be at most 80 characters")
    if not isinstance(tags, (tuple, list)):
        raise ValueError("Tags must be a list or tuple of individual tags")
    normalized = []
    seen = set()
    for tag in tags:
        tag = _text(tag, "Tag", single_line=True).strip()
        if not 1 <= len(tag) <= 32 or "," in tag:
            raise ValueError("Each tag must contain 1 to 32 characters and no commas")
        folded = tag.casefold()
        if folded not in seen:
            normalized.append(tag)
            seen.add(folded)
        if len(normalized) > 8:
            raise ValueError("Use at most 8 distinct tags")
    note = _text(note, "Note")
    if len(note) > 4000:
        raise ValueError("Note must be at most 4000 characters")
    return label, tuple(normalized), note


def normalize_annotation_query(query: str | None) -> str | None:
    """Normalize a literal single-line History query before any database access."""
    if query is None:
        return None
    query = _text(query, "Search", single_line=True).strip()
    if len(query) > 200:
        raise ValueError("Search must be at most 200 characters")
    return query or None


@dataclass(frozen=True)
class SessionAnnotation:
    """Detached saved values; ``updated_at`` is always an aware UTC timestamp."""

    session_id: int
    label: str
    tags: tuple[str, ...]
    note: str
    revision: int
    updated_at: datetime

    def __post_init__(self):
        # Manual snapshots must not retain a caller-owned mutable tags list.
        object.__setattr__(self, "tags", tuple(self.tags))
        timestamp = (self.updated_at.replace(tzinfo=timezone.utc)
                     if self.updated_at.tzinfo is None else self.updated_at.astimezone(timezone.utc))
        object.__setattr__(self, "updated_at", timestamp)

    def to_dict(self) -> dict:
        return {
            "available": True,
            "session_id": self.session_id,
            "label": self.label,
            "tags": list(self.tags),
            "note": self.note,
            "revision": self.revision,
            "updated_at": self.updated_at.isoformat(),
        }


def annotation_from_storage(record) -> SessionAnnotation:
    """Validate byte-selected SQLite values without leaking corrupt text in errors.

    Casting text/date columns to BLOB avoids SQLite/SQLAlchemy decoding failures
    before validation. The original SQLite storage types remain authoritative.
    """
    try:
        values = {}
        for field in ("label", "tags_text", "note", "updated_at"):
            if record[f"{field}_type"] != "text" or not isinstance(record[field], bytes):
                raise ValueError("invalid storage type")
            values[field] = record[field].decode("utf-8", errors="strict")
        validate_annotation_id(record["session_id"])
        validate_annotation_revision(record["revision"])
        if record["revision"] == 0:
            raise ValueError("invalid stored revision")
        tags = tuple(values["tags_text"].split(", ")) if values["tags_text"] else ()
        normalized = normalize_annotation(values["label"], tags, values["note"])
        if normalized != (values["label"], tags, values["note"]):
            raise ValueError("noncanonical stored values")
        if ", ".join(normalized[1]) != values["tags_text"]:
            raise ValueError("noncanonical stored tags")
        timestamp = values["updated_at"]
        if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", timestamp):
            raise ValueError("invalid timestamp")
        updated_at = datetime.fromisoformat(timestamp)
        updated_at = (updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None
                      else updated_at.astimezone(timezone.utc))
        return SessionAnnotation(record["session_id"], *normalized, record["revision"], updated_at)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise InvalidSessionAnnotation() from None
