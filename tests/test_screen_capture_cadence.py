"""Current-header cadence only; injected OCR is not a live accuracy claim."""

import copy
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src import app as app_module
from src.app import AppState, CaptureResult, GTABusinessManager
from src.capture import screen_capture
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.detection_sample import DetectionSampleCollector
from src.detection.ocr_engine import OCRResult
from src.detection.parsers.mission_parser import MissionParser, MissionType
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app
from src.detection.mission_episode import MissionIdentity, TerminalMissionEpisode
from src.detection.parsers.money_parser import MoneyReading
from tests.test_vip_status_admission import detect


MARKER = "NIGHTCLUB_MANAGEMENT_HEADER"
# Exact existing result-header OCR of the publisher's 1600x900 Nightclub still.
# Original SHA256 f36e52c5550c27868c8be6944b1200556688e4b4c2ff38f6307bf1aea95a6e35.
HEADER = "a Nightclub Popularity 1\n7\\ — ~¥ .\nfi I - _—~Player Visits\n> . Current Clientéle G"
LABELS = ("Nightclub Popularity", "Player Visits", "Current Clientéle")


def header_result(raw=HEADER, **changes):
    initial = StateDetectionResult(GameState.UNKNOWN, 0.0, "unchanged reason",
                                   mission=MissionParser().parse(""), **changes)
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=True,
        recognize_preprocessed=lambda *_a, **_k: SimpleNamespace(text=raw)))
    return initial, detector._result_header_observation(initial, object())


def test_observed_header_gains_only_screen_evidence():
    original, result = header_result()
    assert getattr(result, "screen_evidence", "") == MARKER
    expected = asdict(original)
    expected.update(result_header_text=HEADER, screen_evidence=MARKER)
    assert asdict(result) == expected
    assert result.mission is original.mission


def test_both_result_defaults_are_empty():
    for result in (CaptureResult(), StateDetectionResult(GameState.UNKNOWN, 0, "")):
        assert getattr(result, "screen_evidence", None) == ""


@pytest.mark.parametrize("raw", [HEADER, HEADER.replace("Clientéle", "Clientèle"),
    HEADER.replace("Nightclub Popularity", "nightclub\tPOPULARITY").replace("Player Visits", "PLAYER  visits")])
def test_explicit_observed_spellings_and_ascii_layout(raw):
    _, result = header_result(raw)
    assert getattr(result, "screen_evidence", "") == MARKER
    assert result.result_header_text == raw


BAD_HEADERS = ["", "$250000", HEADER.replace("Clientéle", "Clientele"),
    HEADER.replace("Clientéle", "Clientêle"), HEADER.replace("Player Visits", "Player\nVisits"),
    HEADER.replace("Nightclub Popularity", "Nİghtclub Popularity"),
    HEADER.replace("Player Visits", "Player\u00a0Visits"),
    "\n".join(reversed(LABELS)), HEADER + "\n" * 9,
    HEADER + "\n" + "x" * 129, HEADER + "\n" + "é" * 65,
    HEADER + "\n" + "\n".join(["x" * 110] * 4), HEADER + "\ud800",
    *[HEADER.replace(label, "") for label in LABELS],
    *[HEADER.replace(label, "x" + label) for label in LABELS],
    *[HEADER.replace(label, label + "x") for label in LABELS],
    *[HEADER + "\n" + extra for extra in ("Headhunter", "MISSION PASSED", "MISSION FAILED",
       "HEIST PASSED", "Go to the location", "Escape Cayo Perico", "VIP WORK")]]


@pytest.mark.parametrize("raw", BAD_HEADERS)
def test_incomplete_mixed_unbounded_or_repaired_headers_have_no_marker(raw):
    _, result = header_result(raw)
    assert getattr(result, "screen_evidence", "") == ""


@pytest.mark.parametrize("field", ["bottom_objective_command", "vip_status_evidence",
    "active_title_evidence", "result_header_evidence"])
def test_independently_admitted_evidence_blocks_screen_marker(field):
    _, result = header_result(**{field: "independent evidence"})
    assert getattr(result, "screen_evidence", "") == ""


@pytest.mark.parametrize("state", [state for state in GameState if state != GameState.UNKNOWN])
def test_only_unknown_state_can_gain_marker(state):
    base = StateDetectionResult(state, .7, "existing", mission=MissionParser().parse(""))
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=True,
        recognize_preprocessed=lambda *_a, **_k: SimpleNamespace(text=HEADER)))
    result = detector._result_header_observation(base, object())
    assert getattr(result, "screen_evidence", "") == ""


@pytest.mark.parametrize("text", ["Headhunter", "VIP WORK", "Cayo Perico prep", "MISSION PASSED",
    "MISSION FAILED", "Headhunter\nSightseer"])
def test_primary_reading_is_never_rescued_by_labels(text):
    base = StateDetectionResult(GameState.UNKNOWN, 0, "primary", mission=MissionParser().parse(text))
    detector = StateDetector(ocr_engine=SimpleNamespace(is_available=True,
        recognize_preprocessed=lambda *_a, **_k: SimpleNamespace(text=HEADER)))
    result = detector._result_header_observation(base, object())
    assert getattr(result, "screen_evidence", "") == ""
    assert result.mission is base.mission


@pytest.mark.parametrize("source", ["top", "center", "banner", "bottom", "status"])
def test_other_sources_cannot_supply_header_labels(source):
    result, _ = detect(HEADER if source == "status" else "", **({source: HEADER} if source != "status" else {}))
    assert getattr(result, "screen_evidence", "") == ""


def test_split_sources_cannot_supply_conjunction():
    result, _ = detect("", top=LABELS[0], bottom=LABELS[1], header=LABELS[2])
    assert getattr(result, "screen_evidence", "") == ""


class CycleFrames:
    """Native rate setter and app cycle; replace only capture and OCR boundaries."""

    def __init__(self, manager, monkeypatch, frames):
        self.manager, self.frames = manager, list(frames)
        self.index = 0
        self.intervals, self.calls, self.results = [], [], []
        self.texts, self.images = {}, {}
        monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _i: SimpleNamespace(
            width=200, height=120, offset=(0, 0), scale_factor=1.0))
        self.capture = screen_capture.ScreenCapture()
        self.capture.set_capture_rate(.5)
        monkeypatch.setattr(self.capture, "capture_multiple_regions", self.batch)
        self.engine = SimpleNamespace(is_available=True, recognize_preprocessed=self.recognize)
        manager._capture = self.capture
        manager._ocr = self.engine
        manager._state_detector = StateDetector(ocr_engine=self.engine,
            template_matcher=SimpleNamespace(match_any=lambda *_a: None))
        # Preserve the recorded positive's UNKNOWN branch without depending on
        # synthetic colours; seven original-pixel replays cover visual routing.
        manager._state_detector._quick_state_check = lambda _im: StateDetectionResult(
            GameState.UNKNOWN, 0.0, "controlled unknown visual")
        manager._state_machine = GameStateMachine()
        manager._perf_monitor = PerformanceMonitor()
        manager._state = AppState.RUNNING
        monkeypatch.setattr(manager, "_finish_stop", lambda _worker: None)

    def batch(self, regions):
        self.intervals.append(self.capture._min_capture_interval)
        if self.index == len(self.frames):
            self.manager._stop_event.set()
            return dict.fromkeys(range(len(regions)))
        frame = self.frames[self.index]
        self.index += 1
        if frame is None:
            return dict.fromkeys(range(len(regions)))
        if isinstance(frame, Exception):
            raise frame
        self.texts, self.images = {}, {}
        for i in range(len(regions)):
            self.images[i] = np.full((12, 20, 3), 70, np.uint8)
            self.texts[id(self.images[i])] = frame.get(i, "")
        return self.images

    def recognize(self, image, **kwargs):
        self.calls.append((next(i for i, value in self.images.items() if value is image), kwargs))
        return SimpleNamespace(text=self.texts[id(image)])

    def run(self):
        self.manager.on_capture(self.results.append)
        self.manager._capture_loop()
        return self.intervals


POSITIVE = {7: HEADER}


def test_native_current_header_renews_then_negative_retires(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, POSITIVE, {}])
    assert frames.run() == [2.0, 1.0, 1.0, 2.0]
    assert [getattr(r, "screen_evidence", "") for r in frames.results] == [MARKER, MARKER, ""]
    assert all(r.game_state is GameState.UNKNOWN for r in frames.results)
    assert [i for i, _ in frames.calls] == [2, 3, 5, 6, 8, 7, 1] * 3
    assert app._activity_tracker.current_activity is None
    assert app._business_parser.get_all_last_readings() == {}


@pytest.mark.parametrize("idle,business,expected", [(0.5, 4, 1), (.1, .25, 4),
    (2, 4, .5), (.5, float("nan"), 1), (float("nan"), 4, 1), (.5, 0, 1)])
def test_native_hint_respects_configured_rates(app, monkeypatch, idle, business, expected):
    app._settings.set("capture.idle_fps", idle, save=False, validate=False)
    app._settings.set("capture.business_fps", business, save=False, validate=False)
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    assert frames.run()[1] == expected


@pytest.mark.parametrize("mode", ["missing", "error", "ocr_error", "unavailable"])
def test_negative_missing_or_failed_cycle_retires_previous_hint(app, monkeypatch, mode):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, None if mode == "missing" else
        RuntimeError("controlled capture failure") if mode == "error" else POSITIVE])
    if mode == "unavailable":
        app.on_capture(lambda result: setattr(frames.engine, "is_available", False))
    if mode == "ocr_error":
        recognize = frames.engine.recognize_preprocessed
        def failed_header(image, **kwargs):
            if frames.index == 2 and image is frames.images[7]:
                raise RuntimeError("controlled header OCR failure")
            return recognize(image, **kwargs)
        frames.engine.recognize_preprocessed = failed_header
    # Preserve the real error wait boundary without waiting or retrying forever.
    event = app._stop_event
    monkeypatch.setattr(event, "wait", lambda timeout: event.set() if timeout == 1.0 else False)
    frames.run()
    assert frames.capture._min_capture_interval == 2.0
    if mode not in ("error", "ocr_error"):
        assert frames.intervals[-1] == 2.0


@pytest.mark.parametrize("mode", ["none", "mismatch", "missing_crop", "request_only", "error",
    "unavailable", "duplicate", "malformed", "wrong_marker", "result_before_request", "legacy"])
def test_receipt_rejects_recycled_or_malformed_custom_observation(app, monkeypatch, mode):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    _, cached = header_result()
    cached.screen_evidence = MARKER
    if mode == "wrong_marker":
        cached.screen_evidence = {"marker": MARKER}
    if mode == "missing_crop":
        batch = frames.batch
        monkeypatch.setattr(frames.capture, "capture_multiple_regions", lambda regions:
            {**batch(regions), 7: None})
    def recycled(*args, **kwargs):
        observe = kwargs.get("observation")
        if mode == "legacy":
            assert observe is None
        elif mode != "none" and observe is not None:
            if mode == "result_before_request":
                observe("header", "result", {"result": SimpleNamespace(text=HEADER)})
            observe("header", "request", {"kwargs": {"threshold": False, "invert": False, "scale": 2.0}})
            if mode != "request_only":
                observe("header", "result", {"result": SimpleNamespace(text=(42 if mode == "malformed"
                    else HEADER + " " if mode == "mismatch" else HEADER))})
            if mode in ("error", "unavailable", "duplicate"):
                observe("header", "result" if mode == "duplicate" else mode,
                        {"result": SimpleNamespace(text=HEADER)})
        return cached
    recycled.supports_detection_sample_observation = mode != "legacy"
    app._state_detector = SimpleNamespace(detect=recycled)
    result = app._do_capture_cycle()
    assert getattr(result, "screen_evidence", "") == ""
    assert result.game_state == cached.state


def test_current_receipt_cannot_change_custom_rate_policy(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    states = []
    monkeypatch.setattr(app, "_adjust_capture_rate", states.append)
    frames.run()
    assert states == [GameState.UNKNOWN]
    assert frames.intervals == [2.0, 2.0]


def test_setter_only_custom_provider_keeps_ordinary_policy(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    rates = []
    app._capture = SimpleNamespace(regions=frames.capture.regions,
        capture_multiple_regions=frames.batch, set_capture_rate=rates.append)
    frames.run()
    assert rates == [.5]


def test_old_positional_header_helper_stays_compatible_without_receipt(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    calls = []
    def old_header(result, image):
        calls.append(image)
        result.result_header_text = HEADER
        result.screen_evidence = MARKER
        return result
    monkeypatch.setattr(app._state_detector, "_result_header_observation", old_header)
    result = app._do_capture_cycle()
    assert calls == [frames.images[7]]
    assert result.result_header_text == HEADER and result.screen_evidence == ""


def test_override_cycle_cannot_reuse_a_prior_result_to_boost(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    old = app._do_capture_cycle()
    assert getattr(old, "screen_evidence", "") == MARKER
    count = 0
    def old_cycle():
        nonlocal count
        count += 1
        if count == 2:
            app._stop_event.set()
        return old
    monkeypatch.setattr(app, "_do_capture_cycle", old_cycle)
    app._capture_loop()
    assert frames.capture._min_capture_interval == 2.0


@pytest.mark.parametrize("state,fps", [(GameState.MISSION_ACTIVE, 3), (GameState.SELLING, 3),
    (GameState.HEIST_PREP, 3), (GameState.HEIST_FINALE, 3), (GameState.BUSINESS_COMPUTER, 4)])
def test_admitted_unknown_preserves_higher_accepted_state_rate(app, monkeypatch, state, fps):
    app._settings.set("capture.active_fps", 3, save=False)
    frames = CycleFrames(app, monkeypatch, [POSITIVE, {}])
    app._state_machine._context.state = state
    assert frames.run() == [2.0, 1 / fps, 2.0]


def test_callback_cannot_add_stale_scheduling_evidence(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [{}, {}])
    app.on_capture(lambda result: setattr(result, "screen_evidence", MARKER))
    assert frames.run() == [2.0, 2.0, 2.0]


@pytest.mark.parametrize("inside", ["callback", "ocr"])
def test_pause_resume_in_same_cycle_retires_authority(app, monkeypatch, inside):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, POSITIVE])
    def interrupt(*_args, **_kwargs):
        app.pause()
        app.resume()
    if inside == "callback":
        app.on_capture(interrupt)
    else:
        recognize = frames.engine.recognize_preprocessed
        def interrupted(image, **kwargs):
            interrupt()
            return recognize(image, **kwargs)
        frames.engine.recognize_preprocessed = interrupted
    assert frames.run() == [2.0, 2.0, 2.0]


def test_pause_drops_applied_hint_before_wait_and_resume_needs_fresh_frame(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, POSITIVE, {}])
    app.on_capture(lambda _result: app.pause() if frames.index == 2 else None)
    waits = []
    def wait(timeout):
        waits.append((timeout, frames.capture._min_capture_interval))
        app.resume()
        return False
    monkeypatch.setattr(app._stop_event, "wait", wait)
    assert frames.run() == [2.0, 1.0, 2.0, 2.0]
    assert waits == [(.1, 2.0)]


def test_pause_resume_after_scheduling_retires_before_next_acquisition(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, {}])
    finish = app._finish_detection_sample
    def interrupted(collection, failed):
        finish(collection, failed)
        if frames.index == 1:
            assert frames.capture._min_capture_interval == 1.0
            app.pause()
            app.resume()
    monkeypatch.setattr(app, "_finish_detection_sample", interrupted)
    assert frames.run() == [2.0, 2.0, 2.0]


def test_stop_retires_hint_and_new_loop_cannot_inherit_it(app, monkeypatch):
    first = CycleFrames(app, monkeypatch, [POSITIVE])
    first.run()
    assert first.capture._min_capture_interval == 2.0
    app._stop_event.clear()
    app._on_capture.clear()
    second = CycleFrames(app, monkeypatch, [{}])
    assert second.run() == [2.0, 2.0]


@pytest.mark.parametrize("selected,owner", [(False, False), (True, False), (False, True), (True, True)])
def test_identical_frames_preserve_accounting_business_and_owner(tmp_path, monkeypatch, selected, owner):
    paired = []
    intervals = []
    for suppressed in (False, True):
        directory = tmp_path / str(suppressed)
        directory.mkdir()
        manager = GTABusinessManager(Settings(directory / "settings.yaml"))
        repository = Repository(str(directory / "parity.db"))
        assert repository.initialize()
        manager._repository = repository
        character = repository.get_or_create_character("Parity")
        manager._data.character_id = character.id
        manager._data.db_session_id = repository.start_session(character).id
        manager._session_tracker.start_session()
        manager._process_money_change(MoneyReading(total=100000))
        frames = CycleFrames(manager, monkeypatch, [{1: "$100000", 7: HEADER},
            {1: "$125000", 7: HEADER}, {1: "$120000"}, {1: "$130000", 7: HEADER}])
        if selected:
            manager.set_business_screen_target("nightclub")
        if owner:
            manager._process_state(StateDetectionResult(GameState.MISSION_ACTIVE, 1, "seed",
                mission_text="Headhunter"), CaptureResult())
            manager._activity_tracker.current_activity.started_at = datetime(2026, 1, 1, 12)
            manager._data.mission_start_time = datetime(2026, 1, 1, 12)
        else:
            manager._data.terminal_mission_episode = TerminalMissionEpisode(MissionIdentity(name="Sightseer"))
        current_owner = manager._activity_tracker.current_activity
        owner_fields = copy.deepcopy(vars(current_owner)) if current_owner else None
        transitions = []
        manager._state_machine.add_listener(lambda change: transitions.append((change.from_state, change.to_state)))
        if suppressed:
            original = manager._state_detector.detect
            def suppress(*args, **kwargs):
                detected = original(*args, **kwargs)
                detected.screen_evidence = ""
                return detected
            suppress.supports_detection_sample_observation = True
            manager._state_detector.detect = suppress
        snapshots = []
        def snapshot(_result):
            data = repository.export_session_data(manager._data.db_session_id)
            for row in data["earnings"]:
                row["timestamp"] = "<independent database write time>"
            snapshots.append(copy.deepcopy((manager.current_money, manager.session_earnings,
                manager.session_stats.total_earnings, manager.session_stats.activities_completed,
                data["activities"], data["earnings"], manager._data.terminal_mission_episode,
                manager.cooldown_tracker.get_active_cooldowns(), manager._data.business_states,
                manager._business_parser.get_all_last_readings(), manager._optimizer._business_states,
                vars(current_owner) if current_owner else None, transitions)))
        manager.on_capture(snapshot)
        try:
            intervals.append(frames.run())
            assert manager._activity_tracker.current_activity is current_owner
            assert (vars(current_owner) if current_owner else None) == owner_fields
            assert manager.session_earnings == 35000
            assert all(index not in (4, 9, 10, 11) for index, _kwargs in frames.calls)
            paired.append(snapshots)
        finally:
            repository.close()
    assert paired[0] == paired[1]
    assert intervals[0][1] == 1.0 and intervals[1][1] == 2.0


def test_unarmed_receipt_does_not_create_or_finish_sample_work(app, monkeypatch):
    CycleFrames(app, monkeypatch, [POSITIVE])
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unarmed cadence must not activate sample work")
    monkeypatch.setattr(app_module, "DetectionSampleCollector", forbidden)
    monkeypatch.setattr(app_module, "_DetectionSampleCollection", forbidden)
    monkeypatch.setattr(DetectionSampleCollector, "capture", forbidden)
    monkeypatch.setattr(DetectionSampleCollector, "finish", forbidden)
    result = app._do_capture_cycle()
    assert getattr(result, "screen_evidence", "") == MARKER
    assert app.get_detection_sample_status().state == "idle"


def test_sample_contains_only_marker_and_same_existing_events(app, monkeypatch):
    frames = CycleFrames(app, monkeypatch, [POSITIVE])
    collector = DetectionSampleCollector("a" * 32, "b" * 32,
        "2026-10-08T16:00:00+00:00", "2026-10-08T16:00:01+00:00")
    events = []
    def observe(source, event, payload):
        events.append((source, event))
        collector.observe_ocr(source, event, payload)
    sample = SimpleNamespace(call=lambda method, *args: getattr(collector, method)(*args),
                             observe_ocr=observe)
    result = app._do_capture_cycle(sample)
    sample_data = json.loads(collector.finish().json_bytes)
    assert getattr(result, "screen_evidence", "") == MARKER
    for stage in ("detector_candidate", "first_guarded", "processed"):
        assert sample_data[stage]["screen_evidence"] == MARKER
    assert events.count(("header", "request")) == events.count(("header", "result")) == 1
    serialized = json.dumps(sample_data)
    assert sample_data["backend"]["class"] == "types.SimpleNamespace"
    assert "receipt" not in serialized and "namespace(" not in serialized and " at 0x" not in serialized


def test_armed_sample_still_captures_and_finishes_once(app, monkeypatch, tmp_path):
    frames = CycleFrames(app, monkeypatch, [POSITIVE, POSITIVE])
    counts = {"capture": 0, "finish": 0}
    for name in counts:
        original = getattr(DetectionSampleCollector, name)
        def counted(self, *args, _name=name, _original=original):
            counts[_name] += 1
            return _original(self, *args)
        monkeypatch.setattr(DetectionSampleCollector, name, counted)
    before = set(tmp_path.rglob("*"))
    request = app.request_detection_sample()
    assert request.state == "armed"
    frames.run()
    app._stop_event.clear()
    app.pause()
    assert app.get_detection_sample_status().state == "ready"
    sample = app.claim_detection_sample_for_save(request.token)
    assert sample is not None
    assert json.loads(sample.json_bytes)["processed"]["screen_evidence"] == MARKER
    assert counts == {"capture": 1, "finish": 1}
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.skipif(not os.environ.get("GTA_SCREEN_REPLAY_MANIFEST"), reason="Original pixels remain local")
def test_seven_original_frames_with_recorded_ocr(tmp_path, monkeypatch):
    from PIL import Image
    from src.detection import screenshot_diagnostic as diagnostic
    entries = json.loads(Path(os.environ["GTA_SCREEN_REPLAY_MANIFEST"]).read_text())
    reports, total_calls = [], 0
    for entry in entries:
        path = Path(entry["image"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        saved = json.loads(Path(entry["recorded"]).read_text())
        pending = [source for source in ("mission_text", "center_prompt", "mission_banner",
                   "bottom_objective", "vip_status", "result_header")
                   if saved["sources"][source]["status"] in ("recognized", "recognized_empty")]
        calls = []
        def recognize(image, **kwargs):
            source = pending[len(calls)]
            record = saved["sources"][source]
            assert kwargs == record["preprocessing"]
            calls.append(source)
            return OCRResult(text=record["text"], confidence=record["ocr_confidence"], words=[])
        monkeypatch.setattr(diagnostic, "_TesseractDiagnostic", lambda: SimpleNamespace(
            is_available=True, recognize_preprocessed=recognize))
        report = diagnostic.diagnose_image(path, backend="tesseract")
        candidate = report["detector_candidate"].copy()
        marker = candidate.pop("screen_evidence", "")
        assert candidate == saved["detector_candidate"]
        assert report["sources"] == saved["sources"]
        assert marker == (MARKER if entry["id"] == "business_computer" else "")
        assert len(calls) == len(pending)
        total_calls += len(calls)
        reports.append({"id": entry["id"], "sha256": entry["sha256"], "calls": calls,
                        "candidate": report["detector_candidate"]})
    assert len(reports) == 7 and total_calls <= 42
    Path(os.environ["GTA_SCREEN_REPLAY_REPORT"]).write_text(json.dumps({
        "calls": total_calls, "frames": reports}, indent=2) + "\n")
