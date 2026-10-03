"""Manual observation exports preserve their accepted data and prior files."""

from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from src.utils import exporter as module
from src.utils.exporter import DataExporter


class Snapshot:
    def __init__(self, value=None):
        self.rows = (object(), object())
        self.calls = 0
        self.value = value if value is not None else {
            'format_version': 1, 'kind': 'manual_business_checkins',
            'character': {'id': 9223372036854775807, 'name': '<b>雪</b>'},
            'rows': [{'stock_percent': 0, 'supply_percent': None, 'stock_value': 9223372036854775807,
                      'note': 'Literal <b>text</b>\nPersonal observation'},
                     {'stock_percent': None, 'supply_percent': 0, 'stock_value': None}],
        }

    def to_report(self):
        self.calls += 1
        return self.value


@pytest.fixture
def exporter():
    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f'Snapshot export must not query storage: {name}')
    return DataExporter(NoReads())


def test_exact_snapshot_exports_unicode_unknown_zero_and_large_integers(exporter, tmp_path):
    snapshot = Snapshot()
    path = tmp_path / 'check-ins.json'
    result = exporter.export_business_checkins_snapshot(snapshot, path)
    assert result.success and result.file_path == path and result.rows_exported == 2
    assert json.loads(path.read_text()) == snapshot.value
    assert '雪' in path.read_text()
    assert snapshot.calls == 1
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('exists', [False, True])
@pytest.mark.parametrize('value', [float('nan'), '\ud800', object(), 'large'])
def test_invalid_or_oversized_report_never_opens_output(exporter, tmp_path, monkeypatch, exists, value):
    path = tmp_path / 'check-ins.json'
    if exists:
        path.write_text('prior observation')
    if value == 'large':
        value = '雪' * 20
        monkeypatch.setattr(module, 'MAX_BUSINESS_CHECKINS_EXPORT_BYTES', 32)
    monkeypatch.setattr(module, 'atomic_text_writer', lambda *a: pytest.fail('Invalid export opened output'))
    result = exporter.export_business_checkins_snapshot(Snapshot({'value': value}), path)
    assert not result.success
    assert path.read_text() == 'prior observation' if exists else not path.exists()


@pytest.mark.parametrize('exists', [False, True])
def test_interrupted_staging_write_preserves_previous_observation(exporter, tmp_path, monkeypatch, exists):
    path = tmp_path / 'check-ins.json'
    if exists:
        path.write_text('prior observation')
    original = module.atomic_text_writer

    @contextmanager
    def interrupted(destination):
        with original(destination) as stream:
            def fail(text):
                stream.write(text[:30])
                stream.flush()
                assert path.read_text() == 'prior observation' if exists else not path.exists()
                raise OSError('synthetic interrupted write')
            yield SimpleNamespace(write=fail)

    monkeypatch.setattr(module, 'atomic_text_writer', interrupted)
    assert not exporter.export_business_checkins_snapshot(Snapshot(), path).success
    assert path.read_text() == 'prior observation' if exists else not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if exists else [])


def test_replace_failure_then_retry_preserves_complete_report(exporter, tmp_path, monkeypatch):
    from src.utils import persistence
    path = tmp_path / 'check-ins.json'
    path.write_text('prior observation')
    original = persistence.os.replace
    monkeypatch.setattr(persistence.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('synthetic replace failure')))
    assert not exporter.export_business_checkins_snapshot(Snapshot(), path).success
    assert path.read_text() == 'prior observation'
    assert list(tmp_path.iterdir()) == [path]
    monkeypatch.setattr(persistence.os, 'replace', original)
    snapshot = Snapshot()
    assert exporter.export_business_checkins_snapshot(snapshot, path).success
    assert json.loads(path.read_text()) == snapshot.value
