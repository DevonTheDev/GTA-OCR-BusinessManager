"""Trend export retains its complete fixed capture and atomic file guarantees."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from importlib import import_module
from importlib.util import find_spec
import json
from types import SimpleNamespace

import pytest

from src.database.business_checkins import BusinessCheckIn, BusinessCheckInHistoryFilters, SQLITE_MAX_INTEGER
from src.utils import exporter as module
from src.utils.exporter import DataExporter


def make_trend():
    assert find_spec("src.database.business_checkin_trend") is not None, "Missing trend domain"
    domain = import_module("src.database.business_checkin_trend")
    stamp = datetime(2026, 10, 4, 12, 34, 56, 123456, tzinfo=timezone.utc)
    rows = (
        BusinessCheckIn(2**53 + 1, 1, "bunker", stamp, 0, None, SQLITE_MAX_INTEGER, "literal %_\\ 雪\n\tfull note"),
        BusinessCheckIn(SQLITE_MAX_INTEGER, 1, "bunker", stamp, None, 100, 0, "note only stock"),
    )
    return domain.BusinessCheckInTrend(1, "Saved <b>雪</b>", "bunker", stamp, rows,
                                       BusinessCheckInHistoryFilters("%_\\", date.min, date.max))


@pytest.fixture
def exporter():
    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f"Fixed trend export accessed storage: {name}")
    return DataExporter(NoReads())


def test_export_uses_existing_atomic_snapshot_writer_and_exact_complete_report(exporter, tmp_path):
    trend = make_trend()
    path = tmp_path / "trend.json"
    expected = trend.to_report()
    result = exporter.export_business_checkins_snapshot(trend, path)
    assert result.success and result.file_path == path and result.rows_exported == 2
    encoded = path.read_text(encoding="utf-8")
    assert encoded == json.dumps(expected, ensure_ascii=False, indent=2, allow_nan=False)
    assert json.loads(encoded) == expected
    assert "雪" in encoded and str(SQLITE_MAX_INTEGER) in encoded
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("exists", [False, True])
def test_export_bound_is_utf8_bytes_and_preserves_prior_destination(exporter, tmp_path, monkeypatch, exists):
    trend = make_trend()
    path = tmp_path / "trend.json"
    if exists:
        path.write_bytes(b"prior report")
    payload = json.dumps(trend.to_report(), ensure_ascii=False, indent=2, allow_nan=False)
    assert len(payload.encode("utf-8")) > len(payload)
    monkeypatch.setattr(module, "MAX_BUSINESS_CHECKINS_EXPORT_BYTES", len(payload.encode("utf-8")) - 1)
    with monkeypatch.context() as context:
        context.setattr(module, "atomic_text_writer", lambda *a: pytest.fail("Oversized export opened destination"))
        result = exporter.export_business_checkins_snapshot(trend, path)
    assert not result.success and result.rows_exported == 0
    assert path.read_bytes() == b"prior report" if exists else not path.exists()
    monkeypatch.setattr(module, "MAX_BUSINESS_CHECKINS_EXPORT_BYTES", len(payload.encode("utf-8")))
    assert exporter.export_business_checkins_snapshot(trend, path).success
    assert path.read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("exists", [False, True])
def test_interrupted_write_preserves_prior_destination_and_cleans_staging(exporter, tmp_path, monkeypatch, exists):
    trend = make_trend()
    path = tmp_path / "trend.json"
    if exists:
        path.write_bytes(b"prior report")
    original = module.atomic_text_writer

    @contextmanager
    def interrupted(destination):
        with original(destination) as stream:
            def fail(payload):
                stream.write(payload[:100])
                stream.flush()
                assert path.read_bytes() == b"prior report" if exists else not path.exists()
                raise OSError("synthetic interrupted trend write")
            yield SimpleNamespace(write=fail)

    monkeypatch.setattr(module, "atomic_text_writer", interrupted)
    result = exporter.export_business_checkins_snapshot(trend, path)
    assert not result.success
    assert path.read_bytes() == b"prior report" if exists else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if exists else [])


def test_replace_failure_preserves_prior_export_and_retry_is_complete(exporter, tmp_path, monkeypatch):
    trend = make_trend()
    from src.utils import persistence
    path = tmp_path / "trend.json"
    path.write_bytes(b"prior report")

    def fail_replace(*args):
        raise OSError("synthetic trend replacement failure")

    with monkeypatch.context() as context:
        context.setattr(persistence.os, "replace", fail_replace)
        assert not exporter.export_business_checkins_snapshot(trend, path).success
    assert path.read_bytes() == b"prior report"
    assert list(tmp_path.iterdir()) == [path]
    assert exporter.export_business_checkins_snapshot(trend, path).success
    assert json.loads(path.read_text(encoding="utf-8")) == trend.to_report()
