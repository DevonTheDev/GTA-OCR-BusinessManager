"""Pytest configuration and fixtures for GTA Business Manager tests."""

import pytest
import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent
sys.path.insert(0, str(src_path))


@pytest.fixture(scope="session")
def test_data_dir():
    """Get the test data directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_money_texts():
    """Sample money display texts for testing."""
    return [
        ("$1,234,567", 1234567),
        ("$50,000", 50000),
        ("$999,999,999", 999999999),
        ("$500", 500),
        ("$0", 0),
        ("$1.234.567", 1234567),  # European format
    ]


@pytest.fixture
def sample_timer_texts():
    """Sample timer display texts for testing."""
    return [
        ("5:30", 330),
        ("0:45", 45),
        ("15:00", 900),
        ("1:30:00", 5400),
        ("0:00", 0),
    ]


@pytest.fixture
def sample_ocr_errors():
    """Sample OCR error corrections for testing."""
    return [
        # (input, expected_money_value)
        ("$1,OOO,OOO", 1000000),  # O -> 0
        ("$l,234,567", 1234567),  # l -> 1
        ("$1,2S4,S67", 1254567),  # S -> 5
    ]
