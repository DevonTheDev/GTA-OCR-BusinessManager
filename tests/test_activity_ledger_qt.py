"""Opt-in native Qt activity-ledger workflows over disposable SQLite records."""

import importlib
import json
import os
from datetime import date, datetime, timedelta

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native activity-ledger tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QDate, Qt
from src.database.models import Activity, Character, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repository = Repository(str(tmp_path / "ledger.db"))
    assert repository.initialize()
    alpha = repository.get_or_create_character("<b>Alpha & Ω</b>")
    beta = repository.get_or_create_character("Beta")
    empty = repository.get_or_create_character("No recorded activities")
    repository.set_active_character(beta.id)
    sessions = [repository.start_session(character, start_money=100) for character in (alpha, beta)]
    start = datetime(2026, 1, 1)
    ids = []
    with repository._session_scope() as session:
        for item in sessions:
            session.query(Session).filter_by(id=item.id).update({"ended_at": start + timedelta(days=60)})
        for index in range(57):
            row = Activity(
                session_id=sessions[index % 2].id, activity_type="HEIST" if index % 2 else "SELL",
                activity_name=f"Job {index}", notes=f"note {index}",
                started_at=start + timedelta(days=index, hours=1),
                ended_at=start + timedelta(days=index, hours=2),
                earnings=index, duration_seconds=index * 10, success=(True, False, None)[index % 3],
                business_type="Bunker",
            )
            session.add(row)
            session.flush()
            ids.append(row.id)
    open_session = repository.start_session(alpha)
    repository.log_activity(open_session.id, "OPEN", activity_name="Never in the ledger")
    yield repository, alpha, beta, empty, ids
    repository.close()


def ledger_class():
    assert importlib.util.find_spec("src.ui.widgets.activity_ledger_dialog") is not None, "Missing activity ledger dialog"
    return importlib.import_module("src.ui.widgets.activity_ledger_dialog").ActivityLedgerDialog


@pytest.fixture
def dialog(records, qt):
    widget = ledger_class()(records[0])
    widget.show()
    qt.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def rows(table):
    return [[table.item(row, col).text() for col in range(table.columnCount())]
            for row in range(table.rowCount())]


def test_history_launches_one_modeless_dialog_from_current_character(records, qt):
    module = importlib.import_module("src.ui.widgets.history_panel")
    panel = module.SessionHistoryPanel(repository=records[0])
    panel.refresh()
    panel._character_combo.setCurrentIndex(panel._character_combo.findData(records[1].id))
    assert hasattr(panel, "_activity_ledger_button"), "History needs the recorded-activity launcher"
    panel._activity_ledger_button.click()
    qt.processEvents()
    first = panel._activity_ledger_dialog
    assert first is not None and first.isVisible()
    assert first.parent() is panel
    assert not first.isModal()
    assert first._page.filters.character_id == records[1].id
    panel._character_combo.setCurrentIndex(panel._character_combo.findData(records[2].id))
    panel._activity_ledger_button.click()
    assert panel._activity_ledger_dialog is first
    assert first._page.filters.character_id == records[1].id
    first.close()
    assert panel._activity_ledger_dialog is None
    panel._activity_ledger_button.click()
    second = panel._activity_ledger_dialog
    assert second is not first
    assert second._page.filters.character_id == records[2].id
    panel._activity_ledger_finished(first)
    assert panel._activity_ledger_dialog is second
    second.close()
    panel.close()
    panel.deleteLater()
    qt.processEvents()


def test_paging_selects_detached_records_with_all_saved_fields(dialog, records):
    assert dialog._page.total == 57
    assert len(dialog._page.rows) == dialog._activities_table.rowCount() == 25
    assert not dialog._previous_button.isEnabled()
    assert dialog._next_button.isEnabled()
    assert "1" in dialog._page_label.text() and "57" in dialog._page_label.text()
    assert dialog._activities_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == records[-1][-1]
    details = dialog._details_edit.toPlainText()
    for expected in ("Job 56", "note 56", "Bunker", "Recorded start", "Recorded completion", "Stored duration", "560", "Unknown"):
        assert expected in details
    assert dialog._details_edit.isReadOnly()
    assert dialog._activities_table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    dialog._next_button.click()
    assert dialog._page.offset == 25 and len(dialog._page.rows) == 25
    dialog._next_button.click()
    assert dialog._page.offset == 50 and len(dialog._page.rows) == 7
    assert not dialog._next_button.isEnabled()
    dialog._previous_button.click()
    assert dialog._page.offset == 25
    assert records[0].get_active_character().id == records[2].id


def test_all_filters_apply_explicitly_and_page_resets(dialog, records, monkeypatch):
    repository, alpha, _, _, _ = records
    dialog._next_button.click()
    real_read = repository.get_completed_activity_ledger
    reads = []
    def read(*args, **kwargs):
        reads.append((args, kwargs))
        return real_read(*args, **kwargs)
    monkeypatch.setattr(repository, "get_completed_activity_ledger", read)
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(alpha.id))
    dialog._type_edit.setText("SELL")
    dialog._outcome_combo.setCurrentIndex(dialog._outcome_combo.findData("passed"))
    dialog._from_checkbox.setChecked(True)
    dialog._from_date.setDate(QDate(2026, 1, 7))
    dialog._until_checkbox.setChecked(True)
    dialog._until_date.setDate(QDate(2026, 1, 13))
    dialog._query_edit.setText("note")
    assert not reads
    assert dialog._page is None and dialog._activities_table.rowCount() == 0
    assert not dialog._details_edit.toPlainText()
    assert not dialog._export_button.isEnabled()
    assert not dialog._previous_button.isEnabled() and not dialog._next_button.isEnabled()
    dialog._apply_button.click()
    assert len(reads) == 1
    page = dialog._page
    assert page.offset == 0 and page.total == 2
    assert page.filters.character_id == alpha.id
    assert page.filters.date_from == date(2026, 1, 7) and page.filters.date_until == date(2026, 1, 13)
    assert page.filters.activity_type == "SELL" and page.filters.outcome == "passed"
    assert [row.name for row in page.rows] == ["Job 12", "Job 6"]
    assert dialog._export_button.isEnabled()


@pytest.mark.parametrize("control,value", [
    ("_query_edit", "x" * 201), ("_query_edit", "   "), ("_query_edit", "x\u2028y"),
    ("_type_edit", "x" * 257), ("_type_edit", "x\u0001y"),
])
def test_invalid_visible_text_retires_page_before_any_query(dialog, records, monkeypatch, control, value):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid controls must be rejected before querying")
    monkeypatch.setattr(records[0], "get_completed_activity_ledger", unexpected)
    getattr(dialog, control).setText(value)
    dialog._apply_button.click()
    assert dialog._page is None and dialog._activities_table.rowCount() == 0
    assert not dialog._export_button.isEnabled()
    assert "Invalid" in dialog._status_label.text()


def test_invalid_date_range_then_empty_and_recovery(dialog, records):
    dialog._from_checkbox.setChecked(True)
    dialog._until_checkbox.setChecked(True)
    dialog._from_date.setDate(QDate(2026, 2, 2))
    dialog._until_date.setDate(QDate(2026, 1, 1))
    dialog._apply_button.click()
    assert dialog._page is None and "Invalid" in dialog._status_label.text()
    dialog._from_checkbox.setChecked(False)
    dialog._until_checkbox.setChecked(False)
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[3].id))
    dialog._apply_button.click()
    assert dialog._page.total == 0 and dialog._export_button.isEnabled()
    assert "No recorded activities" in dialog._status_label.text()
    dialog._character_combo.setCurrentIndex(0)
    dialog._apply_button.click()
    assert dialog._page.total == 57


def test_database_failure_clears_stale_display_and_refresh_recovers(dialog, records, monkeypatch):
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise DatabaseError("<b>synthetic ledger read failure</b>")
        patch.setattr(records[0], "get_completed_activity_ledger", fail)
        dialog._refresh_button.click()
        assert dialog._page is None and dialog._activities_table.rowCount() == 0
        assert not dialog._details_edit.toPlainText()
        assert not dialog._export_button.isEnabled()
        assert "synthetic ledger read failure" in dialog._status_label.text()
        assert dialog._status_label.textFormat() == Qt.TextFormat.PlainText
    dialog._refresh_button.click()
    assert dialog._page.total == 57 and dialog._export_button.isEnabled()


def test_refresh_updates_characters_and_rereads_last_valid_page(dialog, records):
    repository = records[0]
    dialog._next_button.click()
    dialog._next_button.click()
    assert dialog._page.offset == 50
    repository.get_or_create_character("Newly available")
    with repository._session_scope() as session:
        session.query(Activity).filter(Activity.id.in_(records[-1][:40])).delete(synchronize_session=False)
    dialog._refresh_button.click()
    assert dialog._page.offset == 0 and dialog._page.total == 17
    assert dialog._activities_table.rowCount() == 17
    assert dialog._character_combo.findText("Newly available") >= 0


def test_literal_long_values_nulls_and_signed_seconds_remain_plain(dialog, records):
    repository, alpha, _, _, ids = records
    name = "<b>literal %_\\ Ω</b>" + "長" * 400
    notes = "<img src='file:///not-an-image'>\n" + "notes " * 1500
    with repository._session_scope() as session:
        session.query(Character).filter_by(id=alpha.id).update({"name": "<i>literal character</i>"})
        session.query(Activity).filter_by(id=ids[-1]).update({
            "activity_name": name, "notes": notes, "duration_seconds": -12.5,
            "earnings": 0, "success": None, "business_type": None,
        })
    dialog._refresh_button.click()
    assert name in [value for row in rows(dialog._activities_table) for value in row]
    assert name in dialog._details_edit.toPlainText() and notes in dialog._details_edit.toPlainText()
    assert "-12.5" in dialog._details_edit.toPlainText()
    assert "Unknown" in dialog._details_edit.toPlainText()
    assert "Cancelled" not in dialog._details_edit.toPlainText()
    for label in dialog.findChildren(QtWidgets.QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
    for row in range(dialog._activities_table.rowCount()):
        for column in range(dialog._activities_table.columnCount()):
            assert not dialog._activities_table.item(row, column).toolTip()
    dialog._query_edit.setText("%_\\")
    dialog._apply_button.click()
    assert dialog._page.total == 1 and dialog._page.rows[0].name == name
    assert "A–Z" in dialog._search_hint.text()
    assert "stored" in dialog._notes_label.text().lower()
    assert "session net" in dialog._notes_label.text().lower()


def test_export_cancel_is_quiet_and_reentrant_snapshot_is_fixed(dialog, records, tmp_path, monkeypatch):
    original_status = dialog._status_label.text()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    dialog._export_button.click()
    assert dialog._status_label.text() == original_status and dialog._export_button.isEnabled()
    original = dialog._page
    destination = tmp_path / "page.json"
    choices = []
    def choose(*args):
        choices.append(args[2])
        dialog._export_page()
        dialog._activities_table.selectRow(2)
        dialog._query_edit.setText("not present")
        dialog._apply_button.click()
        with records[0]._session_scope() as session:
            session.query(Activity).filter_by(id=original.rows[0].id).update({"activity_name": "Changed later"})
        return str(destination), "JSON files (*.json)"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert len(choices) == 1
    assert json.loads(destination.read_text()) == original.to_report()
    assert dialog._page.total == 0 and dialog._export_button.isEnabled()


def test_export_failure_keeps_snapshot_for_retry(dialog, tmp_path, monkeypatch):
    destination = tmp_path / "page.json"
    destination.mkdir()
    original = dialog._page
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    dialog._export_button.click()
    assert "Export failed" in dialog._status_label.text()
    assert dialog._page is original and dialog._export_button.isEnabled()
    destination.rmdir()
    dialog._export_button.click()
    assert json.loads(destination.read_text()) == original.to_report()


def test_dirty_controls_during_export_remain_retired(dialog, tmp_path, monkeypatch):
    original = dialog._page
    destination = tmp_path / "fixed.json"
    def choose(*args):
        dialog._type_edit.setText("Different")
        return str(destination), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert json.loads(destination.read_text()) == original.to_report()
    assert dialog._page is None and not dialog._export_button.isEnabled()
    assert "Apply" in dialog._status_label.text()


def test_refresh_preserves_unapplied_controls_until_apply(dialog, records):
    dialog._query_edit.setText("Job 56")
    records[0].get_or_create_character("Appeared while editing")
    dialog._refresh_button.click()
    assert dialog._query_edit.text() == "Job 56"
    assert dialog._character_combo.findText("Appeared while editing") >= 0
    assert dialog._page is None and dialog._activities_table.rowCount() == 0
    assert not dialog._export_button.isEnabled()
    assert "Apply" in dialog._status_label.text()
    dialog._apply_button.click()
    assert dialog._page.total == 1 and dialog._page.rows[0].name == "Job 56"


@pytest.mark.parametrize("outcome, expected", [("passed", 19), ("failed", 19), ("unknown", 19)])
def test_each_stored_outcome_is_filterable(dialog, outcome, expected):
    dialog._outcome_combo.setCurrentIndex(dialog._outcome_combo.findData(outcome))
    dialog._apply_button.click()
    assert dialog._page.total == expected
    assert all(row.outcome == outcome for row in dialog._page.rows)


def test_optional_date_bounds_include_max_date_and_undated_only_when_unbounded(dialog, records):
    with records[0]._session_scope() as session:
        session.query(Activity).filter_by(id=records[-1][0]).update({"started_at": None, "ended_at": None})
    dialog._until_checkbox.setChecked(True)
    dialog._until_date.setDate(QDate(9999, 12, 31))
    dialog._apply_button.click()
    assert dialog._page.total == 56
    assert dialog._page.filters.date_until == date.max
    dialog._until_checkbox.setChecked(False)
    dialog._apply_button.click()
    assert dialog._page.total == 57


def test_closing_during_chooser_cannot_export_or_clear_reopened_dialog(records, qt, tmp_path, monkeypatch):
    panel = importlib.import_module("src.ui.widgets.history_panel").SessionHistoryPanel(repository=records[0])
    panel.refresh()
    panel._activity_ledger_button.click()
    first = panel._activity_ledger_dialog
    destination = tmp_path / "closed.json"
    def choose(*args):
        first.close()
        panel._activity_ledger_button.click()
        qt.processEvents()
        return str(destination), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    first._export_button.click()
    assert not destination.exists()
    assert panel._activity_ledger_dialog is not first
    assert panel._activity_ledger_dialog._page is not None
    assert panel._activity_ledger_dialog._export_button.isEnabled()
    panel._activity_ledger_dialog.close()
    panel.close()
    panel.deleteLater()
    qt.processEvents()


def test_refresh_preserves_visible_selected_activity_by_id(dialog, records):
    dialog._activities_table.selectRow(3)
    selected_id = dialog._activities_table.item(3, 0).data(Qt.ItemDataRole.UserRole)
    with records[0]._session_scope() as session:
        session.query(Activity).filter_by(id=records[-1][-1]).update({"ended_at": datetime(2000, 1, 1)})
    dialog._refresh_button.click()
    current_row = dialog._activities_table.currentRow()
    assert dialog._activities_table.item(current_row, 0).data(Qt.ItemDataRole.UserRole) == selected_id
    assert f"Activity ID: {selected_id}\n" in dialog._details_edit.toPlainText()


def test_full_details_support_keyboard_selection_without_editing(dialog, qt):
    from PyQt6.QtTest import QTest
    details = dialog._details_edit
    assert details.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    original = details.toPlainText()
    details.setFocus()
    QTest.keyClick(details, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(details, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert details.textCursor().selectedText() == original[0]
    QTest.keyClick(details, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert details.textCursor().hasSelection()
    assert len(details.textCursor().selectedText()) == len(original)
    QTest.keyClicks(details, "should not replace saved details")
    assert details.toPlainText() == original
