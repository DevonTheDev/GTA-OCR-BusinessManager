"""Mission result bookkeeping through actual trackers and disposable SQLite."""

from datetime import datetime, timedelta

import pytest

from src import app as module
from src.app import CaptureResult
from src.detection.parsers.money_parser import MoneyReading
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime(2026, 1, 1, 12)

        @classmethod
        def now(cls, tz=None):
            return cls.value if tz is None else cls.value.replace(tzinfo=tz)

    monkeypatch.setattr(module, "datetime", Clock)
    return Clock


def detect(app, state, text=""):
    app._process_state(
        StateDetectionResult(state=state, confidence=1.0, reason="synthetic", mission_text=text),
        CaptureResult(),
    )


def activities(app):
    return app._repository.export_session_data(app._data.db_session_id)["activities"]


@pytest.mark.parametrize(
    "opening,ending,expected",
    [
        (0, 25000, 25000),
        (0, 0, 0),
        (100, 0, 0),
        (100, 150, 50),
        (100, 50, 0),
        (None, 150, 0),
        (100, None, 0),
    ],
)
def test_mission_balance_difference_distinguishes_zero_from_missing(
    app, clock, opening, ending, expected
):
    app._data.current_money = opening
    detect(app, GameState.MISSION_ACTIVE, "Headhunter")
    clock.value += timedelta(seconds=120)
    app._data.current_money = ending
    detect(app, GameState.MISSION_COMPLETE)
    (row,) = activities(app)
    assert row["earnings"] == expected
    assert row["duration_seconds"] == 120
    assert app._activity_tracker.completed_activities[0].earnings == expected
    assert app._data.mission_start_time is None
    assert app._data.mission_start_money is None


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    "state,text,kind,name",
    [
        (GameState.MISSION_ACTIVE, "Headhunter", ActivityType.VIP_WORK, "Headhunter"),
        (
            GameState.MISSION_ACTIVE,
            "Security contract",
            ActivityType.SECURITY_CONTRACT,
            "Security contract",
        ),
        (GameState.MISSION_ACTIVE, "Deliver goods", ActivityType.SELL_MISSION, "Deliver goods"),
        (GameState.SELLING, "", ActivityType.SELL_MISSION, "Sell Mission"),
    ],
)
def test_result_preserves_tracked_type_name_and_sell_count(
    app, clock, success, state, text, kind, name
):
    app._data.current_money = 1000
    detect(app, state, text)
    clock.value += timedelta(seconds=180)
    app._data.current_money = 1250
    result_state = GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED
    detect(app, result_state)
    detect(app, result_state)  # A repeated result screen cannot add another row.
    (row,) = activities(app)
    assert row["type"] == kind.name
    assert row["name"] == name
    assert row["success"] is success
    assert row["earnings"] == (250 if success else 0)
    assert row["duration_seconds"] == 180
    (tracked,) = app._activity_tracker.completed_activities
    assert tracked.activity_type == kind and tracked.name == name
    stats = app.session_stats
    assert stats.activities_completed == 1
    assert stats.missions_passed == int(success)
    assert stats.missions_failed == int(not success)
    assert stats.sells_completed == int(success and kind == ActivityType.SELL_MISSION)


def test_zero_balance_mission_records_income_once_in_each_existing_ledger(app, clock):
    app._process_money_change(MoneyReading(total=0))
    detect(app, GameState.MISSION_ACTIVE, "Headhunter")
    app._process_money_change(MoneyReading(total=25000))
    clock.value += timedelta(seconds=120)
    detect(app, GameState.MISSION_COMPLETE)
    data = app._repository.export_session_data(app._data.db_session_id)
    assert [row["amount"] for row in data["earnings"]] == [25000]
    assert [row["earnings"] for row in data["activities"]] == [25000]
    assert app.session_earnings == app.session_stats.total_earnings == 25000
    assert app.session_stats.activities_completed == 1


@pytest.mark.parametrize("success", [True, False])
def test_missing_tracked_activity_retains_generic_fallback(app, clock, success):
    app._data.mission_start_time = clock.value
    app._data.current_mission = "Known mission"
    app._data.mission_start_money = 100
    app._data.current_money = 200
    clock.value += timedelta(seconds=30)
    detect(app, GameState.MISSION_COMPLETE if success else GameState.MISSION_FAILED)
    (row,) = activities(app)
    assert row["type"] == "MISSION"
    assert row["name"] == "Known mission"
    assert app.session_stats.sells_completed == 0
