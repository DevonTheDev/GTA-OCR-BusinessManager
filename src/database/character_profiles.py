"""Strict, detached saved-character creation values, independent of capture."""

from dataclasses import dataclass
import unicodedata


MAX_CHARACTER_NAME_CHARACTERS = 50
MAX_CHARACTER_NAME_BYTES = MAX_CHARACTER_NAME_CHARACTERS * 4
_SQLITE_MAX_INTEGER = 2**63 - 1


class CharacterProfileError(Exception):
    """A saved-character failure whose message is safe to show directly."""


class CharacterProfileValidationError(CharacterProfileError, ValueError):
    """The proposed name is invalid; storage has not been accessed."""


class CharacterProfileAmbiguous(CharacterProfileError):
    """Several legacy rows have this exact name; do not choose or merge them."""

    def __init__(self):
        super().__init__("More than one saved character has this name. Choose an existing character by ID.")


class CharacterProfileLimitError(CharacterProfileError):
    """Another character would exceed the manual board's existing capacity."""

    def __init__(self):
        super().__init__("The saved-character limit has been reached. Choose an existing saved character.")


class CharacterProfileUnavailable(CharacterProfileError):
    """Storage is unavailable or cannot return a valid saved identity."""

    def __init__(self):
        super().__init__("The saved character could not be saved or loaded. Refresh and try again.")


def normalize_character_name(name: str) -> str:
    """Trim only outer spaces; retain case, internal spacing and Unicode form.

    Validate before trimming so controls or pasted line breaks never disappear
    silently. Format characters may accompany visible text (for example an emoji
    joiner), but cannot constitute the entire name.
    """
    if not isinstance(name, str):
        raise CharacterProfileValidationError("Enter a character name as text.")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in name):
        raise CharacterProfileValidationError("Use a single-line name without control characters.")
    name = name.strip()
    if not 1 <= len(name) <= MAX_CHARACTER_NAME_CHARACTERS:
        raise CharacterProfileValidationError("Use a character name from 1 to 50 characters.")
    if not any(not char.isspace() and unicodedata.category(char) != "Cf" for char in name):
        raise CharacterProfileValidationError("Enter a nonblank character name.")
    return name


@dataclass(frozen=True)
class SavedCharacterResult:
    """A validated identity; repository callers receive it only after commit."""

    id: int
    name: str
    created: bool

    def __post_init__(self):
        try:
            if (isinstance(self.id, bool) or not isinstance(self.id, int)
                    or not 1 <= self.id <= _SQLITE_MAX_INTEGER
                    or not isinstance(self.created, bool)
                    or normalize_character_name(self.name) != self.name):
                raise ValueError("Invalid saved identity")
        except (ValueError, TypeError, UnicodeError):
            raise CharacterProfileUnavailable() from None


def saved_character_from_storage(record, *, created: bool) -> SavedCharacterResult:
    """Validate bounded raw storage without decoding arbitrary legacy values."""
    try:
        raw_name = record["name"]
        if (record["name_type"] != "text" or not isinstance(raw_name, bytes)
                or len(raw_name) > MAX_CHARACTER_NAME_BYTES):
            raise ValueError("Invalid saved name")
        name = raw_name.decode("utf-8", errors="strict")
        return SavedCharacterResult(record["id"], name, created)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CharacterProfileUnavailable() from None
