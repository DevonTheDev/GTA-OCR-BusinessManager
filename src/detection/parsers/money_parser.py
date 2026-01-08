"""Parser for extracting money values from OCR text."""

import re
from typing import Optional
from dataclasses import dataclass

from ...utils.logging import get_logger


logger = get_logger("parser.money")


@dataclass
class MoneyReading:
    """Parsed money reading from screen."""

    cash: Optional[int] = None
    bank: Optional[int] = None
    total: Optional[int] = None
    raw_text: str = ""

    @property
    def has_value(self) -> bool:
        """Check if any money value was parsed."""
        return self.cash is not None or self.bank is not None or self.total is not None

    @property
    def display_value(self) -> int:
        """Get the best available value for display."""
        if self.total is not None:
            return self.total
        if self.cash is not None and self.bank is not None:
            return self.cash + self.bank
        return self.cash or self.bank or 0


class MoneyParser:
    """Parser for GTA money display text."""

    # Characters that OCR commonly confuses with digits (conservative set)
    # O/o -> 0, l -> 1, S -> 5 (most common confusions)
    # Excluding B, I, g, q as they're too likely to match word text
    OCR_DIGIT_CHARS = r"\dOolS"

    # Patterns for money values
    # GTA formats: "$1,234,567" or "$1.234.567" (locale-dependent)
    # Include OCR-confused characters in the pattern, but require $ to anchor
    MONEY_PATTERN = re.compile(
        rf"\$\s*([{OCR_DIGIT_CHARS},.\s]+)",
        re.IGNORECASE
    )

    # Pattern for cash/bank split display
    # More specific pattern to avoid matching across words
    CASH_BANK_PATTERN = re.compile(
        rf"(?:CASH|DINERO|BARGELD)\s+\$?\s*([{OCR_DIGIT_CHARS},.\s]+)\s+"
        rf"(?:BANK|BANCO)\s+\$?\s*([{OCR_DIGIT_CHARS},.\s]+)",
        re.IGNORECASE
    )

    # Common OCR mistakes and corrections
    OCR_CORRECTIONS = {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "g": "9",
        "q": "9",
        " ": "",  # Remove spaces within numbers
    }

    def __init__(self):
        """Initialize money parser."""
        self._last_valid_reading: Optional[MoneyReading] = None

    def parse(self, text: str) -> MoneyReading:
        """Parse money value(s) from OCR text.

        Args:
            text: Raw OCR text from money display region

        Returns:
            MoneyReading with parsed values
        """
        if not text or not text.strip():
            return MoneyReading(raw_text=text or "")

        try:
            # Clean up the text
            cleaned = self._clean_text(text)

            if not cleaned:
                return MoneyReading(raw_text=text)

            # Try to parse as cash/bank split first
            cash_bank = self._parse_cash_bank(cleaned)
            if cash_bank.has_value:
                self._last_valid_reading = cash_bank
                return cash_bank

            # Try to parse as single value
            single = self._parse_single_value(cleaned)
            if single.has_value:
                self._last_valid_reading = single
                return single

            # Return empty reading but keep raw text
            return MoneyReading(raw_text=text)

        except Exception as e:
            logger.error(f"Unexpected error parsing money text '{text[:50]}...': {e}")
            return MoneyReading(raw_text=text)

    def _clean_text(self, text: str) -> str:
        """Clean OCR text for parsing.

        Args:
            text: Raw OCR text

        Returns:
            Cleaned text ready for parsing
        """
        if not text:
            return ""

        try:
            cleaned = text

            # Normalize unicode characters
            cleaned = cleaned.replace("\u00a0", " ")  # Non-breaking space
            cleaned = cleaned.replace("\u202f", " ")  # Narrow no-break space

            # Note: OCR corrections are applied in _extract_number() to avoid
            # corrupting word text like "CASH" or "BANK"

            return cleaned.strip()
        except Exception as e:
            logger.debug(f"Error cleaning text: {e}")
            return text.strip() if text else ""

    def _apply_ocr_corrections(self, number_text: str) -> str:
        """Apply OCR corrections to number-like text only.

        Args:
            number_text: Text that appears to be a number

        Returns:
            Corrected text
        """
        corrected = number_text
        for wrong, right in self.OCR_CORRECTIONS.items():
            corrected = corrected.replace(wrong, right)
        return corrected

    def _extract_number(self, text: str) -> Optional[int]:
        """Extract a number from text, handling OCR errors.

        Args:
            text: Text containing a number

        Returns:
            Extracted integer or None
        """
        if not text:
            return None

        # Remove currency symbols and whitespace
        cleaned = re.sub(r"[$€£¥\s]", "", text)

        # Apply OCR corrections for number-like contexts
        for wrong, right in self.OCR_CORRECTIONS.items():
            cleaned = cleaned.replace(wrong, right)

        # Remove separators (commas, periods used as thousand separators)
        # GTA uses commas in US locale, periods in EU locales
        # Detect which is the decimal separator (if any)
        if cleaned.count(".") == 1 and cleaned.count(",") == 0:
            # Could be decimal, but GTA shows whole numbers
            cleaned = cleaned.replace(".", "")
        else:
            # Remove all separators
            cleaned = re.sub(r"[,.]", "", cleaned)

        # Extract just digits
        digits = re.sub(r"[^\d]", "", cleaned)

        if digits:
            try:
                value = int(digits)
                # Sanity check: GTA max money is around 2.1B
                if value > 2_200_000_000:
                    logger.debug(f"Parsed value too large, likely OCR error: {value}")
                    return None
                return value
            except ValueError as e:
                logger.debug(f"Failed to convert '{digits}' to int: {e}")
            except OverflowError as e:
                logger.debug(f"Number overflow for '{digits}': {e}")

        return None

    def _parse_cash_bank(self, text: str) -> MoneyReading:
        """Try to parse as cash/bank split display.

        Args:
            text: Cleaned text

        Returns:
            MoneyReading with cash and bank values
        """
        # Look for patterns like "CASH $X | BANK $Y" or just two money values
        match = self.CASH_BANK_PATTERN.search(text)
        if match:
            cash_str = match.group(1)
            bank_str = match.group(2) if match.lastindex >= 2 else None

            cash = self._extract_number(cash_str)
            bank = self._extract_number(bank_str) if bank_str else None

            if cash is not None or bank is not None:
                total = None
                if cash is not None and bank is not None:
                    total = cash + bank
                return MoneyReading(
                    cash=cash,
                    bank=bank,
                    total=total,
                    raw_text=text,
                )

        return MoneyReading(raw_text=text)

    def _parse_single_value(self, text: str) -> MoneyReading:
        """Try to parse as single money value.

        Args:
            text: Cleaned text

        Returns:
            MoneyReading with total value
        """
        # Find all money-like patterns
        matches = self.MONEY_PATTERN.findall(text)

        if matches:
            # Take the first (or largest) match
            values = []
            for match in matches:
                value = self._extract_number(match)
                if value is not None:
                    values.append(value)

            if values:
                # Use the largest value (most likely the actual balance)
                total = max(values)
                return MoneyReading(total=total, raw_text=text)

        # Last resort: just try to find any number
        all_numbers = re.findall(r"[\d,.\s]{3,}", text)
        for num_str in all_numbers:
            value = self._extract_number(num_str)
            if value is not None and value > 0:
                return MoneyReading(total=value, raw_text=text)

        return MoneyReading(raw_text=text)

    def get_last_valid(self) -> Optional[MoneyReading]:
        """Get the last successfully parsed reading."""
        return self._last_valid_reading

    def validate_reading(self, reading: MoneyReading) -> bool:
        """Validate a money reading for sanity.

        Args:
            reading: Reading to validate

        Returns:
            True if reading seems valid
        """
        try:
            if not reading or not reading.has_value:
                return False

            value = reading.display_value

            # GTA Online money is always positive and has a max (around 2.1B for int32)
            if value < 0:
                logger.debug(f"Invalid negative money value: {value}")
                return False

            # Suspiciously small values might be OCR errors
            if value < 100:
                logger.debug(f"Money value too small, likely OCR error: {value}")
                return False

            # Maximum sanity check
            if value > 2_200_000_000:
                logger.debug(f"Money value exceeds GTA maximum: {value}")
                return False

            # Check against last reading if available
            if self._last_valid_reading:
                last_value = self._last_valid_reading.display_value
                # Flag dramatic changes (could be OCR error)
                if last_value > 0:
                    try:
                        ratio = value / last_value
                        # Flag if change is 100x or more in either direction
                        if ratio >= 100 or ratio <= 0.01:
                            logger.warning(
                                f"Suspicious money change: ${last_value:,} -> ${value:,}"
                            )
                            return False
                    except (ZeroDivisionError, OverflowError):
                        # If calculation fails, allow the reading
                        pass

            return True

        except Exception as e:
            logger.error(f"Error validating money reading: {e}")
            return False
