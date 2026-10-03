"""Opt-in exact insight-to-ledger filter handoff in the real Qt dialog."""

import os
from datetime import date

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native insights ledger handoff", allow_module_level=True)

pytest.importorskip("PyQt6.QtWidgets")
from src.database.activity_ledger import ActivityLedgerFilters
from src.ui.widgets.activity_ledger_dialog import ActivityLedgerDialog
from tests.test_activity_ledger_qt import qt as qt, records as records


def test_seeded_filters_apply_before_the_first_ledger_query(records, qt, monkeypatch):
    repo, alpha, active, _empty, _ids = records
    wanted = ActivityLedgerFilters(character_id=alpha.id, date_from=date(2026, 1, 5),
        date_until=date(2026, 2, 1), activity_type="SELL", outcome="passed", query="note 1")
    calls = []
    original = repo.get_completed_activity_ledger
    def read(filters, **kwargs):
        calls.append(filters)
        return original(filters, **kwargs)
    monkeypatch.setattr(repo, "get_completed_activity_ledger", read)
    view = ActivityLedgerDialog(repo, initial_filters=wanted)
    try:
        assert calls == [wanted]
        assert view._page.filters == wanted
        assert {row.name for row in view._page.rows} == {"Job 12", "Job 18"}
        assert view._character_combo.currentData() == alpha.id
        assert view._from_date.date().toPyDate() == wanted.date_from
        assert view._until_date.date().toPyDate() == wanted.date_until
        assert view._type_edit.text() == "SELL"
        assert view._query_edit.text() == "note 1"
        assert view._outcome_combo.currentData() == "passed"
        assert repo.get_active_character().id == active.id
        # Further changes are ordinary explicit ledger edits, not a hidden pin.
        view._type_edit.setText("HEIST")
        assert view._page is None and not view._export_button.isEnabled()
        view._apply_button.click()
        assert view._page.filters.activity_type == "HEIST"
        assert view._page.total == 0
        assert wanted.activity_type == "SELL"
    finally:
        view.close(); view.deleteLater(); qt.processEvents()


def test_unavailable_character_and_maximum_date_never_broaden_seed(records, qt):
    wanted = ActivityLedgerFilters(character_id=2**63-1, date_from=date.max, date_until=date.max,
                                  activity_type="SELL", outcome="unknown")
    view = ActivityLedgerDialog(records[0], initial_filters=wanted)
    try:
        assert view._page.filters == wanted
        assert view._page.total == 0
        assert view._character_combo.currentData() == wanted.character_id
        assert "unavailable" in view._character_combo.currentText()
    finally:
        view.close(); view.deleteLater(); qt.processEvents()


@pytest.mark.parametrize("initial,character", [
    (ActivityLedgerFilters(activity_type=""), None),
    (ActivityLedgerFilters(activity_type="bad\nvalue"), None),
    (ActivityLedgerFilters(character_id=True), None),
    (ActivityLedgerFilters(query=" "), None),
    (ActivityLedgerFilters(character_id=1), 2),
    (ActivityLedgerFilters(character_id=1), True),
    ({"character_id": 1}, None),
])
def test_invalid_or_unrepresentable_seed_is_rejected_before_read(records, qt, monkeypatch, initial, character):
    monkeypatch.setattr(records[0], "get_all_characters", lambda: pytest.fail("Invalid seed read storage"))
    with pytest.raises(ValueError):
        ActivityLedgerDialog(records[0], initial_filters=initial, character_id=character)
