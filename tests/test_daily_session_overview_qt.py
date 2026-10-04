"""Opt-in real Qt daily snapshots over disposable SQLite session records."""

import importlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native daily overview tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QDate, Qt

from src.database.models import Character, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repository = Repository(str(tmp_path / "daily.db"))
    assert repository.initialize()
    alpha = repository.get_or_create_character("<b>Alpha & Ω</b>")
    beta = repository.get_or_create_character("Beta")
    empty = repository.get_or_create_character("Empty")
    repository.set_active_character(beta.id)
    today = datetime.now(UTC).date()
    end = datetime.combine(today - timedelta(days=1), datetime.min.time())
    ids = []
    with repository._session_scope() as session:
        for index in range(28):
            row = Session(character_id=alpha.id, started_at=end - timedelta(hours=1),
                          ended_at=end, total_earnings=index, start_money=100,
                          end_money=99999)
            session.add(row)
            session.flush()
            ids.append(row.id)
        for start, earnings in ((end, 0), (end + timedelta(seconds=1), -30), (None, 20)):
            row = Session(character_id=beta.id, started_at=start, ended_at=end,
                          total_earnings=earnings)
            session.add(row)
            session.flush()
            if start is None:
                session.query(Session).filter_by(id=row.id).update({"started_at": None})
            ids.append(row.id)
        # Open records must never enter completed-day metrics.
        session.add(Session(character_id=alpha.id, started_at=end, total_earnings=999999))
    yield repository, alpha, beta, empty, today, ids
    repository.close()


def dialog_class():
    name = "src.ui.widgets.daily_session_overview_dialog"
    assert importlib.util.find_spec(name) is not None, "Daily overview dialog is missing"
    return importlib.import_module(name).DailySessionOverviewDialog


@pytest.fixture
def dialog(records, qt):
    widget = dialog_class()(records[0])
    widget.show()
    qt.processEvents()
    yield widget
    # Dispose this root dialog first; never delete arbitrary Qt top-level auxiliaries.
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def select_day(dialog, day):
    row = next(index for index, value in enumerate(dialog._display_days) if value.day == day)
    dialog._days_table.selectRow(row)


def test_default_window_includes_all_utc_days_and_exact_coverage(dialog, records):
    today = records[4]
    assert not dialog.isModal()
    assert dialog._snapshot.filters.date_from == today - timedelta(days=29)
    assert dialog._snapshot.filters.date_until == today
    assert dialog._days_table.rowCount() == 30
    assert dialog._days_table.item(0, 0).text() == today.isoformat()
    assert dialog._days_table.item(0, 1).text() == "0"
    select_day(dialog, today)
    assert "No completed sessions" in dialog._day_details_edit.toPlainText()
    assert dialog._days_table.item(0, 2).text() == "--"
    assert dialog._snapshot.overall.sessions == 31
    assert "31 sessions" in dialog._overall_label.text()
    assert "Known net: 31/31" in dialog._coverage_label.text()
    assert "Positive: 28" in dialog._coverage_label.text()
    assert "Zero: 1" in dialog._coverage_label.text()
    assert "Negative: 1" in dialog._coverage_label.text()
    assert "Unavailable: 1" in dialog._coverage_label.text()
    select_day(dialog, today - timedelta(days=1))
    assert dialog._sources_table.rowCount() == 25
    assert "1–25 of 31" in dialog._page_label.text()
    assert dialog._next_button.isEnabled() and not dialog._previous_button.isEnabled()


def test_filter_edits_immediately_retire_snapshot_and_apply_independent_scope(dialog, records):
    today = records[4]
    select_day(dialog, today - timedelta(days=1))
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[2].id))
    assert dialog._snapshot is None
    assert dialog._days_table.rowCount() == dialog._sources_table.rowCount() == 0
    assert not dialog._day_details_edit.toPlainText()
    assert not dialog._source_details_edit.toPlainText()
    assert not dialog._export_button.isEnabled()
    dialog._from_date.setDate(QDate(today.year, today.month, today.day).addDays(-2))
    dialog._apply_button.click()
    assert dialog._snapshot.filters.character_id == records[2].id
    assert dialog._snapshot.overall.sessions == 3
    assert dialog._days_table.rowCount() == 3
    assert records[0].get_active_character().id == records[2].id


def test_dirty_refresh_preserves_draft_and_never_reads_summary(dialog, records, monkeypatch):
    applied = dialog._applied_filters
    draft = dialog._from_date.date().addDays(2)
    dialog._from_date.setDate(draft)
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
    records[0].get_or_create_character("New saved choice")

    def unexpected(*args):
        pytest.fail("Dirty refresh must not apply controls or create an accepted snapshot")

    with monkeypatch.context() as patch:
        patch.setattr(records[0], "get_daily_session_overview", unexpected)
        dialog._refresh_button.click()
    assert dialog._from_date.date() == draft
    assert dialog._character_combo.currentData() == records[1].id
    assert dialog._character_combo.findText("New saved choice") >= 0
    assert dialog._applied_filters is applied
    assert dialog._snapshot is None and not dialog._export_button.isEnabled()
    assert "Apply" in dialog._status_label.text()
    dialog._apply_button.click()
    assert dialog._snapshot.overall.sessions == 28


@pytest.mark.parametrize("days", [-1, 366])
def test_invalid_inclusive_window_retires_data_before_storage(dialog, records, monkeypatch, days):
    dialog._until_date.setDate(dialog._from_date.date().addDays(days))

    def unexpected(*args):
        pytest.fail("Invalid date windows must not reach storage")

    monkeypatch.setattr(records[0], "get_daily_session_overview", unexpected)
    dialog._apply_button.click()
    assert "Invalid filters" in dialog._status_label.text()
    assert dialog._snapshot is None and dialog._days_table.rowCount() == 0
    assert not dialog._export_button.isEnabled()


def test_deleted_explicit_character_keeps_scope_and_clear_error(dialog, records):
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[3].id))
    dialog._apply_button.click()
    assert dialog._snapshot.overall.sessions == 0
    assert dialog._export_button.isEnabled()
    with records[0]._session_scope() as session:
        session.query(Character).filter_by(id=records[3].id).delete()
    dialog.refresh()
    assert dialog._character_combo.currentData() == records[3].id
    assert "unavailable" in dialog._character_combo.currentText()
    assert dialog._applied_filters.character_id == records[3].id
    assert dialog._snapshot is None and not dialog._export_button.isEnabled()
    assert "character" in dialog._status_label.text().lower()
    dialog._character_combo.setCurrentIndex(0)
    dialog._apply_button.click()
    assert dialog._snapshot.overall.sessions == 31


def test_sources_page_captured_rows_without_rereading_storage(dialog, records, monkeypatch):
    accepted = dialog._snapshot
    day = records[4] - timedelta(days=1)
    original_ids = {row.session_id for row in accepted.rows_for_day(day)}
    with records[0]._session_scope() as session:
        session.query(Session).filter_by(id=records[-1][0]).update({"total_earnings": 123456})

    def unexpected(*args, **kwargs):
        pytest.fail("Browsing source pages must use the accepted snapshot")

    with monkeypatch.context() as patch:
        patch.setattr(records[0], "get_daily_session_overview", unexpected)
        patch.setattr(records[0], "get_all_characters", unexpected)
        select_day(dialog, day)
        first = {int(dialog._sources_table.item(i, 0).text()) for i in range(25)}
        dialog._next_button.click()
        assert dialog._sources_table.rowCount() == 6
        assert "26–31 of 31" in dialog._page_label.text()
        second = {int(dialog._sources_table.item(i, 0).text()) for i in range(6)}
        assert first.isdisjoint(second) and first | second == original_ids
        assert not dialog._next_button.isEnabled() and dialog._previous_button.isEnabled()
        dialog._previous_button.click()
        assert dialog._sources_table.rowCount() == 25
        select_day(dialog, records[4])
        assert dialog._sources_table.rowCount() == 0
        assert "No completed sessions" in dialog._page_label.text()
        assert not dialog._source_details_edit.toPlainText()
    assert dialog._snapshot is accepted
    dialog.refresh()
    assert dialog._snapshot.overall.net_change_total != accepted.overall.net_change_total
    assert dialog._days_table.currentRow() == 0  # Refresh preserves selected day.


def test_full_source_details_preserve_literals_values_and_duration_coverage(dialog, records):
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[2].id))
    dialog._apply_button.click()
    assert "Saved net change total: -10" in dialog._day_details_edit.toPlainText()
    assert "Positive durations: 0" in dialog._day_details_edit.toPlainText()
    assert "Net per hour: --" in dialog._day_details_edit.toPlainText()
    details = []
    for index in range(dialog._sources_table.rowCount()):
        dialog._sources_table.selectRow(index)
        details.append(dialog._source_details_edit.toPlainText())
    assert any("Saved net change: 0" in text and "Elapsed duration (seconds): 0" in text for text in details)
    assert any("Saved net change: -30" in text and "Elapsed duration (seconds): -1" in text for text in details)
    assert any("Started (UTC): --" in text and "Elapsed duration (seconds): --" in text for text in details)
    literal = "<img src=x>& Ω " + "x" * 980
    with records[0]._session_scope() as session:
        session.query(Character).filter_by(id=records[1].id).update({"name": literal})
        session.query(Session).filter_by(id=records[-1][0]).update({"total_earnings": 9007199254740991})
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
    dialog._apply_button.click()
    assert dialog._scope_label.text().startswith(f"Character #{records[1].id}\n")
    assert records[1].name not in dialog._scope_label.text()
    assert dialog._character_combo.currentText() == records[1].name
    day_rows = dialog._snapshot.rows_for_day(records[4] - timedelta(days=1))
    index = next(i for i, row in enumerate(day_rows) if row.session_id == records[-1][0])
    for _ in range(index // 25):
        dialog._next_button.click()
    dialog._sources_table.selectRow(index % 25)
    text = dialog._source_details_edit.toPlainText()
    assert literal in text
    assert "Saved net change: 9007199254740991" in text
    assert "Elapsed duration (microseconds): 3600000000" in text
    assert dialog._source_details_edit.isReadOnly()
    assert dialog._source_details_edit.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert dialog._scope_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog._sources_table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    tooltip = dialog._sources_table.item(index % 25, 1).toolTip()
    assert "&lt;img src=x&gt;" in tooltip and "<img" not in tooltip
    assert "spending" in dialog._notes_label.text()
    assert "paused" in dialog._notes_label.text() and "cross-midnight" in dialog._notes_label.text()
    assert "positive elapsed" in dialog._notes_label.text()


def test_read_failure_retires_data_then_refresh_recovers(dialog, records, monkeypatch):
    def fail(*args):
        raise DatabaseError("synthetic failure")

    with monkeypatch.context() as patch:
        patch.setattr(records[0], "get_daily_session_overview", fail)
        dialog.refresh()
    assert dialog._snapshot is None
    assert dialog._days_table.rowCount() == dialog._sources_table.rowCount() == 0
    assert not dialog._source_details_edit.toPlainText()
    assert not dialog._coverage_label.text()
    assert not dialog._export_button.isEnabled()
    assert "retry" in dialog._status_label.text().lower()
    dialog.refresh()
    assert dialog._snapshot.overall.sessions == 31 and dialog._export_button.isEnabled()


@pytest.mark.parametrize("late_error", [False, True])
def test_filter_edit_during_read_discards_late_success_or_error(dialog, records, monkeypatch, late_error):
    old = dialog._snapshot

    def reenter(filters):
        dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
        if late_error:
            raise DatabaseError("late old failure")
        return old

    monkeypatch.setattr(records[0], "get_daily_session_overview", reenter)
    dialog.refresh()
    assert dialog._snapshot is None and dialog._dirty
    assert dialog._days_table.rowCount() == 0 and not dialog._export_button.isEnabled()
    assert "Filters changed" in dialog._status_label.text()
    assert "failure" not in dialog._status_label.text()


@pytest.mark.parametrize("late_error", [False, True])
def test_newer_nested_apply_wins_over_old_read_or_error(dialog, records, monkeypatch, late_error):
    original = records[0].get_daily_session_overview
    old = dialog._snapshot
    nested = False

    def reenter(filters):
        nonlocal nested
        if nested:
            return original(filters)
        nested = True
        dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[2].id))
        dialog._apply_button.click()
        if late_error:
            raise DatabaseError("late old failure")
        return old

    monkeypatch.setattr(records[0], "get_daily_session_overview", reenter)
    dialog.refresh()
    assert dialog._snapshot.filters.character_id == records[2].id
    assert dialog._snapshot.overall.sessions == 3
    assert dialog._export_button.isEnabled() and not dialog._dirty
    assert "3 sessions" in dialog._overall_label.text()
    assert "failed" not in dialog._status_label.text()


@pytest.mark.parametrize("late_error", [False, True])
def test_close_during_read_never_repopulates_dialog(dialog, records, monkeypatch, late_error):
    old = dialog._snapshot

    def close_during_read(filters):
        dialog.reject()
        if late_error:
            raise DatabaseError("late after close")
        return old

    monkeypatch.setattr(records[0], "get_daily_session_overview", close_during_read)
    dialog.refresh()
    assert dialog._closed and dialog._snapshot is None
    assert not dialog.isVisible()
    assert dialog._days_table.rowCount() == 0 and not dialog._export_button.isEnabled()


@pytest.mark.parametrize("late_error", [False, True])
def test_character_reload_reentry_cannot_overwrite_newer_controls(dialog, records, monkeypatch, late_error):
    choices = records[0].get_all_characters()

    def reenter():
        dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[2].id))
        dialog._apply_button.click()
        if late_error:
            raise DatabaseError("late character failure")
        return choices

    monkeypatch.setattr(records[0], "get_all_characters", reenter)
    dialog.refresh()
    assert dialog._character_combo.currentData() == records[2].id
    assert dialog._snapshot.filters.character_id == records[2].id
    assert dialog._snapshot.overall.sessions == 3


def test_export_uses_whole_accepted_snapshot_without_repository_reads(dialog, records, monkeypatch, tmp_path):
    accepted = dialog._snapshot
    destination = tmp_path / "daily.json"
    dialog._next_button.click()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))

    def unexpected(*args, **kwargs):
        pytest.fail("Export must use the accepted snapshot")

    monkeypatch.setattr(records[0], "get_daily_session_overview", unexpected)
    dialog._export_button.click()
    assert json.loads(destination.read_text()) == accepted.to_report()
    assert len(accepted.rows) == 31 and len(accepted.days) == 30
    assert "Exported" in dialog._status_label.text()


def test_export_cancellation_leaves_existing_file_untouched(dialog, monkeypatch, tmp_path):
    destination = tmp_path / "untouched.json"
    destination.write_text("keep me")
    old_status = dialog._status_label.text()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: ("", ""))

    def unexpected(*args):
        pytest.fail("Cancellation must not call the exporter")

    monkeypatch.setattr(dialog._exporter, "export_daily_session_overview", unexpected)
    dialog._export_button.click()
    assert destination.read_text() == "keep me"
    assert old_status == dialog._status_label.text()
    assert not dialog._exporting and dialog._export_button.isEnabled()


@pytest.mark.parametrize("raises", [False, True])
def test_export_failure_keeps_snapshot_retryable(dialog, monkeypatch, tmp_path, raises):
    from src.utils.exporter import ExportResult
    accepted = dialog._snapshot
    destination = tmp_path / "daily.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))

    def fail(*args):
        if raises:
            raise OSError("disk unavailable")
        return ExportResult(False, error_message="disk unavailable")

    monkeypatch.setattr(dialog._exporter, "export_daily_session_overview", fail)
    dialog._export_button.click()
    assert dialog._snapshot is accepted
    assert "Export failed" in dialog._status_label.text()
    assert not dialog._exporting and dialog._export_button.isEnabled()
    assert not destination.exists()


@pytest.mark.parametrize("apply_new", [False, True])
def test_chooser_reentry_never_retargets_captured_snapshot(dialog, records, monkeypatch, tmp_path, apply_new):
    accepted = dialog._snapshot
    destination = tmp_path / "captured.json"
    chooser_calls = []

    def chooser(*args):
        chooser_calls.append(True)
        dialog._export_snapshot()  # Nested native chooser callbacks cannot export twice.
        with records[0]._session_scope() as session:
            session.query(Session).filter_by(id=records[-1][0]).update({"total_earnings": 999999})
        dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[2].id))
        if apply_new:
            dialog._apply_button.click()
        return str(destination), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", chooser)
    dialog._export_button.click()
    assert len(chooser_calls) == 1
    assert json.loads(destination.read_text()) == accepted.to_report()
    assert "Exported" not in dialog._status_label.text()
    if apply_new:
        assert dialog._snapshot.filters.character_id == records[2].id
        assert dialog._export_button.isEnabled()
    else:
        assert dialog._snapshot is None and not dialog._export_button.isEnabled()
        assert "Filters changed" in dialog._status_label.text()


@pytest.mark.parametrize("close_method", ["close", "reject"])
def test_closing_while_chooser_is_open_cancels_export(dialog, monkeypatch, tmp_path, close_method):
    destination = tmp_path / "must_not_exist.json"

    def chooser(*args):
        getattr(dialog, close_method)()
        return str(destination), ""

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", chooser)
    dialog._export_button.click()
    assert dialog._closed and not dialog._exporting
    assert not destination.exists() and dialog._snapshot is None
    assert not dialog._export_button.isEnabled()


def test_long_literals_keep_actions_visible_at_small_and_normal_sizes(dialog, records, qt):
    literal = "<b>" + "Ω & literal " * 80 + "</b>"
    with records[0]._session_scope() as session:
        session.query(Character).filter_by(id=records[1].id).update({"name": literal})
    dialog._character_combo.setCurrentIndex(dialog._character_combo.findData(records[1].id))
    dialog.refresh()
    dialog._apply_button.click()
    assert dialog._scope_label.text().startswith(f"Character #{records[1].id}\n")
    assert literal in dialog._source_details_edit.toPlainText()
    for width, height in ((1000, 750), (480, 440)):
        dialog.resize(width, height)
        qt.processEvents()
        assert dialog.width() <= width and dialog.height() <= height
        for control in (dialog._apply_button, dialog._refresh_button, dialog._export_button):
            position = control.mapTo(dialog, control.rect().topLeft())
            assert control.isVisible()
            assert 0 <= position.x() < dialog.width()
            assert position.y() + control.height() <= dialog.height()
        for text in ("Character", "From (UTC, inclusive)", "Until (UTC, inclusive)"):
            label = next(label for label in dialog.findChildren(QtWidgets.QLabel) if label.text() == text)
            assert label.width() >= 100, f"{text} filter label must stay readable"
        assert dialog._scroll_area.verticalScrollBar().maximum() > 0


@pytest.mark.parametrize("character_id", [True, 1.0, "1", 0, -1, 2**63])
def test_constructor_rejects_invalid_explicit_identity_before_qt_coercion(records, qt, monkeypatch, character_id):
    def unexpected(*args):
        pytest.fail("Invalid explicit identity must be rejected before reading character choices")

    monkeypatch.setattr(records[0], "get_all_characters", unexpected)
    with pytest.raises(ValueError):
        dialog_class()(records[0], character_id=character_id)


def test_initial_character_choice_read_failure_never_broadens_explicit_scope(records, qt, monkeypatch):
    def fail():
        raise DatabaseError("saved characters temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(records[0], "get_all_characters", fail)
        widget = dialog_class()(records[0], character_id=records[1].id)
    try:
        assert widget._snapshot is None
        assert widget._character_combo.currentData() == records[1].id
        widget.refresh()
        assert widget._snapshot.filters.character_id == records[1].id
        assert widget._snapshot.overall.sessions == 28
    finally:
        widget.close()
        widget.deleteLater()
        qt.processEvents()


@pytest.mark.parametrize("kind", ["limit", "data", "unavailable"])
def test_typed_read_failure_keeps_clear_complete_report_error(dialog, records, monkeypatch, kind):
    from src.database.daily_session_overview import (
        DailySessionDataError,
        DailySessionLimitError,
        DailySessionUnavailable,
    )
    error = {"limit": DailySessionLimitError(10001), "data": DailySessionDataError(2),
             "unavailable": DailySessionUnavailable(records[1].id)}[kind]

    def fail(*args):
        raise error

    monkeypatch.setattr(records[0], "get_daily_session_overview", fail)
    dialog.refresh()
    assert str(error) in dialog._status_label.text()
    assert dialog._snapshot is None and dialog._days_table.rowCount() == 0
    assert not dialog._export_button.isEnabled()


def test_oversized_character_source_is_unavailable_instead_of_silently_truncated(dialog, records):
    with records[0]._session_scope() as session:
        session.query(Character).filter_by(id=records[1].id).update({"name": "x" * 1001})
    dialog.refresh()
    assert dialog._snapshot is None
    assert "source" in dialog._status_label.text()
    assert not dialog._export_button.isEnabled()
