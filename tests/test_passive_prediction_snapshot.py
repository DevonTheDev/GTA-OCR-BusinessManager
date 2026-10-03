"""Passive prediction amounts, capacity flags and both widget views agree."""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tracking import passive_income as module
from src.tracking.passive_income import PassiveIncomeState, PassiveIncomeTracker
from src.utils.helpers import format_money_short


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    tracker = PassiveIncomeTracker(tmp_path / "passive.json")
    for state in (tracker.nightclub, tracker.agency):
        state.current_value = 100
        state.max_value = 1000
        state.rate_per_hour = 100
        state.last_updated = NOW - timedelta(hours=2)
    tracker._save()
    return tracker


@pytest.mark.parametrize("elapsed,rate,linked,current,expected,pct,full,eta", [
    (2, 100, True, 100, 300, 30.0, False, "7h 0m"),
    (9, 100, True, 100, 1000, 100.0, True, "Full"),
    (20, 100, True, 100, 1000, 100.0, True, "Full"),
    (0, 100, True, 1000, 1000, 100.0, True, "Full"),
    (0.5, 100, True, 900, 950, 95.0, False, "30m"),
    (2, 0, True, 100, 100, 10.0, False, "N/A"),
    (2, 100, False, 100, 100, 10.0, False, "N/A"),
    (-1, 100, True, 100, 100, 10.0, False, "9h 0m"),
    (0, 10, True, 100, 100, 10.0, False, "3d 18h"),
])
def test_prediction_fields_describe_the_same_estimated_value(
    tracker, elapsed, rate, linked, current, expected, pct, full, eta,
):
    state = tracker.agency
    state.current_value = current
    state.last_updated = NOW - timedelta(hours=elapsed)
    state.rate_per_hour = rate
    state.is_linked = linked
    before = state.to_dict()
    result = next(row for row in tracker.get_predictions() if row["name"] == "Agency Safe")
    assert result == {
        "name": "Agency Safe", "current_value": expected, "max_value": 1000,
        "fill_percent": pct, "is_full": full, "time_until_full": eta,
    }
    assert state.to_dict() == before


def changing_estimates(monkeypatch):
    calls = {}

    def estimate(state):
        count = calls[state.source_id] = calls.get(state.source_id, 0) + 1
        return 500 if count == 1 else 900

    monkeypatch.setattr(PassiveIncomeState, "estimated_current_value", property(estimate))
    return calls


def test_prediction_samples_each_estimate_once_for_all_derived_fields(tracker, monkeypatch):
    calls = changing_estimates(monkeypatch)
    predictions = tracker.get_predictions()
    assert calls == {"nightclub": 1, "agency": 1}
    assert all(row["current_value"] == 500 and row["fill_percent"] == 50.0
               and row["time_until_full"] == "5h 0m" and not row["is_full"]
               for row in predictions)


def test_predictions_do_not_promote_estimates_to_observed_state_or_save_files(tracker):
    path = tracker._data_path
    previous = path.read_bytes()
    state = tracker.agency
    state.last_updated = NOW - timedelta(hours=10)
    before = state.to_dict()
    result = next(row for row in tracker.get_predictions() if row["name"] == "Agency Safe")
    assert result["is_full"] is True
    assert state.current_value == 100
    assert state.fill_percent == 10.0 and state.is_full is False
    assert state.to_dict() == before
    assert path.read_bytes() == previous


def widget_update(class_name):
    """Compile the actual display method; label objects replace native Qt only."""
    source = Path(__file__).resolve().parents[1] / "src/ui/widgets/passive_income_widget.py"
    cls = next(node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_update_display")
    namespace = {"format_money_short": format_money_short}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_update_display"]


class Display:
    def __init__(self):
        self.text = None
        self.style = None
        self.values = None

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, style):
        self.style = style

    def update_display(self, *values):
        self.values = values


def test_detailed_widget_total_and_rows_share_the_same_prediction_snapshot(tracker, monkeypatch):
    calls = changing_estimates(monkeypatch)
    widget = SimpleNamespace(_tracker=tracker, _total_value=Display(),
                             _nc_widget=Display(), _agency_widget=Display())
    widget_update("PassiveIncomeWidget")(widget)
    assert calls == {"nightclub": 1, "agency": 1}
    assert widget._total_value.text == "$1.0K"
    assert widget._nc_widget.values == (500, 1000, 50.0, "5h 0m", False)
    assert widget._agency_widget.values == widget._nc_widget.values


@pytest.mark.parametrize("elapsed,amount,suffix", [(2, "$300", "(30%)"), (10, "$1.0K", "FULL")])
def test_compact_widget_uses_estimated_amount_percentage_and_full_flag(tracker, elapsed, amount, suffix):
    for state in (tracker.nightclub, tracker.agency):
        state.last_updated = NOW - timedelta(hours=elapsed)
    widget = SimpleNamespace(_tracker=tracker, _nc_label=Display(), _agency_label=Display())
    widget_update("CompactPassiveIncomeWidget")(widget)
    assert widget._nc_label.text == f"NC: {amount} {suffix}"
    assert widget._agency_label.text == f"Safe: {amount} {suffix}"
    assert ("#FF9800" in widget._nc_label.style) is (suffix == "FULL")


def test_optional_missing_source_keeps_other_prediction(tracker):
    tracker._nightclub = None
    predictions = tracker.get_predictions()
    assert [row["name"] for row in predictions] == ["Agency Safe"]
