"""Opt-in controlled image OCR integration; these are not gameplay screenshots.

Run with GTA_RUN_OCR_TESTS=1. Requires an already installed Tesseract with eng,
DejaVuSans.ttf, Pillow, NumPy and OpenCV; nothing is installed or downloaded.
GTA_OCR_TEST_FONT may point to a local copy of DejaVuSans.ttf.

Rendered text -> production ScreenRegions coordinates -> production OCREngine
preprocessing -> real Tesseract CLI -> real StateDetector/capture loop/tracker
and disposable SQLite. The test-only Tesseract adapter does not add a production
fallback or validate native Windows OCR, actual HUD placement, gameplay accuracy,
or native Qt. Exact transcription expectations concern these controlled fixtures.
"""

import os

import pytest

if os.environ.get("GTA_RUN_OCR_TESTS") != "1":
    pytest.skip("Set GTA_RUN_OCR_TESTS=1 for synthetic-image Tesseract integration", allow_module_level=True)

# Keep optional tooling and production imports below the opt-in gate.
import csv
import io
import shutil
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

np = pytest.importorskip("numpy", reason="Synthetic image OCR requires NumPy")
pytest.importorskip("cv2", reason="Production OCR preprocessing requires OpenCV")
Image = pytest.importorskip("PIL.Image", reason="Synthetic image OCR requires Pillow")
ImageDraw = pytest.importorskip("PIL.ImageDraw", reason="Synthetic image OCR requires Pillow")
ImageFont = pytest.importorskip("PIL.ImageFont", reason="Synthetic image OCR requires Pillow")

TESSERACT = shutil.which("tesseract")
if TESSERACT is None:
    pytest.skip("Synthetic image OCR requires Tesseract on PATH", allow_module_level=True)
try:
    _languages = subprocess.run(
        [TESSERACT, "--list-langs"], capture_output=True, check=True,
        timeout=10, shell=False, text=True,
    )
except (OSError, subprocess.SubprocessError) as error:
    pytest.skip(f"Tesseract is unavailable: {error}", allow_module_level=True)
if "eng" not in _languages.stdout.splitlines():
    pytest.skip("Synthetic image OCR requires installed Tesseract eng data", allow_module_level=True)
try:
    _font = ImageFont.truetype(os.environ.get("GTA_OCR_TEST_FONT", "DejaVuSans.ttf"), 24)
except OSError as error:
    pytest.skip(f"Synthetic image OCR requires DejaVuSans.ttf: {error}", allow_module_level=True)
FONT_PATH = Path(_font.path)

from src.app import AppState
from src.capture.regions import ScreenRegions
from src.detection.ocr_engine import OCREngine, OCRResult
from src.detection.parsers.mission_parser import MissionType
from src.detection.state_detector import StateDetector
from src.game.activities import ActivityType
from src.game.state_machine import GameState, GameStateMachine
from src.utils.performance import PerformanceMonitor
from tests.test_app_accounting import app as app


RESOLUTIONS = ((1280, 720), (1920, 1080), (2560, 1440))
REGIONS = ScreenRegions()


class TesseractDiagnostic(OCREngine):
    """Replace only the OCR backend; inherit production preprocessing unchanged.

    Line keys and actual Tesseract word confidence belong to this diagnostic
    adapter. Windows OCR does not expose these confidence scores.
    """

    def __init__(self):
        # Avoid importing/initializing winocr on this explicitly separate backend.
        self._winocr_available = True
        self.results = []

    def recognize(self, image):
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image[:, :, ::-1] if image.ndim == 3 else image)
        png = io.BytesIO()
        image.save(png, format="PNG")
        process = subprocess.run(
            [TESSERACT, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
            input=png.getvalue(), capture_output=True, check=True,
            timeout=15, shell=False,
        )
        # TSV quotes are literal OCR text, not CSV quote delimiters.
        rows = csv.DictReader(
            io.StringIO(process.stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE,
        )
        lines = OrderedDict()
        words = []
        for row in rows:
            if row["level"] != "5" or not row["text"].strip():
                continue
            key = tuple(int(row[column]) for column in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(row["text"])
            words.append({
                "text": row["text"],
                "confidence": float(row["conf"]) / 100.0,
                "line_key": key,
                "bounds": {name: int(row[column]) for name, column in (
                    ("x", "left"), ("y", "top"), ("width", "width"), ("height", "height"),
                )},
            })
        result = OCRResult(
            text="\n".join(" ".join(line) for line in lines.values()),
            confidence=sum(word["confidence"] for word in words) / len(words) if words else 0.0,
            words=words,
        )
        self.results.append(result)
        return result


def synthetic_frame(resolution, mission_text="", center_text=""):
    """Put controlled text inside current production crop bounds, not a real HUD."""
    width, height = resolution
    frame = Image.new("RGB", resolution, (70, 70, 70))
    draw = ImageDraw.Draw(frame)
    scale = height / 1080
    font = ImageFont.truetype(str(FONT_PATH), round(24 * scale))
    for region, text in ((REGIONS.mission_text, mission_text), (REGIONS.center_prompt, center_text)):
        if not text:
            continue
        left, top, right, bottom = region.to_absolute(width, height)
        position = (left + round(12 * scale), top + round(3 * scale))
        bounds = draw.multiline_textbbox(position, text, font=font, spacing=round(7 * scale))
        assert left <= bounds[0] < bounds[2] <= right
        assert top <= bounds[1] < bounds[3] <= bottom
        draw.multiline_text(position, text, font=font, fill="white", spacing=round(7 * scale))
    return np.asarray(frame)[:, :, ::-1].copy()


class SyntheticFrameCapture:
    """Supply full BGR frames, slicing every requested production region."""

    def __init__(self, frames, stop_event):
        self.regions = REGIONS
        self.frames = iter(frames)
        self.stop_event = stop_event
        self.requested_regions = []
        self.rates = []

    def capture_multiple_regions(self, regions):
        try:
            frame = next(self.frames)
        except StopIteration:
            # Bound the real loop even if a cycle failed before its callback.
            self.stop_event.set()
            raise AssertionError("Capture loop consumed all synthetic frames before completing")
        height, width = frame.shape[:2]
        self.requested_regions.append(tuple(regions))
        images = []
        for region in regions:
            left, top, right, bottom = region.to_absolute(width, height)
            images.append(frame[top:bottom, left:right].copy())
        return images

    def set_capture_rate(self, rate):
        self.rates.append(rate)


def run_frames(app, monkeypatch, frames):
    """Run actual capture-loop cycles and retain database/cooldown checkpoints."""
    backend = TesseractDiagnostic()
    capture = SyntheticFrameCapture(frames, app._stop_event)
    app._capture = capture
    app._ocr = backend
    app._state_detector = StateDetector(ocr_engine=backend)
    app._state_machine = GameStateMachine()
    app._perf_monitor = PerformanceMonitor()
    app._state = AppState.RUNNING
    # No native screen resources exist; retain the disposable repository for assertions.
    monkeypatch.setattr(app, "_finish_stop", lambda worker: None)
    checkpoints = []

    def captured(result):
        checkpoints.append((
            result,
            app._repository.export_session_data(app._data.db_session_id)["activities"],
            app.cooldown_tracker.get_cooldown("hostile_takeover"),
        ))
        if len(checkpoints) == len(frames):
            app._stop_event.set()

    app.on_capture(captured)
    app._capture_loop()
    assert len(checkpoints) == len(frames), "A real capture cycle failed; inspect captured logs"
    assert app._data.total_captures == len(frames)
    assert capture.requested_regions == [(
        REGIONS.full_screen, REGIONS.money_display, REGIONS.mission_text,
        REGIONS.center_prompt, REGIONS.timer_bottom_right,
    )] * len(frames)
    return checkpoints, backend


@dataclass(frozen=True)
class MissionCase:
    label: str
    mission_text: str
    center_text: str
    status: str
    candidates: tuple
    mission_type: MissionType
    activity_type: ActivityType
    activity_name: str
    state: GameState = GameState.MISSION_ACTIVE


MISSION_CASES = (
    MissionCase("hostile-takeover", "Mission: Hostile Takeover\nSteal the briefcase.", "",
                "known_name", ("Hostile Takeover",), MissionType.VIP_WORK,
                ActivityType.VIP_WORK, "Hostile Takeover"),
    MissionCase("wrapped-executive-search", "Executive\nSearch", "",
                "known_name", ("Executive Search",), MissionType.VIP_WORK,
                ActivityType.VIP_WORK, "Executive Search"),
    MissionCase("center-only-headhunter", "", "Mission: Headhunter\nEliminate the targets.",
                "known_name", ("Headhunter",), MissionType.VIP_WORK,
                ActivityType.VIP_WORK, "Headhunter"),
    MissionCase("security-with-delivery", "Recover Valuables\nDeliver the goods", "",
                "known_name", ("Recover Valuables",), MissionType.SECURITY_CONTRACT,
                ActivityType.SECURITY_CONTRACT, "Recover Valuables"),
    MissionCase("customer-vehicle", "Deliver the vehicle", "Customer vehicle",
                "type_only", ("AUTO_SHOP_DELIVERY",), MissionType.AUTO_SHOP_DELIVERY,
                ActivityType.AUTO_SHOP_DELIVERY, "Deliver the vehicle"),
    MissionCase("generic-sell-objective", "Deliver the goods", "",
                "unknown", (), MissionType.UNKNOWN, ActivityType.SELL_MISSION,
                "Deliver the goods", GameState.SELLING),
    MissionCase("headhunter-with-delivery", "Headhunter\nDeliver the goods", "",
                "known_name", ("Headhunter",), MissionType.VIP_WORK,
                ActivityType.VIP_WORK, "Headhunter"),
    MissionCase("literal-quoted-sightseer", 'Mission: "Sightseer"\nCollect the packages.', "",
                "known_name", ("Sightseer",), MissionType.VIP_WORK,
                ActivityType.VIP_WORK, "Sightseer"),
)


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("case", MISSION_CASES, ids=lambda case: case.label)
def test_rendered_mission_identity_reaches_capture_and_tracker(app, monkeypatch, resolution, case):
    checkpoints, backend = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, case.mission_text, case.center_text),
    ])
    observed, rows, cooldown = checkpoints[0]
    # These exact strings are an assertion about the controlled font/fixture,
    # not a transcription guarantee for other OCR engines or gameplay images.
    assert observed.mission_text == case.mission_text
    assert observed.objective_text == case.center_text
    assert observed.game_state == case.state
    assert observed.mission is not None
    assert observed.mission.identity_status == case.status
    assert observed.mission.candidates == case.candidates
    assert observed.mission.mission_type == case.mission_type
    assert observed.mission.raw_text == "\n".join(filter(None, (case.mission_text, case.center_text)))
    assert observed.activity_type == case.activity_type
    assert observed.activity_name == case.activity_name
    assert observed.activity_identity_status == case.status
    current = app._activity_tracker.current_activity
    assert current is not None
    assert (current.activity_type, current.name) == (case.activity_type, case.activity_name)
    assert rows == [] and cooldown is None
    assert app._activity_tracker.completed_activities == []
    for result, text in zip(backend.results[:2], (case.mission_text, case.center_text)):
        assert result.get_text_lines() == text.splitlines()
        assert len({word["line_key"] for word in result.words}) == len(text.splitlines())
        if result.words:
            assert all(0 <= word["confidence"] <= 1 for word in result.words)
            assert result.confidence == pytest.approx(
                sum(word["confidence"] for word in result.words) / len(result.words)
            )
        else:
            assert result.confidence == 0.0


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
@pytest.mark.parametrize("text,status,candidates", (
    ("", "unknown", ()),
    ("Browse the latest offers.", "unknown", ()),
    ("Mission: Moonlight Courier", "unknown", ()),
    ("Headhunter\nSightseer", "ambiguous", ("Headhunter", "Sightseer")),
), ids=("blank", "irrelevant", "unrecognized-name", "ambiguous-names"))
def test_rendered_uncertainty_does_not_invent_activity(app, monkeypatch, resolution, text, status, candidates):
    checkpoints, _backend = run_frames(app, monkeypatch, [synthetic_frame(resolution, text)])
    observed, rows, cooldown = checkpoints[0]
    assert observed.mission_text == text
    assert observed.objective_text == ""
    if observed.mission is None:
        assert text == ""
    else:
        assert observed.mission.identity_status == status
        assert observed.mission.candidates == candidates
        assert observed.mission.mission_type == MissionType.UNKNOWN
        assert observed.mission.mission_name == ""
        assert observed.mission.raw_text == text
    assert observed.activity_name == ""
    assert observed.activity_type is None
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert rows == [] and cooldown is None


@pytest.mark.parametrize("resolution", RESOLUTIONS, ids=("720p", "1080p", "1440p"))
def test_rendered_explicit_pass_completes_once_in_sqlite_and_starts_cooldown(app, monkeypatch, resolution):
    completed = []
    app.on_mission_complete(completed.append)
    checkpoints, _backend = run_frames(app, monkeypatch, [
        synthetic_frame(resolution, "Hostile Takeover"),
        synthetic_frame(resolution, "Bonus available\nCollect the bonus"),
        synthetic_frame(resolution, center_text="MISSION PASSED"),
        synthetic_frame(resolution, center_text="MISSION PASSED"),
    ])
    started, ordinary_objective, passed, repeated = checkpoints
    assert started[0].mission_text == "Hostile Takeover"
    assert ordinary_objective[0].mission_text == "Bonus available\nCollect the bonus"
    for observed, rows, cooldown in (started, ordinary_objective):
        assert observed.game_state == GameState.MISSION_ACTIVE
        assert observed.activity_name == "Hostile Takeover"
        assert observed.activity_type == ActivityType.VIP_WORK
        assert observed.activity_identity_status == "known_name"
        assert rows == [] and cooldown is None
    for observed, rows, cooldown in (passed, repeated):
        assert observed.objective_text == "MISSION PASSED"
        assert observed.game_state == GameState.MISSION_COMPLETE
        assert len(rows) == 1
        assert rows[0]["name"] == "Hostile Takeover"
        assert rows[0]["type"] == "VIP_WORK"
        assert rows[0]["success"] is True
        assert rows[0]["earnings"] == 0
        assert cooldown is not None
        assert cooldown.activity_name == "hostile_takeover"
        assert cooldown.duration_seconds == 300
    assert passed[1] == repeated[1]
    assert passed[2].started_at == repeated[2].started_at
    assert len(completed) == len(app._activity_tracker.completed_activities) == 1
    assert app._activity_tracker.current_activity is None
    assert app._data.mission_start_time is None
    assert app.session_stats.activities_completed == 1
