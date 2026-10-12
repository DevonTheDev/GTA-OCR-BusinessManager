"""Unit tests for OCR text parsers."""

import pytest

from src.detection.parsers.money_parser import MoneyParser, MoneyReading
from src.detection.parsers.timer_parser import TimerParser, TimerReading


class TestMoneyReading:
    """Tests for MoneyReading dataclass."""

    def test_empty_reading_has_no_value(self):
        reading = MoneyReading()
        assert not reading.has_value
        assert reading.display_value == 0

    def test_cash_only_reading(self):
        reading = MoneyReading(cash=50000)
        assert reading.has_value
        assert reading.display_value == 50000

    def test_bank_only_reading(self):
        reading = MoneyReading(bank=100000)
        assert reading.has_value
        assert reading.display_value == 100000

    def test_total_only_reading(self):
        reading = MoneyReading(total=1500000)
        assert reading.has_value
        assert reading.display_value == 1500000

    def test_cash_and_bank_reading(self):
        reading = MoneyReading(cash=25000, bank=75000)
        assert reading.has_value
        assert reading.display_value == 100000

    def test_total_takes_precedence(self):
        reading = MoneyReading(cash=25000, bank=75000, total=150000)
        assert reading.display_value == 150000


class TestMoneyParser:
    """Tests for MoneyParser."""

    @pytest.fixture
    def parser(self):
        return MoneyParser()

    # Basic parsing tests
    def test_parse_simple_dollar_amount(self, parser):
        reading = parser.parse("$1,234,567")
        assert reading.has_value
        assert reading.display_value == 1234567

    def test_parse_no_commas(self, parser):
        reading = parser.parse("$1234567")
        assert reading.has_value
        assert reading.display_value == 1234567

    def test_parse_small_amount(self, parser):
        reading = parser.parse("$500")
        assert reading.has_value
        assert reading.display_value == 500

    def test_parse_million(self, parser):
        reading = parser.parse("$5,000,000")
        assert reading.has_value
        assert reading.display_value == 5000000

    def test_parse_european_format(self, parser):
        """European format uses periods as thousand separators."""
        reading = parser.parse("$1.234.567")
        assert reading.has_value
        assert reading.display_value == 1234567

    # OCR error correction tests
    def test_ocr_correction_O_to_0(self, parser):
        reading = parser.parse("$1,OOO,OOO")
        assert reading.has_value
        assert reading.display_value == 1000000

    def test_ocr_correction_l_to_1(self, parser):
        reading = parser.parse("$l,234,567")
        assert reading.has_value
        assert reading.display_value == 1234567

    def test_ocr_correction_S_to_5(self, parser):
        reading = parser.parse("$1,2S4,S67")
        assert reading.has_value
        assert reading.display_value == 1254567

    # Edge cases
    def test_parse_empty_string(self, parser):
        reading = parser.parse("")
        assert not reading.has_value

    def test_parse_whitespace_only(self, parser):
        reading = parser.parse("   ")
        assert not reading.has_value

    def test_parse_no_dollar_sign(self, parser):
        """Should still try to find numbers without dollar sign."""
        reading = parser.parse("1,234,567")
        assert reading.has_value
        assert reading.display_value == 1234567

    def test_parse_with_extra_text(self, parser):
        reading = parser.parse("Balance: $1,234,567 available")
        assert reading.has_value
        assert reading.display_value == 1234567

    def test_parse_multiple_values_takes_largest(self, parser):
        """When multiple values present without labels, take the largest."""
        reading = parser.parse("$500 $1,000,000")
        assert reading.has_value
        # Without CASH/BANK labels, parser takes the largest single value
        assert reading.display_value == 1000000

    # Cash/Bank split tests
    def test_parse_cash_bank_format(self, parser):
        reading = parser.parse("CASH $50,000 BANK $100,000")
        assert reading.has_value
        assert reading.cash == 50000
        assert reading.bank == 100000
        assert reading.display_value == 150000

    # Validation tests
    def test_validate_negative_value(self, parser):
        reading = MoneyReading(total=-1000)
        assert not parser.validate_reading(reading)

    def test_validate_very_small_value(self, parser):
        """Values under 100 are likely OCR errors."""
        reading = MoneyReading(total=50)
        assert not parser.validate_reading(reading)

    def test_validate_normal_value(self, parser):
        reading = MoneyReading(total=1000000)
        assert parser.validate_reading(reading)

    def test_validate_suspicious_change(self, parser):
        """Flag dramatic changes that could be OCR errors."""
        # First establish a baseline
        first = parser.parse("$1,000,000")
        assert parser.validate_reading(first)

        # Now a dramatically different value
        second = MoneyReading(total=100000000)  # 100x increase
        assert not parser.validate_reading(second)


class TestTimerReading:
    """Tests for TimerReading dataclass."""

    def test_empty_reading_has_no_value(self):
        reading = TimerReading()
        assert not reading.has_value
        assert reading.formatted == "0:00"

    def test_seconds_only(self):
        reading = TimerReading(seconds=45, total_seconds=45)
        assert reading.has_value
        assert reading.formatted == "0:45"

    def test_minutes_and_seconds(self):
        reading = TimerReading(minutes=5, seconds=30, total_seconds=330)
        assert reading.has_value
        assert reading.formatted == "5:30"

    def test_hours_minutes_seconds(self):
        reading = TimerReading(hours=1, minutes=30, seconds=45, total_seconds=5445)
        assert reading.has_value
        assert reading.formatted == "1:30:45"

    def test_zero_time_with_raw_text(self):
        """0:00 is valid when explicitly shown."""
        reading = TimerReading(raw_text="0:00")
        assert reading.has_value


class TestTimerParser:
    """Tests for TimerParser."""

    @pytest.fixture
    def parser(self):
        return TimerParser()

    # MM:SS format tests
    def test_parse_mmss_basic(self, parser):
        reading = parser.parse("5:30")
        assert reading.has_value
        assert reading.minutes == 5
        assert reading.seconds == 30
        assert reading.total_seconds == 330

    def test_parse_mmss_leading_zero(self, parser):
        reading = parser.parse("05:30")
        assert reading.has_value
        assert reading.minutes == 5
        assert reading.seconds == 30

    def test_parse_mmss_single_digit_minutes(self, parser):
        reading = parser.parse("1:45")
        assert reading.has_value
        assert reading.minutes == 1
        assert reading.seconds == 45

    def test_parse_mmss_zero_seconds(self, parser):
        reading = parser.parse("3:00")
        assert reading.has_value
        assert reading.minutes == 3
        assert reading.seconds == 0

    @pytest.mark.parametrize("text, minutes, cleaned", [
        ("10:00", 10, "10:00"),
        ("20:00", 20, "20:00"),
        ("90:00", 90, "90:00"),
        ("10;00", 10, "10:00"),
        ("2O:OO", 20, "20:00"),
        ("10.00", 10, "10:00"),
        ("10 :00", 10, "10 :00"),
        (" 20:00 ", 20, "20:00"),
        ("Time: 20:00 remaining", 20, "Time: 20:00 remaining"),
    ])
    def test_parse_mmss_zero_suffix_keeps_minute_value(self, parser, text, minutes, cleaned):
        """An unmatched hours stage must let the minute parser own this reading."""
        reading = parser.parse(text)

        assert reading.has_value
        assert (reading.hours, reading.minutes, reading.seconds) == (0, minutes, 0)
        assert reading.total_seconds == minutes * 60
        assert reading.formatted == f"{minutes}:00"
        assert reading.raw_text == cleaned
        assert parser.get_last_valid() is reading

    def test_unmatched_hhmmss_is_an_empty_unsuccessful_reading(self, parser):
        reading = parser._parse_hhmmss("20:00")

        assert not reading.has_value
        assert reading == TimerReading()
        assert parser.get_last_valid() is None

    @pytest.mark.parametrize("text", ["0:00", "00:00", "0:00:00", "00:00:00"])
    def test_parse_genuine_zero_is_valid_and_cached(self, parser, text):
        reading = parser.parse(text)

        assert reading.has_value
        assert (reading.hours, reading.minutes, reading.seconds, reading.total_seconds) == (0, 0, 0, 0)
        assert reading.formatted == "0:00"
        assert reading.raw_text == text
        assert parser.get_last_valid() is reading

    @pytest.mark.parametrize("text, hours, minutes, seconds, total", [
        ("1:00:00", 1, 0, 0, 3600),
        ("10:00:00", 10, 0, 0, 36000),
        ("1:20:00", 1, 20, 0, 4800),
        ("99:59:59", 99, 59, 59, 359999),
    ])
    def test_matched_hours_keep_precedence(self, parser, text, hours, minutes, seconds, total):
        reading = parser.parse(text)

        assert reading.has_value
        assert (reading.hours, reading.minutes, reading.seconds) == (hours, minutes, seconds)
        assert reading.total_seconds == total
        assert reading.formatted == text
        assert reading.raw_text == text
        assert parser.get_last_valid() is reading

    def test_rejected_hhmmss_match_keeps_existing_raw_text_fallback(self, parser):
        # Preserve this legacy admission while fixing only the no-match branch.
        # Dropping raw text on every HH failure would instead admit MM prefix 60:00.
        reading = parser.parse("60:00:60")

        assert reading == TimerReading(raw_text="60:00:60")
        assert reading.has_value
        assert parser.get_last_valid() is reading

    @pytest.mark.parametrize("text, minutes, seconds, total", [
        ("10:01", 10, 1, 601),
        ("99:59", 99, 59, 5999),
        ("120s", 2, 0, 120),
    ])
    def test_unaffected_minute_and_seconds_values_remain_cached(self, parser, text, minutes, seconds, total):
        reading = parser.parse(text)

        assert reading.has_value
        assert (reading.hours, reading.minutes, reading.seconds) == (0, minutes, seconds)
        assert reading.total_seconds == total
        assert reading.formatted == f"{minutes}:{seconds:02d}"
        assert reading.raw_text == text
        assert parser.get_last_valid() is reading

    @pytest.mark.parametrize("text", ["", "   ", "5:60", " 5;6O ", "not a timer", "0 : 00"])
    def test_unsuccessful_parse_preserves_raw_text_and_previous_cache(self, parser, text):
        previous = parser.parse("3:00")
        reading = parser.parse(text)

        assert not reading.has_value
        assert reading.raw_text == text
        assert parser.get_last_valid() is previous

    # HH:MM:SS format tests
    def test_parse_hhmmss_basic(self, parser):
        reading = parser.parse("1:30:45")
        assert reading.has_value
        assert reading.hours == 1
        assert reading.minutes == 30
        assert reading.seconds == 45
        assert reading.total_seconds == 5445

    def test_parse_hhmmss_two_digit_hours(self, parser):
        reading = parser.parse("12:30:00")
        assert reading.has_value
        assert reading.hours == 12
        assert reading.minutes == 30
        assert reading.seconds == 0

    # OCR error correction tests
    def test_ocr_correction_semicolon_to_colon(self, parser):
        reading = parser.parse("5;30")
        assert reading.has_value
        assert reading.minutes == 5
        assert reading.seconds == 30

    def test_ocr_correction_O_to_0(self, parser):
        reading = parser.parse("5:OO")
        assert reading.has_value
        assert reading.minutes == 5
        assert reading.seconds == 0

    def test_ocr_correction_l_to_1(self, parser):
        reading = parser.parse("l:30")
        assert reading.has_value
        assert reading.minutes == 1
        assert reading.seconds == 30

    # Edge cases
    def test_parse_empty_string(self, parser):
        reading = parser.parse("")
        assert not reading.has_value

    def test_parse_whitespace_only(self, parser):
        reading = parser.parse("   ")
        assert not reading.has_value

    def test_parse_invalid_seconds(self, parser):
        """Seconds > 59 should be rejected."""
        reading = parser.parse("5:60")
        assert not reading.has_value

    def test_parse_with_extra_text(self, parser):
        reading = parser.parse("Time: 5:30 remaining")
        assert reading.has_value
        assert reading.minutes == 5
        assert reading.seconds == 30

    def test_parse_seconds_only(self, parser):
        reading = parser.parse("120s")
        assert reading.has_value
        assert reading.total_seconds == 120
        assert reading.minutes == 2
        assert reading.seconds == 0

    # Estimation tests
    def test_estimate_time_remaining(self, parser):
        current = TimerReading(minutes=4, seconds=0, total_seconds=240)
        previous = TimerReading(minutes=5, seconds=0, total_seconds=300)
        elapsed = 60.0  # 1 minute real time

        remaining = parser.estimate_time_remaining(current, previous, elapsed)
        assert remaining == 240  # At 1:1 rate, 4 min remaining


class TestMoneyParserIntegration:
    """Integration tests simulating real OCR outputs."""

    @pytest.fixture
    def parser(self):
        return MoneyParser()

    def test_realistic_gta_money_display(self, parser):
        """Test with realistic GTA money display formats."""
        test_cases = [
            ("$1,234,567", 1234567),
            ("$50,000", 50000),
            ("$999,999,999", 999999999),
            ("$   1,234,567  ", 1234567),  # Extra whitespace
        ]
        for text, expected in test_cases:
            reading = parser.parse(text)
            assert reading.has_value, f"Failed to parse: {text}"
            assert reading.display_value == expected, f"Wrong value for: {text}"

    def test_noisy_ocr_output(self, parser):
        """Test with noisy OCR that may have artifacts."""
        # Common OCR noise scenarios
        reading = parser.parse("$l,O00,OOO")  # 1,000,000 with OCR errors
        assert reading.has_value
        assert reading.display_value == 1000000


class TestTimerParserIntegration:
    """Integration tests simulating real OCR outputs."""

    @pytest.fixture
    def parser(self):
        return TimerParser()

    def test_realistic_gta_timer_display(self, parser):
        """Test with realistic GTA timer formats."""
        test_cases = [
            ("5:30", 330),
            ("0:45", 45),
            ("15:00", 900),
            ("1:30:00", 5400),
        ]
        for text, expected in test_cases:
            reading = parser.parse(text)
            assert reading.has_value, f"Failed to parse: {text}"
            assert reading.total_seconds == expected, f"Wrong value for: {text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
