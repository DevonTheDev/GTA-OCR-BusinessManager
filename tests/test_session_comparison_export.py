"""Fixed comparison snapshots exported to real, atomically replaced JSON files."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.database.repository import Repository
from src.utils import exporter as module
from src.utils import persistence


PREVIOUS = b'{"previous":"complete export"}\n'
LITERAL_NAME = '<b>Café 雪 🚀</b>, "quoted"\r\nsecond line'
LITERAL_TYPE = '<script>literal & 雪</script>\ncustom'


@pytest.fixture
def exporter(tmp_path):
    repo = Repository(str(tmp_path / "source.sqlite"))
    assert repo.initialize()
    yield module.DataExporter(repo)
    repo.close()


@pytest.fixture
def snapshot():
    from src.database.session_comparison import (
        ActivityTypeComparison, SessionComparison, SessionMetrics, SessionSummary,
    )

    return SessionComparison(
        baseline=SessionSummary(
            session_id=11,
            character_id=3,
            character_name=LITERAL_NAME,
            started_at=datetime(2026, 10, 1, 10, tzinfo=timezone.utc),
            ended_at=datetime(2026, 10, 1, 11, tzinfo=timezone.utc),
            start_money=100,
            end_money=900,
            metrics=SessionMetrics(-1200, 3600.0, -1200.0, 4, 1, 2, 1),
        ),
        comparison=SessionSummary(
            session_id=27,
            character_id=8,
            character_name="Other player",
            started_at=None,
            ended_at=datetime(2026, 10, 2, 10, tzinfo=timezone.utc),
            start_money=None,
            end_money=0,
            metrics=SessionMetrics(0, None, None, 2, 1, 0, 1),
        ),
        differences=SessionMetrics(1200, None, None, -2, 0, -2, 0),
        activity_types=(
            ActivityTypeComparison(None, 1, 0, -1),
            ActivityTypeComparison("", 1, 0, -1),
            ActivityTypeComparison(LITERAL_TYPE, 2, 1, -1),
            ActivityTypeComparison("VIP_WORK", 0, 1, 1),
        ),
        generated_at=datetime(2026, 10, 3, 9, 8, 7, tzinfo=timezone.utc),
    )


def test_exports_complete_snapshot_as_readable_literal_unicode_json(exporter, snapshot, tmp_path):
    path = tmp_path / "nested" / "exports" / "comparison.json"

    result = exporter.export_session_comparison(snapshot, path)

    assert result.success is True
    assert result.file_path == path
    assert result.rows_exported == 2
    assert result.error_message == ""
    raw = path.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report == snapshot.to_report()
    assert '\n  "format_version": 1' in raw
    assert "Café 雪 🚀" in raw
    assert "\\u00e9" not in raw
    assert report["orientation"] == "comparison_minus_baseline"
    assert report["timezone"] == "UTC"
    assert report["baseline"]["session_id"] == 11
    assert report["baseline"]["character_name"] == LITERAL_NAME
    assert report["baseline"]["metrics"]["net_change"] == -1200
    assert report["comparison"]["session_id"] == 27
    assert report["comparison"]["metrics"]["net_change"] == 0
    assert report["comparison"]["metrics"]["net_per_hour"] is None
    assert report["differences"]["recorded_activities"] == -2
    assert report["differences"]["duration_seconds"] is None
    assert [row["activity_type"] for row in report["activity_types"]] == [
        None, "", LITERAL_TYPE, "VIP_WORK",
    ]
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("existing", [True, False], ids=["existing", "first-save"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), object()],
                         ids=["nan", "infinity", "negative-infinity", "unsupported-object"])
def test_invalid_payload_never_touches_output(exporter, snapshot, tmp_path, monkeypatch, existing, bad_value):
    path = tmp_path / "new" / "comparison.json"
    if existing:
        path.parent.mkdir()
        path.write_bytes(PREVIOUS)
    # Inject a corrupt report at the serialization boundary. It must be fully
    # validated before even opening a temporary output or creating directories.
    bad_snapshot = SimpleNamespace(to_report=lambda: {"invalid": bad_value})

    def unexpected_output(*args, **kwargs):
        pytest.fail("invalid reports must not open output")

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_text_writer", unexpected_output)
        result = exporter.export_session_comparison(bad_snapshot, path)

    assert result.success is False
    assert result.file_path is None
    assert result.rows_exported == 0
    assert result.error_message
    if existing:
        assert path.read_bytes() == PREVIOUS
        assert list(path.parent.iterdir()) == [path]
    else:
        assert not path.parent.exists()
    assert exporter.export_session_comparison(snapshot, path).success is True
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot.to_report()


def test_report_build_failure_leaves_output_untouched(exporter, tmp_path):
    path = tmp_path / "new" / "comparison.json"

    def fail_report():
        raise ValueError("comparison snapshot is unavailable")

    result = exporter.export_session_comparison(SimpleNamespace(to_report=fail_report), path)

    assert result.success is False
    assert result.error_message == "comparison snapshot is unavailable"
    assert result.rows_exported == 0
    assert not path.parent.exists()


@pytest.mark.parametrize("existing", [True, False], ids=["existing", "first-save"])
@pytest.mark.parametrize("stage", ["write", "close", "replace"])
def test_output_failure_preserves_destination_and_allows_retry(
    exporter, snapshot, tmp_path, monkeypatch, existing, stage,
):
    path = tmp_path / "comparison.json"
    if existing:
        path.write_bytes(PREVIOUS)
    original_factory = persistence.NamedTemporaryFile
    opened = []
    replacements = []
    error = f"comparison {stage} failed"

    class FaultingFile:
        def __init__(self, **kwargs):
            self.handle = original_factory(**kwargs)
            opened.append(self.handle)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            self.handle.__enter__()
            return self

        def write(self, content):
            if stage == "write":
                self.handle.write(content[:20])
                self.handle.flush()
                raise OSError(error)
            return self.handle.write(content)

        def __exit__(self, *args):
            result = self.handle.__exit__(*args)
            if stage == "close":
                raise OSError(error)
            return result

    def fail_replace(source, destination):
        replacements.append(source)
        assert destination == path
        assert source.parent == path.parent
        assert source != path
        assert opened[0].closed
        assert json.loads(source.read_text(encoding="utf-8")) == snapshot.to_report()
        raise PermissionError(error)

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "NamedTemporaryFile", FaultingFile)
        if stage == "replace":
            patch.setattr(persistence.os, "replace", fail_replace)
        result = exporter.export_session_comparison(snapshot, path)

    assert result.success is False
    assert result.error_message == error
    assert result.rows_exported == 0
    assert result.file_path is None
    assert opened and all(handle.closed for handle in opened)
    assert len(replacements) == (1 if stage == "replace" else 0)
    if existing:
        assert path.read_bytes() == PREVIOUS
    else:
        assert not path.exists()
    assert list(tmp_path.glob("*.tmp")) == []

    result = exporter.export_session_comparison(snapshot, path)
    assert result.success is True
    assert result.rows_exported == 2
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot.to_report()
    assert list(tmp_path.glob("*.tmp")) == []


def test_temporary_creation_failure_preserves_previous_export(exporter, snapshot, tmp_path, monkeypatch):
    path = tmp_path / "comparison.json"
    path.write_bytes(PREVIOUS)

    def fail_creation(**kwargs):
        raise PermissionError("comparison temporary file denied")

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "NamedTemporaryFile", fail_creation)
        result = exporter.export_session_comparison(snapshot, path)

    assert result.success is False
    assert result.error_message == "comparison temporary file denied"
    assert path.read_bytes() == PREVIOUS
    assert list(tmp_path.glob("*.tmp")) == []
    assert exporter.export_session_comparison(snapshot, path).success is True


def test_cleanup_failure_does_not_hide_replace_error(exporter, snapshot, tmp_path, monkeypatch, caplog):
    path = tmp_path / "comparison.json"
    path.write_bytes(PREVIOUS)
    original_unlink = Path.unlink

    def fail_replace(*args):
        raise PermissionError("original comparison replacement denied")

    def fail_cleanup(candidate, *args, **kwargs):
        if candidate.suffix == ".tmp":
            raise PermissionError("temporary cleanup denied")
        return original_unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        result = exporter.export_session_comparison(snapshot, path)

    assert result.success is False
    assert result.error_message == "original comparison replacement denied"
    assert path.read_bytes() == PREVIOUS
    assert "Unable to remove temporary state file" in caplog.text
    temporary = list(tmp_path.glob("*.tmp"))
    assert len(temporary) == 1
    temporary[0].unlink()
    assert exporter.export_session_comparison(snapshot, path).success is True


def test_snapshot_export_does_not_read_repository(exporter, snapshot, tmp_path, monkeypatch):
    def forbid_read(*args, **kwargs):
        pytest.fail("export must use the supplied snapshot without reading SQLite")

    monkeypatch.setattr(exporter._repo, "_session_scope", forbid_read)
    paths = [tmp_path / "first.json", tmp_path / "second.json"]
    for path in paths:
        result = exporter.export_session_comparison(snapshot, path)
        assert result.success is True
        assert result.rows_exported == 2
    assert paths[0].read_bytes() == paths[1].read_bytes()


def test_saved_snapshot_survives_later_database_changes(exporter, tmp_path, monkeypatch):
    repo = exporter._repo
    character = repo.get_or_create_character(LITERAL_NAME)
    baseline = repo.start_session(character, start_money=100)
    repo.log_activity(baseline.id, LITERAL_TYPE, "Original activity", 500, True, 120)
    assert repo.end_session(baseline.id, 400)
    comparison = repo.start_session(character, start_money=400)
    assert repo.end_session(comparison.id, 200)
    displayed_snapshot = repo.get_session_comparison(baseline.id, comparison.id)
    expected = displayed_snapshot.to_report()
    path = tmp_path / "snapshot.json"
    assert exporter.export_session_comparison(displayed_snapshot, path).success is True
    previous = path.read_bytes()

    with sqlite3.connect(repo._db_path) as connection:
        connection.execute("UPDATE characters SET name = ?", ("Renamed after comparison",))
        connection.execute("UPDATE sessions SET total_earnings = 999999 WHERE id = ?", (baseline.id,))
        connection.execute("DELETE FROM activities WHERE session_id = ?", (baseline.id,))
        connection.execute("DELETE FROM sessions WHERE id = ?", (comparison.id,))
        connection.commit()
        before = list(connection.iterdump())

    def forbid_read(*args, **kwargs):
        pytest.fail("source mutations must not retarget the displayed snapshot")

    monkeypatch.setattr(repo, "_session_scope", forbid_read)
    result = exporter.export_session_comparison(displayed_snapshot, path)

    assert result.success is True
    assert path.read_bytes() == previous
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert expected["baseline"]["character_name"] == LITERAL_NAME
    assert expected["baseline"]["metrics"]["net_change"] == 300
    assert expected["comparison"]["metrics"]["net_change"] == -200
    with sqlite3.connect(repo._db_path) as connection:
        assert list(connection.iterdump()) == before


def test_symlink_export_preserves_link_and_reports_supplied_path(exporter, snapshot, tmp_path):
    target = tmp_path / "actual.json"
    target.write_bytes(PREVIOUS)
    link = tmp_path / "chosen.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    result = exporter.export_session_comparison(snapshot, link)

    assert result.success is True
    assert result.file_path == link
    assert link.is_symlink()
    assert target.read_bytes() == link.read_bytes()
    assert json.loads(target.read_text(encoding="utf-8")) == snapshot.to_report()
    assert list(tmp_path.glob("*.tmp")) == []
