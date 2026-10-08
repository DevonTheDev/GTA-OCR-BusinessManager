"""Exact synthetic observation and bounded local export; no screen or native OCR."""

import dataclasses
import hashlib
import importlib.util
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src.capture import screen_capture
from src.capture.regions import Region
from src.detection.ocr_engine import OCRResult, OCREngine
from src.detection.state_detector import StateDetector, StateDetectionResult
from src.game.state_machine import GameState


def sample_module():
    assert importlib.util.find_spec("src.detection.detection_sample") is not None, "bounded sample core is missing"
    from src.detection import detection_sample
    return detection_sample


@pytest.fixture
def capture(monkeypatch):
    scaler = SimpleNamespace(width=100, height=80, offset=(-300, 20), scale_factor=1.0)
    monkeypatch.setattr(screen_capture, "ResolutionScaler", lambda _index: scaler)
    capture = screen_capture.ScreenCapture()
    capture.grabs = []

    def grab(bounds):
        capture.grabs.append(bounds.copy())
        image = np.empty((bounds["height"], bounds["width"], 4), dtype=np.uint8)
        image[:, :, 0] = (np.arange(bounds["width"]) + bounds["left"]) % 256
        image[:, :, 1] = (np.arange(bounds["height"])[:, None] + bounds["top"]) % 256
        image[:, :, 2] = len(capture.grabs)
        image[:, :, 3] = 255
        return image

    monkeypatch.setattr(capture, "_ensure_mss", lambda: SimpleNamespace(grab=grab))
    return capture


def make_collector(images=None, provider=None):
    core = sample_module()
    collector = core.DetectionSampleCollector("a" * 32, "b" * 32,
        "2026-10-08T16:00:00+00:00", "2026-10-08T16:00:01+00:00")
    if images is None:
        images = {index: np.full((20, 30, 3), index + 30, np.uint8) for index in range(9)}
    collector.capture(images, dict.fromkeys(core.SOURCE_INDICES, True), provider or object())
    return collector


def test_same_invocation_geometry_is_immutable_and_preserves_outside_crop(capture):
    images = capture.capture_multiple_regions([Region(.1, .1, .8, .8), None, Region(-.1, -.1, .2, .2)])
    assert hasattr(images, "geometry"), "same-call capture geometry is missing"
    geometry = images.geometry
    assert geometry.resolution == (100, 80) and geometry.offset == (-300, 20)
    assert geometry.regions[2].rectangle.left == -310
    assert geometry.regions[1].status == "invalid_region"
    assert geometry.grab_started_at <= geometry.grab_ended_at
    assert geometry.grab_duration_ns >= 0
    assert len(capture.grabs) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        geometry.offset = (0, 0)
    capture._scaler.width = 200
    capture._scaler.offset = (500, 400)
    assert geometry.resolution == (100, 80) and geometry.offset == (-300, 20)


def test_metadata_failure_keeps_successful_capture_arrays(capture, monkeypatch):
    assert hasattr(screen_capture, "_capture_geometry"), "isolated geometry construction is missing"
    monkeypatch.setattr(screen_capture, "_capture_geometry", lambda **_kwargs: (_ for _ in ()).throw(ValueError("metadata")))
    images = capture.capture_multiple_regions([capture.regions.full_screen])
    assert images[0].shape == (80, 100, 3)
    assert images.geometry is None and len(capture.grabs) == 1


def detector_fixture():
    calls = []
    outputs = iter([OCRResult("", None, []) for _ in range(6)])

    def recognize(image, **kwargs):
        result = next(outputs)
        calls.append((image, kwargs, result))
        return result

    engine = SimpleNamespace(is_available=True, recognize_preprocessed=recognize)
    detector = StateDetector(ocr_engine=engine,
        template_matcher=SimpleNamespace(match_any=lambda *_args, **_kwargs: None))
    detector._quick_state_check = lambda _image: StateDetectionResult(GameState.IDLE, .5, "synthetic")
    return detector, calls


def invoke(detector, **kwargs):
    # Deliberately alias crop arrays: source identity must not use array identity.
    crop = np.full((20, 30, 3), 50, np.uint8)
    return detector.detect(crop, crop, crop, crop, crop, crop, crop, **kwargs)


def test_observer_records_six_real_calls_without_changing_arguments_or_result():
    plain, baseline = detector_fixture()
    expected = invoke(plain)
    detector, calls = detector_fixture()
    assert getattr(detector.detect, "supports_detection_sample_observation", False) is True
    events = []
    observed = invoke(detector, observation=lambda *args: events.append(args))
    assert observed == expected
    assert len(calls) == len(baseline) == 6
    assert [item[1] for item in calls] == [item[1] for item in baseline]
    assert [source for source, event, _payload in events if event == "request"] == [
        "mission", "center", "banner", "bottom", "vip_status", "header"]
    assert all(payload["result"] is calls[index][2] for index, (_source, _event, payload) in
        enumerate(event for event in events if event[1] == "result"))


def test_observer_errors_never_suppress_calls_or_replace_backend_exception():
    detector, calls = detector_fixture()
    assert getattr(detector.detect, "supports_detection_sample_observation", False) is True

    def broken(*_args):
        raise RuntimeError("observer secret")

    invoke(detector, observation=broken)
    assert len(calls) == 6
    original = RuntimeError("backend original")
    detector._ocr.recognize_preprocessed = lambda *_args, **_kwargs: (_ for _ in ()).throw(original)
    with pytest.raises(RuntimeError) as raised:
        invoke(detector, observation=broken)
    assert raised.value is original


def test_collector_freezes_exact_frame_and_allowlisted_stage():
    images = {index: np.full((20, 30, 3), index + 30, np.uint8) for index in range(9)}
    images[0][0, 0] = [1, 2, 3]
    collector = make_collector(images)
    result = StateDetectionResult(GameState.IDLE, .5, "before")
    result.password = "must not serialize"
    collector.stage("candidate", result)
    result.reason = "after"
    images[0][:] = 255
    sample = collector.finish()
    report = json.loads(sample.json_bytes)
    assert report["detector_candidate"]["reason"] == "before"
    assert b"must not serialize" not in sample.json_bytes
    assert report["geometry"]["status"] == "unknown"
    assert report["capture_provider"]["kind"] == "custom_or_unknown"
    assert report["frame"]["sha256"] == hashlib.sha256(sample.png_bytes).hexdigest()
    with Image.open(io.BytesIO(sample.png_bytes)) as image:
        assert image.getpixel((0, 0)) == (3, 2, 1)
        assert image.getpixel((1, 0)) == (30, 30, 30)
    assert collector._frame is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.png_bytes = b"changed"


def test_collector_six_source_statuses_and_native_confidence():
    core = sample_module()
    images = dict.fromkeys(range(9))
    images[0] = np.zeros((20, 30, 3), np.uint8)
    for index in (2, 3, 5, 6, 7):
        images[index] = images[0]
    collector = make_collector(images)
    engine = object.__new__(OCREngine)
    collector.observe_ocr(None, "backend", {"engine": engine})
    for source, result in (("mission", OCRResult("", None, [])),
                           ("center", OCRResult("", 0.0, [])),
                           ("banner", OCRResult("Headhunter", None, []))):
        collector.observe_ocr(source, "request", {"kwargs": {"invert": True, "scale": 2.0}})
        collector.observe_ocr(source, "result", {"result": result})
    collector.observe_ocr("bottom", "availability", {"available": False})
    report = json.loads(collector.finish().json_bytes)
    sources = report["sources"]
    assert sources["mission"]["ocr_status"] == "recognized_empty"
    assert sources["center"]["ocr_status"] == "failed"
    assert sources["center"]["confidence"] is None
    assert sources["banner"]["ocr_status"] == "recognized"
    assert sources["bottom"]["ocr_status"] == "backend_unavailable"
    assert sources["header"]["ocr_status"] == "not_requested_by_detector"
    assert sources["vip_status"]["ocr_status"] == "capture_unavailable"
    assert report["backend"]["name"] == "windows_ocr"


def test_collector_oversize_ocr_fails_without_retaining_the_frame():
    core = sample_module()
    collector = make_collector()
    collector.observe_ocr("mission", "result", {"result": OCRResult("x" * 16385, None, [])})
    with pytest.raises(core.DetectionSampleError):
        collector.finish()
    assert collector._frame is None


def test_export_exclusively_writes_exact_bytes_and_valid_completion_marker(tmp_path):
    core = sample_module()
    sample = make_collector().finish()
    result = core.export_detection_sample(sample, tmp_path)
    assert result.success and not result.partial_files
    from pathlib import Path
    directory = Path(result.path)
    assert (directory / "frame.png").read_bytes() == sample.png_bytes
    assert (directory / "sample.json").read_bytes() == sample.json_bytes
    marker = json.loads((directory / "COMPLETE.json").read_bytes())
    assert marker["files"]["frame.png"] == {"sha256": hashlib.sha256(sample.png_bytes).hexdigest(), "bytes": len(sample.png_bytes)}
    second = core.export_detection_sample(sample, tmp_path)
    assert not second.success and second.completed_files == () and second.partial_files == ()
    assert (directory / "frame.png").read_bytes() == sample.png_bytes


def test_export_records_partial_payload_without_completion_marker(tmp_path, monkeypatch):
    core = sample_module()
    sample = make_collector().finish()
    from pathlib import Path
    real_open = Path.open

    class BrokenWriter:
        def __enter__(self):
            return self
        def write(self, data):
            self.stream.write(data[:5])
            raise OSError("private path text")
        def __exit__(self, *_args):
            self.stream.close()

    def fail_json(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        if path.name == "sample.json":
            writer = BrokenWriter()
            writer.stream = stream
            return writer
        return stream

    monkeypatch.setattr(Path, "open", fail_json)
    result = core.export_detection_sample(sample, tmp_path)
    assert not result.success
    assert result.completed_files == ("frame.png",)
    assert result.partial_files == ("sample.json",)
    assert not (Path(result.path) / "COMPLETE.json").exists()
    assert "private path text" not in result.message


def test_marker_close_failure_cannot_leave_a_valid_completion_marker(tmp_path, monkeypatch):
    core = sample_module()
    sample = make_collector().finish()
    from pathlib import Path
    real_open = Path.open

    class FailedClose:
        def __enter__(self):
            return self
        def write(self, data):
            return self.stream.write(data)
        def __exit__(self, *_args):
            self.stream.close()
            raise OSError("marker close failed")

    def marker_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        if path.name.startswith("COMPLETE"):
            writer = FailedClose()
            writer.stream = stream
            return writer
        return stream

    monkeypatch.setattr(Path, "open", marker_open)
    result = core.export_detection_sample(sample, tmp_path)
    assert not result.success
    assert not (Path(result.path) / "COMPLETE.json").exists()
    assert result.completed_files == ("frame.png", "sample.json")
    assert result.partial_files == ("COMPLETE.pending",)


def test_custom_ocr_helper_override_keeps_original_call_shape():
    detector, calls = detector_fixture()
    detector._ocr_state_check = lambda _mission, _center, _banner: None
    detector._vip_status_observation = lambda result, _image: result
    detector._result_header_observation = lambda result, _image: result
    events = []
    invoke(detector, observation=lambda *args: events.append(args))
    assert len(calls) == 1  # Only the detector-owned bottom OCR remains observed.
    assert {source for source, event, _ in events if event == "unavailable"} == {
        "mission", "center", "banner", "vip_status", "header"}


def test_legacy_frame_timestamp_is_labeled_as_receipt_time():
    report = json.loads(make_collector().finish().json_bytes)
    assert report["captured_at_provenance"] == "frame_received"


def test_native_request_defaults_match_the_real_backend_signature():
    collector = make_collector()
    collector.observe_ocr(None, "backend", {"engine": object.__new__(OCREngine)})
    collector.observe_ocr("mission", "request", {"kwargs": {}})
    report = json.loads(collector.finish().json_bytes)
    flags = report["sources"]["mission"]["preprocessing"]
    assert flags == {"threshold": True, "invert": True, "scale": 2.0, "explicit_arguments": []}


def test_marker_link_failure_is_truthful_and_keeps_closed_staging_file(tmp_path, monkeypatch):
    core = sample_module()
    monkeypatch.setattr(core.os, "link", lambda *_args: (_ for _ in ()).throw(OSError("no hard links")))
    result = core.export_detection_sample(make_collector().finish(), tmp_path)
    from pathlib import Path
    assert not result.success
    assert result.completed_files == ("frame.png", "sample.json", "COMPLETE.pending")
    assert result.partial_files == ()
    assert not (Path(result.path) / "COMPLETE.json").exists()


def test_marker_cleanup_failure_keeps_successful_completion_truthful(tmp_path, monkeypatch):
    core = sample_module()
    from pathlib import Path
    monkeypatch.setattr(Path, "unlink", lambda *_args: (_ for _ in ()).throw(OSError("cleanup failure")))
    result = core.export_detection_sample(make_collector().finish(), tmp_path)
    assert result.success
    assert result.completed_files == ("frame.png", "sample.json", "COMPLETE.pending", "COMPLETE.json")
    marker = json.loads((Path(result.path) / "COMPLETE.json").read_bytes())
    assert marker["files"]["frame.png"]["sha256"] == hashlib.sha256((Path(result.path) / "frame.png").read_bytes()).hexdigest()


def test_sample_preserves_effective_geometry_and_labels_synthetic_provider(capture):
    images = capture.capture_multiple_regions([Region(.1, .1, .8, .8), None, Region(-.1, -.1, .2, .2)])
    collector = make_collector(images, capture)
    # A subsequent monitor change cannot rewrite this invocation's metadata.
    capture._scaler.offset = (2000, 2000)
    report = json.loads(collector.finish().json_bytes)
    assert report["frame"]["width"] == 80 and report["frame"]["height"] == 64
    assert report["geometry"]["regions"][2]["box_ltrb_in_frame"] == [-20, -16, 0, 0]
    assert report["geometry"]["regions"][2]["contained_in_frame"] is False
    assert report["geometry"]["offset"] == [-300, 20]
    assert report["captured_at_provenance"] == "same_call_grab_start"
    assert report["capture_provider"]["kind"] == "custom_or_unknown"
    assert len(capture.grabs) == 1


@pytest.mark.parametrize("frame", [None, np.zeros((0, 2, 3), np.uint8),
    np.zeros((2, 0, 3), np.uint8), np.zeros((1, 8193, 3), np.uint8),
    np.zeros((2, 3), np.uint8), np.zeros((2, 3, 4), np.uint8), np.zeros((2, 3, 3), np.float32)])
def test_invalid_or_oversized_frames_are_rejected_before_retention(frame):
    core = sample_module()
    collector = core.DetectionSampleCollector("sample", "run", "2026-10-08T00:00:00Z", "2026-10-08T00:00:01Z")
    with pytest.raises(core.DetectionSampleError):
        collector.capture({0: frame}, {}, object())
    assert collector._frame is None


def test_pixel_count_bound_rejects_without_copy(monkeypatch):
    core = sample_module()
    monkeypatch.setattr(core, "MAX_PIXELS", 599)
    with pytest.raises(core.DetectionSampleError):
        make_collector()


@pytest.mark.parametrize("kind", ["png_bound", "json_bound", "png_encode"])
def test_encoding_limits_and_failures_release_the_frame(monkeypatch, kind):
    core = sample_module()
    collector = make_collector()
    activity = SimpleNamespace(name="Selected", activity_type="VIP_WORK")
    collector.selection("before", activity, "type_only", False)
    if kind == "png_bound":
        monkeypatch.setattr(core, "MAX_PNG_BYTES", 20)
    elif kind == "json_bound":
        monkeypatch.setattr(core, "MAX_JSON_BYTES", 100)
    else:
        monkeypatch.setattr(Image.Image, "save", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("encode")))
    with pytest.raises(core.DetectionSampleError):
        collector.finish()
    assert collector._frame is None and collector._owners == {}


def test_copy_failure_is_sample_specific_and_does_not_mutate_input():
    core = sample_module()

    class CopyFailure(np.ndarray):
        def copy(self, *args, **kwargs):
            raise MemoryError("synthetic copy failure")

    frame = np.full((20, 30, 3), 52, np.uint8).view(CopyFailure)
    collector = core.DetectionSampleCollector("sample", "run", "2026-10-08T00:00:00Z", "2026-10-08T00:00:01Z")
    with pytest.raises(core.DetectionSampleError):
        collector.capture({0: frame}, {}, object())
    assert collector._frame is None and np.all(frame == 52)


def test_finite_stage_numbers_and_utf8_source_bounds():
    core = sample_module()
    collector = make_collector()
    with pytest.raises(core.DetectionSampleError):
        collector.stage("candidate", StateDetectionResult(GameState.IDLE, float("nan"), "invalid"))
    collector.observe_ocr("mission", "result", {"result": OCRResult("é" * 8193, None, [])})
    with pytest.raises(core.DetectionSampleError):
        collector.finish()
    assert collector._frame is None


def test_custom_empty_zero_confidence_is_not_assumed_to_be_native_failure():
    collector = make_collector()
    collector.observe_ocr(None, "backend", {"engine": SimpleNamespace()})
    collector.observe_ocr("mission", "result", {"result": OCRResult("", 0.0, [])})
    report = json.loads(collector.finish().json_bytes)
    assert report["sources"]["mission"]["ocr_status"] == "recognized_empty"
    assert report["sources"]["mission"]["confidence"] == 0.0


def test_unsupported_detector_and_missing_region_are_distinct():
    core = sample_module()
    collector = core.DetectionSampleCollector("sample", "run", "2026-10-08T00:00:00Z", "2026-10-08T00:00:01Z")
    presence = dict.fromkeys(core.SOURCE_INDICES, True)
    presence["banner"] = False
    collector.capture([np.zeros((20, 30, 3), np.uint8)] * 9, presence, object())
    collector.mission_observation_unavailable()
    report = json.loads(collector.finish().json_bytes)
    assert report["mission_observation_status"] == "unsupported_detector"
    assert report["sources"]["mission"]["ocr_status"] == "observation_unavailable"
    assert report["sources"]["banner"]["ocr_status"] == "missing_region"
    assert report["geometry"]["status"] == "unknown"


@pytest.mark.parametrize("ownership", ["retained", "refined", "new", "cleared", "none"])
def test_selected_owner_identity_is_observed_without_serializing_activity(ownership):
    collector = make_collector()
    before = None if ownership == "none" else SimpleNamespace(name="VIP Work", activity_type="VIP_WORK", money=123456789)
    collector.selection("before", before, "type_only", False)
    after = before
    if ownership == "refined":
        after.name = "Headhunter"
    elif ownership == "new":
        after = SimpleNamespace(name="VIP Work", activity_type="VIP_WORK")
    elif ownership == "cleared":
        after = None
    collector.selection("after", after, "known_name" if ownership == "refined" else "type_only", True)
    sample = collector.finish()
    report = json.loads(sample.json_bytes)
    assert report["selection"]["ownership"] == ownership
    assert b"123456789" not in sample.json_bytes
    assert collector._owners == {}
