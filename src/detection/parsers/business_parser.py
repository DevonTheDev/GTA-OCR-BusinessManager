"""Parser for extracting business information from OCR text."""

import re
from typing import Optional
from dataclasses import dataclass
from enum import Enum, auto

from ...utils.logging import get_logger


logger = get_logger("parser.business")


class BusinessType(Enum):
    """Types of businesses in GTA Online."""

    UNKNOWN = auto()
    COCAINE = auto()
    METH = auto()
    CASH = auto()
    WEED = auto()
    DOCUMENTS = auto()
    BUNKER = auto()
    NIGHTCLUB = auto()
    AGENCY = auto()
    ACID_LAB = auto()
    HANGAR = auto()
    VEHICLE_WAREHOUSE = auto()
    SPECIAL_CARGO = auto()
    AUTO_SHOP = auto()


@dataclass
class BusinessReading:
    """Parsed business information from screen."""

    business_type: BusinessType = BusinessType.UNKNOWN
    stock_level: Optional[int] = None  # Percentage 0-100 or unit count
    stock_value: Optional[int] = None  # Dollar value
    supply_level: Optional[int] = None  # Percentage 0-100
    product_units: Optional[int] = None  # Number of product units
    is_full: bool = False
    is_empty: bool = False
    needs_supplies: bool = False
    raw_text: str = ""

    @property
    def has_data(self) -> bool:
        """Check if any business data was parsed."""
        return (
            self.stock_level is not None
            or self.supply_level is not None
            or self.stock_value is not None
        )


class BusinessParser:
    """Parser for GTA business computer displays."""

    # Keywords to identify business type
    BUSINESS_KEYWORDS = {
        BusinessType.COCAINE: ["cocaine", "coke", "lockup"],
        BusinessType.METH: ["meth", "methamphetamine", "lab"],
        BusinessType.CASH: ["cash", "counterfeit", "factory"],
        BusinessType.WEED: ["weed", "marijuana", "farm"],
        BusinessType.DOCUMENTS: ["document", "forgery", "office"],
        BusinessType.BUNKER: ["bunker", "research", "manufacturing"],
        BusinessType.NIGHTCLUB: ["nightclub", "club", "warehouse"],
        BusinessType.AGENCY: ["agency", "security", "contract"],
        BusinessType.ACID_LAB: ["acid", "lab"],
        BusinessType.HANGAR: ["hangar", "air freight", "cargo"],
        BusinessType.VEHICLE_WAREHOUSE: ["vehicle", "warehouse", "import", "export"],
        BusinessType.SPECIAL_CARGO: ["special cargo", "crate", "warehouse"],
        BusinessType.AUTO_SHOP: ["auto shop", "service", "mod shop"],
    }

    # Patterns for extracting values
    STOCK_PATTERNS = [
        re.compile(r"stock[:\s]*(\d+)\s*[/%]", re.IGNORECASE),
        re.compile(r"stock[:\s]*(\d+)\s*/\s*(\d+)", re.IGNORECASE),
        re.compile(r"product[:\s]*(\d+)", re.IGNORECASE),
        re.compile(r"(\d+)\s*[/%]\s*(?:full|stock)", re.IGNORECASE),
    ]

    SUPPLY_PATTERNS = [
        re.compile(r"suppl(?:y|ies)[:\s]*(\d+)\s*[/%]", re.IGNORECASE),
        re.compile(r"suppl(?:y|ies)[:\s]*(\d+)\s*/\s*(\d+)", re.IGNORECASE),
        re.compile(r"(\d+)\s*[/%]\s*suppl", re.IGNORECASE),
    ]

    VALUE_PATTERNS = [
        re.compile(r"value[:\s]*\$?\s*([\d,]+)", re.IGNORECASE),
        re.compile(r"\$\s*([\d,]+)\s*(?:value|worth)", re.IGNORECASE),
        re.compile(r"sell[:\s]*\$?\s*([\d,]+)", re.IGNORECASE),
        re.compile(r"worth[:\s]*\$?\s*([\d,]+)", re.IGNORECASE),
    ]

    UNIT_PATTERNS = [
        re.compile(r"(\d+)\s*(?:units?|crates?|bars?)", re.IGNORECASE),
        re.compile(r"units?[:\s]*(\d+)", re.IGNORECASE),
    ]

    def __init__(self):
        """Initialize business parser."""
        self._last_readings: dict[BusinessType, BusinessReading] = {}

    def parse(self, text: str, business_hint: Optional[BusinessType] = None) -> BusinessReading:
        """Parse business information from OCR text.

        Args:
            text: Raw OCR text from business screen
            business_hint: Optional hint about which business this is

        Returns:
            BusinessReading with parsed information
        """
        if not text or not text.strip():
            return BusinessReading(raw_text=text)

        reading = BusinessReading(raw_text=text)

        # Identify business type
        reading.business_type = business_hint or self._identify_business(text)

        # Extract stock level
        reading.stock_level = self._extract_stock(text)

        # Extract supply level
        reading.supply_level = self._extract_supply(text)

        # Extract value
        reading.stock_value = self._extract_value(text)

        # Extract units
        reading.product_units = self._extract_units(text)

        # Determine states
        if reading.stock_level is not None:
            reading.is_full = reading.stock_level >= 95
            reading.is_empty = reading.stock_level <= 5

        if reading.supply_level is not None:
            reading.needs_supplies = reading.supply_level <= 20

        # Store last reading per business type
        if reading.has_data and reading.business_type != BusinessType.UNKNOWN:
            self._last_readings[reading.business_type] = reading

        return reading

    def _identify_business(self, text: str) -> BusinessType:
        """Identify business type from text.

        Args:
            text: Text to analyze

        Returns:
            Identified BusinessType
        """
        text_lower = text.lower()

        for business_type, keywords in self.BUSINESS_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return business_type

        return BusinessType.UNKNOWN

    def _extract_stock(self, text: str) -> Optional[int]:
        """Extract stock level percentage."""
        for pattern in self.STOCK_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    value = int(match.group(1))
                    # Check for X/Y format
                    if match.lastindex >= 2:
                        max_val = int(match.group(2))
                        if max_val > 0:
                            value = int((value / max_val) * 100)
                    # Ensure it's a percentage
                    if 0 <= value <= 100:
                        return value
                except ValueError:
                    continue
        return None

    def _extract_supply(self, text: str) -> Optional[int]:
        """Extract supply level percentage."""
        for pattern in self.SUPPLY_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    value = int(match.group(1))
                    if match.lastindex >= 2:
                        max_val = int(match.group(2))
                        if max_val > 0:
                            value = int((value / max_val) * 100)
                    if 0 <= value <= 100:
                        return value
                except ValueError:
                    continue
        return None

    def _extract_value(self, text: str) -> Optional[int]:
        """Extract dollar value of stock."""
        for pattern in self.VALUE_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    # Remove commas from number
                    value_str = match.group(1).replace(",", "")
                    value = int(value_str)
                    if value >= 0:
                        return value
                except ValueError:
                    continue
        return None

    def _extract_units(self, text: str) -> Optional[int]:
        """Extract number of product units."""
        for pattern in self.UNIT_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    value = int(match.group(1))
                    if value >= 0:
                        return value
                except ValueError:
                    continue
        return None

    def get_last_reading(self, business_type: BusinessType) -> Optional[BusinessReading]:
        """Get the last reading for a specific business.

        Args:
            business_type: Type of business

        Returns:
            Last reading or None
        """
        return self._last_readings.get(business_type)

    def get_all_last_readings(self) -> dict[BusinessType, BusinessReading]:
        """Get all stored last readings."""
        return self._last_readings.copy()
