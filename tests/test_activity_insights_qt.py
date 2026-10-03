"""Opt-in real Qt historical insights over disposable SQLite records."""

import importlib
import importlib.util
import json
import os
from dataclasses import replace
from datetime import date, datetime

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native insights tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QDate, Qt
from src.database.activity_ledger import ActivityLedgerFilters
from src.database.models import Activity, Character, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repository = Repository(str(tmp_path / "insights.db"))
    assert repository.initialize()
    alpha = repository.get_or_create_character("<b>Alpha & Ω</b>")
    beta = repository.get_or_create_character("Beta")
    empty = repository.get_or_create_character("Empty")
    repository.set_active_character(beta.id)
    sessions = [repository.start_session(character) for character in (alpha, alpha, beta)]
    # Mixed coverage includes known zero, negative duration and unavailable values.
    rows = [
        (0, "SELL", 0, 60, True, "literal %_\\ first", datetime(2026, 1, 1)),
        (1, "SELL", 300, 120, False, "literal %_\\ second", datetime(2026, 1, 2)),
        (1, "SELL", None, 0, None, "other", datetime(2026, 1, 3)),
        (2, "<b>Custom Ω</b>", -20, -1, True, "custom", datetime(2026, 1, 4)),
        (2, "HEIST", 600, None, False, "heist", datetime(2026, 1, 5)),
    ]
    ids = []
    with repository._session_scope() as session:
        for item in sessions:
            session.query(Session).filter_by(id=item.id).update({"ended_at": datetime(2026, 2, 1)})
        for index, activity_type, amount, duration, outcome, name, timestamp in rows:
            row = Activity(session_id=sessions[index].id, activity_type=activity_type,
                           activity_name=name, started_at=timestamp, ended_at=timestamp,
                           earnings=amount, duration_seconds=duration, success=outcome,
                           notes="<i>notes</i>")
            session.add(row)
            session.flush()
            ids.append(row.id)
        # Avoid the ORM earnings default replacing this deliberately missing value.
        session.query(Activity).filter_by(id=ids[2]).update({"earnings": None})
    opened = repository.start_session(alpha)
    repository.log_activity(opened.id, "OPEN", earnings=999999, success=True)
    yield repository, alpha, beta, empty, sessions, ids
    repository.close()


def insights_class():
    name = "src.ui.widgets.activity_insights_dialog"
    assert importlib.util.find_spec(name) is not None, "Activity insights dialog is missing"
    return importlib.import_module(name).ActivityInsightsDialog


@pytest.fixture
def dialog(records, qt):
    widget = insights_class()(records[0])
    widget.show()
    qt.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def select_type(dialog, activity_type):
    row = next(i for i, group in enumerate(dialog._snapshot.groups)
               if group.activity_type == activity_type)
    dialog._types_table.selectRow(row)


def test_history_launches_modeless_insights_and_preserves_identity(records, qt):
    panel = importlib.import_module("src.ui.widgets.history_panel").SessionHistoryPanel(repository=records[0])
    panel.refresh()
    assert hasattr(panel, "_activity_insights_button"), "History needs an Activity insights launcher"
    try:
        panel._character_combo.setCurrentIndex(panel._character_combo.findData(records[1].id))
        panel._annotation_query_edit.setText("unapplied session notes")
        panel._activity_insights_button.click()
        first = panel._activity_insights_dialog
        assert first.isVisible() and not first.isModal() and first.parent() is panel
        assert first._snapshot.filters == ActivityLedgerFilters(character_id=records[1].id)
        panel._character_combo.setCurrentIndex(panel._character_combo.findData(records[2].id))
        panel._activity_insights_button.click()
        assert panel._activity_insights_dialog is first
        assert first._snapshot.filters.character_id == records[1].id
        first.close()
        assert panel._activity_insights_dialog is None
        panel._activity_insights_button.click()
        second = panel._activity_insights_dialog
        assert second is not first and second._snapshot.filters.character_id == records[2].id
        panel._activity_insights_finished(first)
        assert panel._activity_insights_dialog is second
        second.close()
    finally:
        panel.close()
        panel.deleteLater()
        qt.processEvents()


def test_group_table_details_and_overall_expose_coverage(dialog, records):
    assert dialog._snapshot.overall.activities == 5
    assert dialog._snapshot.overall.sessions == 3
    assert dialog._types_table.rowCount() == 3
    assert "5 activities" in dialog._overall_label.text()
    assert "3 sessions" in dialog._overall_label.text()
    assert "3 types" in dialog._overall_label.text()
    assert "UTC" in dialog._overall_label.text()
    select_type(dialog, "SELL")
    values = [dialog._types_table.item(2, i).text() for i in range(dialog._types_table.columnCount())]
    assert "300" in " ".join(values) and "2/3" in " ".join(values)
    details = dialog._details_edit.toPlainText()
    for expected in ("Activities: 3", "Sessions: 2", "Passed: 1", "Failed: 1", "Unknown: 1",
                     "Known outcomes: 2", "Pass rate: 0.5", "Known amounts: 2",
                     "Unavailable amounts: 1", "Recorded amount total: 300",
                     "Recorded amount mean: 150", "Positive durations: 2", "Zero durations: 1",
                     "Negative durations: 0", "Unavailable durations: 0",
                     "Positive duration total (seconds): 180", "Positive duration mean (seconds): 90",
                     "Paired records: 2", "Recorded amount per hour: 6000"):
        assert expected in details
    assert dialog._details_edit.isReadOnly()
    assert records[0].get_active_character().id == records[2].id
    assert "known amount and positive duration" in dialog._notes_label.text()


def test_literal_rendering_and_keyboard_copy(dialog):
    from PyQt6.QtTest import QTest
    select_type(dialog, "<b>Custom Ω</b>")
    assert dialog._types_table.item(0, 0).text() == "<b>Custom Ω</b>"
    assert "<b>Custom Ω</b>" in dialog._details_edit.toPlainText()
    for label in dialog.findChildren(QtWidgets.QLabel):
        assert label.textFormat() == Qt.TextFormat.PlainText
    details = dialog._details_edit
    assert details.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    original = details.toPlainText()
    details.setFocus()
    QTest.keyClick(details, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert details.textCursor().hasSelection()
    QTest.keyClicks(details, "do not edit")
    assert details.toPlainText() == original


def test_filters_retire_snapshot_and_apply_once(dialog, records, monkeypatch):
    original_read = records[0].get_completed_activity_insights
    reads = []
    def read(filters=None):
        reads.append(filters)
        return original_read(filters)
    monkeypatch.setattr(records[0], "get_completed_activity_insights", read)
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
    dialog._from_checkbox.setChecked(True)
    dialog._from_date.setDate(QDate(2026, 1, 1))
    dialog._until_checkbox.setChecked(True)
    dialog._until_date.setDate(QDate(2026, 1, 2))
    dialog._outcome_combo.setCurrentIndex(dialog._outcome_combo.findData("failed"))
    dialog._query_edit.setText("%_\\")
    assert not reads and dialog._snapshot is None
    assert dialog._types_table.rowCount() == 0 and not dialog._details_edit.toPlainText()
    assert not dialog._export_button.isEnabled() and not dialog._drill_button.isEnabled()
    dialog._apply_button.click()
    assert reads == [ActivityLedgerFilters(character_id=records[1].id, date_from=date(2026, 1, 1),
                                           date_until=date(2026, 1, 2), outcome="failed", query="%_\\")]
    assert dialog._snapshot.overall.activities == 1
    assert dialog._snapshot.overall.recorded_amount_total == 300


@pytest.mark.parametrize("query", [" ", "x" * 201, "x\u2028y"])
def test_invalid_query_is_rejected_before_storage(dialog, records, monkeypatch, query):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid filters must not reach storage")
    monkeypatch.setattr(records[0], "get_completed_activity_insights", unexpected)
    dialog._query_edit.setText(query)
    dialog._apply_button.click()
    assert dialog._snapshot is None and "Invalid" in dialog._status_label.text()
    assert not dialog._export_button.isEnabled()


def test_refresh_preserves_dirty_draft(dialog, records, monkeypatch):
    dialog._query_edit.setText("first")
    records[0].get_or_create_character("New choice")
    with monkeypatch.context() as patch:
        def unexpected(*args, **kwargs):
            pytest.fail("Refresh must not silently apply draft filters")
        patch.setattr(records[0], "get_completed_activity_insights", unexpected)
        dialog.refresh()
    assert dialog._query_edit.text() == "first" and dialog._snapshot is None
    assert dialog._character_combo.findText("New choice") >= 0
    assert "Apply" in dialog._status_label.text()
    dialog._apply_button.click()
    assert dialog._snapshot.overall.activities == 1


def test_missing_character_selection_never_broadens(dialog, records):
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[3].id))
    dialog._apply_button.click()
    with records[0]._session_scope() as session:
        session.query(Character).filter_by(id=records[3].id).delete()
    dialog.refresh()
    assert dialog._character_combo.currentData() == records[3].id
    assert "unavailable" in dialog._character_combo.currentText()
    assert dialog._snapshot.filters.character_id == records[3].id
    assert dialog._snapshot.overall.activities == 0


def test_invalid_date_empty_result_and_recovery(dialog, records):
    dialog._from_checkbox.setChecked(True)
    dialog._until_checkbox.setChecked(True)
    dialog._from_date.setDate(QDate(2026, 2, 1))
    dialog._until_date.setDate(QDate(2026, 1, 1))
    dialog._apply_button.click()
    assert dialog._snapshot is None and "Invalid" in dialog._status_label.text()
    dialog._from_checkbox.setChecked(False)
    dialog._until_date.setDate(QDate(9999, 12, 31))
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[3].id))
    dialog._apply_button.click()
    assert dialog._snapshot.filters.date_until == date.max
    assert dialog._snapshot.overall.activities == 0 and dialog._export_button.isEnabled()
    assert not dialog._drill_button.isEnabled() and not dialog._details_edit.toPlainText()
    assert "No recorded activities" in dialog._status_label.text()
    dialog._character_combo.setCurrentIndex(0)
    dialog._apply_button.click()
    assert dialog._snapshot.overall.activities == 5


@pytest.mark.parametrize("failure", ["database", "limit", "data"])
def test_read_failure_retires_snapshot_and_retry_recovers(dialog, records, monkeypatch, failure):
    from src.database.activity_insights import ActivityInsightsDataError, ActivityInsightsLimitError
    error = {"database": DatabaseError("synthetic read unavailable"),
             "limit": ActivityInsightsLimitError(), "data": ActivityInsightsDataError()}[failure]
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise error
        patch.setattr(records[0], "get_completed_activity_insights", fail)
        dialog.refresh()
    assert dialog._snapshot is None and dialog._types_table.rowCount() == 0
    assert not dialog._details_edit.toPlainText()
    assert not dialog._export_button.isEnabled() and not dialog._drill_button.isEnabled()
    assert "narrow" in dialog._status_label.text().lower() or "retry" in dialog._status_label.text().lower()
    dialog.refresh()
    assert dialog._snapshot.overall.activities == 5


def test_all_groups_are_visible_without_sampling(dialog, records):
    with records[0]._session_scope() as session:
        session.add_all([Activity(session_id=records[4][0].id, activity_type=f"TYPE_{i:03d}",
                                  earnings=0, duration_seconds=1, success=True)
                         for i in range(253)])
    dialog.refresh()
    assert dialog._types_table.rowCount() == 256
    assert dialog._snapshot.overall.activities == 258
    dialog._types_table.selectRow(255)
    assert "TYPE_252" in dialog._details_edit.toPlainText()
    assert dialog.minimumSizeHint().width() <= 1100
    dialog.resize(1100, 800)
    assert not dialog.grab().isNull()


@pytest.mark.parametrize("activity_type", [None, "", "   ", "bad\ncontrol", "bad\u2028separator"])
def test_unrepresentable_exact_type_disables_drilldown(dialog, records, monkeypatch, activity_type):
    original = dialog._snapshot
    snapshot = replace(original, groups=(replace(original.groups[0], activity_type=activity_type),))
    monkeypatch.setattr(records[0], "get_completed_activity_insights", lambda filters=None: snapshot)
    dialog.refresh()
    assert not dialog._drill_button.isEnabled()
    assert "unavailable" in dialog._drill_hint.text().lower()
    dialog._open_activity_ledger()
    assert dialog._ledger_dialog is None
    if activity_type is None:
        assert "missing" in dialog._types_table.item(0, 0).text()
    elif activity_type == "":
        assert "blank" in dialog._types_table.item(0, 0).text()


def test_drilldown_carries_exact_filters_and_owns_one_child(dialog, records, qt):
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
    dialog._from_checkbox.setChecked(True)
    dialog._from_date.setDate(QDate(2026, 1, 1))
    dialog._until_checkbox.setChecked(True)
    dialog._until_date.setDate(QDate(2026, 1, 2))
    dialog._outcome_combo.setCurrentIndex(dialog._outcome_combo.findData("failed"))
    dialog._query_edit.setText("%_\\")
    dialog._apply_button.click()
    select_type(dialog, "SELL")
    dialog._drill_button.click()
    first = dialog._ledger_dialog
    assert first is not None and first.isVisible() and not first.isModal()
    assert first.parent() is dialog
    assert first._page.filters == replace(dialog._snapshot.filters, activity_type="SELL")
    assert first._page.total == 1
    assert "fresh" in dialog._drill_hint.text().lower()
    dialog._character_combo.setCurrentIndex(0)
    dialog._from_checkbox.setChecked(False)
    dialog._until_checkbox.setChecked(False)
    dialog._outcome_combo.setCurrentIndex(0)
    dialog._query_edit.clear()
    dialog._apply_button.click()
    select_type(dialog, "HEIST")
    dialog._drill_button.click()
    assert dialog._ledger_dialog is first
    assert first._page.filters.activity_type == "SELL"
    first.close()
    assert dialog._ledger_dialog is None
    dialog._drill_button.click()
    second = dialog._ledger_dialog
    assert second is not first and second._page.filters.activity_type == "HEIST"
    dialog._ledger_finished(first)
    assert dialog._ledger_dialog is second
    dialog.close()
    assert not second.isVisible()


def test_drilldown_is_fresh_observation(dialog, records):
    select_type(dialog, "SELL")
    assert dialog._snapshot.groups[-1].metrics.activities == 3
    records[0].log_activity(records[4][0].id, "SELL", earnings=123)
    dialog._drill_button.click()
    assert dialog._ledger_dialog._page.total == 4
    assert dialog._snapshot.groups[-1].metrics.activities == 3


def test_numeric_issues_are_explained_without_inventing_zero(dialog, records, monkeypatch):
    from src.database.activity_insights import INSIGHT_ISSUE_MESSAGES
    original = dialog._snapshot
    metrics = replace(original.groups[0].metrics, recorded_amount_per_hour=None,
                      issues=("recorded_amount_per_hour_out_of_range",))
    snapshot = replace(original, groups=(replace(original.groups[0], metrics=metrics),))
    monkeypatch.setattr(records[0], "get_completed_activity_insights", lambda filters=None: snapshot)
    dialog.refresh()
    assert "Recorded amount per hour: --" in dialog._details_edit.toPlainText()
    assert INSIGHT_ISSUE_MESSAGES[metrics.issues[0]] in dialog._details_edit.toPlainText()


@pytest.mark.parametrize("activity_type", [None, "", "(missing type)", "(blank type)", "line\nbreak"])
def test_exact_type_details_distinguish_missing_blank_and_literal_labels(dialog, records, monkeypatch, activity_type):
    original = dialog._snapshot
    snapshot = replace(original, groups=(replace(original.groups[0], activity_type=activity_type),))
    monkeypatch.setattr(records[0], "get_completed_activity_insights", lambda filters=None: snapshot)
    dialog.refresh()
    assert f"Exact activity type: {activity_type!r}\n" in dialog._details_edit.toPlainText()


def test_cancel_export_does_not_write_or_query(dialog, records, monkeypatch, tmp_path):
    before = set(tmp_path.iterdir())
    def unexpected(*args, **kwargs):
        pytest.fail("export must use the captured accepted snapshot")
    monkeypatch.setattr(records[0], "get_completed_activity_insights", unexpected)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    dialog._export_button.click()
    assert set(tmp_path.iterdir()) == before
    assert dialog._export_button.isEnabled()


def test_export_captures_snapshot_before_reentrant_refresh(dialog, records, monkeypatch, tmp_path):
    original = dialog._snapshot
    target = tmp_path / "snapshot.json"
    choices = []
    def choose(*args):
        choices.append(True)
        dialog._export_snapshot()
        dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[3].id))
        dialog._apply_button.click()
        return str(target), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert choices == [True]
    assert json.loads(target.read_text()) == original.to_report()
    assert dialog._snapshot.overall.activities == 0
    assert "No recorded activities" in dialog._status_label.text()
    assert dialog._export_button.isEnabled()


def test_dirty_draft_during_export_stays_retired(dialog, monkeypatch, tmp_path):
    original = dialog._snapshot
    target = tmp_path / "captured.json"
    def choose(*args):
        dialog._query_edit.setText("new draft")
        return str(target), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert json.loads(target.read_text()) == original.to_report()
    assert dialog._snapshot is None and not dialog._export_button.isEnabled()
    assert "Apply" in dialog._status_label.text()


def test_export_failure_is_retryable_and_preserves_destination(dialog, monkeypatch, tmp_path):
    from src.utils import persistence
    target = tmp_path / "existing.json"
    target.write_text("previous complete file")
    original = dialog._snapshot
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("synthetic replace failure")
        patch.setattr(persistence.os, "replace", fail)
        dialog._export_button.click()
    assert target.read_text() == "previous complete file"
    assert "Export failed" in dialog._status_label.text()
    assert dialog._snapshot is original and dialog._export_button.isEnabled()
    dialog._export_button.click()
    assert json.loads(target.read_text()) == original.to_report()


def test_close_during_chooser_does_not_write_or_clear_new_identity(records, qt, monkeypatch, tmp_path):
    panel = importlib.import_module("src.ui.widgets.history_panel").SessionHistoryPanel(repository=records[0])
    panel.refresh()
    panel._activity_insights_button.click()
    first = panel._activity_insights_dialog
    target = tmp_path / "closed.json"
    def choose(*args):
        first.close()
        panel._activity_insights_button.click()
        qt.processEvents()
        return str(target), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    first._export_button.click()
    assert not target.exists()
    second = panel._activity_insights_dialog
    assert second is not first and second._snapshot is not None
    assert second._export_button.isEnabled()
    second.close()
    panel.close()
    panel.deleteLater()
    qt.processEvents()


def test_large_character_id_survives_filter_and_drilldown(records, qt):
    identifier = 2**62 + 37
    with records[0]._session_scope() as session:
        session.add(Character(id=identifier, name="Large SQLite ID", is_active=False))
    character = next(item for item in records[0].get_all_characters() if item.id == identifier)
    completed = records[0].start_session(character)
    with records[0]._session_scope() as session:
        session.query(Session).filter_by(id=completed.id).update({"ended_at": datetime(2026, 2, 1)})
    records[0].log_activity(completed.id, "LARGE", earnings=0, duration_seconds=1)
    widget = insights_class()(records[0], character_id=identifier)
    try:
        assert widget._snapshot.filters.character_id == identifier
        assert widget._snapshot.overall.activities == 1
        widget._drill_button.click()
        assert widget._ledger_dialog._page.filters.character_id == identifier
        assert widget._ledger_dialog._page.total == 1
    finally:
        widget.close()
        widget.deleteLater()
        qt.processEvents()
