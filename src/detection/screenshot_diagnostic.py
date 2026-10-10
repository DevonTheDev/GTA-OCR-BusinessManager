"""Bounded local single-image evidence, upstream of all application activity.

Tesseract is an explicit diagnostic backend only. Default Windows recognition
and StateDetector preprocessing, priorities, and source gates remain unchanged.
"""

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import warnings

import numpy as np
from PIL import Image

from ..capture.regions import ScreenRegions
from .ocr_engine import OCREngine, OCRResult
from .state_detector import StateDetector


MAX_ENCODED_BYTES = 32 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 16_777_216
MIN_DIMENSION = 100
SOURCES = (
    "mission_text", "center_prompt", "mission_banner", "bottom_objective",
    "result_header", "vip_status",
)


class DiagnosticError(Exception):
    """A safe explanation of an input or diagnostic execution failure."""


class _OCRDiagnosticError(DiagnosticError):
    """Carry a controlled explanation until the requesting crop is known."""

    def __init__(self, message, *, source=None):
        self.message = message
        super().__init__(message if source is None else f"{message} (source: {source})")


def _tesseract_process_error(error, *, stage, timeout):
    """Classify child failures without exposing commands or captured output."""
    if isinstance(error, subprocess.TimeoutExpired):
        detail = f"timed out ({timeout}-second limit)"
    elif isinstance(error, subprocess.CalledProcessError):
        # Even unusable metadata must not hide a known nonzero child exit.
        try:
            returncode = error.returncode
        except Exception:
            returncode = None
        # Bound formatting to OS exit/signal codes, even for malformed exceptions.
        status = "nonzero status"
        if type(returncode) is int and -(2**31) <= returncode < 2**32:
            status = f"status {returncode}"
        detail = f"failed: child exited with {status}"
    elif isinstance(error, OSError):
        detail = "failed: operating system error"
    else:
        detail = "failed: subprocess error"
    return _OCRDiagnosticError(f"Tesseract {stage} {detail}")


def _load_image(path):
    """Read bounded bytes and validate the stored canvas before loading pixels."""
    try:
        supplied = os.fspath(path)
        if (not isinstance(supplied, str) or not supplied or supplied == "-"
                or "://" in supplied or supplied.startswith(("//", "\\\\"))):
            raise DiagnosticError("Supply one existing regular local PNG or JPEG file")
        path = Path(supplied)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise DiagnosticError("Input must have a PNG or JPEG filename extension")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise DiagnosticError("Input must be an existing regular file")
        if metadata.st_size > MAX_ENCODED_BYTES:
            raise DiagnosticError("Input exceeds the 32 MiB encoded size limit")
        with path.open("rb") as file:
            # Recheck the opened object, and bound the read if the file grows.
            opened = os.fstat(file.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise DiagnosticError("Input must be an existing regular file")
            if opened.st_size > MAX_ENCODED_BYTES:
                raise DiagnosticError("Input exceeds the 32 MiB encoded size limit")
            encoded = file.read(MAX_ENCODED_BYTES + 1)
        if len(encoded) > MAX_ENCODED_BYTES:
            raise DiagnosticError("Input exceeds the 32 MiB encoded size limit")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with io.BytesIO(encoded) as stream, Image.open(stream) as image:
                if image.format not in ("PNG", "JPEG"):
                    raise DiagnosticError("Decoded input must actually be PNG or JPEG")
                width, height = image.size
                if not (MIN_DIMENSION <= width <= MAX_DIMENSION
                        and MIN_DIMENSION <= height <= MAX_DIMENSION):
                    raise DiagnosticError("Image dimensions must each be between 100 and 8192 pixels")
                if width * height > MAX_PIXELS:
                    raise DiagnosticError("Image exceeds the 16,777,216 decoded pixels limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise DiagnosticError("Only a single static image is supported; animated or multiframe PNG is unsupported")
                if image.getexif().get(274, 1) not in (None, 1):
                    raise DiagnosticError("Nontrivial image orientation metadata is unsupported")
                image_format = image.format
                with image.convert("RGB") as rgb:
                    frame = np.asarray(rgb)[:, :, ::-1].copy()
        return frame, {
            "name": path.name, "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded), "format": image_format, "width": width, "height": height,
        }
    except DiagnosticError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DiagnosticError("Image exceeds Pillow decompression safety limits") from None
    except (OSError, ValueError, TypeError, SyntaxError):
        raise DiagnosticError("Input could not be read as a complete local PNG or JPEG image") from None


class _TesseractDiagnostic(OCREngine):
    """Inherit production preprocessing; replace only explicit OCR recognition."""

    def __init__(self):
        self._binary = shutil.which("tesseract")
        if self._binary is None:
            raise DiagnosticError("Tesseract is unavailable; an installed binary is required")
        try:
            probe = subprocess.run(
                [self._binary, "--list-langs"], capture_output=True, check=True,
                timeout=10, shell=False, text=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise _tesseract_process_error(error, stage="availability check", timeout=10) from None
        if "eng" not in probe.stdout.splitlines():
            raise DiagnosticError("Tesseract requires installed English (eng) language data")
        # Do not import or initialize winocr for this separate diagnostic backend.
        self._winocr_available = True

    def recognize(self, image):
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image[:, :, ::-1] if image.ndim == 3 else image)
        with io.BytesIO() as png:
            image.save(png, format="PNG")
            try:
                process = subprocess.run(
                    [self._binary, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
                    input=png.getvalue(), capture_output=True, check=True, timeout=15, shell=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise _tesseract_process_error(error, stage="recognition", timeout=15) from None
        try:
            # Quotes are literal OCR characters, not CSV delimiters. Dict order
            # retains the observed line order; geometry is deliberately omitted.
            rows = csv.DictReader(io.StringIO(process.stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
            required = {"level", "text", "conf", "page_num", "block_num", "par_num", "line_num"}
            if not required.issubset(rows.fieldnames or ()):
                raise ValueError("Missing TSV columns")
            lines, words = {}, []
            for row in rows:
                if row["level"] != "5" or not row["text"].strip():
                    continue
                key = tuple(int(row[column]) for column in ("page_num", "block_num", "par_num", "line_num"))
                confidence = float(row["conf"]) / 100.0
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("Invalid word confidence")
                lines.setdefault(key, []).append(row["text"])
                words.append({"text": row["text"], "confidence": confidence})
            return OCRResult(
                text="\n".join(" ".join(line) for line in lines.values()),
                confidence=sum(word["confidence"] for word in words) / len(words) if words else 0.0,
                words=words,
            )
        except (ValueError, TypeError, KeyError, AttributeError, csv.Error):
            raise _OCRDiagnosticError("Tesseract recognition failed: invalid TSV output") from None


class _RecordingOCR:
    """Observe detector requests without creating additional OCR observations."""

    def __init__(self, engine, backend, crops, sources):
        self._engine = engine
        self._backend = backend
        self._names = {id(crop): name for name, crop in crops.items()}
        self._sources = sources

    @property
    def is_available(self):
        return self._engine.is_available

    def recognize_preprocessed(self, image, invert=True, scale=2.0, *, threshold=True):
        name = self._names[id(image)]
        source = self._sources[name]
        source["preprocessing"] = {"threshold": threshold, "invert": invert, "scale": scale}
        source["status"] = "failed"
        try:
            result = self._engine.recognize_preprocessed(image, invert=invert, scale=scale, threshold=threshold)
        except _OCRDiagnosticError as error:
            raise _OCRDiagnosticError(error.message, source=name) from None
        except Exception:
            backend = "Windows OCR" if self._backend == "windows" else "Tesseract"
            raise _OCRDiagnosticError(
                f"{backend} recognition failed: unexpected backend error", source=name,
            ) from None
        # Production native failure is distinct from a successful empty native
        # reading (null confidence). Tesseract legitimately uses 0 for no words.
        if (self._backend == "windows" and result.text == ""
                and result.confidence == 0.0 and not result.words):
            raise _OCRDiagnosticError(
                "Windows OCR recognition failed: native backend reported failure", source=name,
            )
        source.update(
            status="recognized_empty" if result.is_empty else "recognized",
            text=result.text, ocr_confidence=result.confidence,
        )
        return result


def diagnose_image(path, *, backend="windows") -> dict:
    """Inspect one bounded local PNG/JPEG; return evidence, never activity state.

    Raises DiagnosticError for invalid input, unavailable OCR, or failed OCR.
    Empty recognized text and unknown/ambiguous detector candidates are valid.
    """
    if backend not in ("windows", "tesseract"):
        raise DiagnosticError("OCR backend must be 'windows' or explicit diagnostic 'tesseract'")
    try:
        frame, input_metadata = _load_image(path)
        height, width = frame.shape[:2]
        regions = ScreenRegions()
        crops, sources = {}, {}
        for name in SOURCES:
            left, top, right, bottom = getattr(regions, name).to_absolute(width, height)
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise DiagnosticError(f"Default region {name} produces an invalid or empty crop")
            crops[name] = frame[top:bottom, left:right].copy()
            sources[name] = {"box_ltrb": [left, top, right, bottom], "status": "not_requested_by_detector"}
        if backend == "windows":
            if platform.system() != "Windows":
                raise DiagnosticError("Native Windows OCR is unavailable on this platform")
            engine = OCREngine()
            if not engine.is_available:
                raise DiagnosticError("Native Windows OCR is unavailable")
        else:
            engine = _TesseractDiagnostic()
        observer = _RecordingOCR(engine, backend, crops, sources)
        detector = StateDetector(ocr_engine=observer)
        result = detector.detect(
            frame, mission_text_image=crops["mission_text"], center_text_image=crops["center_prompt"],
            mission_banner_image=crops["mission_banner"], bottom_objective_image=crops["bottom_objective"],
            result_header_image=crops["result_header"], vip_status_image=crops["vip_status"],
        )
        mission = result.mission
        identity = None if mission is None else {
            "status": mission.identity_status, "candidates": list(mission.candidates),
            "mission_name": mission.mission_name, "mission_type": mission.mission_type.name,
            "heist_phase": mission.heist_phase.name, "outcome": mission.outcome,
            "outcome_scope": mission.outcome_scope,
        }
        limits = [
            "One still image with default HUD regions and fresh detector context",
            "Detector candidates and text evidence; no application activity completion, episode guard, session, accounting or cooldown validation",
            "Heuristic state scores are not OCR confidence or calibrated accuracy",
            "No live capture, recorded sequence, GUI or gameplay-accuracy validation",
        ]
        if backend == "tesseract":
            limits.append("This diagnostic-only Tesseract backend does not execute or validate native Windows OCR")
        report = {
            "schema_version": 1, "mode": "single_image_diagnostic", "status": "ok",
            "input": input_metadata,
            "backend": {
                "name": backend, "diagnostic_only": backend == "tesseract",
                "ocr_confidence_source": "not_provided_by_native_backend" if backend == "windows" else "tesseract_word_mean",
            },
            "context": {"fresh": True, "images": 1, "loaded_templates": []},
            "sources": sources,
            "detector_candidate": {
                "state": result.state.name, "heuristic_score": float(result.confidence),
                "reason": result.reason, "identity": identity,
                "active_title_evidence": result.active_title_evidence,
            },
            "limits": limits,
        }
        json.dumps(report, allow_nan=False)
        return report
    except _OCRDiagnosticError as error:
        # Keep the public exception and CLI error.type compatible.
        raise DiagnosticError(str(error)) from None
    except DiagnosticError:
        raise
    except Exception:
        raise DiagnosticError("Single-image diagnostic could not complete") from None
