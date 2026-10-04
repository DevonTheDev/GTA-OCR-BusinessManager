"""Captured pair export never rereads storage or truncates an earlier report."""

from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from src.utils import exporter as module
from src.utils.exporter import DataExporter


class CapturedPair:
    def __init__(self, report=None):
        self.calls = 0
        self.report = report if report is not None else {
            'format_version': 1, 'kind': 'manual_business_checkin_comparison',
            'orientation': 'comparison_minus_baseline',
            'character': {'id': 1, 'name': '<b>雪</b>'},
            'baseline': {'id': 8, 'stock_percent': None, 'supply_percent': 100,
                         'stock_value': 9223372036854775807, 'note': 'Recorded A\nPlain text'},
            'comparison': {'id': 3, 'stock_percent': 0, 'supply_percent': 0,
                           'stock_value': 0, 'note': 'Recorded B\t🦙'},
            'differences': {'stock_percentage_points': None, 'supply_percentage_points': -100,
                            'stock_value': -9223372036854775807},
        }

    def to_report(self):
        self.calls += 1
        return self.report


@pytest.fixture
def exporter():
    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f'Captured pair export queried storage: {name}')
    return DataExporter(NoReads())


def test_pair_exports_exact_unicode_signed_integers_and_missing_values(exporter, tmp_path):
    capture = CapturedPair()
    destination = tmp_path / 'captured-pair.json'
    result = exporter.export_business_checkin_comparison(capture, destination)
    assert result.success and result.file_path == destination and result.rows_exported == 2
    assert json.loads(destination.read_text()) == capture.report
    assert '雪' in destination.read_text() and '🦙' in destination.read_text()
    assert capture.calls == 1
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize('exists', [False, True])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), '\ud800', object()])
def test_invalid_pair_serialization_does_not_open_output(exporter, tmp_path, monkeypatch, exists, bad):
    destination = tmp_path / 'captured-pair.json'
    if exists:
        destination.write_text('Previous complete comparison')
    monkeypatch.setattr(module, 'atomic_text_writer', lambda *_: pytest.fail('Invalid report opened output'))
    result = exporter.export_business_checkin_comparison(CapturedPair({'bad': bad}), destination)
    assert not result.success and result.rows_exported == 0
    assert destination.read_text() == 'Previous complete comparison' if exists else not destination.exists()


def test_pair_export_uses_exact_utf8_byte_cap_before_creating_output(exporter, tmp_path):
    limit = module.MAX_BUSINESS_CHECKIN_COMPARISON_EXPORT_BYTES
    assert limit == 256 * 1024
    report = {'note': ''}
    overhead = len(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    report['note'] = '🦙' * ((limit - overhead) // 4) + 'x' * ((limit - overhead) % 4)
    assert len(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')) == limit
    destination = tmp_path / 'bounded-pair.json'
    assert exporter.export_business_checkin_comparison(CapturedPair(report), destination).success
    previous = destination.read_bytes()
    report['note'] += 'x'
    assert not exporter.export_business_checkin_comparison(CapturedPair(report), destination).success
    assert destination.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize('exists', [False, True])
def test_partial_staging_failure_preserves_previous_pair(exporter, tmp_path, monkeypatch, exists):
    destination = tmp_path / 'captured-pair.json'
    if exists:
        destination.write_text('Previous complete comparison')
    original = module.atomic_text_writer

    @contextmanager
    def interrupted(path):
        with original(path) as stream:
            def write(content):
                stream.write(content[:45])
                stream.flush()
                assert destination.read_text() == 'Previous complete comparison' if exists else not destination.exists()
                raise OSError('Synthetic interrupted comparison write')
            yield SimpleNamespace(write=write)

    monkeypatch.setattr(module, 'atomic_text_writer', interrupted)
    result = exporter.export_business_checkin_comparison(CapturedPair(), destination)
    assert not result.success
    assert destination.read_text() == 'Previous complete comparison' if exists else not destination.exists()
    assert list(tmp_path.iterdir()) == ([destination] if exists else [])


def test_pair_replace_failure_can_retry_without_rebuilding_snapshot(exporter, tmp_path, monkeypatch):
    from src.utils import persistence
    destination = tmp_path / 'captured-pair.json'
    destination.write_text('Previous complete comparison')
    original = persistence.os.replace
    monkeypatch.setattr(persistence.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('Synthetic replace failure')))
    capture = CapturedPair()
    assert not exporter.export_business_checkin_comparison(capture, destination).success
    assert destination.read_text() == 'Previous complete comparison'
    assert list(tmp_path.iterdir()) == [destination]
    monkeypatch.setattr(persistence.os, 'replace', original)
    assert exporter.export_business_checkin_comparison(capture, destination).success
    assert json.loads(destination.read_text()) == capture.report


def test_pair_report_failure_leaves_destination_and_parent_absent(exporter, tmp_path):
    class BrokenPair:
        def to_report(self):
            raise ValueError('Synthetic unavailable capture')
    destination = tmp_path / 'not-created' / 'pair.json'
    assert not exporter.export_business_checkin_comparison(BrokenPair(), destination).success
    assert not destination.parent.exists()
