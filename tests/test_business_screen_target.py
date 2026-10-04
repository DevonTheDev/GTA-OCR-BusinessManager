"""Explicit live screen assignment through the real loop and parser, without game capture."""

import copy
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app import AppState, GTABusinessManager
from src.capture.regions import ScreenRegions
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.parsers.business_parser import BusinessType
from src.detection.state_detector import StateDetectionResult
from src.game.businesses import BUSINESSES
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor


@pytest.fixture
def manager(tmp_path, caplog):
    yield GTABusinessManager(Settings(tmp_path / "settings.yaml"))
    assert "Error processing business computer:" not in caplog.text
    assert "Capture cycle error:" not in caplog.text


def business_snapshot(manager):
    """Include timestamps and parser state so a discarded batch cannot hide side effects."""
    return copy.deepcopy((
        manager.data.business_states,
        manager._optimizer._business_states,
        manager._business_parser.get_all_last_readings(),
    ))


class SyntheticBusinessCapture:
    """Replace only platform boundaries, keeping capture-loop dispatch and parsing real."""

    def __init__(self, manager, frames, *, hook=None, state=GameState.BUSINESS_COMPUTER):
        self.manager = manager
        self.frames = frames
        self.hook = hook or (lambda *args: None)
        self.state = state
        self.regions = ScreenRegions()
        self.batches = 0
        self.calls = []
        self.ocr_calls = []
        self.rates = []
        self.observations = []
        self.closed = False
        self.frame = {}
        self.ocr = SimpleNamespace(
            is_available=True, recognize_preprocessed=self.recognize_preprocessed,
        )

    def install(self):
        manager = self.manager
        manager._capture = self
        manager._ocr = self.ocr
        manager._state_detector = SimpleNamespace(
            detect=lambda *args, **kwargs: StateDetectionResult(
                self.state, 0.9, "synthetic business screen",
            )
        )
        manager._state_machine = GameStateMachine()
        manager._state_machine.add_listener(manager._on_game_state_transition)
        manager._perf_monitor = PerformanceMonitor()

    def capture_multiple_regions(self, requested):
        # Bounded fallback even if a swallowed production exception skips a callback.
        if self.batches >= len(self.frames):
            self.manager._stop_event.set()
            return dict.fromkeys(range(len(requested)))
        self.frame = dict(zip(
            self.regions.get_business_regions().values(), self.frames[self.batches],
        ))
        self.batches += 1
        return {0: object(), 1: None, 2: None, 3: None, 4: None, 5: None}

    def capture_region(self, region, wait_for_rate=True):
        self.calls.append((region, wait_for_rate))
        self.hook("capture", len(self.calls))
        return self.frame[region]

    def recognize_preprocessed(self, image, **kwargs):
        self.ocr_calls.append((image, kwargs))
        self.hook("ocr", len(self.ocr_calls))
        return SimpleNamespace(text=image)

    def set_capture_rate(self, rate):
        self.rates.append(rate)

    def close(self):
        self.closed = True

    def observe(self, result):
        self.observations.append((result, business_snapshot(self.manager)))
        if len(self.observations) >= len(self.frames):
            self.manager._stop_event.set()

    def run(self):
        self.install()
        self.manager._state = AppState.RUNNING
        self.manager._capture_thread = threading.current_thread()
        self.manager._stop_event.clear()
        self.manager.on_capture(self.observe)
        try:
            self.manager._capture_loop()
        finally:
            self.manager._on_capture.remove(self.observe)
        assert len(self.observations) == len(self.frames)
        assert self.closed


@pytest.mark.parametrize("business_id", list(BUSINESSES))
def test_all_catalog_targets_assign_unnamed_labeled_readings(manager, business_id, caplog):
    caplog.set_level(logging.INFO)
    assert len(BUSINESSES) == 11
    assert manager.business_screen_target is None
    manager.set_business_screen_target(business_id)
    assert manager.business_screen_target == business_id
    capture = SyntheticBusinessCapture(
        manager, [("Stock: 5/10", "Supplies: 3/4", "Value: $123,456")],
    )
    capture.run()

    assert set(manager.data.business_states) == {business_id}
    state = manager.get_business_state(business_id)
    supply = 75 if BUSINESSES[business_id].uses_supplies else None
    assert (state["stock"], state["supply"], state["value"]) == (50, supply, 123456)
    assert state["identity_source"] == "selected_target"
    optimized = manager._optimizer._business_states[business_id]
    assert (optimized.stock_percent, optimized.supply_percent, optimized.estimated_value) == (
        50, supply, 123456,
    )
    reading = manager._business_parser.get_last_reading(BusinessType[business_id.upper()])
    assert reading.stock_level == 50
    assert f"Business assigned to selected target: {business_id.upper()}" in caplog.text
    assert "Business detected:" not in caplog.text
    assert [region for region, _ in capture.calls] == list(
        capture.regions.get_business_regions().values(),
    )
    assert all(wait is False for _, wait in capture.calls)
    assert all(options == {"invert": True, "scale": 2.0} for _, options in capture.ocr_calls)


@pytest.mark.parametrize("invalid", [
    "hangar", "auto_shop", "unknown", "BUNKER", " bunker", "bunker ", "", "Automatic",
    BusinessType.BUNKER, 1, True, [], {},
])
def test_invalid_targets_leave_selection_and_live_state_untouched(manager, invalid):
    manager.set_business_screen_target("bunker")
    manager.update_business_state("bunker", 10, 20, 300)
    before = business_snapshot(manager)
    with pytest.raises(ValueError):
        manager.set_business_screen_target(invalid)
    assert manager.business_screen_target == "bunker"
    assert business_snapshot(manager) == before


@pytest.mark.parametrize("text,business_id", [
    ("Bunker Stock: 5/10", "bunker"),
    ("Acid Lab Stock: 5/10", "meth"),
    ("Vehicle Warehouse Stock: 5/10", "nightclub"),
    ("Special Cargo Stock: 5/10", "hangar"),
    ("Stock: 5/10", "unknown"),
])
def test_automatic_keeps_existing_keyword_precedence(manager, text, business_id, caplog):
    caplog.set_level(logging.INFO)
    manager.set_business_screen_target(None)
    SyntheticBusinessCapture(manager, [(text, "Supplies: 3/4", "Value: $123,456")]).run()
    # Identification is unchanged; only cataloged cards accept live observations.
    if business_id in BUSINESSES:
        assert set(manager.data.business_states) == {business_id}
        assert manager.get_business_state(business_id)["identity_source"] == "ocr_text"
        assert f"Business detected: {business_id.upper()}" in caplog.text
    else:
        assert manager.data.business_states == {}
        assert manager._optimizer._business_states == {}
        if business_id != "unknown":
            assert manager._business_parser.get_last_reading(
                BusinessType[business_id.upper()]
            ).stock_level == 50
    assert "Business assigned" not in caplog.text


@pytest.mark.parametrize("target,text", [
    ("acid_lab", "Meth Lab Stock: 50%"),
    ("vehicle_warehouse", "Nightclub Warehouse Stock: 50%"),
    ("special_cargo", "Hangar Stock: 50%"),
    ("agency", "Bunker Stock: 50%"),
])
def test_selected_target_overrides_conflicting_identity_text(manager, target, text):
    manager.set_business_screen_target(target)
    SyntheticBusinessCapture(manager, [(text, "Supplies: 75%", "Value: $123,456")]).run()
    assert set(manager.data.business_states) == {target}
    assert manager.get_business_state(target)["identity_source"] == "selected_target"
    assert set(manager._business_parser.get_all_last_readings()) == {BusinessType[target.upper()]}


@pytest.mark.parametrize("frame", [
    ("50%", "75%", "$123,456"),
    ("5/10", "3/4", "123456"),
    ("", "", ""),
    ("  ", "\n", "\t"),
    ("Stock: 5/0", "Supplies: 11/10", ""),
    ("Stock: 101%", "Supplies: 101%", "Value: unavailable"),
])
@pytest.mark.parametrize("target", [None, "bunker"])
def test_selection_does_not_make_blank_bare_or_invalid_readings_publishable(manager, frame, target):
    manager.set_business_screen_target(target)
    manager.update_business_state("bunker", 70, 60, 900)
    manager._business_parser.parse("Bunker Stock: 70% Supplies: 60% Value: $900")
    before = business_snapshot(manager)
    SyntheticBusinessCapture(manager, [frame]).run()
    assert business_snapshot(manager) == before


@pytest.mark.parametrize("frame,expected", [
    (("", "Supplies: 1/10", ""), (None, 10, None)),
    (("Stock: 1/4", "", ""), (25, None, None)),
    (("", "", "Value: $123,456"), (None, None, 123456)),
    (("Stock: 5/0", "Supplies: 1/2", ""), (None, 50, None)),
])
def test_selected_partial_readings_replace_missing_fields_with_unknown(manager, frame, expected):
    manager.set_business_screen_target("bunker")
    manager.update_business_state("bunker", 70, 60, 900)
    SyntheticBusinessCapture(manager, [frame]).run()
    state = manager.get_business_state("bunker")
    assert (state["stock"], state["supply"], state["value"]) == expected
    assert state["identity_source"] == "selected_target"


def test_selection_does_not_force_business_detection(manager):
    manager.set_business_screen_target("bunker")
    capture = SyntheticBusinessCapture(
        manager, [("Stock: 50%", "Supplies: 75%", "Value: $123,456")], state=GameState.IDLE,
    )
    capture.run()
    assert manager.data.business_states == {}
    assert capture.calls == [] and capture.ocr_calls == []


@pytest.mark.parametrize("source", [None, "ocr_text", "selected_target", "manual_entry"])
def test_optional_identity_source_preserves_direct_update_compatibility(manager, source):
    if source is None:
        manager.update_business_state("bunker", 10, 20, 300)
    else:
        manager.update_business_state("bunker", 10, 20, 300, identity_source=source)
    state = manager.get_business_state("bunker")
    expected_keys = {"stock", "supply", "value", "updated"}
    assert set(state) == expected_keys | ({"identity_source"} if source else set())
    assert state.get("identity_source") == source
    assert manager._optimizer._business_states["bunker"].estimated_value == 300


@pytest.mark.parametrize("source", ["manual", "", 42, [], {}])
def test_invalid_identity_source_cannot_mutate_either_cache(manager, source):
    manager.update_business_state("bunker", 10, 20, 300)
    before = business_snapshot(manager)
    with pytest.raises(ValueError):
        manager.update_business_state("bunker", 90, 80, 700, identity_source=source)
    assert business_snapshot(manager) == before


@pytest.mark.parametrize("boundary,index", [("capture", 1), ("ocr", 1), ("ocr", 3)])
@pytest.mark.parametrize("initial,changes", [
    ("agency", ["bunker"]),
    ("agency", ["bunker", "agency"]),
    (None, ["bunker"]),
    ("bunker", [None]),
])
def test_switch_during_batch_discards_before_parser_then_accepts_next_batch(
    manager, boundary, index, initial, changes,
):
    manager.set_business_screen_target(initial)
    for business_id in ("bunker", "agency"):
        manager.update_business_state(business_id, 10, 20, 300)
        manager._business_parser.parse(
            "Stock: 10%", business_hint=BusinessType[business_id.upper()],
        )
    before = business_snapshot(manager)
    entered, release = threading.Event(), threading.Event()

    def pause_boundary(kind, count):
        if (kind, count) == (boundary, index):
            entered.set()
            assert release.wait(3), "Test did not release synthetic OCR"

    capture = SyntheticBusinessCapture(manager, [
        ("Bunker Stock: 50%", "Supplies: 75%", "Value: $123,456"),
        ("Bunker Stock: 60%", "Supplies: 80%", "Value: $234,567"),
    ], hook=pause_boundary)
    errors = []

    def run():
        try:
            capture.run()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        # The getter and setter must remain available while capture/OCR waits.
        assert manager._data_lock.acquire(timeout=0.2)
        manager._data_lock.release()
        for target in changes:
            manager.set_business_screen_target(target)
        assert business_snapshot(manager) == before
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and errors == []
    assert capture.observations[0][1] == before
    final_target = changes[-1]
    business_id = final_target or "bunker"
    state = manager.get_business_state(business_id)
    supply = 80 if BUSINESSES[business_id].uses_supplies else None
    assert (state["stock"], state["supply"], state["value"]) == (60, supply, 234567)
    assert state["identity_source"] == ("selected_target" if final_target else "ocr_text")
    reading = manager._business_parser.get_last_reading(BusinessType[business_id.upper()])
    assert reading.stock_level == 60
    assert manager._optimizer._business_states[business_id].estimated_value == 234567


@pytest.mark.parametrize("target", [None, "agency"])
def test_reselecting_same_target_does_not_discard_current_batch(manager, target):
    manager.set_business_screen_target(target)

    def repeat_selection(kind, count):
        if (kind, count) == ("ocr", 1):
            manager.set_business_screen_target(target)

    SyntheticBusinessCapture(manager, [("Bunker Stock: 50%", "", "")], hook=repeat_selection).run()
    assert manager.get_business_state(target or "bunker")["stock"] == 50


def test_rejected_selection_does_not_retire_current_batch(manager):
    manager.set_business_screen_target("agency")

    def reject_selection(kind, count):
        if (kind, count) == ("ocr", 1):
            with pytest.raises(ValueError):
                manager.set_business_screen_target("hangar")

    SyntheticBusinessCapture(manager, [("Stock: 50%", "", "")], hook=reject_selection).run()
    assert manager.business_screen_target == "agency"
    assert manager.get_business_state("agency")["stock"] == 50


@pytest.mark.parametrize("boundary", ["parser", "optimizer"])
def test_target_change_waits_until_parser_and_both_caches_publish_atomically(
    manager, boundary, monkeypatch,
):
    manager.set_business_screen_target("agency")
    entered, release = threading.Event(), threading.Event()
    setter_started, setter_done = threading.Event(), threading.Event()
    errors, at_selection = [], []
    component, method = (
        (manager._business_parser, "parse") if boundary == "parser"
        else (manager._optimizer, "update_business_state")
    )
    original = getattr(component, method)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(component, method, blocked)
    capture = SyntheticBusinessCapture(
        manager, [("Stock: 50%", "Supplies: 75%", "Value: $123,456")],
    )

    def run():
        try:
            capture.run()
        except BaseException as error:
            errors.append(error)

    def change_selection():
        setter_started.set()
        manager.set_business_screen_target("bunker")
        at_selection.append(business_snapshot(manager))
        setter_done.set()

    worker = threading.Thread(target=run, daemon=True)
    setter = threading.Thread(target=change_selection, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        acquired = manager._data_lock.acquire(timeout=0.05)
        if acquired:
            manager._data_lock.release()
        assert not acquired, "Business parsing and both cache writes need one critical section"
        setter.start()
        assert setter_started.wait(2)
        assert not setter_done.wait(0.05)
    finally:
        release.set()
        worker.join(3)
        if setter.ident is not None:
            setter.join(3)
    assert not worker.is_alive() and not setter.is_alive() and errors == []
    assert manager.business_screen_target == "bunker"
    app_states, optimized_states, parser_states = at_selection[0]
    assert app_states["agency"]["stock"] == 50
    assert optimized_states["agency"].stock_percent == 50
    assert parser_states[BusinessType.AGENCY].stock_level == 50
    assert "bunker" not in app_states and "bunker" not in optimized_states


@pytest.mark.parametrize("target", [None, "agency"])
def test_stop_retires_inflight_batch_even_when_already_automatic(manager, target, monkeypatch):
    manager.set_business_screen_target(target)
    manager.update_business_state("bunker", 10, 20, 300)
    manager._business_parser.parse("Bunker Stock: 10%")
    before = business_snapshot(manager)
    entered, release = threading.Event(), threading.Event()

    def pause_ocr(kind, count):
        if (kind, count) == ("ocr", 1):
            entered.set()
            assert release.wait(3)

    capture = SyntheticBusinessCapture(
        manager, [("Bunker Stock: 50%", "Supplies: 75%", "")], hook=pause_ocr,
    )
    monkeypatch.setattr(manager, "_initialize_components", capture.install)
    assert manager.start()
    worker = manager._capture_thread
    stopper = threading.Thread(target=manager.stop, daemon=True)
    try:
        assert entered.wait(2)
        stopper.start()
        assert manager._stop_event.wait(2)
        assert manager.business_screen_target is None
    finally:
        release.set()
        worker.join(3)
        if stopper.ident is not None:
            stopper.join(3)
    assert not worker.is_alive() and not stopper.is_alive()
    assert manager.state is AppState.STOPPED
    assert business_snapshot(manager) == before
    assert capture.closed


@pytest.mark.parametrize("target", [None, "agency"])
def test_stop_from_state_change_prevents_business_extraction(manager, target):
    manager.set_business_screen_target(target)
    manager.update_business_state("bunker", 10, 20, 300)
    manager._business_parser.parse("Bunker Stock: 10%")
    before = business_snapshot(manager)
    capture = SyntheticBusinessCapture(
        manager, [("Bunker Stock: 50%", "Supplies: 75%", "Value: $123,456")],
    )
    capture.install()
    transitions = []

    def stop_on_business_screen(old_state, new_state):
        transitions.append((old_state, new_state))
        manager.stop()

    manager.on_state_change(stop_on_business_screen)
    manager._state = AppState.RUNNING
    worker = threading.Thread(target=manager._capture_loop, daemon=True)
    manager._capture_thread = worker
    worker.start()
    try:
        worker.join(3)
    finally:
        manager._stop_event.set()
        worker.join(3)
    assert not worker.is_alive()
    assert transitions == [(GameState.UNKNOWN, GameState.BUSINESS_COMPUTER)]
    assert manager.state is AppState.STOPPED and capture.closed
    assert manager.business_screen_target is None
    assert business_snapshot(manager) == before
    assert capture.calls == [] and capture.ocr_calls == []


@pytest.mark.parametrize("target", [None, "agency"])
def test_stop_during_state_detection_prevents_business_extraction(manager, target):
    manager.set_business_screen_target(target)
    manager.update_business_state("bunker", 10, 20, 300)
    manager._business_parser.parse("Bunker Stock: 10%")
    before = business_snapshot(manager)
    entered, release = threading.Event(), threading.Event()
    capture = SyntheticBusinessCapture(
        manager, [("Bunker Stock: 50%", "Supplies: 75%", "Value: $123,456")],
    )
    capture.install()
    detect = manager._state_detector.detect

    def blocked_detection(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return detect(*args, **kwargs)

    manager._state_detector.detect = blocked_detection
    manager._state = AppState.RUNNING
    worker = threading.Thread(target=manager._capture_loop, daemon=True)
    manager._capture_thread = worker
    stopper = threading.Thread(target=manager.stop, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        stopper.start()
        assert manager._stop_event.wait(2)
    finally:
        release.set()
        worker.join(3)
        if stopper.ident is not None:
            stopper.join(3)
    assert not worker.is_alive() and not stopper.is_alive()
    assert manager.state is AppState.STOPPED and capture.closed
    assert manager.business_screen_target is None
    assert business_snapshot(manager) == before
    assert capture.calls == [] and capture.ocr_calls == []


@pytest.mark.parametrize("state", [AppState.STOPPED, AppState.RUNNING])
def test_stop_marker_is_set_before_retired_generation_becomes_visible(manager, state, monkeypatch):
    manager.set_business_screen_target("agency")
    manager._state = state
    generation = manager._business_screen_generation
    original_lock = manager._data_lock
    markers_at_unlock = []

    class ObservedLock:
        def __enter__(self):
            original_lock.acquire()

        def __exit__(self, *_args):
            if manager._business_screen_generation != generation:
                markers_at_unlock.append(manager._stop_event.is_set())
            original_lock.release()

    monkeypatch.setattr(manager, "_data_lock", ObservedLock())
    manager.stop()
    assert markers_at_unlock and all(markers_at_unlock)
    assert manager.business_screen_target is None


def test_stop_marker_during_ocr_prevents_publication_even_without_target_change(manager):
    manager.set_business_screen_target("agency")
    manager.update_business_state("agency", 10, 20, 300)
    manager._business_parser.parse("Agency Stock: 10%")
    before = business_snapshot(manager)

    def mark_stopped(kind, count):
        if (kind, count) == ("ocr", 3):
            manager._stop_event.set()

    capture = SyntheticBusinessCapture(
        manager, [("Stock: 50%", "Supplies: 75%", "Value: $123,456")], hook=mark_stopped,
    )
    capture.install()
    capture.capture_multiple_regions([None] * 5)
    manager._process_business_computer()
    assert business_snapshot(manager) == before


def test_initial_stopped_private_processing_remains_available(manager):
    manager.set_business_screen_target("agency")
    capture = SyntheticBusinessCapture(manager, [("Stock: 50%", "", "")])
    capture.install()
    capture.capture_multiple_regions([None] * 5)
    assert manager.state is AppState.STOPPED and not manager._stop_event.is_set()
    manager._process_business_computer()
    assert manager.get_business_state("agency")["stock"] == 50


def test_stopped_preselection_survives_start_pause_resume_until_explicit_stop(manager, monkeypatch):
    manager.set_business_screen_target("acid_lab")
    entered, release = threading.Event(), threading.Event()

    def pause_ocr(kind, count):
        if (kind, count) == ("ocr", 1):
            entered.set()
            assert release.wait(3)

    capture = SyntheticBusinessCapture(manager, [("Stock: 50%", "", "")], hook=pause_ocr)
    monkeypatch.setattr(manager, "_initialize_components", capture.install)
    assert manager.start()
    worker = manager._capture_thread
    try:
        assert entered.wait(2)
        assert manager.business_screen_target == "acid_lab"
        manager.pause()
        assert manager.state is AppState.PAUSED and manager.business_screen_target == "acid_lab"
        manager.resume()
        assert manager.state is AppState.RUNNING and manager.business_screen_target == "acid_lab"
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert manager.get_business_state("acid_lab")["stock"] == 50
    manager.stop()
    assert manager.business_screen_target is None
    manager.set_business_screen_target("bunker")
    manager.stop()
    assert manager.business_screen_target is None


def test_selection_and_stop_preserve_files_records_schedules_and_old_observations(manager, tmp_path):
    repository = Repository(str(tmp_path / "test.db"))
    assert repository.initialize()
    try:
        character = repository.get_or_create_character("Synthetic Player")
        repository.set_active_character(character.id)
        repository.save_business_checkin(character.id, "bunker", 10, 20, 300, "Manual source")
        repository.set_manual_business_pin(character.id, "bunker", True)
        manager._repository = repository
        manager._data.character_id = character.id
        manager.update_business_state("bunker", 50, 75, 1000, identity_source="selected_target")
        manager._optimizer._scheduler.schedule_sell("bunker", estimated_value=123456)
        manager._optimizer._scheduler.schedule_resupply(
            "bunker", datetime.now() + timedelta(hours=1),
        )
        before = business_snapshot(manager)
        schedule_before = copy.deepcopy(manager._optimizer._scheduler.get_upcoming())
        files_before = {
            path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
        }
        with sqlite3.connect(tmp_path / "test.db") as connection:
            records_before = list(connection.iterdump())

        for target in ("agency", "bunker", None, "acid_lab"):
            manager.set_business_screen_target(target)
        manager.stop()

        assert manager.business_screen_target is None
        assert manager.state is AppState.STOPPED and manager._capture_thread is None
        assert manager.data.character_id == character.id
        assert business_snapshot(manager) == before
        assert manager._optimizer._scheduler.get_upcoming() == schedule_before
        assert {
            path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
        } == files_before
        with sqlite3.connect(tmp_path / "test.db") as connection:
            assert list(connection.iterdump()) == records_before
        fresh_manager = GTABusinessManager(Settings(tmp_path / "settings.yaml"))
        assert fresh_manager.business_screen_target is None
    finally:
        repository.close()
