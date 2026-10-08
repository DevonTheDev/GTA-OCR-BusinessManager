"""Independent live-cycle sample parity using synthetic capture/OCR boundaries.

Production detection, guards, tracking, accounting, callbacks and SQLite remain
in use. These checks make no native Windows or gameplay accuracy claim.
"""

from src.app import GTABusinessManager


def test_detection_sample_public_workflow_api_exists():
    for method in (
        "request_detection_sample", "get_detection_sample_status",
        "cancel_detection_sample", "claim_detection_sample_for_save",
        "complete_detection_sample_save",
    ):
        assert callable(getattr(GTABusinessManager, method, None)), method

import dataclasses
from collections import deque
from datetime import datetime, timedelta
from enum import Enum
import hashlib
from io import BytesIO
import json
import sys
import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from src.app import AppState, CaptureResult
from src.capture.regions import ScreenRegions
from src.config.settings import Settings
from src.database.repository import Repository
from src.detection.ocr_engine import OCREngine, OCRResult
from src.detection.state_detector import StateDetectionResult, StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState
from tests.test_bottom_objective_capture import CaptureHarness
from tests.test_mission_result_accounting import clock as clock  # noqa: PLC0414


REGIONS = ScreenRegions()
SOURCE_REGIONS = {
    "mission": REGIONS.mission_text,
    "center": REGIONS.center_prompt,
    "banner": REGIONS.mission_banner,
    "bottom": REGIONS.bottom_objective,
    "header": REGIONS.result_header,
    "vip_status": REGIONS.vip_status,
}
# Only wall-clock/timing observations may differ between the paired executions.
TIMING_FIELDS = {
    "timestamp", "started_at", "ended_at", "start_time", "end_time", "created_at",
    "updated_at", "updated", "entered_at", "last_state_time", "in_mission_since",
    "last_money_change_time", "mission_start_time", "capture_time_ms", "ocr_time_ms",
    "total_time_ms", "time_in_missions", "time_idle",
}


def semantic(value, key=""):
    """Project tracking state and call values; preserve all non-timing fields."""
    if value is None:
        return None
    if key in TIMING_FIELDS or isinstance(value, datetime):
        return "<observed time>"
    if isinstance(value, Enum):
        return value.name
    if dataclasses.is_dataclass(value):
        return {field.name: semantic(getattr(value, field.name), field.name)
                for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): semantic(v, str(k)) for k, v in value.items()}
    if isinstance(value, (tuple, list, deque)):
        return [semantic(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(semantic(item) for item in value)
    if isinstance(value, np.ndarray):
        return (value.shape, str(value.dtype), hashlib.sha256(value.tobytes()).hexdigest())
    if isinstance(value, SimpleNamespace):
        return semantic(vars(value))
    return value


class CycleBoundary:
    """Observe actual paused/error loop boundaries without sleep synchronization."""

    def __init__(self, manager):
        self.manager = manager
        self.event = threading.Event()
        self.drained = threading.Event()
        self.error = False

    def is_set(self):
        return self.event.is_set()

    def set(self):
        self.event.set()

    def clear(self):
        self.event.clear()

    def wait(self, timeout=None):
        if timeout == 1.0:
            self.manager.pause()
            self.error = True
            self.drained.set()
        elif timeout == .1 and self.manager.state is AppState.PAUSED:
            self.drained.set()
        return self.event.wait(timeout)


class RecordedHud(CaptureHarness):
    def __init__(self, manager, monkeypatch):
        self.backend_mode = "normal"
        self.mutate_during_ocr = False
        self.did_mutate = False
        self.backend_results = []
        self.detector_calls = []
        self.rate_calls = []
        super().__init__(manager, monkeypatch)
        self.capture._regions = dataclasses.replace(self.capture.regions)
        original = manager._state_detector.detect
        supports = getattr(StateDetector.detect, "supports_detection_sample_observation", False)

        def record(*args, **kwargs):
            # The observer itself is the sole permitted extra detector argument.
            self.detector_calls.append((semantic(args), semantic({
                name: value for name, value in kwargs.items() if name != "observation"
            })))
            return original(*args, **kwargs)

        record.supports_detection_sample_observation = supports
        monkeypatch.setattr(manager._state_detector, "detect", record)
        original_rate = self.capture.set_capture_rate

        def rate(fps):
            self.rate_calls.append(fps)
            return original_rate(fps)

        monkeypatch.setattr(self.capture, "set_capture_rate", rate)
        monkeypatch.setattr(self.capture, "save_capture", lambda *_a, **_kw: pytest.fail(
            "Sampling must not request another capture"))

    def recognize(self, image, **kwargs):
        region = self.images[id(image)]
        self.ocr_calls.append((region, kwargs.copy(), semantic(image)))
        if self.mutate_during_ocr and not self.did_mutate:
            self.crops[-1][0][:] = 211
            self.did_mutate = True
        if self.backend_mode == "raise" and region == REGIONS.mission_text:
            self.backend_results.append((region, "raised", "LookupError"))
            raise LookupError("synthetic original OCR exception")
        if self.backend_mode == "sentinel" and region == REGIONS.mission_text:
            result = OCRResult(text="", confidence=0.0, words=[])
        elif self.backend_mode == "empty" and region == REGIONS.mission_text:
            result = OCRResult(text="", confidence=None, words=[])
        elif self.backend_mode == "legacy_result":
            result = SimpleNamespace(text=self.words[id(image)])
        else:
            result = OCRResult(text=self.words[id(image)], confidence=None, words=[])
        self.backend_results.append((region, semantic(result)))
        return result


class WorkflowRun:
    def __init__(self, directory, monkeypatch, clock):
        self.clock = clock
        self.clock.value = datetime(2026, 1, 1, 12)
        self.manager = GTABusinessManager(Settings(directory / "settings.yaml"))
        self.repository = Repository(str(directory / "workflows.db"))
        assert self.repository.initialize()
        self.character = self.repository.get_or_create_character("Synthetic Workflow Player")
        self.manager._stop_event = CycleBoundary(self.manager)
        self.events = []
        self.results = []
        self.owner_ids = {}
        self.after_money = None
        self.legacy_calls = []
        self.native_calls = []
        self.snapshots = []

        def initialize():
            self.hud = RecordedHud(self.manager, monkeypatch)
            self.manager._repository = self.repository
            self.manager._data.character_id = self.character.id
            self.manager._data.db_session_id = self.repository.start_session(self.character).id
            self.manager._session_tracker.start_session()

        monkeypatch.setattr(self.manager, "_initialize_components", initialize)
        self.manager.on_state_change(lambda old, new: self.events.append(("state", old.name, new.name)))
        self.manager.on_money_change(self.money_callback)
        self.manager.on_mission_complete(lambda activity: self.events.append(
            ("complete", self.owner_id(activity), semantic(activity))))
        self.manager.on_capture(self.capture_callback)
        assert self.manager.start()
        assert self.manager._stop_event.drained.wait(3), "Initial cycle failed to pause"
        assert not self.manager._stop_event.error
        self.snapshots.append(self.snapshot())

    def owner_id(self, activity):
        if activity is None:
            return None
        return self.owner_ids.setdefault(id(activity), len(self.owner_ids) + 1)

    def capture_callback(self, result):
        self.manager.pause()
        self.results.append(result)
        self.events.append(("capture", semantic(result),
                            self.owner_id(self.manager._activity_tracker.current_activity)))

    def money_callback(self, reading, change):
        self.events.append(("money", semantic(reading), change))
        if self.after_money:
            self.after_money()

    def frame(self, *, armed=False, top="", center="", banner="", bottom="", header="",
              vip_status="", money="$1,000", timer="", business=None, mutate=False,
              backend_mode="normal", unavailable=False):
        manager, hud = self.manager, self.hud
        with manager._lifecycle_lock:
            assert manager.state is AppState.PAUSED and not manager._capture_busy
            self.clock.value += timedelta(seconds=30)
            hud.texts = {
                REGIONS.mission_text: top, REGIONS.center_prompt: center,
                REGIONS.mission_banner: banner, REGIONS.bottom_objective: bottom,
                REGIONS.result_header: header, REGIONS.vip_status: vip_status,
                REGIONS.money_display: money, REGIONS.timer_bottom_right: timer,
            }
            if business:
                hud.texts.update(zip(REGIONS.get_business_regions().values(), business))
            hud.backend_mode = backend_mode
            hud.mutate_during_ocr, hud.did_mutate = mutate, False
            if type(manager._ocr) is OCREngine:
                manager._ocr._winocr_available = not unavailable
            else:
                manager._ocr.is_available = not unavailable
            self.last_ocr_start = len(hud.ocr_calls)
            self.last_backend_start = len(hud.backend_results)
            hud.frame[:, :, :3] = (61, 73, 89)
            expected_pixels = hud.frame[:, :, :3].copy()
            request = manager.request_detection_sample() if armed else None
            if request:
                assert request.state == "armed", request
            manager._stop_event.drained.clear()
            manager._stop_event.error = False
            manager.resume()
        assert manager._stop_event.drained.wait(5), "Capture did not drain"
        assert not manager._capture_busy
        self.snapshots.append(self.snapshot())
        if request:
            return request, expected_pixels
        return None

    def snapshot(self):
        manager, hud = self.manager, self.hud
        persisted = manager._repository.export_session_data(manager._data.db_session_id)
        persisted["session"].pop("duration_seconds", None)
        return {
            "data": semantic(manager._data),
            "session": semantic(manager.session_stats),
            "persisted": semantic(persisted),
            "state": semantic(manager._state_machine.context),
            "detector_context": semantic(manager._state_detector._context)
                if hasattr(manager._state_detector, "_context") else None,
            "money_parser": semantic(vars(manager._money_parser)),
            "current": semantic(manager._activity_tracker.current_activity),
            "current_identity": self.owner_id(manager._activity_tracker.current_activity),
            "completed": [(self.owner_id(item), semantic(item))
                          for item in manager._activity_tracker.completed_activities],
            "cooldowns": semantic(manager.cooldown_tracker.get_active_cooldowns()),
            "events": list(self.events),
            "results": semantic(self.results),
            "capture_batches": semantic(hud.batches),
            "grabs": semantic(hud.grabs),
            "region_calls": semantic(hud.region_calls),
            "waits": list(hud.waits),
            "ocr_calls": semantic(hud.ocr_calls),
            "ocr_results": semantic(hud.backend_results),
            "detector_calls": semantic(hud.detector_calls),
            "rate_calls": list(hud.rate_calls),
            "legacy_calls": semantic(self.legacy_calls),
            "native_calls": semantic(self.native_calls),
            "cycle_error": manager._stop_event.error,
        }

    def sample(self, request, expected_pixels):
        status = self.manager.get_detection_sample_status()
        assert status.state == "ready", status
        assert status.token is request.token
        assert status.sample_id == request.sample_id
        sample = self.manager.claim_detection_sample_for_save(request.token)
        assert sample is not None
        assert isinstance(sample.png_bytes, bytes) and isinstance(sample.json_bytes, bytes)
        assert self.manager.get_detection_sample_status().state == "saving"
        document = json.loads(sample.json_bytes, parse_constant=lambda _value: pytest.fail(
            "Sample JSON must contain finite values"))
        assert document["frame"]["sha256"] == hashlib.sha256(sample.png_bytes).hexdigest()
        assert document["frame"]["bytes"] == len(sample.png_bytes)
        assert (document["frame"]["height"], document["frame"]["width"]) == expected_pixels.shape[:2]
        decoded = np.asarray(Image.open(BytesIO(sample.png_bytes)).convert("RGB"))
        np.testing.assert_array_equal(decoded[:, :, ::-1], expected_pixels)
        # The frozen result must outlive original pixels and mutable app state.
        frozen_json, frozen_png = sample.json_bytes, sample.png_bytes
        for batch in self.hud.crops:
            for image in batch.values() if isinstance(batch, dict) else batch:
                if image is not None:
                    image[:] = 3
        self.hud.frame[:] = 4
        if self.manager._activity_tracker.current_activity:
            self.manager._activity_tracker.current_activity.name = "changed after observation"
        self.manager._data.current_mission = "changed after observation"
        assert sample.json_bytes == frozen_json and sample.png_bytes == frozen_png
        np.testing.assert_array_equal(np.asarray(Image.open(BytesIO(sample.png_bytes)))[:, :, ::-1],
                                      expected_pixels)
        return sample, document

    def close(self):
        self.manager.stop()
        worker = self.manager._capture_thread
        if worker:
            worker.join(3)
            assert not worker.is_alive()
        self.repository.close()


@pytest.fixture
def runs(tmp_path, monkeypatch, clock):
    created = []
    from src.detection import screenshot_diagnostic
    monkeypatch.setattr(screenshot_diagnostic, "diagnose_image", lambda *_a, **_kw: pytest.fail(
        "Live-cycle samples must not rerun screenshot diagnostics"))

    def create():
        directory = tmp_path / str(len(created))
        directory.mkdir()
        run = WorkflowRun(directory, monkeypatch, clock)
        created.append(run)
        return run

    yield create
    for run in reversed(created):
        run.close()


def selected(run):
    manager = run.manager
    activity = manager._activity_tracker.current_activity
    return (activity, activity.started_at if activity else None,
            manager._data.mission_start_time, manager._data.mission_start_money)


def execute_case(run, case, armed):
    manager = run.manager
    if case in {"retained_candidate", "terminal_conflict", "terminal_repeat", "callback_guard"}:
        run.frame(banner="Headhunter", center="Eliminate the targets")
    elif case == "category_refinement":
        run.frame(vip_status="VIP WORK END 12:34")
    elif case == "header_completion":
        run.frame(bottom="Escape Cayo Perico")
    before = selected(run)
    if case == "terminal_repeat":
        run.frame(banner="MISSION PASSED\nHeadhunter", money="$1,250")
        terminal = manager._data.terminal_mission_episode
        request = run.frame(armed=armed, banner="Headhunter", money="$1,250", mutate=True)
        assert manager._data.terminal_mission_episode is terminal
        assert manager._activity_tracker.current_activity is None
        assert run.results[-1].game_state is GameState.UNKNOWN
    elif case == "retained_candidate":
        request = run.frame(armed=armed, banner="Sightseer", money="$1,250", mutate=True)
        assert selected(run) == before
        assert run.results[-1].mission.mission_name == "Sightseer"
        assert run.results[-1].activity_name == "Headhunter"
    elif case == "terminal_conflict":
        request = run.frame(armed=armed, banner="MISSION PASSED\nSightseer", money="$1,250", mutate=True)
        assert selected(run) == before
        assert run.results[-1].game_state is GameState.UNKNOWN
        assert run.results[-1].activity_name == "Headhunter"
    elif case == "category_refinement":
        request = run.frame(armed=armed, banner="Headhunter", center="Go to the location",
                            vip_status="VIP WORK END 0:00", money="$1,250", mutate=True)
        assert selected(run) == before
        assert run.results[-1].activity_name == "Headhunter"
        assert run.results[-1].activity_identity_status == "known_name"
    elif case == "callback_guard":
        def replace_owner():
            manager._activity_tracker.cancel_activity()
            manager._reset_mission_state()
            # A real reentrant callback changes ownership through normal policy.
            manager._process_state(StateDetectionResult(
                GameState.MISSION_ACTIVE, 1.0, "Callback selected Sightseer",
                banner_text="Sightseer"), CaptureResult())
            run.events.append(("callback_owner", manager._activity_tracker.current_activity.name))
        run.after_money = replace_owner
        request = run.frame(armed=armed, banner="MISSION PASSED\nHeadhunter", money="$1,250", mutate=True)
        assert run.results[-1].game_state is GameState.UNKNOWN
        assert run.results[-1].activity_name == "Sightseer"
        assert manager._activity_tracker.completed_activities == []
    elif case == "header_completion":
        request = run.frame(armed=armed, header="HEIST PASSED\nThe Cayo Perico Heist",
                            money="$1,250", mutate=True)
        assert run.results[-1].game_state is GameState.MISSION_COMPLETE
        assert manager._activity_tracker.completed_activities == [before[0]]
    elif case == "business_later_grabs":
        old_grabs = len(run.hud.grabs)
        request = run.frame(armed=armed, top="Bunker Stock Supplies", bottom="Escape Cayo Perico",
                            vip_status="VIP WORK END 12:34", header="HEIST PASSED\nCayo Perico",
                            business=("Bunker Stock: 40%", "Supplies: 60%", "Value: $7000"),
                            mutate=True)
        assert len(run.hud.grabs) - old_grabs == 4
        assert manager.get_business_state("bunker")["value"] == 7000
        assert run.hud.region_calls == [(r, False) for r in REGIONS.get_business_regions().values()]
        assert run.results[-1].game_state is GameState.BUSINESS_COMPUTER
    else:
        raise AssertionError(case)
    return request


@pytest.mark.parametrize("case", [
    "retained_candidate", "terminal_conflict", "terminal_repeat", "category_refinement",
    "callback_guard", "header_completion", "business_later_grabs",
])
def test_armed_sample_preserves_real_processing_and_exact_earlier_pixels(runs, case):
    baseline = runs()
    execute_case(baseline, case, False)
    observed = runs()
    request, pixels = execute_case(observed, case, True)
    assert observed.snapshots == baseline.snapshots
    sample, document = observed.sample(request, pixels)
    assert document["mode"] == "live_cycle_observation"
    assert document["sample_id"] == sample.sample_id == request.sample_id
    assert document["run_id"] == sample.run_id == request.run_id
    assert_sample_stages(document, observed, case)
    assert_source_truth(document, observed)


def configure_source_boundary(run, mode):
    manager, hud = run.manager, run.hud
    if mode == "missing_region":
        hud.capture.regions.mission_text = None
    elif mode in {"capture_unavailable", "full_screen_unavailable"}:
        original = hud.capture.capture_multiple_regions

        def no_mission(regions):
            images = original(regions)
            images[0 if mode == "full_screen_unavailable" else 2] = None
            return images

        hud.capture.capture_multiple_regions = no_mission
    elif mode == "optional_missing":
        # A genuinely old provider never had the optional VIP field.
        manager._capture = SimpleNamespace(
            regions=SimpleNamespace(**{name: value for name, value in vars(hud.capture.regions).items()
                                       if name != "vip_status"}),
            capture_multiple_regions=hud.capture.capture_multiple_regions,
            set_capture_rate=hud.capture.set_capture_rate,
            close=hud.capture.close,
        )


@pytest.mark.parametrize("mode", [
    "missing_region", "capture_unavailable", "full_screen_unavailable", "optional_missing", "unavailable",
    "skipped", "empty", "sentinel", "legacy_result", "raise",
])
def test_source_presence_status_and_backend_failures_are_observational(runs, mode):
    pairs = []
    for armed in (False, True):
        run = runs()
        configure_source_boundary(run, mode)
        before = len(run.hud.grabs)
        kwargs = {"armed": armed, "mutate": True}
        if mode in {"empty", "sentinel", "legacy_result", "raise"}:
            kwargs["backend_mode"] = mode
        if mode == "unavailable":
            kwargs["unavailable"] = True
        if mode == "skipped":
            kwargs.update(top="Headhunter", banner="Sightseer", bottom="Escape Cayo Perico",
                          header="HEIST PASSED\nCayo Perico", vip_status="VIP WORK END 12:34")
        request = run.frame(**kwargs)
        assert len(run.hud.grabs) - before == 1
        pairs.append((run, request))
    baseline, observed = pairs[0][0], pairs[1][0]
    assert observed.snapshots == baseline.snapshots
    request, pixels = pairs[1][1]
    if mode in {"raise", "full_screen_unavailable"}:
        assert baseline.manager._stop_event.error is (mode == "raise")
        assert observed.manager._stop_event.error is (mode == "raise")
        status = observed.manager.get_detection_sample_status()
        assert status.state == "failed"
        assert "synthetic original OCR exception" not in status.message
        assert observed.manager.claim_detection_sample_for_save(request.token) is None
    else:
        _, document = observed.sample(request, pixels)
        assert document["mode"] == "live_cycle_observation"
        assert_source_truth(document, observed)
        sources = document["sources"]
        if mode == "missing_region":
            assert sources["mission"]["region_present"] is False
            assert sources["mission"]["image_present"] is False
            assert sources["mission"]["ocr_status"] == "missing_region"
        elif mode == "capture_unavailable":
            assert sources["mission"]["region_present"] is True
            assert sources["mission"]["image_present"] is False
            assert sources["mission"]["ocr_status"] == "capture_unavailable"
        elif mode == "optional_missing":
            assert sources["vip_status"]["region_present"] is False
            assert sources["vip_status"]["image_present"] is False
            assert sources["vip_status"]["ocr_status"] == "missing_region"
        elif mode == "unavailable":
            assert {source["ocr_status"] for source in sources.values()} == {"backend_unavailable"}
        elif mode == "skipped":
            assert all(sources[name]["ocr_status"] == "not_requested_by_detector"
                       for name in ("bottom", "header", "vip_status"))
        elif mode in {"empty", "sentinel", "legacy_result"}:
            assert sources["mission"]["ocr_status"] == "recognized_empty"
            assert sources["mission"]["confidence"] == (0.0 if mode == "sentinel" else None)
            assert document["backend"]["name"] == "unknown"


class LegacyDetector:
    """Pre-observer custom implementation with the original explicit signature."""

    def __init__(self, real_detector, recorded):
        self.real_detector = real_detector
        self.recorded = recorded

    def detect(self, image, mission_text_image=None, center_text_image=None,
               mission_banner_image=None, bottom_objective_image=None,
               result_header_image=None, vip_status_image=None):
        kwargs = dict(mission_text_image=mission_text_image, center_text_image=center_text_image,
                      mission_banner_image=mission_banner_image,
                      bottom_objective_image=bottom_objective_image,
                      result_header_image=result_header_image, vip_status_image=vip_status_image)
        self.recorded.append((semantic(image), semantic(kwargs)))
        return self.real_detector.detect(image, **kwargs)


@pytest.mark.parametrize("container_type", [dict, list], ids=["plain-dict", "plain-list"])
def test_legacy_provider_and_detector_are_called_once_without_new_arguments(runs, container_type):
    pairs = []
    for armed in (False, True):
        run = runs()
        hud = run.hud

        def legacy_batch(regions, capture=hud.capture):
            images = capture.capture_multiple_regions(regions)
            return dict(images) if container_type is dict else [images[i] for i in range(len(regions))]

        run.manager._capture = SimpleNamespace(
            regions=hud.capture.regions, capture_multiple_regions=legacy_batch,
            set_capture_rate=hud.capture.set_capture_rate, close=hud.capture.close,
        )
        run.manager._state_detector = LegacyDetector(run.manager._state_detector, run.legacy_calls)
        before = len(hud.detector_calls), len(hud.grabs)
        request = run.frame(armed=armed, banner="Headhunter", mutate=True)
        assert (len(hud.detector_calls), len(hud.grabs)) == (before[0] + 1, before[1] + 1)
        assert len(run.legacy_calls) == 1
        pairs.append((run, request))
    baseline, observed = pairs[0][0], pairs[1][0]
    assert observed.snapshots == baseline.snapshots
    request, pixels = pairs[1][1]
    _, document = observed.sample(request, pixels)
    assert document["mode"] == "live_cycle_observation"
    assert document["geometry"]["status"] == "unknown"
    assert document["capture_provider"]["kind"] == "custom_or_unknown"
    assert document["mission_observation_status"] == "unsupported_detector"
    assert {source["ocr_status"] for source in document["sources"].values()} == {"observation_unavailable"}
    assert all(source["raw_text"] is None for source in document["sources"].values())


def assert_source_truth(document, run):
    """Compare exported observations to the actual backend boundary trace."""
    assert set(document["sources"]) == set(SOURCE_REGIONS)
    calls = {region: kwargs for region, kwargs, _pixels in run.hud.ocr_calls[run.last_ocr_start:]}
    results = {region: result for region, result in run.hud.backend_results[run.last_backend_start:]}
    for source, region in SOURCE_REGIONS.items():
        record = document["sources"][source]
        if region not in calls:
            assert record["raw_text"] is None
            assert record["preprocessing"] is None
            assert record["confidence"] is None
            continue
        actual = results[region]
        assert record["raw_text"] == actual["text"]
        assert record["ocr_status"] == ("recognized" if actual["text"].strip() else "recognized_empty")
        assert record["confidence"] == actual.get("confidence")
        assert record["confidence_provenance"] == (
            "unknown" if actual.get("confidence") is not None else "not_reported")
        flags = record["preprocessing"]
        assert flags["explicit_arguments"] == sorted(calls[region])
        for flag in ("threshold", "invert", "scale"):
            assert flags[flag] == calls[region].get(flag)
        assert record["region_present"] is True and record["image_present"] is True
    assert document["mission_observation_status"] == "observed"
    assert document["capture_provider"]["kind"] == "custom_or_unknown"
    assert document["backend"]["internal_resize"] == "unknown"
    assert document["backend"]["internal_dimensions"] is None
    if document["geometry"]["status"] == "known":
        geometry = document["geometry"]
        assert geometry["resolution"] == [400, 240]
        assert geometry["offset"] == [-200, 30]
        assert geometry["native_grab_rectangle"] == {
            "left": -200, "top": 30, "width": 400, "height": 240,
        }
        assert datetime.fromisoformat(geometry["grab_started_at"]) <= datetime.fromisoformat(geometry["grab_ended_at"])
        assert geometry["grab_duration_ns"] >= 0


def assert_sample_stages(document, run, case):
    candidate = document["detector_candidate"]
    guarded = document["first_guarded"]
    processed = document["processed"]
    real_candidate = run.hud.detections[-1]
    real_processed = run.results[-1]
    assert candidate["state"] == real_candidate.state.name
    assert candidate["heuristic_score"] == real_candidate.confidence
    assert candidate["reason"] == real_candidate.reason
    assert processed["state"] == real_processed.game_state.name
    assert processed["heuristic_score"] == real_processed.state_confidence
    assert processed["reason"] == real_processed.state_reason
    for field in ("mission_text", "objective_text", "banner_text", "bottom_objective_text",
                  "bottom_objective_command", "result_header_text", "result_header_evidence",
                  "vip_status_text", "vip_status_evidence"):
        assert candidate[field] == getattr(real_candidate, field)
        assert processed[field] == getattr(real_processed, field)
    if real_candidate.mission:
        assert candidate["identity"]["mission_name"] == real_candidate.mission.mission_name
        assert candidate["identity"]["status"] == real_candidate.mission.identity_status
        assert candidate["identity"]["mission_type"] == real_candidate.mission.mission_type.name
    changed = case in {"terminal_conflict", "terminal_repeat"}
    assert document["first_guard_changed"] is changed
    assert (candidate != guarded) is changed
    if changed:
        assert guarded["state"] == "UNKNOWN" and guarded["heuristic_score"] == 0
    else:
        assert guarded == candidate
    selection = document["selection"]
    if case in {"retained_candidate", "terminal_conflict"}:
        assert selection["ownership"] == "retained"
        assert selection["before"]["activity"] == selection["after"]["activity"] == {
            "name": "Headhunter", "type": "VIP_WORK", "identity_status": "known_name",
        }
        assert candidate["identity"]["mission_name"] == "Sightseer"
    elif case == "category_refinement":
        assert selection["ownership"] == "refined"
        assert selection["before"]["activity"] == {
            "name": "VIP Work", "type": "VIP_WORK", "identity_status": "type_only",
        }
        assert selection["after"]["activity"] == {
            "name": "Headhunter", "type": "VIP_WORK", "identity_status": "known_name",
        }
    elif case == "callback_guard":
        assert candidate["state"] == guarded["state"] == "MISSION_COMPLETE"
        assert processed["state"] == "UNKNOWN" and processed["heuristic_score"] == 0
        assert processed["reason"] != guarded["reason"]
        assert selection["ownership"] == "new"
        assert selection["before"]["activity"]["name"] == "Headhunter"
        assert selection["after"]["activity"]["name"] == "Sightseer"
    elif case == "header_completion":
        assert selection["ownership"] == "cleared"
        assert selection["before"]["activity"]["type"] == "CAYO_PERICO"
        assert selection["after"]["activity"] is None
        assert processed["result_header_text"] == processed["result_header_evidence"] == (
            "HEIST PASSED\nThe Cayo Perico Heist")
    else:
        assert selection["ownership"] == "none"
        assert selection["before"]["activity"] is selection["after"]["activity"] is None
    # Financial values and original tracking objects are outside the allowlist.
    assert not ({"money", "timer", "business", "current_money", "mission_start_money"} & document.keys())
    assert not ({"money", "timer", "business"} & processed.keys())


@pytest.mark.parametrize("native_failure", [False, True], ids=["native-empty", "native-sentinel"])
def test_native_empty_and_failure_sentinel_keep_native_processing_semantics(runs, monkeypatch, native_failure):
    pairs = []
    active_run = None

    async def native_recognize_pil(image, language):
        run = active_run
        trace = (image.size, image.mode, hashlib.sha256(image.tobytes()).hexdigest(), language)
        run.native_calls.append(trace)
        # Exercise the unchanged native wrapper and its real failure sentinel.
        if not run.hud.did_mutate:
            run.hud.crops[-1][0][:] = 211
            run.hud.did_mutate = True
        mission = run.hud.crops[-1][2]
        if native_failure and image.size == (mission.shape[1] * 2, mission.shape[0] * 2):
            raise LookupError("synthetic WinRT failure")
        return SimpleNamespace(lines=[])

    monkeypatch.setitem(sys.modules, "winocr", SimpleNamespace(
        OcrEngine=SimpleNamespace(max_image_dimension=4096), recognize_pil=native_recognize_pil,
    ))
    for armed in (False, True):
        run = runs()
        active_run = run
        engine = OCREngine()
        assert engine.is_available
        run.manager._ocr = engine
        run.manager._state_detector._ocr = engine
        before_grabs = len(run.hud.grabs)
        request = run.frame(armed=armed)
        assert len(run.hud.grabs) == before_grabs + 1
        assert len(run.native_calls) == 7  # Six mission sources plus separate money OCR.
        assert run.results[-1].game_state is GameState.UNKNOWN
        pairs.append((run, request))
    baseline, observed = pairs[0][0], pairs[1][0]
    assert observed.snapshots == baseline.snapshots
    request, pixels = pairs[1][1]
    _, document = observed.sample(request, pixels)
    assert document["backend"]["name"] == "windows_ocr"
    assert document["backend"]["confidence_source"] == "not_provided_by_native_backend"
    mission = document["sources"]["mission"]
    assert mission["ocr_status"] == ("failed" if native_failure else "recognized_empty")
    assert mission["raw_text"] == "" and mission["confidence"] is None
    assert mission["preprocessing"]["threshold"] is True
    assert mission["confidence_provenance"] == "not_provided_by_native_backend"
    assert all(record["confidence"] is None for record in document["sources"].values())
    assert document["backend"]["internal_dimensions"] is None


@pytest.mark.parametrize("fault", [
    "constructor", "capture", "observe_ocr", "stage", "selection", "finish", "json", "png",
])
def test_sample_recorder_failure_preserves_processing_and_does_not_retry(runs, monkeypatch, fault):
    from src import app as app_module
    from src.detection import detection_sample

    baseline = runs()
    execute_case(baseline, "header_completion", False)
    observed = runs()

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic recorder fault with private text")

    if fault == "constructor":
        monkeypatch.setattr(app_module, "DetectionSampleCollector", fail)
    elif fault == "json":
        monkeypatch.setattr(detection_sample, "_json_bytes", fail)
    elif fault == "png":
        monkeypatch.setattr(Image.Image, "save", fail)
    else:
        monkeypatch.setattr(detection_sample.DetectionSampleCollector, fault, fail)
    before_grabs = len(observed.hud.grabs)
    request, _pixels = execute_case(observed, "header_completion", True)
    assert observed.snapshots == baseline.snapshots
    assert len(observed.hud.grabs) == before_grabs + 2  # Seed and one admitted target cycle.
    assert observed.manager._stop_event.error is False
    assert len(observed.manager._activity_tracker.completed_activities) == 1
    status = observed.manager.get_detection_sample_status()
    assert status.state == "failed"
    assert status.token is request.token
    assert "private text" not in status.message
    assert observed.manager.claim_detection_sample_for_save(request.token) is None


def test_oversize_utf8_ocr_is_not_truncated_or_changed_in_tracking(runs):
    text = "Go to the location\n" + "é" * 9000
    assert len(text) < 16 * 1024 < len(text.encode("utf-8"))
    pairs = []
    for armed in (False, True):
        run = runs()
        request = run.frame(armed=armed, top=text)
        assert run.results[-1].mission_text == text
        assert run.results[-1].game_state is GameState.MISSION_ACTIVE
        pairs.append((run, request))
    baseline, observed = pairs[0][0], pairs[1][0]
    assert observed.snapshots == baseline.snapshots
    request, _pixels = pairs[1][1]
    status = observed.manager.get_detection_sample_status()
    assert status.state == "failed" and status.token is request.token
    assert observed.manager.claim_detection_sample_for_save(request.token) is None
    assert observed.manager._data.total_captures == baseline.manager._data.total_captures == 2


def test_unsupported_detector_typeerror_is_not_retried_as_a_signature_probe(runs):
    pairs = []
    for armed in (False, True):
        run = runs()

        def fails_once(*args, recorded=run.legacy_calls, **kwargs):
            assert "observation" not in kwargs
            recorded.append((semantic(args), semantic(kwargs)))
            # Even a signature-looking backend error is its original exception.
            raise TypeError("synthetic unexpected keyword argument 'observation'")

        run.manager._state_detector.detect = fails_once
        before = len(run.hud.grabs)
        request = run.frame(armed=armed, banner="Headhunter")
        assert len(run.hud.grabs) == before + 1
        assert len(run.legacy_calls) == 1
        assert run.manager._stop_event.error
        pairs.append((run, request))
    baseline, observed = pairs[0][0], pairs[1][0]
    assert observed.snapshots == baseline.snapshots
    request, _pixels = pairs[1][1]
    status = observed.manager.get_detection_sample_status()
    assert status.state == "failed" and status.token is request.token
    assert observed.manager.claim_detection_sample_for_save(request.token) is None
