"""Bounded, read-only reclassification of saved pixels and recorded OCR text.

The hashes check payload consistency, not producer authenticity. Path/descriptor
checks reject observed redirects but are not a sandbox against hostile concurrent
filesystem replacement. No input field is a path, executable, or import name.
"""

import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import warnings
import zlib

import numpy as np
from PIL import Image

from .ocr_engine import OCRResult
from .state_detector import StateDetector
from .template_matcher import TemplateMatcher


# Kept local: importing the producer would also import live capture facilities.
MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_MARKER_BYTES = 4096
MAX_DIMENSION = 8192
MAX_PIXELS = 16_777_216
MAX_DEPTH = 32
SOURCE_INDICES = {"mission": 2, "center": 3, "banner": 5, "bottom": 6, "header": 7, "vip_status": 8}
DETECTOR_ARGUMENTS = {"mission": "mission_text_image", "center": "center_text_image",
    "banner": "mission_banner_image", "bottom": "bottom_objective_image",
    "header": "result_header_image", "vip_status": "vip_status_image"}
OPTIONS = {"threshold", "invert", "scale"}
NATIVE_DEFAULTS = {"threshold": True, "invert": True, "scale": 2.0}
SUCCESS_STATUSES = {"recognized", "recognized_empty"}
SOURCE_STATUSES = SUCCESS_STATUSES | {"missing_region", "capture_unavailable", "not_requested_by_detector",
    "requested", "failed", "backend_unavailable", "observation_unavailable"}
OBSERVATION_TEXT = ("mission_text", "objective_text", "banner_text", "bottom_objective_text",
    "bottom_objective_command", "result_header_text", "result_header_evidence", "vip_status_text",
    "vip_status_evidence", "active_title_evidence", "screen_evidence")


class SampleReplayError(ValueError):
    """Invalid or inconsistent saved input; contains no untrusted source text."""


class UnsupportedSampleError(SampleReplayError):
    """A valid observation lacks evidence required by this fresh detector call."""


def _require(condition, message):
    if not condition:
        raise SampleReplayError(message)


def _text(value, limit=16 * 1024, *, nullable=False):
    if value is None and nullable:
        return
    _require(type(value) is str, "Invalid text field")
    try:
        _require(len(value.encode("utf-8")) <= limit, "Text field exceeds its limit")
    except UnicodeError:
        raise SampleReplayError("Invalid Unicode text") from None


def _integer(value, minimum=-(2**31), maximum=2**31 - 1):
    _require(type(value) is int and minimum <= value <= maximum, "Invalid integer field")


def _number(value):
    _require(value is None or (type(value) in (int, float) and math.isfinite(value)), "Invalid numeric field")


def _object(value):
    _require(type(value) is dict, "Expected a JSON object")
    return value


def _identifier(value):
    _require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None,
             "Invalid sample identity")


def _strict_json(payload):
    """Bound nesting before the parser; reject ambiguous/nonstandard JSON."""
    try:
        text = payload.decode("utf-8")
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                _require(depth <= MAX_DEPTH, "JSON nesting exceeds its limit")
            elif char in "]}":
                depth -= 1
        def pairs(items):
            result = {}
            for key, value in items:
                _require(key not in result, "Duplicate JSON key")
                result[key] = value
            return result
        def invalid_constant(_value):
            raise SampleReplayError("Nonfinite JSON number")
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
        pending = [result]
        while pending:
            value = pending.pop()
            if type(value) is dict:
                pending.extend(value.keys())
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
            elif type(value) is str:
                _text(value, MAX_JSON_BYTES)
            elif type(value) in (int, float):
                _number(value)
        return _object(result)
    except SampleReplayError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise SampleReplayError("Invalid JSON document") from None


def _local_directory(value):
    try:
        supplied = os.fspath(value)
    except TypeError:
        raise SampleReplayError("Supply a local sample directory") from None
    _require(isinstance(supplied, str) and supplied and supplied != "-" and "\x00" not in supplied,
             "Supply a local sample directory")
    normalized = supplied.replace("\\", "/")
    _require(not normalized.startswith("//") and "://" not in normalized
             and ".." not in normalized.split("/"), "Network, URL and traversal paths are unsupported")
    # Accept ordinary Windows drive-letter paths; exclude drive-relative and ADS.
    remainder = normalized
    if re.match(r"^[A-Za-z]:/", normalized):
        remainder = normalized[2:]
    _require(":" not in remainder, "Only ordinary local directory paths are supported")
    return Path(supplied)


def _ordinary(metadata, *, directory=False):
    return (not stat.S_ISLNK(metadata.st_mode)
            and not (getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            and (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)))


def _same_file(first, second):
    return all(getattr(first, field, None) == getattr(second, field, None)
               for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _directory_metadata(directory):
    absolute = directory.absolute()
    for component in (*reversed(absolute.parents), absolute):
        _require(_ordinary(component.lstat(), directory=True), "Sample path must contain ordinary directories")
    return absolute.lstat()


def _read_child(directory, name, maximum):
    """Fixed names only; check metadata before, on, and after the bounded read."""
    path = directory / name
    before = path.lstat()
    _require(_ordinary(before), "Sample payload must be a regular non-symlink file")
    _require(0 < before.st_size <= maximum, "Sample payload size exceeds its limit or is empty")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as file:
        opened = os.fstat(file.fileno())
        _require(_ordinary(opened) and _same_file(before, opened), "Sample payload changed during open")
        data = file.read(maximum + 1)
        after = os.fstat(file.fileno())
    _require(0 < len(data) <= maximum and len(data) == before.st_size, "Sample payload size changed or exceeds its limit")
    _require(_same_file(opened, after) and _same_file(after, path.lstat()), "Sample payload changed during read")
    return data


def _file_record(record, maximum):
    record = _object(record)
    _integer(record.get("bytes"), 1, maximum)
    _require(type(record.get("sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is not None,
             "Invalid payload SHA-256")


def _matches(data, record):
    _require(len(data) == record["bytes"] and hashlib.sha256(data).hexdigest() == record["sha256"],
             "Payload size or SHA-256 does not match")


def _dimensions(width, height):
    _integer(width, 1, MAX_DIMENSION)
    _integer(height, 1, MAX_DIMENSION)
    _require(width * height <= MAX_PIXELS, "Frame exceeds decoded pixel limit")


def _load_png(payload, declared):
    """Check container, dimensions and CRCs before Pillow decodes RGB pixels."""
    _require(payload[:8] == b"\x89PNG\r\n\x1a\n", "Frame must be a PNG")
    position, saw_header, saw_data, ended = 8, False, False, False
    while position < len(payload):
        _require(position + 12 <= len(payload), "Incomplete PNG chunk")
        size = int.from_bytes(payload[position:position + 4], "big")
        kind = payload[position + 4:position + 8]
        end = position + 12 + size
        _require(end <= len(payload), "Incomplete PNG payload")
        data = memoryview(payload)[position + 8:end - 4]
        crc = zlib.crc32(data, zlib.crc32(kind))
        _require(crc == int.from_bytes(payload[end - 4:end], "big"), "Invalid PNG checksum")
        _require(kind not in (b"acTL", b"fcTL", b"fdAT"), "Animated PNG is unsupported")
        if not saw_header:
            _require(kind == b"IHDR" and size == 13, "Missing PNG header")
            width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            _dimensions(width, height)
            _require((width, height) == (declared["width"], declared["height"]), "PNG dimensions do not match declared frame")
            _require((bits, color, compression, filtering) == (8, 2, 0, 0) and interlace in (0, 1),
                     "Frame must be an 8-bit RGB PNG")
            saw_header = True
        else:
            _require(kind != b"IHDR", "Duplicate PNG header")
        if kind == b"IDAT":
            saw_data = True
        if kind == b"IEND":
            _require(size == 0 and end == len(payload) and saw_data, "Incomplete PNG or trailing content")
            ended = True
        position = end
    _require(ended, "PNG end marker is missing")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                _require(image.mode == "RGB" and image.format == "PNG" and getattr(image, "n_frames", 1) == 1,
                         "Frame must be one RGB PNG image")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                return np.asarray(image)[:, :, ::-1].copy()
    except SampleReplayError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise SampleReplayError("Frame could not be decoded as a complete RGB PNG") from None


def _observation(value):
    if value is None:
        return
    value = _object(value)
    _text(value.get("state"), 256, nullable=True)
    _number(value.get("heuristic_score"))
    _text(value.get("reason", ""))
    for key in OBSERVATION_TEXT:
        if key in value:
            _text(value[key])
    identity = value.get("identity")
    if identity is not None:
        identity = _object(identity)
        for key in ("status", "mission_name", "mission_type", "heist_phase", "outcome", "outcome_scope", "objective"):
            if key in identity:
                _text(identity[key], nullable=key in ("mission_type", "heist_phase", "outcome", "outcome_scope"))
        if "raw_text" in identity:
            _text(identity["raw_text"], 6 * 16 * 1024)
        candidates = identity.get("candidates", [])
        _require(type(candidates) is list and len(candidates) <= 64, "Invalid stored identity candidates")
        for candidate in candidates:
            _text(candidate, 256)


def _selection(value):
    if value is None:
        return
    value = _object(value)
    _require(value.get("ownership") in ("unknown", "none", "cleared", "new", "retained", "refined"), "Invalid selection ownership")
    for phase in ("before", "after"):
        if phase not in value:
            continue
        record = _object(value[phase])
        _require(type(record.get("recovery_waiting_for_balance")) is bool, "Invalid stored selection flag")
        activity = record.get("activity")
        if activity is not None:
            activity = _object(activity)
            _text(activity.get("name"))
            _text(activity.get("type"), 256, nullable=True)
            _text(activity.get("identity_status"), 256)


def _source_records(report):
    sources = _object(report.get("sources"))
    _require(set(sources) == set(SOURCE_INDICES), "Expected the six recorded mission sources")
    backend = _object(report.get("backend"))
    _require("class" in backend, "Recorded backend class field is missing")
    for key in ("name", "confidence_source", "internal_resize"):
        _text(backend.get(key), 256)
    _text(backend.get("class"), 512, nullable=True)
    _require(backend.get("internal_dimensions") is None, "Unsupported backend internal dimensions")
    native = (backend["name"] == "windows_ocr" and backend["class"] == "src.detection.ocr_engine.OCREngine"
              and backend["confidence_source"] == "not_provided_by_native_backend")
    for source, record in sources.items():
        record = _object(record)
        _require({"raw_text", "confidence"} <= record.keys(), "Recorded source text or confidence field is missing")
        _require(type(record.get("batch_index")) is int and record["batch_index"] == SOURCE_INDICES[source], "Invalid source batch index")
        _require(type(record.get("image_present")) is bool and type(record.get("region_present")) is bool, "Invalid source presence flags")
        status = record.get("ocr_status")
        _require(type(status) is str and status in SOURCE_STATUSES, "Invalid source OCR status")
        _text(record.get("raw_text"), nullable=True)
        _number(record.get("confidence"))
        _text(record.get("confidence_provenance"), 256)
        preprocessing = record.get("preprocessing")
        if preprocessing is not None:
            preprocessing = _object(preprocessing)
            _require(set(preprocessing) == OPTIONS | {"explicit_arguments"}, "Invalid preprocessing fields")
            explicit = preprocessing["explicit_arguments"]
            _require(type(explicit) is list and all(type(key) is str and key in OPTIONS for key in explicit)
                     and explicit == sorted(set(explicit)), "Invalid explicit OCR arguments")
            for key in OPTIONS:
                value = preprocessing[key]
                if key == "scale":
                    _number(value)
                else:
                    _require(value is None or type(value) is bool, "Invalid preprocessing option")
                if key in explicit:
                    _require(value is not None, "Explicit preprocessing value is missing")
                else:
                    expected = NATIVE_DEFAULTS[key] if native else None
                    _require(type(value) is type(expected) and value == expected,
                             "Recorded omitted default does not match native or unknown custom defaults")
        if status in SUCCESS_STATUSES:
            _require(record["image_present"] and preprocessing is not None and type(record["raw_text"]) is str,
                     "Recognized response is missing its image, request or text")
            _require(bool(record["raw_text"].strip()) == (status == "recognized"), "OCR status and text disagree")
        if status in ("missing_region", "capture_unavailable", "not_requested_by_detector", "backend_unavailable", "observation_unavailable"):
            _require(record.get("raw_text") is None and preprocessing is None and record.get("confidence") is None,
                     "Unobserved source has contradictory response evidence")
        if status == "missing_region":
            _require(not record["region_present"], "Missing source region is marked present")
        if status == "capture_unavailable":
            _require(record["region_present"] and not record["image_present"], "Unavailable capture is marked present")
        if native:
            _require(record.get("confidence") is None, "Native confidence must remain unknown")
    return sources


def _rectangle(value):
    value = _object(value)
    _require(set(value) == {"left", "top", "width", "height"}, "Invalid rectangle fields")
    for key in ("left", "top"):
        _integer(value[key])
    for key in ("width", "height"):
        _integer(value[key], 1)
    return value


def _crops(report, frame):
    geometry = _object(report.get("geometry"))
    status = geometry.get("status")
    _require(status in ("known", "unknown"), "Invalid geometry status")
    if status == "unknown":
        raise UnsupportedSampleError("Same-call geometry is unavailable; current HUD defaults cannot replace it")
    for key in ("resolution", "offset"):
        pair = geometry.get(key)
        _require(type(pair) is list and len(pair) == 2, "Invalid geometry coordinate pair")
        for value in pair:
            _integer(value, 1 if key == "resolution" else -(2**31))
    if geometry.get("native_grab_rectangle") is not None:
        _rectangle(geometry["native_grab_rectangle"])
    for key in ("grab_started_at", "grab_ended_at"):
        _text(geometry.get(key), 64, nullable=True)
    if geometry.get("grab_duration_ns") is not None:
        _integer(geometry["grab_duration_ns"], 0, 2**63 - 1)
    regions = geometry.get("regions")
    _require(type(regions) is list and len(regions) <= 64, "Invalid geometry regions")
    by_index = {}
    for region in regions:
        region = _object(region)
        index = region.get("index")
        _integer(index, 0, 63)
        _require(index not in by_index, "Duplicate geometry region")
        _require(region.get("status") in ("captured", "capture_unavailable", "invalid_region"), "Invalid geometry region status")
        if region.get("absolute_rectangle") is not None:
            _rectangle(region["absolute_rectangle"])
        box = region.get("box_ltrb_in_frame")
        if box is not None:
            _require(type(box) is list and len(box) == 4, "Invalid frame crop box")
            for value in box:
                _integer(value)
            _require(box[0] < box[2] and box[1] < box[3], "Empty frame crop box")
        contained = region.get("contained_in_frame")
        _require(contained is None or type(contained) is bool, "Invalid crop containment flag")
        by_index[index] = region
    full = by_index.get(0, {}).get("absolute_rectangle")
    if full is None:
        raise UnsupportedSampleError("Saved full-frame geometry is unavailable")
    height, width = frame.shape[:2]
    _require((full["width"], full["height"]) == (width, height), "Full-frame geometry and PNG dimensions disagree")
    for region in regions:
        rectangle, box = region.get("absolute_rectangle"), region.get("box_ltrb_in_frame")
        if rectangle is None:
            _require(box is None and region.get("contained_in_frame") is None, "Crop geometry contradicts missing rectangle")
            continue
        expected = [rectangle["left"] - full["left"], rectangle["top"] - full["top"]]
        expected += [expected[0] + rectangle["width"], expected[1] + rectangle["height"]]
        _require(box == expected, "Crop box and absolute geometry disagree")
        contained = 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height
        _require(region.get("contained_in_frame") is contained, "Crop containment and geometry disagree")
    crops = {}
    for source, record in report["sources"].items():
        if not record["image_present"]:
            crops[source] = None
            continue
        region = by_index.get(record["batch_index"])
        if region is None or region.get("box_ltrb_in_frame") is None:
            raise UnsupportedSampleError(f"Saved geometry is unavailable for {source}")
        if not region["contained_in_frame"]:
            raise UnsupportedSampleError(f"Saved {source} crop extends outside the retained frame")
        if region["status"] != "captured":
            raise UnsupportedSampleError(f"Saved {source} crop was not observed as captured")
        left, top, right, bottom = region["box_ltrb_in_frame"]
        crops[source] = frame[top:bottom, left:right].copy()
    return crops


def _load_sample(directory):
    directory = _local_directory(directory)
    try:
        directory_before = _directory_metadata(directory)
        marker = _strict_json(_read_child(directory, "COMPLETE.json", MAX_MARKER_BYTES))
        _require(type(marker.get("schema_version")) is int and marker["schema_version"] == 1, "Unsupported completion marker schema")
        _identifier(marker.get("sample_id"))
        files = _object(marker.get("files"))
        _require(set(files) == {"frame.png", "sample.json"}, "Completion marker must name exactly frame.png and sample.json")
        for name, maximum in (("sample.json", MAX_JSON_BYTES), ("frame.png", MAX_PNG_BYTES)):
            _file_record(files[name], maximum)
        metadata = _read_child(directory, "sample.json", MAX_JSON_BYTES)
        _matches(metadata, files["sample.json"])
        report = _strict_json(metadata)
        _require(type(report.get("schema_version")) is int and report["schema_version"] == 1, "Unsupported sample schema")
        _require(report.get("mode") == "live_cycle_observation", "Unsupported sample observation mode")
        _identifier(report.get("sample_id"))
        _identifier(report.get("run_id"))
        _require(report["sample_id"] == marker["sample_id"], "Sample identities do not match")
        for key in ("requested_at", "admitted_at", "captured_at", "cycle_completed_at"):
            _text(report.get(key), 64)
        _text(report.get("captured_at_provenance"), 256)
        provider = _object(report.get("capture_provider"))
        _text(provider.get("kind"), 256)
        _text(provider.get("class"), 512, nullable=True)
        frame_metadata = _object(report.get("frame"))
        _file_record(frame_metadata, MAX_PNG_BYTES)
        _dimensions(frame_metadata.get("width"), frame_metadata.get("height"))
        _require(frame_metadata.get("encoding") == "lossless_rgb_png", "Unsupported stored frame encoding")
        _require(all(frame_metadata[key] == files["frame.png"][key] for key in ("bytes", "sha256")), "Frame integrity declarations disagree")
        _source_records(report)
        for key in ("detector_candidate", "first_guarded", "processed"):
            _observation(report.get(key))
        changed = report.get("first_guard_changed")
        _require(changed is None or type(changed) is bool, "Invalid stored guard-change flag")
        candidate, guarded = report.get("detector_candidate"), report.get("first_guarded")
        expected = None if candidate is None or guarded is None else candidate != guarded
        if "first_guard_changed" in report:
            _require(changed is expected, "Stored guard-change flag contradicts observations")
        _selection(report.get("selection"))
        observation = report.get("mission_observation_status")
        _require(observation in ("observed", "not_observed", "unsupported_detector"), "Invalid detector observation status")
        png = _read_child(directory, "frame.png", MAX_PNG_BYTES)
        _matches(png, files["frame.png"])
        frame = _load_png(png, frame_metadata)
        _require(_same_file(directory_before, _directory_metadata(directory)), "Sample directory changed during read")
        if observation != "observed":
            raise UnsupportedSampleError("Saved detector OCR evidence was not observed by a supported detector")
        crops = _crops(report, frame)
        return report, frame, crops, files
    except SampleReplayError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise SampleReplayError("Sample could not be read as a complete local export") from None


class _RecordedOCR:
    """One recorded request per distinct source, with no real OCR or defaults."""

    is_available = True

    def __init__(self, sources, crops):
        self.sources = sources
        self.names = {id(crop): name for name, crop in crops.items() if crop is not None}
        self.used = []

    def recognize_preprocessed(self, image, **kwargs):
        source = self.names.get(id(image))
        if source is None:
            raise UnsupportedSampleError("Fresh detector requested an unrecorded image response")
        if source in self.used:
            raise UnsupportedSampleError(f"Fresh detector repeated {source}; the sample has no full call history")
        record = self.sources[source]
        if record["ocr_status"] not in SUCCESS_STATUSES:
            raise UnsupportedSampleError(f"Saved {source} response is unavailable for this fresh request")
        preprocessing = record["preprocessing"]
        if set(kwargs) != set(preprocessing["explicit_arguments"]):
            raise UnsupportedSampleError(f"Fresh {source} request changed explicit OCR arguments")
        if any(type(value) is not type(preprocessing[key]) or value != preprocessing[key] for key, value in kwargs.items()):
            raise UnsupportedSampleError(f"Fresh {source} request changed recorded OCR argument values")
        self.used.append(source)
        return OCRResult(record["raw_text"], record["confidence"], [])


def _enum(value):
    return None if value is None else getattr(value, "name", value)


def _candidate(result):
    report = {"state": result.state.name, "heuristic_score": result.confidence, "reason": result.reason,
              **{key: getattr(result, key, "") for key in OBSERVATION_TEXT}, "identity": None}
    mission = result.mission
    if mission is not None:
        report["identity"] = {"status": mission.identity_status, "mission_name": mission.mission_name,
            "mission_type": _enum(mission.mission_type), "heist_phase": _enum(mission.heist_phase),
            "candidates": list(mission.candidates), "outcome": _enum(mission.outcome),
            "outcome_scope": _enum(mission.outcome_scope), "objective": mission.objective, "raw_text": mission.raw_text}
    return report



def _compare_candidates(stored, fresh):
    """Compare only mutually present fields; old optional fields stay unknown."""
    compared, missing, ignored, changed = [], [], [], []
    def compare(left, right, prefix=""):
        if type(left) is dict and type(right) is dict:
            for key in sorted(right):
                path = prefix + key
                if key not in left:
                    missing.append(path)
                else:
                    compare(left[key], right[key], path + ".")
            ignored.extend(prefix + key for key in sorted(set(left) - set(right)))
        else:
            path = prefix.rstrip(".")
            compared.append(path)
            if left != right:
                changed.append(path)
    if stored is not None:
        compare(stored, fresh)
    return (bool(changed) if compared else None), {
        "scope": "mutually_present_observation_fields", "compared_fields": compared,
        "changed_fields": changed, "missing_stored_fields": missing, "uncompared_stored_fields": ignored,
    }


def replay_sample(directory):
    """Reclassify a completed local export; all saved stages remain observations.

    UnsupportedSampleError means the sample cannot answer the fresh call, not that
    the export is corrupt. Candidate disagreement is a successful diagnostic.
    """
    stored, frame, crops, files = _load_sample(directory)
    recorded = _RecordedOCR(stored["sources"], crops)
    detector = StateDetector(ocr_engine=recorded, template_matcher=TemplateMatcher())
    fresh = _candidate(detector.detect(frame, **{DETECTOR_ARGUMENTS[key]: value for key, value in crops.items()}))
    changed, comparison = _compare_candidates(stored.get("detector_candidate"), fresh)
    report = {
        "schema_version": 1, "mode": "saved_sample_replay", "status": "ok",
        "sample_id": stored["sample_id"], "run_id": stored["run_id"], "input": files,
        "frame": stored["frame"], "capture_provider": stored["capture_provider"],
        "backend": stored["backend"], "geometry": stored["geometry"], "sources": stored["sources"],
        "context": {"fresh": True, "images": 1, "loaded_templates": [], "ocr_execution": False},
        **{key: stored.get(key) for key in ("detector_candidate", "first_guarded", "first_guard_changed", "processed", "selection")},
        "fresh_detector_candidate": fresh,
        "candidate_changed": changed, "candidate_comparison": comparison,
        "used_responses": recorded.used,
        "unused_responses": [source for source in SOURCE_INDICES if source not in recorded.used],
        "limits": [
            "Fresh recorded-text reclassification, not an exact historical rerun or an accuracy/parity test",
            "Empty detector history and no loaded templates; saved guards and selection are observations only",
            "No OCR, preprocessing, live capture, application guards, settings, accounting, database or cadence execution",
            "Crop/text association uses saved source identity and same-call geometry, never current HUD defaults",
            "The format retains one response per source, not full historical call order or repeated-call history",
            "Hashes check consistency, not authenticity; source text and report fields are untrusted data",
            "Observed path checks do not guarantee safety against hostile concurrent filesystem replacement",
        ],
    }
    json.dumps(report, allow_nan=False)
    return report
