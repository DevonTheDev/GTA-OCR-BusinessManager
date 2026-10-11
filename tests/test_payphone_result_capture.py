"""Named hits through real capture/accounting with cached or synthetic OCR text.

The two full diagnostic JSONs are unchanged once-only Tesseract responses.
Money/timer inputs and the saved visual-state controls below are synthetic;
they do not establish payout settlement, Windows OCR, or gameplay accuracy.
"""

from datetime import timedelta
import json
from pathlib import Path

import pytest

from src.app import CaptureResult
from src.detection.state_detector import StateDetectionResult
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_app_accounting import app as app
from tests.test_bottom_objective_capture import persisted
from tests.test_mission_result_accounting import clock as clock
from tests.test_payphone_result_header import COFOUNDER, HEADER, TECH, result
from tests.test_terminal_mission_identity_ownership import mission_stats
from tests.test_vip_status_capture import BATCH, REGIONS, VIP, VipCaptureHarness, owner_snapshot


@pytest.fixture
def hud(app, monkeypatch):
    return VipCaptureHarness(app, monkeypatch)


def start(app, hud, name=TECH, money="$1,000"):
    observed = hud.observe(top=name, money=money)
    assert observed.game_state in (GameState.MISSION_ACTIVE, GameState.SELLING, GameState.HEIST_PREP)
    assert app._activity_tracker.current_activity is not None
    return app._activity_tracker.current_activity


@pytest.mark.parametrize("fixture,name", [
    ("payphone_tech_entrepreneur_diagnostic.json", TECH),
    ("payphone_cofounder_diagnostic.json", COFOUNDER),
])
@pytest.mark.parametrize("owned", [False, True])
def test_complete_saved_six_response_capture_replay(app, hud, monkeypatch, fixture, name, owned):
    path = Path(__file__).parent / "fixtures" / fixture
    payload = json.loads(path.read_bytes())
    sources = payload["sources"]
    assert len(sources) == 6
    assert sources["result_header"]["text"] == "hit COMPLEGE\n" + name
    owner = start(app, hud, name) if owned else None
    # Reproduce only the recorded coarse visual outcome, not source pixels.
    cue = payload["detector_candidate"]
    monkeypatch.setattr(hud.app._state_detector, "_quick_state_check", lambda _frame:
        StateDetectionResult(GameState[cue["state"]], cue["heuristic_score"], "controlled saved visual-state cue"))
    calls_before, batches_before = len(hud.ocr_calls), len(hud.batches)
    observed = hud.observe(
        status=sources["vip_status"]["text"], top=sources["mission_text"]["text"],
        center=sources["center_prompt"]["text"], banner=sources["mission_banner"]["text"],
        bottom=sources["bottom_objective"]["text"], header=sources["result_header"]["text"],
        money="$1,000", timer="0:00",  # separately labelled synthetic inputs
    )
    assert observed.game_state is GameState.MISSION_COMPLETE
    assert observed.result_header_text == observed.result_header_evidence == sources["result_header"]["text"]
    assert observed.mission.mission_name == name and observed.mission.outcome_scope == "payphone_hit"
    assert observed.mission_text == sources["mission_text"]["text"]
    assert observed.objective_text == sources["center_prompt"]["text"]
    assert observed.banner_text == sources["mission_banner"]["text"]
    assert observed.bottom_objective_text == sources["bottom_objective"]["text"]
    assert observed.vip_status_text == sources["vip_status"]["text"]
    assert observed.vip_status_evidence == "" and observed.timer is None
    assert hud.batches[batches_before:] == [BATCH]
    assert hud.ocr_calls[calls_before:] == [
        (REGIONS.mission_text, {"invert": True, "scale": 2.0}),
        (REGIONS.center_prompt, {"invert": True, "scale": 2.0}),
        (REGIONS.mission_banner, {"invert": True, "scale": 2.0}),
        (REGIONS.bottom_objective, {"threshold": False, "invert": True, "scale": 2.0}),
        (VIP, {"threshold": False, "invert": True, "scale": 2.0}),
        (REGIONS.result_header, {"threshold": False, "invert": False, "scale": 2.0}),
        (REGIONS.money_display, {"invert": True, "scale": 2.0}),
    ]
    assert observed.money_change == 0 and persisted(app)["earnings"] == []
    assert len(persisted(app)["activities"]) == int(owned)
    assert app._activity_tracker.completed_activities == ([owner] if owned else [])
    assert app._data.terminal_mission_episode.identity.name == name


@pytest.mark.parametrize("first,accepted", [
    (TECH, True), ("Go to the location", True), ("Payphone Hit", True),
    (COFOUNDER, False), ("Headhunter", False), ("Deliver the goods", False),
    ("Cayo Perico prep", False),
])
def test_owner_matrix_preserves_recorded_baseline_or_rejects_before_transition(app, hud, clock, first, accepted):
    owner = start(app, hud, first)
    before = owner_snapshot(app)
    transitions = tuple(app._state_machine.context.transitions)
    completed = []
    app.on_mission_complete(completed.append)
    clock.value += timedelta(seconds=45)
    observed = hud.observe(header=HEADER, money="$1,250")
    assert observed.game_state is (GameState.MISSION_COMPLETE if accepted else GameState.UNKNOWN)
    assert observed.money_change == 250 and app.session_earnings == 250
    assert app.session_stats.total_earnings == 250
    if accepted:
        assert completed == [owner] and app._activity_tracker.current_activity is None
        assert owner.started_at == before[1] and owner.activity_type is ActivityType.PAYPHONE_HIT
        row, = persisted(app)["activities"]
        assert (row["name"], row["type"], row["earnings"], row["duration_seconds"]) == (TECH, "PAYPHONE_HIT", 250, 45)
        assert mission_stats(app) == (1, 1, 0, 0)
        assert app.cooldown_tracker.get_cooldown("payphone_hit") is not None
    else:
        assert owner_snapshot(app) == before
        assert tuple(app._state_machine.context.transitions) == transitions
        assert completed == persisted(app)["activities"] == []
        assert mission_stats(app) == (0, 0, 0, 0)
        assert app.cooldown_tracker.get_cooldown("payphone_hit") is None
        assert persisted(app)["earnings"][0]["source"] == ""
    # Exercise the actual scheduling setter with the accepted observation.
    app._adjust_capture_rate(GameState.MISSION_ACTIVE)
    assert hud.capture._min_capture_interval == .5
    app._schedule_capture_result(observed.game_state, observed.screen_evidence, observed.game_state)
    assert hud.capture._min_capture_interval == 2.0


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("money,delta,activity_amount", [
    ("$1,000", 0, 0), ("", 0, 0), ("$900", -100, 0), ("$1,250", 250, 250),
    ("+$85,000", 0, 0), ("Reward $85,000", 0, 0),
])
def test_money_is_independent_observed_balance_and_notification_only_crop_is_rejected(
        app, hud, owned, money, delta, activity_amount):
    if owned:
        start(app, hud)
    else:
        hud.observe(money="$1,000")
    observed = hud.observe(header=HEADER, status="Transaction Pending ©", money=money)
    assert observed.game_state is GameState.MISSION_COMPLETE
    rows = persisted(app)
    assert observed.money_change == delta, {"activities": rows["activities"], "earnings": rows["earnings"]}
    assert observed.vip_status_text == "Transaction Pending ©"
    assert app.session_earnings == app.session_stats.total_earnings == max(0, delta)
    assert [row["earnings"] for row in rows["activities"]] == ([activity_amount] if owned else [])
    assert [(row["amount"], row["source"]) for row in rows["earnings"]] == (
        [(delta, TECH if owned else "")] if delta > 0 else []
    )
    if money in ("+$85,000", "Reward $85,000"):
        assert observed.money is None and app.current_money == 1000


def test_missing_opening_balance_never_turns_first_observation_into_activity_income(app, hud):
    start(app, hud, money="")
    observed = hud.observe(header=HEADER, money="$85,000")
    assert observed.money_change == 0
    assert persisted(app)["activities"][0]["earnings"] == 0
    assert persisted(app)["earnings"] == []


@pytest.mark.parametrize("source", ["top", "center", "banner", "status"])
def test_bonus_and_notification_text_in_other_crops_is_not_money_authority(app, hud, source):
    start(app, hud)
    arguments = {source: "Assassination Bonus: COMPLETE\n+$85,000"}
    observed = hud.observe(header=HEADER, **arguments)
    assert observed.game_state is GameState.MISSION_COMPLETE
    assert observed.money is None and observed.money_change == 0
    assert persisted(app)["activities"][0]["earnings"] == 0
    assert persisted(app)["earnings"] == []


def test_late_balance_increase_is_unattributed_and_does_not_reconcile_completed_row(app, hud):
    start(app, hud)
    hud.observe(header=HEADER, money="$1,000")
    row, = persisted(app)["activities"]
    cooldown = app.cooldown_tracker.get_cooldown("payphone_hit")
    hud.observe(header=HEADER, money="$1,250", status="Transaction Pending ©")
    hud.observe(header=HEADER, money="$1,250")
    assert persisted(app)["activities"] == [row] and row["earnings"] == 0
    assert [(event["amount"], event["source"]) for event in persisted(app)["earnings"]] == [(250, "")]
    assert app.session_earnings == 250 and mission_stats(app) == (1, 1, 0, 0)
    assert app.cooldown_tracker.get_cooldown("payphone_hit") == cooldown


def test_result_only_title_dropout_and_new_objective_obey_episode_fence(app, hud):
    first = hud.observe(header=HEADER)
    held = hud.observe(top=TECH)
    repeated = hud.observe(header=HEADER)
    assert first.game_state is repeated.game_state is GameState.MISSION_COMPLETE
    assert held.game_state is GameState.UNKNOWN
    assert app._activity_tracker.current_activity is None
    assert persisted(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    fresh = hud.observe(top=TECH, center="Go to the location")
    assert fresh.game_state is GameState.MISSION_ACTIVE
    assert app._activity_tracker.current_activity is not None


@pytest.mark.parametrize("previous,next_name", [(TECH, COFOUNDER), (COFOUNDER, TECH)])
def test_old_named_hit_cannot_consume_the_next_named_owner(app, hud, previous, next_name):
    start(app, hud, previous)
    hud.observe(header="hit complete\n" + previous)
    start(app, hud, next_name)
    before = owner_snapshot(app)
    observed = hud.observe(header="hit complege\n" + previous, money="$1,250")
    assert observed.game_state is GameState.UNKNOWN and owner_snapshot(app) == before
    assert len(persisted(app)["activities"]) == 1
    assert persisted(app)["earnings"][0]["source"] == ""


@pytest.mark.parametrize("window", ["state", "money"])
@pytest.mark.parametrize("next_name", [TECH, COFOUNDER, "Go to the location"])
def test_capture_pins_original_owner_across_both_callback_windows(app, hud, window, next_name):
    original = start(app, hud)
    created = []

    def replace_owner(*_args):
        if created:
            return
        # Explicit nested completion owns its row. The outer stale observation
        # must never consume or relabel the newly created activity, even if its
        # name/family is identical or its identity is unresolved.
        app._process_state(result(), CaptureResult())
        app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .9, "callback start",
            mission_text=next_name, objective_text="Find the target"), CaptureResult())
        created.append(owner_snapshot(app))

    if window == "state":
        app.on_state_change(lambda _previous, current: replace_owner() if current is GameState.MISSION_COMPLETE else None)
    else:
        app.on_money_change(replace_owner)
    observed = hud.observe(header=HEADER, money="$1,250")
    assert len(created) == 1
    assert owner_snapshot(app) == created[0] and created[0][0] is not original
    assert observed.game_state is GameState.UNKNOWN
    assert len(persisted(app)["activities"]) == 1 and mission_stats(app) == (1, 1, 0, 0)
    assert persisted(app)["activities"][0]["earnings"] == (0 if window == "state" else 250)
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [
        (250, "" if window == "state" else TECH),
    ]


def test_ownerless_result_cannot_consume_owner_created_by_state_callback(app, hud):
    hud.observe(money="$1,000")
    created = []

    def create_owner(_previous, current):
        if current is GameState.MISSION_COMPLETE:
            app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .9, "callback start",
                mission_text=TECH), CaptureResult())
            created.append(owner_snapshot(app))

    app.on_state_change(create_owner)
    observed = hud.observe(header=HEADER, money="$1,250")
    assert len(created) == 1 and owner_snapshot(app) == created[0]
    assert observed.game_state is GameState.UNKNOWN
    assert persisted(app)["activities"] == [] and mission_stats(app) == (0, 0, 0, 0)
    assert persisted(app)["earnings"][0]["source"] == ""


def test_state_callback_in_place_refinement_cannot_lend_the_old_result_a_new_owner(app, hud):
    owner = start(app, hud, "Go to the location")
    refined = []

    def refine(_previous, current):
        if current is GameState.MISSION_COMPLETE:
            app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .9, "callback refinement",
                mission_text=COFOUNDER), CaptureResult())
            refined.append(owner_snapshot(app))

    app.on_state_change(refine)
    observed = hud.observe(header=HEADER, money="$1,250")
    assert len(refined) == 1 and refined[0][0] is owner
    assert owner.name == COFOUNDER and owner_snapshot(app) == refined[0]
    assert observed.game_state is GameState.UNKNOWN
    assert persisted(app)["activities"] == []
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [(250, "")]


def test_fallback_detector_without_cached_reading_keeps_ownerless_source_unattributed(app, hud, monkeypatch):
    hud.observe(money="$1,000")
    monkeypatch.setattr(app._state_detector, "detect", lambda *_args, **_kwargs: result())
    observed = hud.observe(money="$1,250")
    assert observed.game_state is GameState.MISSION_COMPLETE
    assert persisted(app)["activities"] == []
    assert [(row["amount"], row["source"]) for row in persisted(app)["earnings"]] == [(250, "")]


@pytest.mark.parametrize("window", ["state", "money"])
def test_fallback_detector_pins_owner_before_callback(app, hud, monkeypatch, window):
    original = start(app, hud)
    created = []
    monkeypatch.setattr(app._state_detector, "detect", lambda *_args, **_kwargs: result())

    def replace_owner(*_args):
        if created:
            return
        app._process_state(result(), CaptureResult())
        app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .9, "callback start",
            mission_text=TECH, objective_text="Find the target"), CaptureResult())
        created.append(owner_snapshot(app))

    if window == "state":
        app.on_state_change(lambda _previous, current: replace_owner() if current is GameState.MISSION_COMPLETE else None)
    else:
        app.on_money_change(replace_owner)
    observed = hud.observe(money="$1,250")
    assert len(created) == 1 and created[0][0] is not original
    assert owner_snapshot(app) == created[0]
    assert observed.game_state is GameState.UNKNOWN
    assert len(persisted(app)["activities"]) == 1
    assert persisted(app)["earnings"][0]["source"] == ("" if window == "state" else TECH)


@pytest.mark.parametrize("action", ["repeat", "start", "nested", "throw"])
def test_completion_callbacks_preserve_consumption_and_argument_amounts(app, hud, action):
    owner = start(app, hud)
    events = []
    created = []

    def callback(activity):
        events.append((activity.name, activity.earnings))
        if activity is not owner:
            return
        assert app._data.mission_start_time is None
        if action == "throw":
            raise RuntimeError("synthetic listener error")
        if action == "repeat":
            app._process_state(result(), CaptureResult())
        else:
            app._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, .9, "callback start",
                mission_text=COFOUNDER), CaptureResult())
            created.append(app._activity_tracker.current_activity)
            if action == "nested":
                app._process_state(result("hit complete\n" + COFOUNDER), CaptureResult())

    app.on_mission_complete(callback)
    app.on_mission_complete(lambda activity: events.append(("later: " + activity.name, activity.earnings)))
    hud.observe(header=HEADER, money="$1,250")
    assert events[0] == (TECH, 250)
    assert events[-1] == ("later: " + TECH, 250)
    assert len(persisted(app)["activities"]) == (2 if action == "nested" else 1)
    if action == "start":
        assert app._activity_tracker.current_activity is created[0]
    else:
        assert app._activity_tracker.current_activity is None
