"""Single local image evidence, with native and subprocess boundaries doubled."""

import builtins
import hashlib
import importlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from src.capture.regions import Region, ScreenRegions
from src.detection.ocr_engine import OCREngine
from src.detection.state_detector import StateDetector


SOURCES = (
    "mission_text", "center_prompt", "mission_banner", "bottom_objective",
    "result_header", "vip_status",
)
REQUEST_ORDER = (*SOURCES[:4], "vip_status", "result_header")
ARGUMENTS = {
    "mission_text": "mission_text_image", "center_prompt": "center_text_image",
    "mission_banner": "mission_banner_image", "bottom_objective": "bottom_objective_image",
    "result_header": "result_header_image", "vip_status": "vip_status_image",
}


def test_diagnostic_entrypoint_exists():
    assert importlib.util.find_spec("src.detection.screenshot_diagnostic") is not None


@pytest.fixture
def diagnostic():
    return importlib.import_module("src.detection.screenshot_diagnostic")


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "screenshot.png"
    Image.new("RGB", (320, 180), (70, 70, 70)).save(path)
    return path


@pytest.fixture
def native(monkeypatch, diagnostic):
    """Double only WinOCR; retain real production preprocessing and detection."""
    calls = []

    def configure(texts=(), *, error=None):
        responses = iter(texts)

        async def recognize_pil(image, language):
            calls.append((np.array(image), language))
            if error is not None:
                raise error
            text = next(responses, "")
            return SimpleNamespace(lines=[SimpleNamespace(text=text, words=[])] if text else [])

        monkeypatch.setattr(diagnostic.platform, "system", lambda: "Windows")
        monkeypatch.setitem(sys.modules, "winocr", SimpleNamespace(
            OcrEngine=SimpleNamespace(max_image_dimension=10000),
            recognize_pil=recognize_pil,
        ))
        return calls

    yield configure
    from src.detection.ocr_engine import _cleanup_ocr_loop
    _cleanup_ocr_loop()


def test_native_report_preserves_null_confidence_and_candidate_limits(diagnostic, image_path, native, capsys):
    calls = native(["Mission: Sightseer"])
    report = diagnostic.diagnose_image(image_path)
    assert report["status"] == "ok"
    assert report["schema_version"] == 1
    assert report["mode"] == "single_image_diagnostic"
    assert report["input"] == {
        "name": image_path.name, "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "bytes": image_path.stat().st_size, "format": "PNG", "width": 320, "height": 180,
    }
    assert report["backend"] == {
        "name": "windows", "diagnostic_only": False,
        "ocr_confidence_source": "not_provided_by_native_backend",
    }
    assert report["context"] == {"fresh": True, "images": 1, "loaded_templates": []}
    assert set(report["sources"]) == set(SOURCES)
    assert len(calls) == 6
    assert report["sources"]["mission_text"]["status"] == "recognized"
    assert report["sources"]["mission_text"]["text"] == "Mission: Sightseer"
    for source in SOURCES:
        assert report["sources"][source]["ocr_confidence"] is None
    assert report["sources"]["result_header"]["status"] == "recognized_empty"
    candidate = report["detector_candidate"]
    assert candidate["state"] == "MISSION_ACTIVE"
    assert candidate["heuristic_score"] == 0.8
    assert candidate["identity"] == {
        "status": "known_name", "candidates": ["Sightseer"], "mission_name": "Sightseer",
        "mission_type": "VIP_WORK", "heist_phase": "UNKNOWN", "outcome": None,
        "outcome_scope": None,
    }
    assert "accounting" in " ".join(report["limits"]).lower()
    assert "accuracy" in " ".join(report["limits"]).lower()
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert capsys.readouterr().out == ""


def test_requests_use_unchanged_production_preprocessing(diagnostic, image_path, native):
    # RGB gradients catch channel order changes and incorrect crop/preprocess choices.
    rgb = np.arange(320 * 180 * 3, dtype=np.uint32).reshape(180, 320, 3).astype(np.uint8)
    Image.fromarray(rgb).save(image_path)
    calls = native(["Mission: Sightseer"])
    report = diagnostic.diagnose_image(image_path)
    frame = rgb[:, :, ::-1].copy()
    engine = OCREngine()
    regions = ScreenRegions()
    for source, (observed, language) in zip(REQUEST_ORDER, calls, strict=True):
        expected_options = {"threshold": source in SOURCES[:3], "invert": source != "result_header", "scale": 2.0}
        left, top, right, bottom = getattr(regions, source).to_absolute(320, 180)
        expected = engine.preprocess_for_ocr(frame[top:bottom, left:right].copy(), **expected_options)
        np.testing.assert_array_equal(observed, cv2.cvtColor(expected, cv2.COLOR_GRAY2RGB))
        assert language == "en"
        assert report["sources"][source]["preprocessing"] == expected_options


@pytest.mark.parametrize("text,state", [
    ("Mission passed\nSightseer", "MISSION_COMPLETE"),
    ("Sightseer\nHeadhunter", "UNKNOWN"),
    ("Stock\nSupplies", "BUSINESS_COMPUTER"),
])
def test_detector_gates_are_reported_as_unrequested(diagnostic, image_path, native, text, state):
    calls = native([text])
    report = diagnostic.diagnose_image(image_path)
    assert len(calls) == 3
    assert report["detector_candidate"]["state"] == state
    for source in SOURCES[3:]:
        assert report["sources"][source]["status"] == "not_requested_by_detector"
        assert "text" not in report["sources"][source]
        assert "preprocessing" not in report["sources"][source]
    assert not {"mission_completed", "payout", "session_totals", "active_activity", "passed"} & report.keys()


def test_each_image_uses_one_fresh_detector(diagnostic, image_path, native, monkeypatch):
    native()
    detectors = []
    detections = []

    class RecordingDetector(StateDetector):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            assert self._context.consecutive_same_state == 0
            assert self._templates._templates == {}
            detectors.append(self)

        def detect(self, *args, **kwargs):
            detections.append(self)
            return super().detect(*args, **kwargs)

    monkeypatch.setattr(diagnostic, "StateDetector", RecordingDetector)
    reports = [diagnostic.diagnose_image(image_path) for _ in range(2)]
    assert len(detectors) == 2
    assert detectors[0]._context is not detectors[1]._context
    assert detections == detectors
    assert all(report["detector_candidate"]["identity"] is None for report in reports)


@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080), (1919, 1079), (3440, 1440)])
def test_crops_match_production_batch_without_capture_construction(
    diagnostic, image_path, native, monkeypatch, resolution,
):
    from src.capture.screen_capture import ScreenCapture
    width, height = resolution
    frame = np.arange(width * height * 3, dtype=np.uint32).reshape(height, width, 3).astype(np.uint8)
    Image.fromarray(frame[:, :, ::-1]).save(image_path)
    regions = ScreenRegions()
    # Exercise the real batch slicer against an in-memory BGRA grab, never a screen.
    capture = object.__new__(ScreenCapture)
    capture._scaler = SimpleNamespace(width=width, height=height, offset=(37, 53))
    capture._min_capture_interval = 0
    capture._last_capture_time = None
    bgra = np.dstack((frame, np.full((height, width), 255, dtype=np.uint8)))
    capture._sct = SimpleNamespace(grab=lambda bounds: bgra)
    crops = capture.capture_multiple_regions([regions.full_screen, *(getattr(regions, name) for name in SOURCES)])
    native(["Mission: Sightseer"])

    class CheckingDetector(StateDetector):
        def detect(self, image, **kwargs):
            np.testing.assert_array_equal(image, frame)
            assert image.flags.owndata and image.flags.c_contiguous
            assert set(kwargs) == set(ARGUMENTS.values())
            for index, name in enumerate(SOURCES, 1):
                crop = kwargs[ARGUMENTS[name]]
                np.testing.assert_array_equal(crop, crops[index])
                assert crop.flags.owndata and crop.flags.c_contiguous
                assert not np.shares_memory(crop, image)
            return super().detect(image, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Image mode must never construct live capture")

    monkeypatch.setattr(diagnostic, "StateDetector", CheckingDetector)
    monkeypatch.setattr(ScreenCapture, "__init__", forbidden)
    monkeypatch.setattr("mss.mss", forbidden)
    report = diagnostic.diagnose_image(image_path)
    for name in SOURCES:
        assert report["sources"][name]["box_ltrb"] == list(getattr(regions, name).to_absolute(width, height))


def test_image_mode_does_not_enter_application_or_financial_services(diagnostic, image_path, native, monkeypatch):
    from src.detection.parsers.money_parser import MoneyParser
    from src.detection.parsers.timer_parser import TimerParser
    native()
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith(("src.app", "src.config", "src.data", "PyQt6", "sqlite3", "requests", "urllib"))
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Image mode entered an unrelated application service")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(MoneyParser, "parse", forbidden)
    monkeypatch.setattr(TimerParser, "parse", forbidden)
    # The existing native event loop legitimately creates a local socketpair.
    # Block external connections, not that unchanged loop implementation.
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.socket.connect_ex", forbidden)
    monkeypatch.setattr("src.utils.logging.setup_logging", forbidden)
    # Retain real imports in the core even if another test imported it earlier.
    importlib.reload(diagnostic)
    assert diagnostic.diagnose_image(image_path)["status"] == "ok"


@pytest.mark.parametrize("path", ["https://example.com/a.png", "file:///tmp/a.png", "//server/share/a.png", r"\\server\share\a.png", "-", "missing.png"])
def test_rejects_nonlocal_or_missing_input(diagnostic, path):
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose_image(path)


def test_rejects_directories_and_nonregular_files(diagnostic, tmp_path):
    directory = tmp_path / "directory.png"
    directory.mkdir()
    with pytest.raises(diagnostic.DiagnosticError, match="regular file"):
        diagnostic.diagnose_image(directory)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo.png"
        os.mkfifo(fifo)
        with pytest.raises(diagnostic.DiagnosticError, match="regular file"):
            diagnostic.diagnose_image(fifo)


@pytest.mark.parametrize("filename,format", [("wrong.gif", "PNG"), ("pretend.png", "GIF"), ("pretend.jpg", "BMP")])
def test_rejects_extensions_and_decoded_formats(diagnostic, tmp_path, filename, format):
    path = tmp_path / filename
    Image.new("RGB", (320, 180)).save(path, format=format)
    with pytest.raises(diagnostic.DiagnosticError, match="PNG|JPEG"):
        diagnostic.diagnose_image(path)


def test_rejects_malformed_and_truncated_input(diagnostic, image_path):
    for payload in (b"", b"not an image", image_path.read_bytes()[:100]):
        image_path.write_bytes(payload)
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.diagnose_image(image_path)


def test_rejects_encoded_oversize_before_opening_image(diagnostic, image_path, monkeypatch):
    with image_path.open("wb") as file:
        file.truncate(32 * 1024 * 1024 + 1)
    monkeypatch.setattr(diagnostic.Image, "open", lambda *args, **kwargs: pytest.fail("Oversize input reached decoder"))
    with pytest.raises(diagnostic.DiagnosticError, match="32 MiB"):
        diagnostic.diagnose_image(image_path)


@pytest.mark.parametrize("failure", [None, "decode", "backend"])
def test_input_handles_close_on_success_and_errors(diagnostic, image_path, native, monkeypatch, failure):
    native(error=RuntimeError("backend failure") if failure == "backend" else None)
    if failure == "decode":
        image_path.write_bytes(b"invalid image")
    files, streams = [], []
    original_file_open = Path.open
    original_image_open = Image.open

    def open_file(path, *args, **kwargs):
        file = original_file_open(path, *args, **kwargs)
        files.append(file)
        return file

    def open_image(stream, *args, **kwargs):
        streams.append(stream)
        return original_image_open(stream, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(Image, "open", open_image)
    if failure:
        with pytest.raises(diagnostic.DiagnosticError):
            diagnostic.diagnose_image(image_path)
    else:
        assert diagnostic.diagnose_image(image_path)["status"] == "ok"
    assert len(files) == len(streams) == 1
    assert files[0].closed
    assert streams[0].closed


def png_header(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


@pytest.mark.parametrize("width,height", [(99, 180), (320, 99), (8193, 100), (100, 8193), (5000, 4000)])
def test_dimension_bounds_reject_before_pixel_load(diagnostic, image_path, monkeypatch, width, height):
    image_path.write_bytes(png_header(width, height))
    monkeypatch.setattr(Image.Image, "convert", lambda *args, **kwargs: pytest.fail("Invalid dimensions reached pixel conversion"))
    with pytest.raises(diagnostic.DiagnosticError, match="dimensions|pixels"):
        diagnostic.diagnose_image(image_path)


@pytest.mark.parametrize("threshold", [30000, 10000])
def test_pillow_bomb_warning_and_error_are_input_errors(diagnostic, image_path, monkeypatch, threshold):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", threshold)
    with pytest.raises(diagnostic.DiagnosticError, match="safety|decompression|pixels"):
        diagnostic.diagnose_image(image_path)


def test_rejects_multiframe_png(diagnostic, image_path):
    first = Image.new("RGB", (320, 180), "gray")
    second = Image.new("RGB", (320, 180), "white")
    first.save(image_path, save_all=True, append_images=[second], duration=100, loop=0)
    with pytest.raises(diagnostic.DiagnosticError, match="single|animated|multiframe"):
        diagnostic.diagnose_image(image_path)


@pytest.mark.parametrize("format,suffix", [("JPEG", ".jpg"), ("PNG", ".png")])
def test_rejects_nontrivial_orientation(diagnostic, tmp_path, format, suffix):
    path = tmp_path / ("rotated" + suffix)
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (320, 180)).save(path, format=format, exif=exif)
    with pytest.raises(diagnostic.DiagnosticError, match="orientation"):
        diagnostic.diagnose_image(path)


@pytest.mark.parametrize("mode,format,suffix", [("RGBA", "PNG", ".png"), ("L", "PNG", ".PNG"), ("RGB", "JPEG", ".jpeg")])
def test_accepts_static_png_and_jpeg_modes(diagnostic, tmp_path, native, mode, format, suffix):
    native()
    path = tmp_path / ("input" + suffix)
    Image.new(mode, (320, 180), 70).save(path, format=format)
    assert diagnostic.diagnose_image(path)["input"]["format"] == format


def test_invalid_crop_is_an_error_before_recognition(diagnostic, image_path, native, monkeypatch):
    calls = native()
    monkeypatch.setattr(diagnostic, "ScreenRegions", lambda: ScreenRegions(mission_text=Region(0, 0, 0, 0)))
    with pytest.raises(diagnostic.DiagnosticError, match="crop|region"):
        diagnostic.diagnose_image(image_path)
    assert not calls


def test_default_never_falls_back_on_nonwindows(diagnostic, image_path, monkeypatch):
    monkeypatch.setattr(diagnostic.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostic.shutil, "which", lambda *args: pytest.fail("Automatic Tesseract fallback"))
    with pytest.raises(diagnostic.DiagnosticError, match="Windows"):
        diagnostic.diagnose_image(image_path)


def test_missing_native_backend_is_an_error(diagnostic, image_path, monkeypatch):
    monkeypatch.setattr(diagnostic.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "winocr", None)
    with pytest.raises(diagnostic.DiagnosticError, match="Windows OCR.*unavailable"):
        diagnostic.diagnose_image(image_path)


def test_native_failure_sentinel_is_not_successful_empty_ocr(diagnostic, image_path, native):
    calls = native(error=RuntimeError("private backend details"))
    with pytest.raises(diagnostic.DiagnosticError, match="Windows OCR.*failed") as error:
        diagnostic.diagnose_image(image_path)
    assert "private backend details" not in str(error.value)
    assert len(calls) == 1


def test_unknown_backend_is_rejected(diagnostic, image_path):
    with pytest.raises(diagnostic.DiagnosticError, match="backend"):
        diagnostic.diagnose_image(image_path, backend="auto")


TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


@pytest.fixture
def tesseract(monkeypatch, diagnostic):
    calls = []

    def configure(tsv=TSV_HEADER, *, languages="List of available languages (1):\neng\n", probe_error=None, recognition_error=None, failure_at=1):
        monkeypatch.setattr(diagnostic.shutil, "which", lambda binary: "/installed/tesseract")
        recognition_calls = 0

        def run(arguments, **kwargs):
            nonlocal recognition_calls
            calls.append((arguments, kwargs))
            if arguments[1] == "--list-langs":
                if probe_error:
                    raise probe_error
                return SimpleNamespace(stdout=languages)
            recognition_calls += 1
            if recognition_error and recognition_calls == failure_at:
                raise recognition_error
            return SimpleNamespace(stdout=tsv if isinstance(tsv, bytes) else tsv.encode("utf-8"))

        monkeypatch.setattr(diagnostic.subprocess, "run", run)
        return calls

    return configure


def test_explicit_tesseract_preserves_quotes_lines_and_real_confidence(diagnostic, image_path, tesseract, monkeypatch):
    tsv = TSV_HEADER + '5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t90\t"quoted\n' + '5\t1\t1\t1\t1\t2\t20\t0\t20\t10\t80\tword"\n' + '5\t1\t1\t1\t2\t1\t0\t10\t20\t10\t70\tnext\n'
    calls = tesseract(tsv)
    monkeypatch.setattr(OCREngine, "_check_winocr", lambda self: pytest.fail("Diagnostic Tesseract initialized native OCR"))
    report = diagnostic.diagnose_image(image_path, backend="tesseract")
    source = report["sources"]["mission_text"]
    assert source["text"] == '"quoted word"\nnext'
    assert source["ocr_confidence"] == pytest.approx(0.8)
    assert report["backend"]["name"] == "tesseract"
    assert report["backend"]["diagnostic_only"] is True
    assert "native Windows OCR" in " ".join(report["limits"])
    assert len(calls) == 7
    for arguments, options in calls[1:]:
        assert arguments == ["/installed/tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"]
        assert options["shell"] is False
        assert options["check"] is True
        assert options["timeout"] == 15
        with Image.open(io.BytesIO(options["input"])) as image:
            assert image.format == "PNG"


def test_tesseract_empty_ocr_is_valid(diagnostic, image_path, tesseract):
    tesseract()
    report = diagnostic.diagnose_image(image_path, backend="tesseract")
    assert report["status"] == "ok"
    assert all(source["status"] == "recognized_empty" for source in report["sources"].values())
    assert all(source["ocr_confidence"] == 0.0 for source in report["sources"].values())


def test_tesseract_requires_installed_binary(diagnostic, image_path, monkeypatch):
    monkeypatch.setattr(diagnostic.shutil, "which", lambda binary: None)
    with pytest.raises(diagnostic.DiagnosticError, match="Tesseract.*installed|Tesseract.*unavailable"):
        diagnostic.diagnose_image(image_path, backend="tesseract")


def test_tesseract_requires_english_data(diagnostic, image_path, tesseract):
    tesseract(languages="List of available languages (1):\nfra\n")
    with pytest.raises(diagnostic.DiagnosticError, match="eng|English"):
        diagnostic.diagnose_image(image_path, backend="tesseract")


@pytest.mark.parametrize("stage", ["probe_error", "recognition_error"])
@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("tesseract", 15), subprocess.CalledProcessError(1, "tesseract"), OSError("private details")])
def test_tesseract_subprocess_failure_is_a_safe_error(diagnostic, image_path, tesseract, stage, error):
    tesseract(**{stage: error})
    with pytest.raises(diagnostic.DiagnosticError, match="Tesseract") as caught:
        diagnostic.diagnose_image(image_path, backend="tesseract")
    assert "private details" not in str(caught.value)


@pytest.mark.parametrize("tsv", ["not tsv", TSV_HEADER + '5\t1\t1\t1\t1\t1\t0\t0\t20\t10\tnan\tword\n'])
def test_invalid_tesseract_output_never_returns_success(diagnostic, image_path, tesseract, tsv):
    tesseract(tsv)
    with pytest.raises(diagnostic.DiagnosticError):
        diagnostic.diagnose_image(image_path, backend="tesseract")


PRIVATE = "/private/image.png secret-command private-output private-stderr"
SUBPROCESS_FAILURES = [
    pytest.param(
        subprocess.TimeoutExpired(PRIVATE, 987, output=PRIVATE, stderr=PRIVATE),
        "timed out", id="timeout",
    ),
    pytest.param(
        subprocess.CalledProcessError(23, PRIVATE, output=PRIVATE, stderr=PRIVATE),
        "failed: child exited with status 23", id="nonzero-exit",
    ),
    pytest.param(OSError(PRIVATE), "failed: operating system error", id="os-error"),
    pytest.param(
        subprocess.SubprocessError(PRIVATE), "failed: subprocess error", id="subprocess-error",
    ),
]


@pytest.mark.parametrize("failure,description", SUBPROCESS_FAILURES)
@pytest.mark.parametrize("stage", ["probe_error", "recognition_error"])
def test_tesseract_failures_identify_stage_and_cause_without_private_details(
    diagnostic, image_path, tesseract, stage, failure, description,
):
    calls = tesseract(**{stage: failure})
    with pytest.raises(diagnostic.DiagnosticError) as caught:
        diagnostic.diagnose_image(image_path, backend="tesseract")
    probing = stage == "probe_error"
    label = "availability check" if probing else "recognition"
    if isinstance(failure, subprocess.TimeoutExpired):
        description += f" ({10 if probing else 15}-second limit)"
    expected = f"Tesseract {label} {description}"
    if not probing:
        expected += " (source: mission_text)"
    assert str(caught.value) == expected
    assert type(caught.value).__name__ == "DiagnosticError"
    assert len(calls) == (1 if probing else 2)
    assert PRIVATE not in str(caught.value)
    assert "987" not in str(caught.value)


@pytest.mark.parametrize("source", REQUEST_ORDER)
def test_tesseract_failure_names_the_actual_crop_and_stops_immediately(
    diagnostic, image_path, tesseract, source,
):
    failure_at = REQUEST_ORDER.index(source) + 1
    calls = tesseract(
        recognition_error=subprocess.TimeoutExpired(PRIVATE, 15), failure_at=failure_at,
    )
    with pytest.raises(diagnostic.DiagnosticError) as caught:
        diagnostic.diagnose_image(image_path, backend="tesseract")
    assert str(caught.value) == (
        f"Tesseract recognition timed out (15-second limit) (source: {source})"
    )
    assert len(calls) == failure_at + 1
    assert calls[0] == (
        ["/installed/tesseract", "--list-langs"],
        {"capture_output": True, "check": True, "timeout": 10, "shell": False, "text": True},
    )
    for arguments, options in calls[1:]:
        assert arguments == [
            "/installed/tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv",
        ]
        assert set(options) == {"input", "capture_output", "check", "timeout", "shell"}
        assert options["capture_output"] is options["check"] is True
        assert options["timeout"] == 15
        assert options["shell"] is False


@pytest.mark.parametrize("payload", [PRIVATE, b"\xff" + PRIVATE.encode(), TSV_HEADER + "5\t1\n"])
def test_invalid_tesseract_output_has_safe_recognition_source(
    diagnostic, image_path, tesseract, payload,
):
    calls = tesseract(payload)
    with pytest.raises(diagnostic.DiagnosticError) as caught:
        diagnostic.diagnose_image(image_path, backend="tesseract")
    assert str(caught.value) == (
        "Tesseract recognition failed: invalid TSV output (source: mission_text)"
    )
    assert len(calls) == 2


@pytest.mark.parametrize("failure_at", [1, 6])
def test_unexpected_native_backend_exception_is_safe_and_identifies_source(
    diagnostic, image_path, native, monkeypatch, failure_at,
):
    native()
    original = OCREngine.recognize_preprocessed
    calls = 0

    def recognize(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise RuntimeError(PRIVATE)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OCREngine, "recognize_preprocessed", recognize)
    with pytest.raises(diagnostic.DiagnosticError) as caught:
        diagnostic.diagnose_image(image_path)
    assert str(caught.value) == (
        "Windows OCR recognition failed: unexpected backend error "
        f"(source: {REQUEST_ORDER[failure_at - 1]})"
    )
    assert calls == failure_at


@pytest.mark.parametrize("failure_at", [1, 4, 6])
def test_actual_image_cli_serializes_safe_failure_without_partial_candidate(
    diagnostic, image_path, tesseract, capsys, failure_at,
):
    import test_capture

    calls = tesseract(
        recognition_error=subprocess.CalledProcessError(23, PRIVATE, output=PRIVATE, stderr=PRIVATE),
        failure_at=failure_at,
    )
    assert test_capture.main(["--image", str(image_path), "--backend", "tesseract"]) == 1
    output = capsys.readouterr()
    expected = {
        "schema_version": 1, "mode": "single_image_diagnostic", "status": "error",
        "backend": {"name": "tesseract"},
        "error": {
            "type": "DiagnosticError",
            "message": "Tesseract recognition failed: child exited with status 23 "
            f"(source: {REQUEST_ORDER[failure_at - 1]})",
        },
    }
    assert output.out == json.dumps(expected, allow_nan=False) + "\n"
    assert output.err == ""
    assert len(calls) == failure_at + 1


def test_actual_image_cli_probe_failure_does_not_invent_source(
    diagnostic, image_path, tesseract, capsys,
):
    import test_capture

    calls = tesseract(probe_error=subprocess.TimeoutExpired(PRIVATE, 987))
    assert test_capture.main(["--image", str(image_path), "--backend", "tesseract"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"] == {
        "type": "DiagnosticError",
        "message": "Tesseract availability check timed out (10-second limit)",
    }
    assert "source" not in output.out
    assert output.err == ""
    assert len(calls) == 1


def test_actual_image_cli_generic_input_error_is_unchanged(diagnostic, capsys):
    import test_capture

    assert test_capture.main(["--image", "https://example.com/private.png"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "schema_version": 1, "mode": "single_image_diagnostic", "status": "error",
        "backend": {"name": "windows"},
        "error": {
            "type": "DiagnosticError",
            "message": "Supply one existing regular local PNG or JPEG file",
        },
    }


@pytest.mark.parametrize("metadata", ["noninteger", "unreadable", "oversized"])
def test_tesseract_nonzero_exit_with_unusable_returncode_stays_safe(
    diagnostic, image_path, tesseract, metadata,
):
    class UnreadableExit(subprocess.CalledProcessError):
        def __getattribute__(self, name):
            if name == "returncode":
                raise RuntimeError(PRIVATE)
            return super().__getattribute__(name)

    if metadata == "unreadable":
        failure = UnreadableExit(23, PRIVATE)
    else:
        returncode = 10 ** 5000 if metadata == "oversized" else PRIVATE
        failure = subprocess.CalledProcessError(returncode, PRIVATE)
    tesseract(recognition_error=failure)
    with pytest.raises(diagnostic.DiagnosticError) as caught:
        diagnostic.diagnose_image(image_path, backend="tesseract")
    assert str(caught.value) == (
        "Tesseract recognition failed: child exited with nonzero status (source: mission_text)"
    )


@pytest.mark.parametrize("text", ["", "Mission: Sightseer"], ids=["empty", "candidate"])
def test_actual_image_cli_success_matches_complete_diagnostic_json(
    diagnostic, image_path, tesseract, capsys, record_property, text,
):
    import test_capture

    tsv = TSV_HEADER + (f"5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t90\t{text}\n" if text else "")
    tesseract(tsv)
    expected = diagnostic.diagnose_image(image_path, backend="tesseract")
    assert expected["status"] == "ok"
    assert set(expected) == {
        "schema_version", "mode", "status", "input", "backend", "context", "sources",
        "detector_candidate", "limits",
    }
    assert len(expected["sources"]) == 6
    if text:
        assert expected["detector_candidate"]["identity"] == {
            "status": "known_name", "candidates": ["Sightseer"], "mission_name": "Sightseer",
            "mission_type": "VIP_WORK", "heist_phase": "UNKNOWN", "outcome": None,
            "outcome_scope": None,
        }
    else:
        assert all(source["status"] == "recognized_empty" for source in expected["sources"].values())
    tesseract(tsv)
    assert test_capture.main(["--image", str(image_path), "--backend", "tesseract"]) == 0
    output = capsys.readouterr()
    assert output.out == json.dumps(expected, allow_nan=False) + "\n"
    assert output.err == ""
    record_property("complete_cli_json", output.out)
