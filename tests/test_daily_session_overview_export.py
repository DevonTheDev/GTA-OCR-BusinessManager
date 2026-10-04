"""Daily overview exports retain all accepted days and source sessions without rereading storage."""

import json
from types import SimpleNamespace

import pytest

from src.utils import exporter as module
from src.utils.exporter import DataExporter


class Snapshot:
    rows = (object(), object())

    def __init__(self, report=None):
        self.report = report if report is not None else {
            "format_version": 1, "kind": "daily_completed_session_overview",
            "days": [{"day": "2026-10-04", "sessions": 2, "net_change_total": -20}],
            "rows": [{"character_name": "<b>雪 & player</b>", "net_change": -20},
                     {"character_name": "Other", "net_change": None}],
        }
        self.calls = 0

    def to_report(self):
        self.calls += 1
        return self.report


@pytest.fixture
def exporter():
    class ForbiddenRepository:
        def __getattr__(self, name):
            pytest.fail(f"Captured export must not read storage: {name}")
    return DataExporter(ForbiddenRepository())


def test_exports_exact_accepted_summary_as_unicode_json(exporter, tmp_path):
    snapshot = Snapshot()
    target = tmp_path / "daily-overview.json"
    result = exporter.export_daily_session_overview(snapshot, target)
    assert result.success and result.file_path == target and result.rows_exported == 2
    assert json.loads(target.read_text()) == snapshot.report
    assert "雪" in target.read_text()
    assert snapshot.calls == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["daily-overview.json"]


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("failure", ["nonfinite", "encoding", "serialization", "oversized"])
def test_invalid_summary_never_opens_or_replaces_destination(exporter, tmp_path, monkeypatch, exists, failure):
    target = tmp_path / "daily-overview.json"
    if exists:
        target.write_text("prior contents")
    report = {"nonfinite": float("nan"), "encoding": "\ud800", "serialization": object(),
              "oversized": "雪" * 20}[failure]
    if failure == "oversized":
        monkeypatch.setattr(module, "MAX_DAILY_SESSION_OVERVIEW_EXPORT_BYTES", 32)
    monkeypatch.setattr(module, "atomic_text_writer", lambda *_args: pytest.fail("Invalid payload opened output"))
    result = exporter.export_daily_session_overview(Snapshot({"value": report}), target)
    assert not result.success
    assert target.read_text() == "prior contents" if exists else not target.exists()


@pytest.mark.parametrize("exists", [False, True])
def test_partial_staging_write_keeps_previous_report_readable(exporter, tmp_path, monkeypatch, exists):
    from contextlib import contextmanager
    target = tmp_path / "daily-overview.json"
    if exists:
        target.write_text("prior contents")
    original = module.atomic_text_writer

    @contextmanager
    def interrupted(path):
        with original(path) as stream:
            def write(text):
                stream.write(text[:10])
                stream.flush()
                assert target.read_text() == "prior contents" if exists else not target.exists()
                raise OSError("synthetic disk failure")
            yield SimpleNamespace(write=write)

    monkeypatch.setattr(module, "atomic_text_writer", interrupted)
    result = exporter.export_daily_session_overview(Snapshot(), target)
    assert not result.success
    assert target.read_text() == "prior contents" if exists else not target.exists()
    assert list(tmp_path.iterdir()) == ([target] if exists else [])


def test_replace_failure_preserves_report_and_allows_retry(exporter, tmp_path, monkeypatch):
    from src.utils import persistence
    target = tmp_path / "daily-overview.json"
    target.write_text("prior contents")
    replace = persistence.os.replace
    def fail(*_args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(persistence.os, "replace", fail)
    assert not exporter.export_daily_session_overview(Snapshot(), target).success
    assert target.read_text() == "prior contents"
    assert list(tmp_path.iterdir()) == [target]
    monkeypatch.setattr(persistence.os, "replace", replace)
    assert exporter.export_daily_session_overview(Snapshot(), target).success
    assert json.loads(target.read_text())["kind"] == "daily_completed_session_overview"
