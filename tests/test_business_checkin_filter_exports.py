"""Filtered manual history exports use the accepted snapshot, never fresh reads."""

from datetime import date, datetime, timezone
import json

import pytest

from src.database import business_checkins
from src.database.repository import Repository
from src.utils.exporter import DataExporter


@pytest.fixture
def records(tmp_path, monkeypatch):
    import src.database.repository as repository_module
    monkeypatch.setattr(repository_module, 'utc_now', lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    repository = Repository(str(tmp_path / 'filter-export.db'))
    assert repository.initialize()
    owner = repository.get_or_create_character('Literal <b>saved owner</b>').id
    repository.save_business_checkin(owner, 'bunker', stock_percent=0, stock_value=0,
                                      note='Keep %_\\ literal 雪\nFull note')
    repository.save_business_checkin(owner, 'bunker', supply_percent=0, note='Other observation')
    yield repository, owner
    repository.close()
    repository._session_factory.kw['bind'].dispose()


def test_filtered_export_retains_exact_accepted_filters_rows_and_unknowns(records, tmp_path):
    repository, owner = records
    filters = business_checkins.BusinessCheckInHistoryFilters(
        note_query='%_\\', recorded_from=date(2026, 10, 3), recorded_until=date(2026, 10, 3),
    )
    page = repository.get_business_checkin_history(owner, 'bunker', filters=filters)
    expected = page.to_report()
    assert expected['filters']['note_query'] == '%_\\'
    assert expected['filters']['recorded_from'] == expected['filters']['recorded_until'] == '2026-10-03'
    assert expected['pagination']['total'] == expected['pagination']['rows_exported'] == 1
    assert expected['rows'][0]['stock_percent'] == expected['rows'][0]['stock_value'] == 0
    assert expected['rows'][0]['supply_percent'] is None
    repository.save_business_checkin(owner, 'bunker', note='Later %_\\ matching observation')
    repository.close()

    class NoReads:
        def __getattr__(self, name):
            pytest.fail(f'Captured export tried to read storage: {name}')

    destination = tmp_path / 'filtered-history.json'
    exported = DataExporter(NoReads()).export_business_checkins_snapshot(page, destination)
    assert exported.success and exported.rows_exported == 1
    assert json.loads(destination.read_text()) == expected
    assert page.to_report() == expected


def test_empty_filters_preserve_the_existing_unfiltered_export_shape(records, tmp_path):
    repository, owner = records
    plain = repository.get_business_checkin_history(owner, 'bunker')
    blank = repository.get_business_checkin_history(
        owner, 'bunker', filters=business_checkins.BusinessCheckInHistoryFilters(note_query=''),
    )
    assert blank.filters is None
    assert blank.to_report() == plain.to_report()
    assert blank.to_report()['filters'] == {'character_id': owner, 'business_id': 'bunker'}
    destination = tmp_path / 'unfiltered-history.json'
    assert DataExporter(repository).export_business_checkins_snapshot(blank, destination).success
    assert json.loads(destination.read_text()) == plain.to_report()
