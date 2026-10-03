"""Accepted ledger pages serialize before staging and never reread SQLite."""

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils import exporter as module
from src.utils import persistence


PREVIOUS = b'{"previous":"complete ledger"}\n'


def snapshot(count=2):
    report = {
        "format_version": 1, "kind": "completed_activity_ledger_page",
        "scope": "completed_sessions", "observed_at": "2026-10-03T12:00:00+00:00",
        "filters": {"query": "Literal 50%_!"},
        "pagination": {"offset": 25, "limit": 25, "total": 40, "rows_exported": count},
        "rows": [{"id": index + 1, "name": '<b>Café 雪 🚀</b> "quoted"\nnext',
                  "notes": None, "recorded_amount": -25 if index == 0 else 0,
                  "duration_seconds": None, "outcome": "unknown"} for index in range(count)],
    }
    return SimpleNamespace(rows=tuple(report["rows"]), to_report=lambda: report), report


@pytest.fixture
def exporter():
    # Any repository use would fail: this operation accepts a captured page only.
    return module.DataExporter(SimpleNamespace())


def test_page_export_api_is_available():
    assert callable(getattr(module.DataExporter, "export_activity_ledger_page", None)), "Ledger needs captured-page export"


@pytest.mark.parametrize("count", [0, 2])
def test_exact_page_export_keeps_metadata_counts_nulls_and_literal_unicode(exporter, tmp_path, count):
    page, report = snapshot(count)
    output = tmp_path / "nested" / "ledger.json"
    result = exporter.export_activity_ledger_page(page, output)
    assert result.success and result.rows_exported == count
    assert result.file_path == output and not result.error_message
    raw = output.read_text(encoding="utf-8")
    assert raw == json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    assert json.loads(raw) == report
    if count:
        assert "Café 雪 🚀" in raw and "\\u00e9" not in raw
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), object(), "\ud800"])
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_report_never_opens_output(exporter, tmp_path, monkeypatch, bad, existing):
    page = SimpleNamespace(rows=(), to_report=lambda: {"bad": bad})
    output = tmp_path / "nested" / "ledger.json"
    if existing:
        output.parent.mkdir()
        output.write_bytes(PREVIOUS)
    monkeypatch.setattr(module, "atomic_text_writer", lambda *args, **kwargs: pytest.fail("Invalid report opened output"))
    result = exporter.export_activity_ledger_page(page, output)
    assert not result.success and result.rows_exported == 0
    assert result.file_path is None and result.error_message
    assert output.read_bytes() == PREVIOUS if existing else not output.parent.exists()


def test_report_and_row_count_are_admitted_before_staging(exporter, tmp_path, monkeypatch):
    output = tmp_path / "ledger.json"
    output.write_bytes(PREVIOUS)
    monkeypatch.setattr(module, "atomic_text_writer", lambda *args, **kwargs: pytest.fail("Incomplete page opened output"))
    page = SimpleNamespace(to_report=lambda: {"valid": "json"})
    result = exporter.export_activity_ledger_page(page, output)
    assert not result.success and output.read_bytes() == PREVIOUS


def test_report_byte_cap_counts_utf8_and_refuses_before_open(exporter, tmp_path, monkeypatch):
    page, report = snapshot()
    output = tmp_path / "ledger.json"
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    monkeypatch.setattr(module, "MAX_ACTIVITY_LEDGER_EXPORT_BYTES", len(encoded), raising=False)
    assert exporter.export_activity_ledger_page(page, output).success
    assert output.read_bytes() == encoded
    output.write_bytes(PREVIOUS)
    monkeypatch.setattr(module, "MAX_ACTIVITY_LEDGER_EXPORT_BYTES", len(encoded) - 1)
    monkeypatch.setattr(module, "atomic_text_writer", lambda *args, **kwargs: pytest.fail("Oversize report opened output"))
    result = exporter.export_activity_ledger_page(page, output)
    assert not result.success and "limit" in result.error_message.lower()
    assert output.read_bytes() == PREVIOUS


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("stage", ["write", "close", "replace"])
def test_partial_failure_preserves_last_complete_page_and_retries(exporter, tmp_path, monkeypatch, existing, stage):
    page, report = snapshot()
    output = tmp_path / "ledger.json"
    if existing:
        output.write_bytes(PREVIOUS)
    original = persistence.NamedTemporaryFile
    handles = []

    class FaultingFile:
        def __init__(self, **kwargs):
            self.handle = original(**kwargs)
            handles.append(self.handle)
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def __enter__(self):
            self.handle.__enter__()
            return self
        def write(self, value):
            if stage == "write":
                self.handle.write(value[:30])
                self.handle.flush()
                assert output.read_bytes() == PREVIOUS if existing else not output.exists()
                raise OSError("ledger write failed")
            return self.handle.write(value)
        def __exit__(self, *args):
            result = self.handle.__exit__(*args)
            if stage == "close":
                raise OSError("ledger close failed")
            return result

    def fail_replace(source, destination):
        assert destination == output and Path(source).parent == output.parent
        assert handles[0].closed
        assert json.loads(Path(source).read_text()) == report
        raise OSError("ledger replace failed")

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "NamedTemporaryFile", FaultingFile)
        if stage == "replace":
            patch.setattr(persistence.os, "replace", fail_replace)
        result = exporter.export_activity_ledger_page(page, output)
    assert not result.success and result.error_message == f"ledger {stage} failed"
    assert result.rows_exported == 0 and result.file_path is None
    assert handles and all(handle.closed for handle in handles)
    assert output.read_bytes() == PREVIOUS if existing else not output.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    assert exporter.export_activity_ledger_page(page, output).success
    assert json.loads(output.read_text()) == report


def test_serialized_page_and_count_are_fixed_before_writer_callbacks(exporter, tmp_path, monkeypatch):
    page, report = snapshot()
    expected = json.loads(json.dumps(report))
    original = module.atomic_text_writer

    @contextmanager
    def change_after_serialization(path):
        report["rows"].clear()
        page.rows = ()
        with original(path) as stream:
            yield stream

    monkeypatch.setattr(module, "atomic_text_writer", change_after_serialization)
    output = tmp_path / "ledger.json"
    result = exporter.export_activity_ledger_page(page, output)
    assert result.success and result.rows_exported == 2
    assert json.loads(output.read_text()) == expected
