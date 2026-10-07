"""Business OCR regressions using real parsing and synthetic capture boundaries."""

import copy
import logging
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app import AppState, GTABusinessManager
from src.capture.regions import ScreenRegions
from src.config.settings import Settings
from src.detection.parsers.business_parser import BusinessParser, BusinessType
from src.detection.state_detector import StateDetectionResult
from src.game.businesses import BUSINESSES
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor


LEVEL_FIELDS = [("Stock", "stock_level"), ("Supplies", "supply_level")]


@pytest.mark.parametrize("label,field", LEVEL_FIELDS)
@pytest.mark.parametrize(
    "counts,expected",
    [
        ("5/10", 50),
        ("10/10", 100),
        ("0/10", 0),
        ("25/50", 50),
        ("100/200", 50),
        ("150/200", 75),
        ("1/3", 33),
        ("2/3", 66),
        ("57/100", 57),
        ("29/100", 29),
        ("005 / 010", 50),
        ("5/10 units", 50),
        ("5/10units", 50),
        ("5/10 crates", 50),
        ("5/10 bars", 50),
        ("5/10.", 50),
        ("5/10, available", 50),
        ("5/10;", 50),
        ("5/10)", 50),
        pytest.param(f"{10**400 - 1}/{10**400}", 99, id="huge-nearly-full"),
        pytest.param(f"{10**400}/{10**400}", 100, id="huge-full"),
        pytest.param(f"1/{10**400}", 0, id="huge-denominator"),
    ],
)
def test_integer_ratios_produce_exact_floored_percentages(label, field, counts, expected):
    reading = BusinessParser().parse(f"{label}: {counts}")
    assert getattr(reading, field) == expected


@pytest.mark.parametrize("label,field", LEVEL_FIELDS)
@pytest.mark.parametrize(
    "counts",
    [
        "5/0", "0/0", "11/10", "1001/1000", "5/", "5/-10", "-1/10",
        "5.5/10", "5/10.5", "5/10,5", "5/10e2", "5/10E+2", "5/10e-2",
        "5/10e", "5/10e+", "5/10E-", "5/10/20", "5/10/",
        "5 / 10 / 20", "5/10 /20", "5/10 /",
        pytest.param(f"{10**400}/1", id="huge-overfull"),
    ],
)
def test_invalid_ratios_do_not_become_percentages_or_partial_counts(label, field, counts):
    reading = BusinessParser().parse(f"{label}: {counts}")
    assert getattr(reading, field) is None
    assert not reading.has_data


@pytest.mark.parametrize("label,field", LEVEL_FIELDS)
@pytest.mark.parametrize(
    "percent,expected", [("0%", 0), ("50%", 50), ("100%", 100), ("101%", None)]
)
def test_literal_percent_controls_are_preserved(label, field, percent, expected):
    assert getattr(BusinessParser().parse(f"{label}: {percent}"), field) == expected


@pytest.mark.parametrize(
    "text,stock,supply,units",
    [
        ("50% full", 50, None, None),
        ("50% stock", 50, None, None),
        ("50% supplies", None, 50, None),
        ("50/ full", None, None, None),
        ("50/ stock", None, None, None),
        ("50/ supplies", None, None, None),
        ("Product: 5", 5, None, None),
        ("Product: 5 units", 5, None, 5),
        ("Product: 101 units", None, None, 101),
        ("Bunker Stock5/10 Supply3/4", 50, 75, None),
    ],
)
def test_existing_formats_and_compact_ratios(text, stock, supply, units):
    reading = BusinessParser().parse(text)
    assert (reading.stock_level, reading.supply_level, reading.product_units) == (
        stock, supply, units
    )


@pytest.mark.parametrize(
    "text,stock,supply",
    [
        ("Stock: 5/10%", 50, None),
        ("Supplies: 5/10%", None, 50),
        ("5/10% stock", 10, None),
        ("5/10% supplies", None, 10),
    ],
)
def test_mixed_ratio_percent_suffix_keeps_pattern_precedence(text, stock, supply):
    # Labeled ratios consume counts; the existing reversed-percent grammar
    # independently recognizes the trailing percentage in unlabeled text.
    reading = BusinessParser().parse(text)
    assert (reading.stock_level, reading.supply_level) == (stock, supply)


@pytest.mark.parametrize(
    "text,business_type",
    [
        ("Acid Lab Stock: 50%", BusinessType.METH),
        ("Vehicle Warehouse Stock: 50%", BusinessType.NIGHTCLUB),
        ("Special Cargo Stock: 50%", BusinessType.HANGAR),
        ("Stock: 50%", BusinessType.UNKNOWN),
    ],
)
def test_existing_business_identity_rules_are_preserved(text, business_type):
    assert BusinessParser().parse(text).business_type is business_type


def test_ratio_states_and_last_readings_keep_existing_semantics():
    parser = BusinessParser()
    reading = parser.parse("Stock: 19/20 Supplies: 1/5", business_hint=BusinessType.BUNKER)
    assert reading.is_full and not reading.is_empty and reading.needs_supplies
    assert parser.get_last_reading(BusinessType.BUNKER) is reading

    empty = parser.parse("Bunker Stock: 0/10 Supplies: 3/4")
    assert empty.is_empty and not empty.is_full and not empty.needs_supplies
    assert parser.get_last_reading(BusinessType.BUNKER) is empty

    invalid = parser.parse("Bunker Stock: 5/0 Supplies: 11/10")
    assert not invalid.has_data
    assert parser.get_last_reading(BusinessType.BUNKER) is empty


@pytest.fixture
def manager(tmp_path):
    return GTABusinessManager(Settings(tmp_path / "settings.yaml"))


def run_business_captures(manager, frames):
    """Run the actual loop, substituting only capture/OCR and state detection."""
    class Capture:
        def __init__(self):
            self.regions = ScreenRegions()
            self.closed = False
            self.batches = 0
            self.calls = []
            self.rates = []
            self.current_text = {}

        def capture_multiple_regions(self, requested):
            # End a broken loop even if a production exception prevents callbacks.
            if self.batches >= len(frames):
                manager._stop_event.set()
                return dict.fromkeys(range(len(requested)))
            self.current_text = dict(
                zip(self.regions.get_business_regions().values(), frames[self.batches])
            )
            self.batches += 1
            images = {0: object(), 1: None, 2: None, 3: None, 4: None, 5: None, 6: None, 7: None}
            if len(requested) > 8:
                assert requested[8:] == [self.regions.vip_status]
                images[8] = None
            return images

        def capture_region(self, region, wait_for_rate=True):
            self.calls.append((region, wait_for_rate))
            return self.current_text[region]

        def set_capture_rate(self, rate):
            self.rates.append(rate)

        def close(self):
            self.closed = True

    capture = Capture()
    manager._capture = capture
    manager._ocr = SimpleNamespace(
        is_available=True,
        recognize_preprocessed=lambda image, **kwargs: SimpleNamespace(text=image),
    )
    manager._state_detector = SimpleNamespace(
        detect=lambda *args, **kwargs: StateDetectionResult(
            GameState.BUSINESS_COMPUTER, 0.9, "synthetic business screen"
        )
    )
    manager._state_machine = GameStateMachine()
    manager._perf_monitor = PerformanceMonitor()
    manager._state = AppState.RUNNING
    manager._capture_thread = threading.current_thread()
    observations = []

    def observe(result):
        observations.append((result, manager.data.business_states))
        if len(observations) == len(frames):
            manager._stop_event.set()

    manager.on_capture(observe)
    manager._capture_loop()
    return capture, observations


@pytest.mark.parametrize(
    "frames,expected",
    [
        (
            [("Bunker Stock5/10", "Supply3/4", "Value: $123,456"),
             ("Bunker Stock: 10/10", "Supplies: 0/5", "Value: $654,321")],
            [(50, 75, 123456), (100, 0, 654321)],
        ),
        (
            [("Bunker Stock: 75%", "Supplies: 25%", "Value: $123,456"),
             ("Bunker Stock: 50%", "Supplies: 0%", "Value: $50,000")],
            [(75, 25, 123456), (50, 0, 50000)],
        ),
    ],
    ids=["ratios", "percentages"],
)
def test_capture_loop_updates_caches_logs_and_listeners_without_changing_schedule(
    manager, caplog, frames, expected
):
    caplog.set_level(logging.INFO)
    scheduler = manager._optimizer._scheduler
    sell = scheduler.schedule_sell("bunker", estimated_value=987654)
    scheduler.schedule_resupply("bunker", datetime.now() + timedelta(hours=1))
    scheduled_before = copy.deepcopy(scheduler.get_upcoming())

    capture, observations = run_business_captures(manager, frames)

    assert len(observations) == 2
    for (result, states), (stock, supply, value) in zip(observations, expected):
        assert result.game_state is GameState.BUSINESS_COMPUTER
        assert result.business is None  # This optional result field is not populated by the app.
        state = states["bunker"]
        assert (state["stock"], state["supply"], state["value"]) == (stock, supply, value)
        message = (
            f"Business detected: BUNKER - Stock: {stock}%, "
            f"Supply: {supply}%, Value: ${value:,}"
        )
        assert message in caplog.text
    state = manager._optimizer._business_states["bunker"]
    assert (state.stock_percent, state.supply_percent, state.estimated_value) == expected[-1]
    assert any(rec.business_type == "bunker" for rec in manager.recommendations)
    assert scheduler.get_upcoming() == scheduled_before
    assert scheduler.get_next_action() is sell
    assert scheduler.scheduled_count == 2
    assert manager.last_capture is observations[-1][0]
    assert manager.data.total_captures == 2
    assert capture.batches == 2 and len(capture.calls) == 6
    assert all(not wait_for_rate for _, wait_for_rate in capture.calls)
    assert capture.rates == [4.0]
    assert capture.closed and manager.state is AppState.STOPPED
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


def test_direct_updates_distinguish_observed_zero_from_missing_value(manager):
    manager.update_business_state("bunker", 50, 75, 123456)
    assert manager._optimizer._business_states["bunker"].estimated_value == 123456
    manager.update_business_state("bunker", 25, 10, 0)
    assert manager.get_business_state("bunker")["value"] == 0
    state = manager._optimizer._business_states["bunker"]
    assert (state.stock_percent, state.supply_percent) == (25, 10)
    assert state.estimated_value == 0
    manager.update_business_state("bunker", 25, 10)
    assert manager.get_business_state("bunker")["value"] is None
    assert manager._optimizer._business_states["bunker"].estimated_value == int(
        BUSINESSES["bunker"].max_value * 0.25
    )
    assert manager._optimizer._scheduler.scheduled_count == 0

    with pytest.raises(ValueError):
        manager.update_business_state("unknown", 50, 75, 123456)
    assert manager.get_business_state("unknown") is None
    assert "unknown" not in manager._optimizer._business_states


def test_supply_only_capture_replaces_missing_fields_with_unknown(manager, caplog):
    manager.update_business_state("bunker", 75, 50, 123456)
    run_business_captures(manager, [("Bunker", "Supplies: 1/10", "")])
    state = manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == (None, 10, None)
    optimized = manager._optimizer._business_states["bunker"]
    assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == (
        None, 10, None
    )
    assert "Error processing business computer:" not in caplog.text


def test_unidentified_capture_does_not_publish_an_uncataloged_live_card(manager):
    run_business_captures(manager, [("Stock: 5/10", "Supplies: 3/4", "Value: $123,456")])
    assert manager.data.business_states == {}
    assert manager._optimizer._business_states == {}


def test_invalid_ratio_capture_does_not_replace_existing_state(manager, caplog):
    manager.update_business_state("bunker", 75, 50, 123456)
    state = manager.get_business_state("bunker").copy()
    optimized = copy.deepcopy(manager._optimizer._business_states["bunker"])
    run_business_captures(manager, [("Bunker Stock: 5/0", "Supplies: 11/10", "")])
    assert manager.get_business_state("bunker") == state
    assert manager._optimizer._business_states["bunker"] == optimized
    assert "Error processing business computer:" not in caplog.text
