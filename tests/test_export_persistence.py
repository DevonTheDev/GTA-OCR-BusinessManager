"""Export failure recovery using disposable SQLite and real CSV/JSON files."""

import csv
import json
from types import SimpleNamespace

import pytest

from src.database.repository import Repository
from src.utils import exporter as module
from src.utils import persistence


KINDS = ["info", "activities", "earnings", "summary", "history", "json", "breakdown"]


@pytest.fixture(params=KINDS)
def export_case(request, tmp_path):
    kind = request.param
    repo = Repository(str(tmp_path / "fixture.db"))
    assert repo.initialize()
    character = repo.get_or_create_character("Fixture player")
    session = repo.start_session(character, start_money=1000)
    label = 'Café, "quoted"\r\nsecond line'
    for name, activity_type in [(label, "VIP_WORK"), ("Second", "SELL_MISSION")]:
        repo.log_activity(session.id, activity_type, name, 100, True, 120)
        repo.log_earning(session.id, 100, name, 1200)
    repo.end_session(session.id, 1200)
    second = repo.start_session(character, start_money=1200)
    repo.end_session(second.id, 1300)
    exporter = module.DataExporter(repo)
    folder = tmp_path / "exports"
    folder.mkdir()
    if kind in {"info", "activities", "earnings"}:
        path = folder / f"session_{session.id}_{kind}.csv"
        call = lambda: exporter.export_session_to_csv(session.id, folder)
        ordinal = ["info", "activities", "earnings"].index(kind)
    else:
        path = folder / ("result.json" if kind == "json" else "result.csv")
        method, entity_id = {
            "summary": (exporter.export_sessions_summary, character.id),
            "history": (exporter.export_activity_history, character.id),
            "json": (exporter.export_to_json, session.id),
            "breakdown": (exporter.export_earnings_breakdown, character.id),
        }[kind]
        call = lambda: method(entity_id, path)
        ordinal = 0
    previous = b'{"saved":"valid"}' if kind == "json" else b"Saved,Result\r\nprevious,valid\r\n"
    yield SimpleNamespace(
        kind=kind, path=path, folder=folder, call=call, ordinal=ordinal,
        previous=previous, label=label,
    )
    repo.close()


def fail_serialization(case, monkeypatch):
    if case.kind == "json":
        def fail_json(data, handle, **kwargs):
            handle.write('{"partial":')
            raise OSError("interrupted export")
        monkeypatch.setattr(module.json, "dump", fail_json)
        return

    original_writer = module.csv.writer
    opened = 0

    def writer_factory(handle, *args, **kwargs):
        nonlocal opened
        this_file = opened
        opened += 1
        writer = original_writer(handle, *args, **kwargs)
        rows = 0

        def writerow(row):
            nonlocal rows
            writer.writerow(row)
            rows += 1
            if this_file == case.ordinal and rows == 2:
                raise OSError("interrupted export")

        return SimpleNamespace(writerow=writerow)

    monkeypatch.setattr(module.csv, "writer", writer_factory)


@pytest.mark.parametrize("existing", [True, False], ids=["existing", "first-save"])
def test_partial_serialization_does_not_replace_destination(export_case, monkeypatch, existing):
    case = export_case
    if existing:
        case.path.write_bytes(case.previous)
    with monkeypatch.context() as patch:
        fail_serialization(case, patch)
        result = case.call()
    assert result.success is False
    assert result.error_message == "interrupted export"
    if existing:
        assert case.path.read_bytes() == case.previous
    else:
        assert not case.path.exists()
    assert list(case.folder.glob("*.tmp")) == []
    # Normal retries replace the old file only after successful serialization.
    assert case.call().success is True
    assert case.path.read_bytes() != case.previous
    assert list(case.folder.glob("*.tmp")) == []


def test_replacement_failure_preserves_previous_export(export_case, monkeypatch):
    case = export_case
    case.path.write_bytes(case.previous)
    original_replace = persistence.os.replace
    attempts = []

    def replace(source, destination):
        if destination == case.path:
            attempts.append(source)
            assert source.parent == case.path.parent
            assert source != case.path and source.read_bytes()
            raise PermissionError("export replacement denied")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", replace)
        result = case.call()
    assert result.success is False
    assert result.error_message == "export replacement denied"
    assert len(attempts) == 1
    assert case.path.read_bytes() == case.previous
    assert list(case.folder.glob("*.tmp")) == []
    assert case.call().success is True


def test_close_failure_preserves_previous_export(export_case, monkeypatch):
    case = export_case
    case.path.write_bytes(case.previous)
    original_factory = persistence.NamedTemporaryFile
    opened = []

    class FailingClose:
        def __init__(self, **kwargs):
            self.handle = original_factory(**kwargs)
            self.ordinal = len(opened)
            opened.append(self.handle)

        def __enter__(self):
            return self.handle.__enter__()

        def __exit__(self, *args):
            self.handle.__exit__(*args)
            if self.ordinal == case.ordinal:
                raise OSError("export flush failed on close")

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "NamedTemporaryFile", FailingClose)
        result = case.call()
    assert result.success is False
    assert result.error_message == "export flush failed on close"
    assert opened and all(handle.closed for handle in opened)
    assert case.path.read_bytes() == case.previous
    assert list(case.folder.glob("*.tmp")) == []


def test_successful_exports_keep_csv_newlines_unicode_and_json(export_case):
    case = export_case
    assert case.call().success is True
    payload = case.path.read_bytes()
    if case.kind == "json":
        assert json.loads(payload)["activities"][0]["name"] == case.label
    else:
        assert b"\r\r\n" not in payload
        with case.path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) >= 2
        if case.kind in {"activities", "earnings", "history"}:
            assert any(case.label in row for row in rows)
    assert list(case.folder.glob("*.tmp")) == []


def test_text_writer_accepts_explicit_newline_translation(tmp_path):
    path = tmp_path / "newlines.txt"
    with persistence.atomic_text_writer(path, newline="\r\n") as stream:
        stream.write("one\ntwo\n")
    assert path.read_bytes() == b"one\r\ntwo\r\n"


def test_export_preserves_existing_symlink_destination(export_case):
    case = export_case
    target = case.folder / "relocated-output"
    target.write_bytes(case.previous)
    try:
        case.path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert case.call().success is True
    assert case.path.is_symlink()
    assert target.read_bytes() == case.path.read_bytes()
    assert target.read_bytes() != case.previous
    assert list(case.folder.glob("*.tmp")) == []


@pytest.mark.parametrize("export_case", ["earnings"], indirect=True)
def test_session_export_failure_is_per_file_not_an_all_files_transaction(export_case, monkeypatch):
    case = export_case
    info = case.path.with_name(case.path.name.replace("earnings", "info"))
    activities = case.path.with_name(case.path.name.replace("earnings", "activities"))
    for path in (info, activities, case.path):
        path.write_bytes(case.previous)
    with monkeypatch.context() as patch:
        fail_serialization(case, patch)
        result = case.call()
    assert result.success is False
    assert info.read_bytes() != case.previous
    assert activities.read_bytes() != case.previous
    assert case.path.read_bytes() == case.previous
    assert list(case.folder.glob("*.tmp")) == []
