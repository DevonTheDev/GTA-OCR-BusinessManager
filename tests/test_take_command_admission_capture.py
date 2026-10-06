"""Take-only admission through real capture cycles, tracking and SQLite.

Only screen/OCR IO is replaced with recorded or synthetic text. The classifier,
app, trackers and database are real; no native Windows/gameplay run is claimed.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_mission_banner_capture import BATCH, activity_rows, banner_frame
from tests.test_mission_result_accounting import clock as clock
from tests.test_take_command_admission import RETAINED_CAYO_TEXTS, TAKE_CASES, crop_texts


REJECTED_CASES = [case[:2] for case in TAKE_CASES if not case[2]]


def persisted_export(app):
    data = app._repository.export_session_data(app._data.db_session_id)
    # An open session's duration is calculated at read time, not stored in
    # SQLite. Compare every exported persisted field and both ledgers.
    del data["session"]["duration_seconds"]
    return data


@pytest.mark.parametrize("offset", range(3), ids=("top", "center", "banner"))
@pytest.mark.parametrize("label,texts", REJECTED_CASES, ids=[case[0] for case in REJECTED_CASES])
def test_refused_take_cues_have_no_activity_or_accounting_side_effects(app, label, texts, offset):
    regions = crop_texts(texts, offset)
    callbacks = []
    app.on_mission_complete(callbacks.append)
    before = persisted_export(app)
    for _ in range(2):
        observed, requested = banner_frame(app, *regions)
        assert observed.game_state is GameState.UNKNOWN
        assert observed.state_confidence == 0.0
        assert (observed.mission_text, observed.objective_text, observed.banner_text) == regions
        assert requested == [BATCH]
        assert not observed.activity_name
        assert observed.activity_type is None
        assert app._activity_tracker.current_activity is None
        assert app._data.mission_start_time is None
        assert app._data.mission_start_money is None
        assert app._data.current_mission is None
        assert not app._data.mission_objectives.entries
        assert app._data.terminal_mission_episode is None
    banner_frame(app, banner="MISSION PASSED")
    assert app._activity_tracker.completed_activities == []
    assert persisted_export(app) == before
    assert app.session_stats.activities_completed == 0
    assert app.session_earnings == app.session_stats.total_earnings == 0
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert callbacks == []


def test_retained_real_cayo_table_text_cannot_start_an_unresolved_activity(app):
    before = persisted_export(app)
    observed, requested = banner_frame(app, *RETAINED_CAYO_TEXTS)
    assert requested == [BATCH]
    assert observed.game_state is GameState.UNKNOWN
    assert observed.state_confidence == 0.0
    assert observed.mission.objective == ""
    assert not observed.activity_name
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert app._data.mission_start_money is None
    assert not app._data.mission_objectives.entries
    assert persisted_export(app) == before


@pytest.mark.parametrize("offset", range(3), ids=("top", "center", "banner"))
@pytest.mark.parametrize("text,command", (
    ("Take the briefcase.", "take the briefcase"),
    ("Take out the guards", "take out the guards"),
    ("Objective: Take the briefcase", "take the briefcase"),
    ("Take\nout the guards", "take out the guards"),
))
@pytest.mark.parametrize("opening", (None, 0, 1000))
def test_clean_take_start_keeps_baseline_and_records_one_real_result(
    app, clock, text, command, offset, opening,
):
    app._data.current_money = opening
    callbacks = []
    app.on_mission_complete(callbacks.append)
    observed, _ = banner_frame(app, *crop_texts((text,), offset))
    current = app._activity_tracker.current_activity
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.state_confidence == 0.7
    assert observed.activity_type is ActivityType.UNKNOWN
    assert current is not None
    assert app._data.mission_start_money == opening
    assert app._data.mission_objectives.entries == {command}
    baseline = app._data.mission_start_time, current.started_at
    banner_frame(app, *crop_texts((text,), offset))
    assert app._activity_tracker.current_activity is current
    assert (app._data.mission_start_time, current.started_at) == baseline
    assert activity_rows(app) == []
    clock.value += timedelta(seconds=45)
    app._data.current_money = 1500
    banner_frame(app, banner="MISSION PASSED")
    banner_frame(app, banner="MISSION PASSED")
    row, = activity_rows(app)
    assert row["type"] == "UNKNOWN"
    assert row["name"] == observed.activity_name
    assert row["duration_seconds"] == 45
    assert row["earnings"] == (0 if opening is None else 1500 - opening)
    assert row["success"] is True
    assert app.session_stats.activities_completed == 1
    assert len(callbacks) == 1
    assert app.cooldown_tracker.get_active_cooldowns() == []
    assert app._activity_tracker.current_activity is None


def test_table_take_does_not_replace_an_existing_named_activity_or_baseline(app, clock):
    app._data.current_money = 1000
    banner_frame(app, banner="Headhunter")
    current = app._activity_tracker.current_activity
    baseline = app._data.mission_start_time, app._data.mission_start_money, current.started_at
    banner_frame(app, *RETAINED_CAYO_TEXTS)
    assert app._activity_tracker.current_activity is current
    assert current.name == "Headhunter"
    assert (app._data.mission_start_time, app._data.mission_start_money, current.started_at) == baseline
    assert activity_rows(app) == []
    clock.value += timedelta(seconds=30)
    app._data.current_money = 1250
    banner_frame(app, banner="MISSION PASSED\nHeadhunter")
    row, = activity_rows(app)
    assert (row["name"], row["type"], row["earnings"], row["duration_seconds"]) == (
        "Headhunter", "VIP_WORK", 250, 30,
    )


def test_strong_existing_template_can_still_admit_unknown_activity(app):
    template = SimpleNamespace(matched=True, confidence=0.95, template_name="mission_banner")
    observed, _ = banner_frame(app, *RETAINED_CAYO_TEXTS, template=template)
    assert observed.game_state is GameState.MISSION_ACTIVE
    assert observed.state_confidence == 0.95
    assert app._activity_tracker.current_activity.activity_type is ActivityType.UNKNOWN
    assert activity_rows(app) == []
