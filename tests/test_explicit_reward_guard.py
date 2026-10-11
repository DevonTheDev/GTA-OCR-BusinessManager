"""Explicit row-leading reward fields cannot become balance observations.

Synthetic OCR text exercises real parsing, capture/accounting and SQLite. Native
OCR, Windows capture, real gameplay and transaction settlement are not tested.
"""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event

from src.detection.parsers.money_parser import MoneyParser
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_bottom_objective_capture import CaptureHarness
from tests.test_mission_result_accounting import clock as clock
from tests.test_money_notifications import accounting_snapshot, capture_money as capture_money


@pytest.mark.parametrize("text", [
    "Reward $85,000", "reward $85,000", "  REWARD $85,000",
    "\treWaRd\t$\t85,000\t", "\nReward $85,000\n",
    "Reward $85.000", "Reward $85 000", "Reward $8S,OOO",
    "Reward\u00a0$85\u202f000", "Reward $85x000", "Reward $abc85000",
    "Reward $85,000oops123", "Reward $85 000x999",
    "Reward $abc85000 000x999",
])
def test_explicit_reward_only_abstains_without_parsed_or_accepted_history(text):
    parser = MoneyParser()
    for _ in range(3):
        reading = parser.parse(text)
        assert not reading.has_value
        assert reading.raw_text == text
        assert not parser.validate_reading(reading)
        assert parser.get_last_valid() is None
        assert parser._last_accepted_value is None


def test_reward_abstention_keeps_last_candidate_and_accepted_baseline():
    parser = MoneyParser()
    opening = parser.parse("$1,000")
    assert parser.validate_reading(opening)
    for text in ("Reward $85,000", "Reward $abc85000", "Reward $85 000"):
        assert not parser.validate_reading(parser.parse(text))
        assert parser.get_last_valid() is opening
        assert parser._last_accepted_value == 1000
    # Reward filtering cannot move the unchanged 100x validation boundary.
    assert not parser.validate_reading(parser.parse("$100,000"))
    assert parser._last_accepted_value == 1000
    assert parser.validate_reading(parser.parse("$1,100"))


@pytest.mark.parametrize("text,expected", [
    ("$1,234,567", 1_234_567), ("$1234567", 1_234_567),
    ("$1.234.567", 1_234_567), ("$1,OOO,OOO", 1_000_000),
    ("$l,234,567", 1_234_567), ("$1,2S4,S67", 1_254_567),
    ("1,234,567", 1_234_567), ("Balance: $10000 available", 10_000),
    ("$500 $1,000,000", 1_000_000), ("$10\u00a0000", 10_000),
    ("$10\u202f000", 10_000), ("+85000 reward 10000", 10_000),
    ("Balance + bonus $10000", 10_000),
])
def test_ordinary_balance_controls_remain_unchanged(text, expected):
    reading = MoneyParser().parse(text)
    assert reading.has_value and reading.display_value == expected
    assert reading.raw_text == text


@pytest.mark.parametrize("balance", [
    "CASH $5000 BANK $10000", "CASH 5000 BANK 10000",
    "DINERO $5000 BANCO $10000", "DINERO 5000 BANCO 10000",
    "BARGELD $5000 BANK $10000", "BARGELD 5000 BANK 10000",
])
@pytest.mark.parametrize("layout", ["{balance}", "{balance}\nReward $85000",
                                     "Reward $85000\n{balance}"])
def test_ordered_cash_bank_pairs_survive_separate_reward_rows(balance, layout):
    text = layout.format(balance=balance)
    reading = MoneyParser().parse(text)
    assert (reading.cash, reading.bank, reading.total) == (5000, 10000, 15000)
    assert reading.raw_text == text


@pytest.mark.parametrize("text", [
    "$10000\nReward $85000", "Reward $85000\n$10000",
    "10000\nReward $85000", "Reward $85000\n10000",
    "Reward $abc85000\n10000", "Reward $85 000x999\n$10000",
    "Reward $85000\n+$25000\n$10000",
])
def test_separate_balance_row_survives_larger_reward(text):
    reading = MoneyParser().parse(text)
    assert reading.has_value and reading.display_value == 10000
    assert reading.raw_text == text


@pytest.mark.parametrize("text", [
    "Reward $85000|10000", "Reward $85000;10000", "Reward $85000:10000",
    "Reward $85000$10000", "Reward $85000€10000",
    "Reward $85000+25000 $10000", "Reward $85000-25000 $10000",
    "Reward $85000−25000 $10000", "Reward $85000 available 10000",
])
def test_payload_ends_at_explicit_barrier_or_independent_word(text):
    reading = MoneyParser().parse(text)
    assert reading.has_value and reading.display_value == 10000
    assert reading.raw_text == text


def test_masked_reward_row_cannot_join_unsigned_fragments_or_cash_bank_labels():
    assert MoneyParser().parse("123\nReward $85000\n456").display_value == 123
    reading = MoneyParser().parse("CASH $5000\nReward $85000\nBANK $10000")
    assert (reading.cash, reading.bank, reading.total) == (None, None, 10000)


@pytest.mark.parametrize("text,expected", [
    ("Balance $1000 Reward $85000", 85000),
    ("Balance after reward $10000", 10000), ("Reward: $85000", 85000),
    ("reward 10000", 10000), ("$85000", 85000), ("Revv ard $85000", 85000),
    ("Rewards $85000", 85000), ("Reward €85000", 85000),
    ("Reward\n$85000", 85000), ("Reward $\n85000", 85000),
    ("BANK $10000 CASH $5000", 10000), ("CASH $5000 | BANK $10000", 10000),
])
def test_out_of_scope_forms_keep_published_behavior_without_admission_guarantee(text, expected):
    # Characterization only: these layouts remain ambiguous and unsupported.
    reading = MoneyParser().parse(text)
    assert (reading.cash, reading.bank, reading.total) == (None, None, expected)
    assert reading.raw_text == text


@contextmanager
def recorded_writes(app):
    writes = []

    def record(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append((statement, parameters))

    engine = app._repository._session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield writes
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("opening", [None, "$1,000"])
def test_reward_only_capture_repeats_without_money_or_sql_mutations(app, capture_money, opening):
    if opening is not None:
        capture_money(opening)
    callbacks = []
    app.on_money_change(lambda reading, change: callbacks.append((reading.display_value, change)))
    before = accounting_snapshot(app)
    last_parsed = app._money_parser.get_last_valid()
    accepted = app._money_parser._last_accepted_value
    with recorded_writes(app) as writes:
        results = [capture_money("Reward $85,000") for _ in range(3)]
    assert all(result.money is None and result.money_change == 0 for result in results)
    assert accounting_snapshot(app) == before
    assert app._money_parser.get_last_valid() is last_parsed
    assert app._money_parser._last_accepted_value == accepted
    assert callbacks == []
    assert writes == []


@pytest.mark.parametrize("opening", [None, "$1,000"])
def test_mixed_capture_keeps_balance_then_records_actual_increase_once(app, capture_money, opening):
    if opening is not None:
        capture_money(opening)
    callbacks = []
    app.on_money_change(lambda reading, change: callbacks.append((reading.display_value, change)))
    mixed = "$1,000\nReward $85,000"
    result = capture_money(mixed)
    assert result.money.display_value == 1000
    assert result.money.raw_text == mixed and result.money_change == 0
    assert app.current_money == app.session_start_money == app._data.db_start_money == 1000
    assert app.session_stats.start_money == app.session_stats.current_money == 1000
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app._repository.export_session_data(app._data.db_session_id)["earnings"] == []
    assert capture_money("$1,250").money_change == 250
    assert capture_money("$1,250").money_change == 0
    assert callbacks == [(1250, 250)]
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert app._data.successful_ocr == (3 if opening is None else 4)
    data = app._repository.export_session_data(app._data.db_session_id)
    assert [(row["amount"], row["balance_after"]) for row in data["earnings"]] == [(250, 1250)]


def test_spending_reward_abstention_recovery_and_session_gross_net(app, capture_money):
    callbacks = []
    app.on_money_change(lambda reading, change: callbacks.append((reading.display_value, change)))
    results = [capture_money(text) for text in
               ("$1,000", "$900", "Reward $85,000", "$1,250", "$1,250")]
    assert [result.money_change for result in results] == [0, -100, 0, 350, 0]
    assert results[2].money is None
    assert callbacks == [(900, -100), (1250, 350)]
    assert app.current_money == app.session_stats.current_money == 1250
    assert app.session_earnings == app.session_stats.total_earnings == 350
    assert app._data.successful_ocr == 4
    app._end_database_session()
    data = app._repository.export_session_data(app._data.db_session_id)
    assert data["session"]["start_money"] == 1000
    assert data["session"]["end_money"] == 1250
    assert data["session"]["total_earnings"] == 250
    assert [(row["amount"], row["balance_after"]) for row in data["earnings"]] == [(350, 1250)]


def test_unlabeled_85000_retains_existing_observed_delta(app, capture_money):
    capture_money("$1,000")
    result = capture_money("$85,000")
    assert result.money.display_value == 85000 and result.money_change == 84000
    assert app.session_earnings == app.session_stats.total_earnings == 84000
    data = app._repository.export_session_data(app._data.db_session_id)
    assert [(row["amount"], row["balance_after"]) for row in data["earnings"]] == [(84000, 85000)]


def test_headhunter_completion_with_reward_abstention_and_later_real_money(app, monkeypatch, clock):
    hud = CaptureHarness(app, monkeypatch)
    completed, money_callbacks = [], []
    app.on_mission_complete(completed.append)
    app.on_money_change(lambda reading, change: money_callbacks.append((reading.display_value, change)))
    started = hud.observe(banner="Headhunter", money="$1,000")
    assert started.game_state is GameState.MISSION_ACTIVE
    current = app._activity_tracker.current_activity
    assert current.activity_type is ActivityType.VIP_WORK and current.name == "Headhunter"
    assert app._data.mission_start_money == 1000
    clock.value += timedelta(seconds=45)
    with recorded_writes(app) as writes:
        result = hud.observe(banner="MISSION PASSED\nHeadhunter", money="Reward $85,000")
    assert result.game_state is GameState.MISSION_COMPLETE
    data = app._repository.export_session_data(app._data.db_session_id)
    row, = data["activities"]
    assert (row["type"], row["name"], row["earnings"], row["success"], row["duration_seconds"]) == (
        "VIP_WORK", "Headhunter", 0, True, 45,
    )
    assert result.money is None and result.money_change == 0
    assert data["earnings"] == []
    assert app.current_money == 1000
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app._data.successful_ocr == 1
    assert completed == [current] and current.earnings == 0
    assert money_callbacks == []
    # A real completion legitimately writes its zero-earnings activity row.
    assert any("INSERT INTO activities" in statement for statement, _ in writes)
    assert not any("INSERT INTO earnings" in statement for statement, _ in writes)
    hud.observe(banner="MISSION PASSED\nHeadhunter", money="Reward $85,000")
    assert app._repository.export_session_data(app._data.db_session_id)["activities"] == [row]
    assert completed == [current] and money_callbacks == []
    assert hud.observe(money="$1,250").money_change == 250
    assert hud.observe(money="$1,250").money_change == 0
    later = app._repository.export_session_data(app._data.db_session_id)
    assert later["activities"] == [row]
    assert [(entry["amount"], entry["balance_after"]) for entry in later["earnings"]] == [(250, 1250)]
    assert app.current_money == 1250
    assert app.session_earnings == app.session_stats.total_earnings == 250
    assert app.session_stats.activities_completed == app.session_stats.missions_passed == 1
    assert money_callbacks == [(1250, 250)] and completed == [current]


def test_headhunter_completion_with_actual_balance_still_records_250(app, monkeypatch, clock):
    hud = CaptureHarness(app, monkeypatch)
    hud.observe(banner="Headhunter", money="$1,000")
    clock.value += timedelta(seconds=45)
    result = hud.observe(banner="MISSION PASSED\nHeadhunter", money="$1,250")
    assert result.game_state is GameState.MISSION_COMPLETE
    assert result.money.display_value == 1250 and result.money_change == 250
    data = app._repository.export_session_data(app._data.db_session_id)
    row, = data["activities"]
    assert (row["type"], row["name"], row["earnings"], row["success"]) == (
        "VIP_WORK", "Headhunter", 250, True,
    )
    assert [(entry["amount"], entry["balance_after"]) for entry in data["earnings"]] == [(250, 1250)]
    assert app.session_earnings == app.session_stats.total_earnings == 250
