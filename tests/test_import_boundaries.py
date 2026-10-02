"""Fresh-process import contracts; the full suite's import order must not hide cycles."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('imports', [
    'from src.database.repository import Repository',
    'from src.database import Repository; from src.utils import DataExporter, ExportResult',
    'from src.database.models import Character; from src.utils.exporter import DataExporter',
    'from src.utils import *; from src.database import Repository',
    'from src.utils import DataExporter; from src.database.repository import Repository',
])
def test_database_and_utility_exports_work_in_any_entry_order(imports):
    script = imports + '''
import src.utils as utils
from src.utils.exporter import DataExporter, ExportResult, quick_export_session, get_default_export_path
for name in ('DataExporter', 'ExportResult', 'quick_export_session', 'get_default_export_path'):
    assert name in dir(utils)
    assert getattr(utils, name) is globals()[name]
assert not hasattr(utils, 'not_a_real_export')
'''
    result = subprocess.run([sys.executable, '-c', script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
