"""Saved text reclassification only: real exporter, no capture or OCR backend."""

import builtins
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image


INDICES = {"mission": 2, "center": 3, "banner": 5, "bottom": 6, "header": 7, "vip_status": 8}
ARGS = {"mission": "mission_text_image", "center": "center_text_image", "banner": "mission_banner_image",
        "bottom": "bottom_objective_image", "header": "result_header_image", "vip_status": "vip_status_image"}
OPTIONS = {source: {"invert": source != "header", "scale": 2.0,
    **({"threshold": False} if source in ("bottom", "header", "vip_status") else {})} for source in INDICES}


def test_saved_sample_entrypoint_exists():
    assert importlib.util.find_spec("src.detection.sample_replay") is not None, "saved-sample replay is missing"


@pytest.fixture
def replay():
    return importlib.import_module("src.detection.sample_replay")


@pytest.fixture
def exported(tmp_path):
    # Unchanged actual producer; these synthetic pixels/text are no accuracy evidence.
    from src.capture.screen_capture import CapturedRegions, CaptureGeometry, CapturedRegionGeometry, CaptureRectangle
    from src.detection.detection_sample import DetectionSampleCollector, export_detection_sample
    from src.detection.ocr_engine import OCRResult
    from src.detection.state_detector import StateDetectionResult
    from src.game.state_machine import GameState
    frame = np.arange(100 * 80 * 3, dtype=np.uint32).reshape(80, 100, 3).astype(np.uint8)
    rectangles = [CapturedRegionGeometry(0, "captured", CaptureRectangle(-10, 20, 100, 80))]
    images = {0: frame}
    for source, index in INDICES.items():
        # Deliberately identical contents/geometry, yet independent source responses.
        images[index] = frame[3:13, 2:22].copy()
        rectangles.append(CapturedRegionGeometry(index, "captured", CaptureRectangle(-8, 23, 20, 10)))
    images = CapturedRegions(images, geometry=CaptureGeometry((100, 80), (-10, 20), tuple(rectangles),
        CaptureRectangle(-10, 20, 100, 80), None, None, None))
    collector = DetectionSampleCollector("testsample", "testrun", "2026-10-11T10:00:00+00:00", "2026-10-11T10:00:01+00:00")
    collector.capture(images, dict.fromkeys(INDICES, True), object())
    collector.observe_ocr(None, "backend", {"engine": SimpleNamespace()})
    for source in INDICES:
        collector.observe_ocr(source, "request", {"kwargs": OPTIONS[source]})
        collector.observe_ocr(source, "result", {"result": OCRResult("", None, [])})
    collector.stage("candidate", StateDetectionResult(GameState.IDLE, .123, "stored decision"))
    result = export_detection_sample(collector.finish(), tmp_path)
    assert result.success
    return Path(result.path)


def rewrite(directory, change=None, *, raw_json=None, png=None):
    report = json.loads((directory / "sample.json").read_bytes())
    if change:
        change(report)
    if png is not None:
        (directory / "frame.png").write_bytes(png)
        report["frame"].update(bytes=len(png), sha256=hashlib.sha256(png).hexdigest())
    (directory / "sample.json").write_bytes(raw_json if raw_json is not None else json.dumps(report, allow_nan=False).encode())
    marker = json.loads((directory / "COMPLETE.json").read_bytes())
    for name in ("sample.json", "frame.png"):
        data = (directory / name).read_bytes()
        marker["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (directory / "COMPLETE.json").write_text(json.dumps(marker))


def test_real_export_reclassifies_separately_with_unknown_defaults(replay, exported):
    report = replay.replay_sample(exported)
    assert report["status"] == "ok" and report["mode"] == "saved_sample_replay"
    assert report["detector_candidate"]["reason"] == "stored decision"
    assert report["first_guarded"] is None and report["processed"] is None
    assert report["selection"] == {"ownership": "unknown"}
    assert report["fresh_detector_candidate"] != report["detector_candidate"]
    assert report["candidate_changed"] is True
    assert report["sources"]["mission"]["preprocessing"]["threshold"] is None
    assert set(report["used_responses"]) == set(INDICES) and report["unused_responses"] == []
    assert report["context"] == {"fresh": True, "images": 1, "loaded_templates": [], "ocr_execution": False}
    assert "accuracy" in " ".join(report["limits"]).lower()


def test_crops_use_saved_geometry_and_source_identity(replay, exported, monkeypatch):
    from src.detection.state_detector import StateDetector
    original = StateDetector.detect
    seen = []
    def detect(self, frame, **images):
        crops = [images[name] for name in ARGS.values()]
        assert len({id(crop) for crop in crops}) == 6
        for crop in crops:
            assert crop.shape == (10, 20, 3)
            assert np.array_equal(crop, frame[3:13, 2:22])
            assert not np.shares_memory(crop, frame)
        seen.append(True)
        return original(self, frame, **images)
    monkeypatch.setattr(StateDetector, "detect", detect)
    rewrite(exported, lambda data: data["sources"]["mission"].update(raw_text="Mission: Sightseer", ocr_status="recognized"))
    report = replay.replay_sample(exported)
    assert seen and report["fresh_detector_candidate"]["identity"]["mission_name"] == "Sightseer"
    assert report["sources"]["center"]["raw_text"] == ""


@pytest.mark.parametrize("status", ["failed", "requested", "not_requested_by_detector", "backend_unavailable", "observation_unavailable"])
def test_unavailable_response_never_becomes_empty_text(replay, exported, status):
    def mutate(data):
        record = data["sources"]["mission"]
        record.update(ocr_status=status, raw_text=None, confidence=None)
        if status not in ("failed", "requested"):
            record["preprocessing"] = None
    rewrite(exported, mutate)
    with pytest.raises(replay.UnsupportedSampleError, match="mission.*response"):
        replay.replay_sample(exported)


def test_missing_images_stay_none_and_are_unused(replay, exported, monkeypatch):
    from src.detection.state_detector import StateDetector
    original = StateDetector.detect
    def detect(self, frame, **images):
        assert images["center_text_image"] is None
        return original(self, frame, **images)
    monkeypatch.setattr(StateDetector, "detect", detect)
    def mutate(data):
        data["sources"]["center"].update(image_present=False, region_present=False, ocr_status="missing_region", raw_text=None, preprocessing=None)
        data["geometry"]["regions"] = [item for item in data["geometry"]["regions"] if item["index"] != 3]
    rewrite(exported, mutate)
    report = replay.replay_sample(exported)
    assert "center" not in report["used_responses"] and "center" in report["unused_responses"]


@pytest.mark.parametrize("case", ["changed_value", "added_default", "removed_argument", "new_image", "repeat"])
def test_changed_or_repeated_requests_fail_closed(replay, exported, monkeypatch, case):
    from src.detection.state_detector import StateDetector
    def detect(self, frame, **images):
        crop = images["mission_text_image"]
        kwargs = OPTIONS["mission"].copy()
        if case == "changed_value": kwargs["invert"] = False
        if case == "added_default": kwargs["threshold"] = True
        if case == "removed_argument": del kwargs["scale"]
        if case == "new_image": crop = crop.copy()
        self._ocr.recognize_preprocessed(crop, **kwargs)
        if case == "repeat": self._ocr.recognize_preprocessed(crop, **kwargs)
        pytest.fail("incompatible request was accepted")
    monkeypatch.setattr(StateDetector, "detect", detect)
    with pytest.raises(replay.UnsupportedSampleError): replay.replay_sample(exported)


def test_native_omitted_defaults_are_validated(replay, exported):
    def native(data):
        data["backend"].update(name="windows_ocr", **{"class": "src.detection.ocr_engine.OCREngine"}, confidence_source="not_provided_by_native_backend")
        for source in ("mission", "center", "banner"):
            data["sources"][source]["preprocessing"]["threshold"] = True
        for record in data["sources"].values(): record["confidence_provenance"] = "not_provided_by_native_backend"
    rewrite(exported, native)
    assert replay.replay_sample(exported)["status"] == "ok"
    rewrite(exported, lambda data: data["sources"]["mission"]["preprocessing"].update(threshold=False))
    with pytest.raises(replay.SampleReplayError, match="default"): replay.replay_sample(exported)


@pytest.mark.parametrize("case", ["unknown", "missing", "outside", "unsupported_detector", "not_observed"])
def test_valid_but_insufficient_evidence_is_unsupported(replay, exported, case):
    def mutate(data):
        if case == "unknown": data["geometry"] = {"status": "unknown", "reason": "provider_did_not_supply_same_call_geometry"}
        elif case == "missing": data["geometry"]["regions"] = data["geometry"]["regions"][:1]
        elif case == "outside":
            region = data["geometry"]["regions"][1]
            region.update(box_ltrb_in_frame=[-2, 3, 18, 13], contained_in_frame=False)
            region["absolute_rectangle"]["left"] = -12
        else: data["mission_observation_status"] = case
    rewrite(exported, mutate)
    with pytest.raises(replay.UnsupportedSampleError): replay.replay_sample(exported)


def test_optional_stages_and_leftover_pending_are_allowed(replay, exported):
    rewrite(exported, lambda data: [data.pop(key, None) for key in ("first_guarded", "processed", "selection", "first_guard_changed")])
    (exported / "COMPLETE.pending").write_bytes(b"leftover exporter staging marker")
    report = replay.replay_sample(exported)
    assert report["first_guarded"] is None and report["selection"] is None


@pytest.mark.parametrize("case", ["payload_hash", "payload_size", "sample_id", "schema", "marker_schema", "extra_payload", "frame_hash", "frame_size", "frame_dimensions", "bool_dimension", "source_index", "source_type", "recognized_null", "empty_nonempty", "bad_explicit", "custom_default", "geometry_conflict", "guard_changed"])
def test_integrity_and_cross_field_failures(replay, exported, case):
    if case in ("payload_hash", "payload_size", "marker_schema", "extra_payload"):
        marker = json.loads((exported / "COMPLETE.json").read_text())
        if case == "payload_hash": marker["files"]["sample.json"]["sha256"] = "0" * 64
        if case == "payload_size": marker["files"]["sample.json"]["bytes"] += 1
        if case == "marker_schema": marker["schema_version"] = True
        if case == "extra_payload": marker["files"]["../other"] = marker["files"]["sample.json"]
        (exported / "COMPLETE.json").write_text(json.dumps(marker))
    else:
        def mutate(data):
            if case == "sample_id": data["sample_id"] = "other"
            if case == "schema": data["schema_version"] = 2
            if case == "frame_hash": data["frame"]["sha256"] = "0" * 64
            if case == "frame_size": data["frame"]["bytes"] += 1
            if case == "frame_dimensions": data["frame"]["width"] = 99
            if case == "bool_dimension": data["frame"]["width"] = True
            if case == "source_index": data["sources"]["mission"]["batch_index"] = 3
            if case == "source_type": data["sources"]["mission"]["image_present"] = 1
            if case == "recognized_null": data["sources"]["mission"].update(ocr_status="recognized", raw_text=None)
            if case == "empty_nonempty": data["sources"]["mission"]["raw_text"] = "not empty"
            if case == "bad_explicit": data["sources"]["mission"]["preprocessing"]["explicit_arguments"] = ["invert", "invert"]
            if case == "custom_default": data["sources"]["mission"]["preprocessing"]["threshold"] = True
            if case == "geometry_conflict": data["geometry"]["regions"][1]["box_ltrb_in_frame"][0] += 1
            if case == "guard_changed": data["first_guard_changed"] = True
        rewrite(exported, mutate)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'[]', b'{"x":' + b'[' * 40 + b'0' + b']' * 40 + b'}', b'{"x":"\\ud800"}'])
def test_strict_json(replay, exported, raw):
    rewrite(exported, raw_json=raw)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


@pytest.mark.parametrize("name", ["COMPLETE.json", "sample.json", "frame.png"])
def test_missing_symlink_and_nonregular_payloads(replay, exported, name):
    path = exported / name
    payload = path.read_bytes()
    path.unlink()
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)
    path.mkdir()
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)
    path.rmdir()
    target = exported.parent / ("target-" + name)
    target.write_bytes(payload)
    try: path.symlink_to(target)
    except OSError: pytest.skip("symlink creation unavailable")
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


@pytest.mark.parametrize("path", ["-", "https://example.org/sample", "file:///tmp/sample", "//server/share", "\\\\server\\share", "../sample", "a/../sample", "a\\..\\sample", "\\\\?\\C:\\sample", "C:relative"])
def test_rejects_nonlocal_or_traversal_paths(replay, path):
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(path)


def test_plain_windows_drive_path_is_not_rejected_as_url(replay):
    assert str(replay._local_directory(r"C:\Users\player\sample")).startswith("C:")


@pytest.mark.parametrize("case", ["truncated", "trailing", "rgba", "animated", "huge_dimensions", "wrong_format"])
def test_png_must_be_one_complete_bounded_rgb_frame(replay, exported, case):
    png = (exported / "frame.png").read_bytes()
    if case == "truncated": png = png[:-10]
    elif case == "trailing": png += b"other bytes"
    elif case in ("rgba", "wrong_format", "animated"):
        output = io.BytesIO()
        image = Image.new("RGBA" if case == "rgba" else "RGB", (100, 80))
        image.save(output, format="JPEG" if case == "wrong_format" else "PNG", **({"save_all": True, "append_images": [Image.new("RGB", (100, 80), "red")]} if case == "animated" else {}))
        png = output.getvalue()
    else:
        import struct, zlib
        header = struct.pack(">IIBBBBB", 8193, 80, 8, 2, 0, 0, 0)
        png = png[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + png[33:]
    rewrite(exported, png=png)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


@pytest.mark.parametrize("name,limit", [("COMPLETE.json", 4096), ("sample.json", 256 * 1024), ("frame.png", 64 * 1024 * 1024)])
def test_size_caps_before_reading_payload(replay, exported, name, limit):
    with (exported / name).open("wb") as file: file.truncate(limit + 1)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


def test_public_cli_dispatch_is_offline_read_only_and_machine_readable(exported, tmp_path):
    # Fresh process catches forbidden imports and writes, including bytecode.
    root = Path(__file__).resolve().parents[1]
    script = r'''
import builtins, importlib.util, io, json, os, pathlib, runpy, socket, subprocess, sys
root, sample = sys.argv[1:]
sys.path.insert(0, root)
real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    absolute = importlib.util.resolve_name("." * level + name, globals["__package__"]) if level else name
    if absolute.startswith(("src.app", "src.capture", "src.config", "src.database", "mss", "winocr", "pytesseract")):
        raise AssertionError("forbidden import: " + absolute)
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import
def blocked(*args, **kwargs): raise AssertionError("unexpected external effect")
subprocess.Popen = blocked
class BlockedSocket(socket.socket):
    def __new__(cls, *args, **kwargs): blocked()
socket.socket = BlockedSocket
real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
def read_only(file, mode="r", *args, **kwargs):
    if any(flag in mode for flag in "wax+"): blocked()
    return real_open(file, mode, *args, **kwargs)
def io_read_only(file, mode="r", *args, **kwargs):
    if any(flag in mode for flag in "wax+"): blocked()
    return real_io_open(file, mode, *args, **kwargs)
def os_read_only(file, flags, *args, **kwargs):
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND): blocked()
    return real_os_open(file, flags, *args, **kwargs)
builtins.open, io.open, os.open = read_only, io_read_only, os_read_only
for name in ("mkdir", "makedirs", "unlink", "remove", "rename", "replace", "link", "symlink"):
    setattr(os, name, blocked)
def audit(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        if (mode and any(flag in mode for flag in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND): blocked()
    if event in ("socket.__new__", "socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"): blocked()
sys.addaudithook(audit)
sys.argv = ["test_capture.py", "--sample", sample]
runpy.run_path(str(pathlib.Path(root) / "test_capture.py"), run_name="__main__")
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(exported)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env={key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"})
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["status"] == "ok"
    assert len(result.stdout.splitlines()) == 1


@pytest.mark.parametrize("arguments", [["--sample", "x", "--image", "y"], ["--sample", "x", "--backend", "tesseract"]])
def test_cli_rejects_conflicting_modes(arguments):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-B", str(root / "test_capture.py"), *arguments], capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and result.stdout == ""


def test_cli_input_error_is_one_json_report():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-B", str(root / "test_capture.py"), "--sample", "https://example.org/no"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["mode"] == "saved_sample_replay" and report["status"] == "error"
    assert len(result.stdout.splitlines()) == 1


def test_equal_candidate_and_older_optional_fields_are_compared_honestly(replay, exported):
    fresh = replay.replay_sample(exported)["fresh_detector_candidate"]
    rewrite(exported, lambda data: data.update(detector_candidate=fresh))
    assert replay.replay_sample(exported)["candidate_changed"] is False
    rewrite(exported, lambda data: data["detector_candidate"].pop("screen_evidence"))
    report = replay.replay_sample(exported)
    assert report["candidate_changed"] is False
    assert "screen_evidence" in report["candidate_comparison"]["missing_stored_fields"]
    assert "screen_evidence" not in report["candidate_comparison"]["compared_fields"]


def test_cli_dependency_import_failure_still_emits_one_json(monkeypatch, capsys):
    import test_capture
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == "src.detection.sample_replay": raise ImportError("dependency missing")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert test_capture.main(["--sample", "unused"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["category"] == "execution_error"
    assert len(output.out.splitlines()) == 1


def test_no_ocr_preprocessing_templates_or_logging_setup(replay, exported, monkeypatch):
    from src.detection.ocr_engine import OCREngine
    from src.detection.template_matcher import TemplateMatcher
    from src.utils import logging
    def blocked(*args, **kwargs): pytest.fail("offline boundary invoked")
    for name in ("__init__", "recognize", "recognize_preprocessed", "preprocess_for_ocr", "_recognize_async"):
        monkeypatch.setattr(OCREngine, name, blocked)
    monkeypatch.setattr(TemplateMatcher, "load_template", blocked)
    monkeypatch.setattr(TemplateMatcher, "load_templates_from_dir", blocked)
    monkeypatch.setattr(logging, "setup_logging", blocked)
    assert replay.replay_sample(exported)["status"] == "ok"


def test_observed_directory_symlink_is_rejected(replay, exported):
    alias = exported.parent / "alias"
    try: alias.symlink_to(exported, target_is_directory=True)
    except OSError: pytest.skip("symlink creation unavailable")
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(alias)


def test_observed_reparse_file_metadata_is_rejected(replay, exported, monkeypatch):
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        metadata = original(path, *args, **kwargs)
        if path.name == "frame.png":
            values = {name: getattr(metadata, name) for name in dir(metadata) if name.startswith("st_")}
            values["st_file_attributes"] = 0x400
            return SimpleNamespace(**values)
        return metadata
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)


def test_descriptor_identity_mismatch_is_rejected(replay, exported, monkeypatch):
    original = os.fstat
    def changed(descriptor):
        metadata = original(descriptor)
        values = {name: getattr(metadata, name) for name in dir(metadata) if name.startswith("st_")}
        values["st_ino"] += 1
        return SimpleNamespace(**values)
    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(replay.SampleReplayError, match="changed during open"): replay.replay_sample(exported)


def test_optional_nofollow_flag_is_not_required(replay, exported, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    assert replay.replay_sample(exported)["status"] == "ok"


def test_source_text_and_unknown_report_fields_are_only_data(replay, exported):
    payload = "__import__('os').system('should never execute')"
    def mutate(data):
        data["sources"]["mission"].update(raw_text=payload, ocr_status="recognized")
        data["untrusted_command"] = payload
        data["capture_provider"]["class"] = payload
    rewrite(exported, mutate)
    report = replay.replay_sample(exported)
    assert report["sources"]["mission"]["raw_text"] == payload
    assert report["capture_provider"]["class"] == payload
    assert "untrusted_command" not in report


def test_fresh_history_is_not_reused_across_replays(replay, exported, monkeypatch):
    from src.detection.state_detector import StateDetector
    original = StateDetector.detect
    contexts = []
    def detect(self, frame, **images):
        contexts.append(self._context)
        return original(self, frame, **images)
    monkeypatch.setattr(StateDetector, "detect", detect)
    first, second = replay.replay_sample(exported), replay.replay_sample(exported)
    assert contexts[0] is not contexts[1]
    assert first["fresh_detector_candidate"] == second["fresh_detector_candidate"]


@pytest.mark.parametrize("parent,key", [("backend", "class"), ("mission", "raw_text"), ("mission", "confidence")])
def test_required_nullable_fields_fail_as_input_errors(replay, exported, parent, key, capsys):
    import test_capture
    def mutate(data):
        record = data["backend"] if parent == "backend" else data["sources"][parent]
        del record[key]
    rewrite(exported, mutate)
    with pytest.raises(replay.SampleReplayError): replay.replay_sample(exported)
    assert test_capture.main(["--sample", str(exported)]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["category"] == "invalid_input"
    assert len(output.out.splitlines()) == 1
