"""Signed HUD changes cannot become observed balances or persisted earnings."""

from types import SimpleNamespace
import sqlite3

import pytest
from sqlalchemy import event

from src.detection.parsers.money_parser import MoneyParser
from src.detection.state_detector import StateDetectionResult
from src.game.state_machine import GameState
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


@pytest.mark.parametrize("sign", ["+", "-", "−"])
@pytest.mark.parametrize("template", [
    "{sign}$85,000", "{sign} $85.000", "$ {sign} 85 000", "{sign}85000",
    "{sign}\t$\t85,000", "{sign}\n$85000", "${sign}8S,OOO", "{sign}€85000",
])
def test_explicit_signed_amount_is_not_a_balance(sign, template):
    text = template.format(sign=sign)
    parser = MoneyParser()
    reading = parser.parse(text)
    assert not reading.has_value
    assert reading.raw_text == text
    assert not parser.validate_reading(reading)
    assert parser.get_last_valid() is None


@pytest.mark.parametrize("text", [
    "+$85x000", "$-85x000", "+$85,000oops123", "reward:+$85000",
    "reward-$85000", "+85000bad999", "+$85-000", "+$85000 +$1000",
    "CASH +$1000 BANK -$85000", "+£85000", "¥−85000",
    "+$,85000", "$-.85000", "+$oops85000", "+$abc85000", "+85 000x999",
])
def test_signed_malformed_or_embedded_tokens_do_not_leak_into_fallback(text):
    assert not MoneyParser().parse(text).has_value


@pytest.mark.parametrize("text", [
    "$10000\n+$85000", "+$85000\n$10000", "$10000 +$85000",
    "+$85000 $10000", "$10000+$85000", "$10000 -85000",
    "$10000 $-85000", "$10000\n−$85000", "+$85x000\n$10000",
    "+85000\n10000", "10000\n+85000", "+85000 | 10000",
    "+85000 reward 10000", "$10000\n+$8S,OOO", "$10 000\n+85 000",
])
def test_unsigned_balance_survives_a_larger_signed_notification(text):
    reading = MoneyParser().parse(text)
    assert reading.has_value
    assert reading.display_value == 10_000
    assert reading.raw_text == text


def test_rejecting_an_amount_does_not_join_unsigned_fragments():
    assert MoneyParser().parse("123 +$85000 456").display_value == 123


def test_rejecting_a_notification_does_not_splice_cash_bank_labels():
    reading = MoneyParser().parse("CASH $5000 +$85000 BANK $10000")
    assert reading.cash is None and reading.bank is None
    assert reading.total == 10000


@pytest.mark.parametrize("text,expected", [
    ("$500 $1,000,000", 1_000_000), ("$1.234.567", 1_234_567),
    ("$1,OOO,OOO", 1_000_000), ("$l,234,567", 1_234_567),
    ("$1,2S4,S67", 1_254_567), ("1,234,567", 1_234_567),
    ("Balance: $10000 available", 10_000), ("$10\u202f000", 10_000),
    ("Balance - available $10000", 10_000), ("Balance + bonus $10000", 10_000),
    ("+85000 SCORE 10000", 10_000),
])
def test_unsigned_balance_conventions_are_preserved(text, expected):
    reading = MoneyParser().parse(text)
    assert reading.has_value
    assert reading.display_value == expected
    assert reading.raw_text == text


@pytest.mark.parametrize("text", [
    "CASH $5000 BANK $10000\n+$85000",
    "+$85000\nCASH $5000 BANK $10000",
    "CASH 5000 BANK 10000\n-85000",
])
def test_unsigned_cash_and_bank_remain_a_split_balance(text):
    reading = MoneyParser().parse(text)
    assert (reading.cash, reading.bank, reading.total) == (5000, 10000, 15000)
    assert reading.raw_text == text


def test_abstention_preserves_parsed_and_accepted_history():
    parser = MoneyParser()
    baseline = parser.parse("$10000")
    assert parser.validate_reading(baseline)
    for text in ("+$85000", "-$85000", "no balance"):
        assert not parser.validate_reading(parser.parse(text))
        assert parser.get_last_valid() is baseline
    # 1,000,000 is 100x the accepted 10,000 baseline but less than 100x 85,000.
    assert not parser.validate_reading(parser.parse("$1000000"))
    assert parser.validate_reading(parser.parse("$11000"))


@pytest.fixture
def capture_money(app):
    """Only hardware acquisition/OCR are controlled; accounting runs unchanged."""
    regions = SimpleNamespace(
        full_screen=0, money_display=1, mission_text=2, center_prompt=3,
        timer_bottom_right=4, mission_banner=5, bottom_objective=6, result_header=7,
    )
    app._capture = SimpleNamespace(
        regions=regions, capture_multiple_regions=lambda _: [object()] * 8,
    )
    app._perf_monitor = PerformanceMonitor()
    app._state_detector = SimpleNamespace(detect=lambda *args, **kwargs: StateDetectionResult(
        state=GameState.UNKNOWN, confidence=0.0, reason="controlled non-mission frame",
    ))

    def observe(text):
        app._ocr = SimpleNamespace(
            is_available=True,
            recognize_preprocessed=lambda *args, **kwargs: SimpleNamespace(text=text),
        )
        return app._do_capture_cycle()

    return observe


def accounting_snapshot(app):
    stats = app.session_stats
    # Compare stored bytes/rows, not export_session_data's live elapsed duration.
    with sqlite3.connect(app._repository._db_path) as connection:
        stored_rows = tuple(connection.iterdump())
    return (
        app.current_money, app.session_start_money, app._data.db_start_money,
        app.session_earnings, app._data.last_money_change, app._data.last_money_change_time,
        stats.start_money, stats.current_money, stats.total_earnings,
        app._data.successful_ocr,
        stored_rows,
    )


@pytest.mark.parametrize("opening", [None, "$10000"])
@pytest.mark.parametrize("notification", ["+$85000", "-$85000", "$−85 000", "+85x000"])
def test_notification_only_capture_does_not_change_accounting(
    app, capture_money, opening, notification,
):
    if opening is not None:
        capture_money(opening)
    callbacks, writes = [], []
    app.on_money_change(lambda reading, change: callbacks.append((reading.display_value, change)))
    before = accounting_snapshot(app)
    last_parsed = app._money_parser.get_last_valid()

    def record_write(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)

    engine = app._repository._session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record_write)
    try:
        result = capture_money(notification)
    finally:
        event.remove(engine, "before_cursor_execute", record_write)
    assert result.money is None
    assert result.money_change == 0
    assert accounting_snapshot(app) == before
    assert app._money_parser.get_last_valid() is last_parsed
    assert callbacks == []
    assert writes == []


@pytest.mark.parametrize("opening", [None, "$10000"])
@pytest.mark.parametrize("text", ["$10000\n+$85000", "+$85000 $10000"])
def test_mixed_capture_uses_unsigned_balance_without_false_income(app, capture_money, opening, text):
    if opening is not None:
        capture_money(opening)
    callbacks = []
    app.on_money_change(lambda reading, change: callbacks.append(change))
    result = capture_money(text)
    assert result.money.display_value == 10_000
    assert result.money.raw_text == text
    assert result.money_change == 0
    assert app.current_money == app.session_start_money == app.session_stats.start_money == 10_000
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert callbacks == []
    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == 10_000
    assert data["earnings"] == []


def test_valid_capture_spending_recovery_and_history_after_notifications(app, capture_money):
    callbacks = []
    app.on_money_change(lambda reading, change: callbacks.append((reading.display_value, change)))
    observations = [
        "$10000", "+$85000", "$8000\n-$2000", "-$2000", "$9000\n+$1000",
    ]
    results = [capture_money(text) for text in observations]
    assert [result.money_change for result in results] == [0, 0, -2000, 0, 1000]
    assert app.current_money == app.session_stats.current_money == 9000
    assert app.session_earnings == app.session_stats.total_earnings == 1000
    assert callbacks == [(8000, -2000), (9000, 1000)]
    assert app._data.successful_ocr == 3
    app._end_database_session()
    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == 10000
    assert data["session"]["end_money"] == 9000
    assert data["session"]["total_earnings"] == -1000
    assert [(row["amount"], row["balance_after"]) for row in data["earnings"]] == [(1000, 9000)]
