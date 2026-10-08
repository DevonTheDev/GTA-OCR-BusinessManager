"""Bounded, one-shot evidence from an existing detection cycle.

This module neither acquires a frame nor invokes recognition. Its writer accepts
only detached bytes, and never has access to the running application.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re

import numpy as np
from PIL import Image

from ..capture.screen_capture import CaptureGeometry, ScreenCapture
from .ocr_engine import OCREngine


SOURCE_INDICES = {"mission": 2, "center": 3, "banner": 5,
                  "bottom": 6, "header": 7, "vip_status": 8}
MAX_DIMENSION = 8192
MAX_PIXELS = 16_777_216
MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_SOURCE_TEXT_BYTES = 16 * 1024


class DetectionSampleError(ValueError):
    """A sample failed independently of normal detection and tracking."""


@dataclass(frozen=True)
class DetectionSample:
    sample_id: str
    run_id: str
    captured_at: str
    png_bytes: bytes
    json_bytes: bytes


@dataclass(frozen=True)
class DetectionSampleExport:
    success: bool
    path: str | None
    completed_files: tuple[str, ...]
    partial_files: tuple[str, ...]
    message: str


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _text(value, maximum=MAX_SOURCE_TEXT_BYTES):
    if not isinstance(value, str) or len(value) > maximum:
        raise DetectionSampleError("Sample text is invalid or exceeds its limit")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise DetectionSampleError("Sample text exceeds its limit")
    except UnicodeError:
        raise DetectionSampleError("Sample text is invalid") from None
    return value


def _number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise DetectionSampleError("Sample number is invalid")
    return value


def _enum(value):
    if value is None:
        return None
    return _text(value.name if isinstance(value, Enum) else value, 256)


def _timestamp(value):
    if isinstance(value, datetime):
        value = value.isoformat()
    value = _text(value, 64)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise DetectionSampleError("Sample timestamp is invalid") from None


def _identifier(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
        raise DetectionSampleError("Sample identity is invalid")
    return value


def _json_bytes(value):
    output = bytearray()
    for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                                 separators=(",", ":")).iterencode(value):
        encoded = part.encode("utf-8")
        if len(output) + len(encoded) > MAX_JSON_BYTES:
            raise DetectionSampleError("Sample JSON exceeds its limit")
        output.extend(encoded)
    return bytes(output)


class _BoundedPNG(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > MAX_PNG_BYTES:
            raise DetectionSampleError("Sample PNG exceeds its limit")
        return super().write(value)


def _frame_at(images, index):
    try:
        return images[index]
    except (KeyError, IndexError):
        return None


def _rectangle(value):
    return {key: getattr(value, key) for key in ("left", "top", "width", "height")}


def _geometry(images):
    geometry = getattr(images, "geometry", None)
    if not isinstance(geometry, CaptureGeometry):
        return {"status": "unknown", "reason": "provider_did_not_supply_same_call_geometry"}
    if len(geometry.regions) > 64:
        raise DetectionSampleError("Sample geometry exceeds its limit")
    rectangles = {item.index: item.rectangle for item in geometry.regions}
    full = rectangles.get(0)
    regions = []
    for item in geometry.regions:
        value = {"index": item.index, "status": item.status,
                 "absolute_rectangle": None if item.rectangle is None else _rectangle(item.rectangle),
                 "box_ltrb_in_frame": None, "contained_in_frame": None}
        if full is not None and item.rectangle is not None:
            rect = item.rectangle
            left, top = rect.left - full.left, rect.top - full.top
            right, bottom = left + rect.width, top + rect.height
            value["box_ltrb_in_frame"] = [left, top, right, bottom]
            value["contained_in_frame"] = left >= 0 and top >= 0 and right <= full.width and bottom <= full.height
        regions.append(value)
    return {"status": "known", "resolution": list(geometry.resolution), "offset": list(geometry.offset),
            "native_grab_rectangle": None if geometry.bounds is None else _rectangle(geometry.bounds),
            "grab_started_at": geometry.grab_started_at, "grab_ended_at": geometry.grab_ended_at,
            "grab_duration_ns": geometry.grab_duration_ns, "regions": regions}


def _provider(provider):
    kind = "custom_or_unknown"
    if (type(provider) is ScreenCapture
            and getattr(provider.capture_multiple_regions, "__func__", None) is ScreenCapture.capture_multiple_regions
            and getattr(provider._ensure_mss, "__func__", None) is ScreenCapture._ensure_mss):
        native = getattr(provider, "_sct", None)
        if native is not None and type(native).__module__.startswith("mss."):
            kind = "native_mss"
    return {"kind": kind, "class": _text(type(provider).__module__ + "." + type(provider).__qualname__, 512)}


def _backend(engine):
    known = type(engine) is OCREngine and all(
        getattr(getattr(engine, name), "__func__", None) is getattr(OCREngine, name)
        for name in ("recognize_preprocessed", "recognize", "_recognize_async")
    )
    result = {"name": "windows_ocr" if known else "unknown",
              "class": _text(type(engine).__module__ + "." + type(engine).__qualname__, 512),
              "confidence_source": "not_provided_by_native_backend" if known else "unknown",
              "internal_resize": "unknown", "internal_dimensions": None}
    descriptor = getattr(engine, "detection_sample_descriptor", None) if not known else None
    if isinstance(descriptor, dict):
        for key in ("name", "confidence_source"):
            if key in descriptor:
                result[key] = _text(descriptor[key], 256)
    return result, known


_OBSERVATION_TEXT = ("mission_text", "objective_text", "banner_text", "bottom_objective_text",
                     "bottom_objective_command", "result_header_text", "result_header_evidence",
                     "vip_status_text", "vip_status_evidence")


def _observation(value, processed=False):
    result = {"state": _enum(getattr(value, "game_state" if processed else "state", None)),
              "heuristic_score": _number(getattr(value, "state_confidence" if processed else "confidence", None)),
              "reason": _text(getattr(value, "state_reason" if processed else "reason", ""))}
    result.update({key: _text(getattr(value, key, "")) for key in _OBSERVATION_TEXT})
    mission = getattr(value, "mission", None)
    identity = None
    if mission is not None:
        candidates = getattr(mission, "candidates", ())
        if not isinstance(candidates, (tuple, list)) or len(candidates) > 64:
            raise DetectionSampleError("Sample identities exceed their limit")
        identity = {"status": _text(getattr(mission, "identity_status", "unknown"), 256),
                    "mission_name": _text(getattr(mission, "mission_name", "")),
                    "mission_type": _enum(getattr(mission, "mission_type", None)),
                    "heist_phase": _enum(getattr(mission, "heist_phase", None)),
                    "candidates": [_text(item, 256) for item in candidates],
                    "outcome": _enum(getattr(mission, "outcome", None)),
                    "outcome_scope": _enum(getattr(mission, "outcome_scope", None)),
                    "objective": _text(getattr(mission, "objective", "")),
                    "raw_text": _text(getattr(mission, "raw_text", ""), MAX_SOURCE_TEXT_BYTES * 6)}
    result["identity"] = identity
    return result


class DetectionSampleCollector:
    """A single admitted cycle's private collector; finish releases raw pixels."""

    def __init__(self, sample_id, run_id, requested_at, admitted_at):
        self.sample_id, self.run_id = _identifier(sample_id), _identifier(run_id)
        self.requested_at, self.admitted_at = _timestamp(requested_at), _timestamp(admitted_at)
        self._frame = None
        self._failure = None
        self._finished = False
        self._stages = {}
        self._selection = {}
        self._owners = {}
        self._geometry = {"status": "unknown", "reason": "capture_not_observed"}
        self._provider = {"kind": "unknown", "class": None}
        self._backend = {"name": "unknown", "class": None, "confidence_source": "unknown",
                         "internal_resize": "unknown", "internal_dimensions": None}
        self._native_backend = False
        self._observation_status = "not_observed"
        self._captured_at = None
        self._captured_at_provenance = "unknown"
        self._sources = {source: {"batch_index": index, "region_present": False, "image_present": False,
            "ocr_status": "missing_region", "preprocessing": None, "raw_text": None,
            "confidence": None, "confidence_provenance": "unknown"} for source, index in SOURCE_INDICES.items()}

    def capture(self, images, region_present, provider):
        if self._frame is not None or self._finished:
            raise DetectionSampleError("Sample capture was already consumed")
        try:
            frame = _frame_at(images, 0)
            if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3
                    or frame.shape[2] != 3 or not 1 <= frame.shape[0] <= MAX_DIMENSION
                    or not 1 <= frame.shape[1] <= MAX_DIMENSION
                    or frame.shape[0] * frame.shape[1] > MAX_PIXELS):
                raise DetectionSampleError("Sample frame is unavailable or exceeds its limit")
            self._frame = frame.copy(order="C")
            self._frame.setflags(write=False)
            self._captured_at = _utcnow()
            self._captured_at_provenance = "frame_received"
            self._geometry = _geometry(images)
            self._provider = _provider(provider)
            if self._geometry.get("grab_started_at") is not None:
                self._captured_at = _timestamp(self._geometry["grab_started_at"])
                self._captured_at_provenance = "same_call_grab_start"
            for source, index in SOURCE_INDICES.items():
                present = bool(region_present.get(source, False))
                image_present = _frame_at(images, index) is not None
                self._sources[source].update(region_present=present, image_present=image_present,
                    ocr_status="missing_region" if not present else "capture_unavailable" if not image_present else "not_requested_by_detector")
        except Exception:
            self._frame = None
            self._failure = "Sample frame could not be collected"
            raise DetectionSampleError(self._failure) from None

    def mission_observation_unavailable(self):
        self._observation_status = "unsupported_detector"
        for record in self._sources.values():
            if record["region_present"] and record["image_present"]:
                record["ocr_status"] = "observation_unavailable"

    def observe_ocr(self, source, event, payload):
        # Observer failures never reach the detector. Poison only this sample,
        # without retaining an oversize value or an exception carrying OCR text.
        if self._failure or self._finished:
            return
        try:
            if event == "backend":
                self._backend, self._native_backend = _backend(payload["engine"])
                self._observation_status = "observed"
                return
            record = self._sources[source]
            if event == "availability":
                if not payload["available"] and record["region_present"] and record["image_present"]:
                    record["ocr_status"] = "backend_unavailable"
            elif event == "request":
                kwargs = payload["kwargs"]
                # Unspecified custom-backend defaults are deliberately unknown.
                defaults = {"threshold": True, "invert": True, "scale": 2.0} if self._native_backend else {}
                flags = {key: kwargs.get(key, defaults.get(key)) for key in ("threshold", "invert", "scale")}
                if any(value is not None and type(value) is not bool for key, value in flags.items() if key != "scale"):
                    raise DetectionSampleError("Unknown OCR request flags")
                flags["scale"] = _number(flags["scale"])
                flags["explicit_arguments"] = sorted(key for key in ("threshold", "invert", "scale") if key in kwargs)
                record.update(ocr_status="requested", preprocessing=flags)
            elif event == "result":
                result = payload["result"]
                raw = _text(result.text)
                confidence = getattr(result, "confidence", None)
                failed = self._native_backend and raw == "" and confidence == 0.0 and getattr(result, "words", None) == []
                record.update(raw_text=raw, ocr_status="failed" if failed else "recognized" if raw.strip() else "recognized_empty",
                    confidence=None if self._native_backend else _number(confidence),
                    confidence_provenance=self._backend["confidence_source"] if self._native_backend or confidence is not None else "not_reported")
            elif event == "error":
                record.update(ocr_status="failed", confidence=None)
            elif event == "unavailable":
                if record["region_present"] and record["image_present"]:
                    record["ocr_status"] = "observation_unavailable"
            else:
                raise DetectionSampleError("Unknown OCR observation")
        except Exception:
            self._failure = "Sample OCR evidence could not be collected"

    def stage(self, name, value):
        if name not in ("candidate", "first_guarded", "processed") or self._finished:
            raise DetectionSampleError("Unknown sample observation stage")
        self._stages[name] = _observation(value, processed=name == "processed")

    def selection(self, phase, activity, identity_status, recovery_waiting_for_balance):
        if phase not in ("before", "after") or self._finished:
            raise DetectionSampleError("Unknown sample selection phase")
        selected = None if activity is None else {
            "name": _text(getattr(activity, "name", "")),
            "type": _enum(getattr(activity, "activity_type", None)),
            "identity_status": _text(identity_status, 256),
        }
        self._selection[phase] = {"activity": selected,
                                  "recovery_waiting_for_balance": bool(recovery_waiting_for_balance)}
        self._owners[phase] = activity

    def finish(self):
        try:
            if self._finished or self._failure or self._frame is None:
                raise DetectionSampleError(self._failure or "Sample frame is unavailable")
            completed_at = _utcnow()
            frame = self._frame
            height, width = frame.shape[:2]
            with _BoundedPNG() as output:
                with Image.frombuffer("RGB", (width, height), frame, "raw", "BGR", 0, 1) as image:
                    image.save(output, format="PNG")
                png = output.getvalue()
            before, after = self._owners.get("before"), self._owners.get("after")
            ownership = "unknown"
            if "before" in self._owners and "after" in self._owners:
                if after is None:
                    ownership = "cleared" if before is not None else "none"
                elif before is after:
                    ownership = "refined" if self._selection["before"]["activity"] != self._selection["after"]["activity"] else "retained"
                else:
                    ownership = "new"
            candidate, guarded = self._stages.get("candidate"), self._stages.get("first_guarded")
            report = {
                "schema_version": 1, "mode": "live_cycle_observation", "sample_id": self.sample_id, "run_id": self.run_id,
                "requested_at": self.requested_at, "admitted_at": self.admitted_at, "captured_at": self._captured_at,
                "captured_at_provenance": self._captured_at_provenance,
                "cycle_completed_at": completed_at,
                "frame": {"sha256": hashlib.sha256(png).hexdigest(), "bytes": len(png), "width": width, "height": height,
                          "encoding": "lossless_rgb_png", "source_pixels": "BGR index zero; no resize, annotation or redaction"},
                "capture_provider": self._provider, "geometry": self._geometry,
                "mission_observation_status": self._observation_status, "backend": self._backend, "sources": self._sources,
                "detector_candidate": candidate, "first_guarded": guarded,
                "first_guard_changed": None if candidate is None or guarded is None else candidate != guarded,
                "processed": self._stages.get("processed"), "selection": {**self._selection, "ownership": ownership},
                "limits": ["Observed live-cycle decisions, not ground truth or calibrated accuracy",
                           "No historical recovery or fresh reclassification",
                           "Business OCR and financial history are excluded; later business captures may occur",
                           "Custom or replay capture does not establish native gameplay accuracy",
                           "Backend internal preprocessing dimensions are unknown unless observed"],
            }
            encoded = _json_bytes(report)
            return DetectionSample(self.sample_id, self.run_id, self._captured_at, png, encoded)
        except Exception:
            raise DetectionSampleError(self._failure or "Sample could not be encoded") from None
        finally:
            self._finished = True
            self._frame = None
            self._owners.clear()


def export_detection_sample(sample, destination_directory):
    """Write one exclusive directory, then its completion marker, without retry."""
    created = None
    completed = []
    partial = []
    try:
        if not isinstance(sample, DetectionSample) or type(sample.png_bytes) is not bytes or type(sample.json_bytes) is not bytes:
            raise DetectionSampleError("Sample is not detached")
        sample_id = _identifier(sample.sample_id)
        stamp = datetime.fromisoformat(_timestamp(sample.captured_at)).strftime("%Y%m%dT%H%M%S%fZ")
        if not 0 < len(sample.png_bytes) <= MAX_PNG_BYTES or not 0 < len(sample.json_bytes) <= MAX_JSON_BYTES:
            raise DetectionSampleError("Sample payload exceeds its limit")
        parent = Path(destination_directory)
        if not parent.is_dir():
            raise DetectionSampleError("Destination directory is unavailable")
        directory = parent / f"gta-detection-{stamp}-{sample_id}"
        directory.mkdir(exist_ok=False)
        created = str(directory)
        payloads = (("frame.png", sample.png_bytes), ("sample.json", sample.json_bytes))
        marker = _json_bytes({"schema_version": 1, "sample_id": sample_id, "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in payloads}})
        # Close the staging marker before atomically admitting a final marker.
        # Hard-link creation is exclusive on supported destination filesystems.
        for name, payload in (*payloads, ("COMPLETE.pending", marker)):
            with (directory / name).open("xb") as output:
                partial.append(name)
                written = output.write(payload)
                if written != len(payload):
                    raise OSError("Incomplete sample write")
            partial.remove(name)
            completed.append(name)
        os.link(directory / "COMPLETE.pending", directory / "COMPLETE.json")
        completed.append("COMPLETE.json")
        try:
            (directory / "COMPLETE.pending").unlink()
            completed.remove("COMPLETE.pending")
        except OSError:
            # Publication already succeeded. A staging cleanup failure cannot
            # turn a verifiable complete sample into a reported failed export.
            return DetectionSampleExport(True, created, tuple(completed), (),
                "Detection sample saved locally; the completed staging marker was also preserved")
        return DetectionSampleExport(True, created, tuple(completed), (), "Detection sample saved locally")
    except Exception:
        return DetectionSampleExport(False, created, tuple(completed), tuple(partial),
                                     "Detection sample could not be saved; any incomplete output was preserved")
