"""Deterministic process sampling and elapsed-time metrics; no native UI needed."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.utils import performance


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(wall=1000.0, elapsed=10.0)
    monkeypatch.setattr(performance, 'time', SimpleNamespace(
        time=lambda: value.wall, monotonic=lambda: value.elapsed,
    ))
    return value


class ProcessError(Exception):
    pass


def fake_psutil(monkeypatch, failure=None, slow_cpu=False):
    calls = []
    class Process:
        def __init__(self):
            calls.append('create')
            if failure == 'create':
                raise ProcessError('unavailable process')
            self.samples = 0
        def cpu_percent(self):
            calls.append('cpu')
            if slow_cpu:
                time.sleep(0.01)  # Model OS metric I/O releasing the GIL.
            if failure == 'cpu':
                raise ProcessError('CPU permission denied')
            self.samples += 1
            return 0.0 if self.samples == 1 else 12.5
        def memory_info(self):
            calls.append('memory')
            if failure == 'memory':
                raise ProcessError('memory permission denied')
            return SimpleNamespace(rss=64 * 1024 * 1024)
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(Process=Process, Error=ProcessError))
    return calls


def test_cpu_sampling_preserves_the_process_baseline(monkeypatch, clock):
    calls = fake_psutil(monkeypatch)
    monitor = performance.PerformanceMonitor()
    assert monitor.get_metrics().cpu_percent == 0.0
    clock.elapsed += 1
    clock.wall += 1
    metrics = monitor.get_metrics()
    assert metrics.cpu_percent == 12.5
    assert metrics.memory_mb == 64.0
    assert calls.count('create') == 1


def test_capture_and_ui_readers_share_a_bounded_sampling_window(monkeypatch, clock):
    calls = fake_psutil(monkeypatch)
    monitor = performance.PerformanceMonitor()
    monitor.get_metrics()
    for _ in range(10):
        monitor.get_metrics()
    assert calls == ['create', 'cpu', 'memory']
    clock.elapsed += 0.5
    assert monitor.get_metrics().cpu_percent == 12.5
    assert calls.count('cpu') == 2


@pytest.mark.parametrize('failure,expected_memory', [('create', 0.0), ('cpu', 64.0), ('memory', 0.0)])
def test_optional_process_errors_do_not_break_capture_metrics(monkeypatch, clock, failure, expected_memory):
    fake_psutil(monkeypatch, failure)
    monitor = performance.PerformanceMonitor()
    monitor.record_capture(12.0)
    clock.elapsed += 1
    clock.wall += 1
    metrics = monitor.get_metrics()
    assert metrics.avg_capture_ms == 12.0
    assert metrics.captures_per_second == 1.0
    assert metrics.memory_mb == expected_memory


def test_missing_optional_psutil_retains_timing_metrics(monkeypatch, clock):
    monkeypatch.setitem(sys.modules, 'psutil', None)
    monitor = performance.PerformanceMonitor()
    monitor.record_total(7)
    assert monitor.get_metrics().avg_total_ms == 7
    assert monitor.get_metrics().cpu_percent == 0


@pytest.mark.parametrize('jump', [-3600, 3600])
def test_fps_uses_monotonic_elapsed_time(monkeypatch, clock, jump):
    fake_psutil(monkeypatch)
    monitor = performance.PerformanceMonitor()
    for _ in range(6):
        monitor.record_capture(1)
    clock.elapsed += 2
    clock.wall += jump
    assert monitor.get_metrics().captures_per_second == 3.0
    monitor.mark_report()
    monitor.record_capture(1)
    clock.elapsed += 0.5
    clock.wall -= jump
    assert monitor.get_metrics().captures_per_second == 2.0


def test_reset_starts_fresh_windows_and_cpu_baseline(monkeypatch, clock):
    calls = fake_psutil(monkeypatch)
    monitor = performance.PerformanceMonitor()
    monitor.get_metrics()
    clock.elapsed += 1
    monitor.record_capture(12)
    assert monitor.get_metrics().cpu_percent == 12.5
    monitor.reset()
    metrics = monitor.get_metrics()
    assert metrics.cpu_percent == 0.0
    assert metrics.avg_capture_ms == metrics.captures_per_second == 0.0
    assert calls.count('create') == 2


def test_concurrent_readers_do_not_duplicate_process_sampling(monkeypatch, clock):
    calls = fake_psutil(monkeypatch, slow_cpu=True)
    monitor = performance.PerformanceMonitor()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: monitor.get_metrics(), range(32)))
    assert len(results) == 32
    assert calls.count('create') == 1
    assert calls.count('cpu') == 1


def test_failed_optional_metric_recovers_without_discarding_the_other(monkeypatch, clock):
    calls = fake_psutil(monkeypatch, failure='memory')
    monitor = performance.PerformanceMonitor()
    assert monitor.get_metrics().memory_mb == 0
    clock.elapsed += 1
    metrics = monitor.get_metrics()
    assert metrics.cpu_percent == 12.5 and metrics.memory_mb == 0
    monkeypatch.setattr(monitor._process, 'memory_info', lambda: SimpleNamespace(rss=32 * 1024 * 1024))
    clock.elapsed += 1
    assert monitor.get_metrics().memory_mb == 32
    assert calls.count('create') == 1


def test_failed_process_construction_can_recover_later(monkeypatch, clock):
    fake_psutil(monkeypatch, failure='create')
    monitor = performance.PerformanceMonitor()
    assert monitor.get_metrics().memory_mb == 0
    calls = fake_psutil(monkeypatch)
    clock.elapsed += 1
    assert monitor.get_metrics().memory_mb == 64
    assert calls.count('create') == 1
