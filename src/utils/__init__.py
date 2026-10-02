"""Utility functions and helpers."""

from typing import TYPE_CHECKING

from .logging import setup_logging, get_logger
from .performance import PerformanceMonitor
from .helpers import format_money, format_time, get_data_dir

if TYPE_CHECKING:
    from .exporter import DataExporter, ExportResult, quick_export_session, get_default_export_path

__all__ = [
    "setup_logging",
    "get_logger",
    "PerformanceMonitor",
    "format_money",
    "format_time",
    "get_data_dir",
    "DataExporter",
    "ExportResult",
    "quick_export_session",
    "get_default_export_path",
]


_EXPORTER_NAMES = {"DataExporter", "ExportResult", "quick_export_session", "get_default_export_path"}


def __getattr__(name):
    # Repository imports utils.logging. Eagerly importing exporter here would
    # import Repository again before its class exists when database is first.
    if name in _EXPORTER_NAMES:
        from importlib import import_module

        value = getattr(import_module(".exporter", __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
