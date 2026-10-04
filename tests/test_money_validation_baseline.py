"""Money plausibility uses accepted history, not the just-parsed OCR candidate."""

from types import SimpleNamespace

import pytest

from src.detection.parsers.money_parser import MoneyParser, MoneyReading
from src.game.state_machine import GameState
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


@pytest.mark.parametrize(
    "opening,spike",
    [(1_000_000, 100_000_000), (1_000_000, 10_000), (100_000, 10_000_001), (100_000_000, 999_999)],
)
def test_parse_then_validate_rejects_existing_jump_threshold(opening, spike):
    parser = MoneyParser()
    assert parser.validate_reading(parser.parse(f"${opening:,}"))
    reading = parser.parse(f"${spike:,}")
    assert reading.display_value == spike
    assert not parser.validate_reading(reading)


def test_rejected_candidates_do_not_rebase_later_validation():
    parser = MoneyParser()
    assert parser.validate_reading(parser.parse("$1,000,000"))
    for _ in range(3):
        assert not parser.validate_reading(parser.parse("$100,000,000"))
    assert parser.validate_reading(parser.parse("$1,010,000"))
    assert not parser.validate_reading(parser.parse("$101,000,000"))


def test_accepted_balance_advances_after_normal_changes():
    parser = MoneyParser()
    assert parser.validate_reading(parser.parse("$1,000"))
    assert parser.validate_reading(parser.parse("$50,000"))
    # This is only 50x the newly accepted value, rather than 2500x the opener.
    assert parser.validate_reading(parser.parse("$2,500,000"))
    assert not parser.validate_reading(parser.parse("$250,000,000"))


def test_parsing_without_acceptance_does_not_establish_a_baseline():
    parser = MoneyParser()
    parser.parse("$100,000,000")
    assert parser.validate_reading(MoneyReading(total=100_000))


def test_invalid_small_candidate_does_not_poison_the_accepted_baseline():
    parser = MoneyParser()
    assert parser.validate_reading(parser.parse("$1,000,000"))
    assert not parser.validate_reading(parser.parse("$50"))
    assert not parser.validate_reading(MoneyReading(total=10_000))
    assert parser.validate_reading(parser.parse("$1,100,000"))


def test_mutating_a_returned_reading_does_not_rewrite_accepted_balance():
    parser = MoneyParser()
    first = parser.parse("$1,000,000")
    assert parser.validate_reading(first)
    first.total = 100_000_000
    assert not parser.validate_reading(MoneyReading(total=100_000_000))
    assert parser.validate_reading(MoneyReading(total=1_001_000))


def test_last_parsed_reading_accessor_keeps_its_existing_contract():
    parser = MoneyParser()
    assert parser.get_last_valid() is None
    candidate = parser.parse("$100,000")
    assert parser.get_last_valid() is candidate
    assert not parser.parse("no balance here").has_value
    assert parser.get_last_valid() is candidate


def test_new_parser_starts_with_no_accepted_history():
    parser = MoneyParser()
    assert parser.validate_reading(parser.parse("$1,000,000"))
    assert not parser.validate_reading(parser.parse("$100,000,000"))
    restarted = MoneyParser()
    assert restarted.validate_reading(restarted.parse("$100,000,000"))


def test_real_capture_accounting_drops_ocr_spike_before_database_earnings(app, monkeypatch):
    regions = SimpleNamespace(
        full_screen=0, money_display=1, mission_text=2, center_prompt=3, timer_bottom_right=4,
        mission_banner=5,
    )
    app._capture = SimpleNamespace(
        regions=regions, capture_multiple_regions=lambda _: [object()] * 6
    )
    app._perf_monitor = PerformanceMonitor()
    texts = iter(["$1,000,000", "$100,000,000", "$1,010,000"])
    app._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda *args, **kwargs: SimpleNamespace(text=next(texts)),
    )
    app._state_detector = SimpleNamespace(
        detect=lambda *args, **kwargs: SimpleNamespace(
            state=GameState.UNKNOWN,
            confidence=0.0,
            mission_text="",
            objective_text="",
        )
    )
    monkeypatch.setattr(app, "_process_state", lambda *args: None)
    first = app._do_capture_cycle()
    spike = app._do_capture_cycle()
    recovered = app._do_capture_cycle()
    assert first.money.display_value == 1_000_000
    assert spike.money is None
    assert spike.money_change == 0
    assert recovered.money_change == 10_000
    assert app.current_money == 1_010_000
    assert app.session_earnings == app.session_stats.total_earnings == 10_000
    assert app._data.successful_ocr == 2
    data = app._repository.export_session_data(app._data.db_session_id)
    assert [event["amount"] for event in data["earnings"]] == [10_000]
