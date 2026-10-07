"""Fake-backed checks for the interactive capture script and image dispatch."""

import builtins
import json
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

import test_capture as capture_checks


IMAGE = SimpleNamespace(shape=(100, 200, 3))


def _module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def live(monkeypatch):
    """Replace only hardware/OCR/parser boundaries; run real script control flow."""
    state = SimpleNamespace(
        captures=[],
        full_image=IMAGE,
        money_images=[IMAGE] * 5,
        ocr_results=[SimpleNamespace(text="$100", confidence=None, words=[])] * 5,
        available=True,
        parsed=[],
        ocr_calls=0,
        parse_error=None,
        ocr_init_error=None,
    )

    def value_or_raise(value):
        if isinstance(value, Exception):
            raise value
        return value

    class Capture:
        resolution = (1920, 1080)
        scale_factor = 1.0

        def __init__(self):
            self.closed = 0
            state.captures.append(self)

        def capture_full_screen(self):
            return value_or_raise(state.full_image)

        def capture_money_display(self):
            return value_or_raise(state.money_images.pop(0))

        def close(self):
            self.closed += 1

    class OCR:
        def __init__(self):
            if state.ocr_init_error:
                raise state.ocr_init_error

        @property
        def is_available(self):
            return state.available

        def recognize_preprocessed(self, image, *, invert, scale):
            assert image is IMAGE
            assert (invert, scale) == (True, 2.0)
            state.ocr_calls += 1
            return value_or_raise(state.ocr_results.pop(0))

    class Parser:
        def parse(self, text):
            if state.parse_error:
                raise state.parse_error
            state.parsed.append(text)
            return SimpleNamespace(has_value=bool(text), display_value=100)

    _module(monkeypatch, "src.capture.screen_capture", ScreenCapture=Capture)
    _module(monkeypatch, "src.capture.regions", ScreenRegions=object)
    _module(monkeypatch, "src.detection.ocr_engine", OCREngine=OCR)
    _module(monkeypatch, "src.detection.parsers.money_parser", MoneyParser=Parser)
    monkeypatch.setattr(capture_checks.time, "sleep", lambda _: None)
    return state


@pytest.mark.parametrize("stage", ["test_screen_capture", "test_ocr", "test_full_pipeline"])
def test_live_success_closes_capture_once(live, stage):
    assert getattr(capture_checks, stage)() is True
    assert [capture.closed for capture in live.captures] == [1]


@pytest.mark.parametrize("missing", ["full_image", "money_images"])
def test_screen_requires_both_captures_and_closes_on_failure(live, missing, capsys):
    setattr(live, missing, None if missing == "full_image" else [None])
    assert capture_checks.test_screen_capture() is False
    assert live.captures[0].closed == 1
    assert "test PASSED" not in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["test_ocr", "test_full_pipeline"])
def test_unavailable_native_ocr_is_not_a_pass(live, stage, capsys):
    live.available = False
    assert getattr(capture_checks, stage)() is False
    assert all(capture.closed == 1 for capture in live.captures)
    assert live.ocr_calls == 0
    assert "test PASSED" not in capsys.readouterr().out


def test_ocr_missing_capture_is_not_a_pass(live, capsys):
    live.money_images = [None]
    assert capture_checks.test_ocr() is False
    assert live.ocr_calls == 0
    assert live.captures[0].closed == 1
    assert "test PASSED" not in capsys.readouterr().out


def test_native_unknown_confidence_is_displayed_without_fabrication(live, capsys):
    assert capture_checks.test_ocr() is True
    output = capsys.readouterr().out.lower()
    assert "confidence: unknown" in output
    assert "confidence: 0" not in output
    assert live.captures[0].closed == 1


@pytest.mark.parametrize("stage", ["test_ocr", "test_full_pipeline"])
def test_successful_empty_native_ocr_is_valid_execution(live, stage):
    live.ocr_results = [SimpleNamespace(text="", confidence=None, words=[])] * 5
    assert getattr(capture_checks, stage)() is True
    assert live.ocr_calls == (5 if stage == "test_full_pipeline" else 1)
    assert live.captures[0].closed == 1


@pytest.mark.parametrize("stage", ["test_ocr", "test_full_pipeline"])
def test_native_failure_sentinel_is_not_a_pass_or_parsed(live, stage, capsys):
    live.ocr_results = [SimpleNamespace(text="", confidence=0.0, words=[])] * 5
    assert getattr(capture_checks, stage)() is False
    assert live.parsed == []
    assert live.captures[0].closed == 1
    assert "test PASSED" not in capsys.readouterr().out


@pytest.mark.parametrize("failed_cycles", [1, 5])
def test_pipeline_requires_all_five_capture_cycles(live, failed_cycles, capsys):
    live.money_images = [None] * failed_cycles + [IMAGE] * (5 - failed_cycles)
    assert capture_checks.test_full_pipeline() is False
    assert live.ocr_calls == 5 - failed_cycles
    assert len(live.parsed) == 5 - failed_cycles
    assert live.captures[0].closed == 1
    output = capsys.readouterr().out
    assert f"Completed {5 - failed_cycles}/5 cycles" in output
    assert "test PASSED" not in output


def test_pipeline_does_not_pass_after_one_native_failure(live, capsys):
    live.ocr_results[2] = SimpleNamespace(text="", confidence=0.0, words=[])
    assert capture_checks.test_full_pipeline() is False
    assert live.ocr_calls == 5
    assert len(live.parsed) == 4
    assert live.captures[0].closed == 1
    assert "Completed 4/5 cycles" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stage, boundary",
    [
        ("test_screen_capture", "full_image"),
        ("test_screen_capture", "money_images"),
        ("test_ocr", "money_images"),
        ("test_ocr", "ocr_results"),
        ("test_full_pipeline", "money_images"),
        ("test_full_pipeline", "ocr_results"),
        ("test_full_pipeline", "parse_error"),
        ("test_full_pipeline", "ocr_init_error"),
    ],
)
def test_live_exception_closes_created_capture(live, stage, boundary, capsys):
    failure = RuntimeError("test boundary failed")
    setattr(live, boundary, [failure] if boundary.endswith("s") else failure)
    assert getattr(capture_checks, stage)() is False
    assert [capture.closed for capture in live.captures] == [1]
    assert "test PASSED" not in capsys.readouterr().out


@pytest.fixture
def forbid_live_mode(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("image mode reached a live check or app/settings import")

    for name in ("test_screen_capture", "test_ocr", "test_money_parser", "test_full_pipeline"):
        monkeypatch.setattr(capture_checks, name, forbidden)
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith(("src.capture", "src.app", "src.config", "src.utils.logging")):
            forbidden()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


@pytest.mark.parametrize("backend", [None, "windows", "tesseract"])
def test_image_cli_dispatches_explicit_backend_and_keeps_stdout_json(
    monkeypatch, capsys, forbid_live_mode, backend
):
    calls = []

    def diagnose(path, *, backend):
        print("dependency chatter")
        calls.append((path, backend))
        return {"status": "ok", "ocr_confidence": None}

    _module(monkeypatch, "src.detection.screenshot_diagnostic", diagnose_image=diagnose)
    args = ["--image", "example.png"]
    if backend:
        args += ["--backend", backend]
    assert capture_checks.main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ok", "ocr_confidence": None}
    assert "dependency chatter" in output.err
    assert calls == [("example.png", backend or "windows")]


@pytest.mark.parametrize("failure", [ValueError("invalid image"), RuntimeError("unavailable OCR")])
def test_image_cli_error_is_structured_and_nonzero(
    monkeypatch, capsys, forbid_live_mode, failure
):
    def diagnose(path, *, backend):
        print("failure detail")
        raise failure

    _module(monkeypatch, "src.detection.screenshot_diagnostic", diagnose_image=diagnose)
    assert capture_checks.main(["--image", "example.jpg"]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "error"
    assert report["error"]["message"] == str(failure)
    assert report["backend"]["name"] == "windows"
    assert "failure detail" in output.err


def test_image_cli_import_failure_is_structured(monkeypatch, capsys, forbid_live_mode):
    monkeypatch.setitem(sys.modules, "src.detection.screenshot_diagnostic", None)
    assert capture_checks.main(["--image", "example.png"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_image_cli_redirects_import_chatter(monkeypatch, capsys, forbid_live_mode):
    _module(
        monkeypatch,
        "src.detection.screenshot_diagnostic",
        diagnose_image=lambda *args, **kwargs: {"status": "ok"},
    )
    real_import = builtins.__import__

    def noisy_import(name, *args, **kwargs):
        if name == "src.detection.screenshot_diagnostic":
            print("dependency import chatter")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", noisy_import)
    assert capture_checks.main(["--image", "example.png"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ok"}
    assert "dependency import chatter" in output.err


def test_script_import_is_lazy(monkeypatch, capsys):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "src" or name.startswith("src."):
            pytest.fail(f"script import eagerly imported {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    runpy.run_path(capture_checks.__file__, run_name="capture_import_check")
    assert capsys.readouterr().out == ""


def test_script_entrypoint_uses_image_arguments(monkeypatch, capsys, forbid_live_mode):
    calls = []

    def diagnose(path, *, backend):
        calls.append((path, backend))
        return {"status": "ok"}

    _module(monkeypatch, "src.detection.screenshot_diagnostic", diagnose_image=diagnose)
    monkeypatch.setattr(sys, "argv", ["test_capture.py", "--image", "example.png"])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(capture_checks.__file__, run_name="__main__")
    assert error.value.code == 0
    assert calls == [("example.png", "windows")]
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}


def test_image_cli_rejects_nonfinite_json(monkeypatch, capsys, forbid_live_mode):
    _module(
        monkeypatch,
        "src.detection.screenshot_diagnostic",
        diagnose_image=lambda *args, **kwargs: {"heuristic_score": float("nan")},
    )
    assert capture_checks.main(["--image", "example.png"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "error"
    assert '"heuristic_score"' not in output


@pytest.mark.parametrize(
    "args",
    [
        ["--backend", "windows"],
        ["--backend", "tesseract"],
        ["--image", "example.png", "--backend", "automatic"],
    ],
)
def test_invalid_cli_options_do_not_start_live_checks(forbid_live_mode, args, capsys):
    with pytest.raises(SystemExit) as error:
        capture_checks.main(args)
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "stage_results, expected", [([True] * 4, 0), ([True, False, True, True], 1)]
)
def test_no_argument_cli_retains_four_live_checks(monkeypatch, capsys, stage_results, expected):
    calls = []
    stages = ("test_screen_capture", "test_ocr", "test_money_parser", "test_full_pipeline")
    for name, result in zip(stages, stage_results):

        def run(stage=name, passed=result):
            calls.append(stage)
            return passed

        monkeypatch.setattr(capture_checks, name, run)
    assert capture_checks.main([]) == expected
    assert calls == list(stages)
    assert "Test Results:" in capsys.readouterr().out
