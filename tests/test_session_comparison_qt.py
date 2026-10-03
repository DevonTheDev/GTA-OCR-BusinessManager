"""Opt-in native Qt comparison workflows over disposable SQLite records."""

import importlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta
from html import escape

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native comparison tests", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import Qt
from src.database.models import Character, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture(scope="module")
def qt():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def records(tmp_path):
    repository = Repository(str(tmp_path / "comparison.db"))
    assert repository.initialize()
    alpha = repository.get_or_create_character("<b>Alpha & Ω</b>")
    beta = repository.get_or_create_character("Beta")
    empty = repository.get_or_create_character("No completed sessions")
    repository.set_active_character(beta.id)
    ids = []
    for index in range(28):
        character = alpha if index % 2 else beta
        record = repository.start_session(character, start_money=1000)
        start = datetime(2026, 1, 1) + timedelta(days=index)
        with repository._session_scope() as session:
            session.query(Session).filter_by(id=record.id).update({
                "started_at": start, "ended_at": start + timedelta(minutes=30),
                "end_money": 1050, "total_earnings": 50,
            })
        ids.append(record.id)
    baseline, comparison = ids[-1], ids[-2]
    with repository._session_scope() as session:
        session.query(Session).filter_by(id=baseline).update({
            "ended_at": datetime(2026, 1, 28, 2), "total_earnings": -300,
        })
    for outcome in (True, False, None):
        repository.log_activity(baseline, "HEIST", success=outcome)
    repository.log_activity(baseline, "<b>delivery & Ω</b>", success=True)
    repository.log_activity(comparison, "HEIST", success=False)
    repository.log_activity(comparison, "", success=None)
    yield repository, alpha, beta, empty, ids
    repository.close()


@pytest.fixture
def panel(records, qt):
    module = importlib.import_module("src.ui.widgets.history_panel")
    widget = module.SessionHistoryPanel(repository=records[0])
    widget.refresh()
    qt.processEvents()
    yield widget
    dialog = getattr(widget, "_comparison_dialog", None)
    if dialog is not None:
        dialog.close()
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def open_comparison(panel, qt):
    panel._pin_baseline_button.click()
    panel._sessions_table.selectRow(1)
    panel._compare_button.click()
    qt.processEvents()
    assert panel._comparison_dialog is not None
    return panel._comparison_dialog


def table_rows(table):
    return [[table.item(row, column).text() for column in range(table.columnCount())]
            for row in range(table.rowCount())]


def test_pin_actual_id_survives_page_character_and_empty_filters(panel, records, qt):
    _, alpha, beta, empty, ids = records
    assert hasattr(panel, "_pin_baseline_button"), "History needs a baseline action"
    assert not panel._compare_button.isEnabled()
    panel._pin_baseline_button.click()
    assert panel._baseline_id == ids[-1]
    assert f"Session #{ids[-1]}" in panel._baseline_label.text()
    assert alpha.name in panel._baseline_label.text()
    assert "2026-01-28" in panel._baseline_label.text()
    assert not panel._compare_button.isEnabled()
    panel._next_button.click()
    assert panel._selected_id == ids[2]
    assert panel._baseline_id == ids[-1]
    assert panel._compare_button.isEnabled()
    panel._character_combo.setCurrentIndex(panel._character_combo.findData(beta.id))
    assert panel._baseline_id == ids[-1]
    assert panel._compare_button.isEnabled()
    panel._character_combo.setCurrentIndex(panel._character_combo.findData(empty.id))
    qt.processEvents()
    assert panel._sessions_table.rowCount() == 0
    assert panel._baseline_id == ids[-1]
    assert not panel._compare_button.isEnabled()
    assert not panel._pin_baseline_button.isEnabled()
    assert panel._clear_baseline_button.isEnabled()


def test_explicit_repin_clear_and_refresh_keep_selection_rules(panel, records, qt):
    ids = records[-1]
    assert hasattr(panel, "_pin_baseline_button"), "History needs a baseline action"
    panel._pin_baseline_button.click()
    panel._sessions_table.selectRow(2)
    panel._pin_baseline_button.click()
    assert panel._baseline_id == ids[-3]
    assert not panel._compare_button.isEnabled()
    panel._refresh_button.click()
    assert panel._selected_id == panel._baseline_id == ids[-3]
    panel._clear_baseline_button.click()
    assert panel._baseline_id is None
    assert "No baseline" in panel._baseline_label.text()
    assert not panel._clear_baseline_button.isEnabled()
    assert not panel._compare_button.isEnabled()
    assert panel._export_button.isEnabled()


def test_compare_rereads_identities_and_exact_metrics(panel, records, qt):
    repository, alpha, beta, _, ids = records
    panel._pin_baseline_button.click()
    panel._sessions_table.selectRow(1)
    with repository._session_scope() as session:
        session.query(Character).filter_by(id=alpha.id).update({"name": "<i>Renamed Ω</i>"})
    panel._compare_button.click()
    qt.processEvents()
    dialog = panel._comparison_dialog
    assert dialog is not None, "Compare must open the selected pair"
    assert dialog._comparison.baseline.session_id == ids[-1]
    assert dialog._comparison.comparison.session_id == ids[-2]
    assert "<i>Renamed Ω</i>" in dialog._baseline_label.text()
    assert f"Character #{alpha.id}" in dialog._baseline_label.text()
    assert f"Character #{beta.id}" in dialog._comparison_label.text()
    assert dialog._baseline_label.textFormat() == Qt.TextFormat.PlainText
    assert [dialog._metrics_table.horizontalHeaderItem(i).text() for i in range(4)] == [
        "Metric", "Baseline A", "Comparison B", "Difference (B − A)",
    ]
    assert table_rows(dialog._metrics_table) == [
        ["Net balance change", "-$300", "$50", "+$350"],
        ["Duration", "2h", "30m", "-1h 30m"],
        ["Net balance change per hour", "-$150/h", "$100/h", "+$250/h"],
        ["Recorded activities", "4", "2", "-2"],
        ["Passed", "2", "0", "-2"],
        ["Failed", "1", "1", "0"],
        ["Unknown outcome", "1", "1", "0"],
    ]
    assert table_rows(dialog._activity_types_table) == [
        ['""', "0", "1", "+1"],
        ['"<b>delivery & Ω</b>"', "1", "0", "-1"],
        ['"HEIST"', "3", "1", "-2"],
    ]
    assert "stored" in dialog._notes_label.text().lower()
    assert "spending" in dialog._notes_label.text().lower()
    assert "all recorded" in dialog._notes_label.text().lower()
    assert "positive duration" in dialog._notes_label.text().lower()
    assert "UTC" in dialog._snapshot_label.text()
    assert dialog._metrics_table.editTriggers() == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    dialog.close()
    qt.processEvents()
    assert panel._comparison_dialog is None
    assert panel._compare_button.isEnabled()


@pytest.mark.parametrize("target, change, clears_pin", [
    ("baseline", "open", True), ("baseline", "delete", True),
    ("comparison", "open", False), ("comparison", "delete", False),
])
def test_unavailable_exact_source_opens_no_dialog(panel, records, qt, target, change, clears_pin):
    repository, _, _, _, ids = records
    panel._pin_baseline_button.click()
    panel._sessions_table.selectRow(1)
    selected = ids[-1] if target == "baseline" else ids[-2]
    with repository._session_scope() as session:
        query = session.query(Session).filter_by(id=selected)
        if change == "delete":
            query.delete()
        else:
            query.update({"ended_at": None})
    panel._compare_button.click()
    qt.processEvents()
    assert panel._comparison_dialog is None
    assert panel._baseline_id == (None if clears_pin else ids[-1])
    assert "no longer available" in panel._status_label.text()
    assert f"#{selected}" in panel._status_label.text()
    if target == "comparison":
        assert not panel._export_button.isEnabled()
        assert not panel._compare_button.isEnabled()


def test_database_failure_keeps_pin_and_compare_retry_reads_fresh(panel, records, qt, monkeypatch):
    repository, _, _, _, ids = records
    panel._pin_baseline_button.click()
    panel._sessions_table.selectRow(1)
    with monkeypatch.context() as patch:
        def fail(*args):
            raise DatabaseError("synthetic comparison read failure")
        patch.setattr(repository, "get_session_comparison", fail)
        panel._compare_button.click()
        assert panel._baseline_id == ids[-1]
        assert panel._comparison_dialog is None
        assert "synthetic comparison read failure" in panel._status_label.text()
        assert panel._compare_button.isEnabled()
    panel._compare_button.click()
    qt.processEvents()
    assert panel._comparison_dialog._comparison.baseline.session_id == ids[-1]


def test_history_failure_retains_pin_and_reenables_after_refresh(panel, records, monkeypatch):
    repository = records[0]
    panel._pin_baseline_button.click()
    pinned = panel._baseline_id
    with monkeypatch.context() as patch:
        def fail(**kwargs):
            raise DatabaseError("synthetic history read failure")
        patch.setattr(repository, "get_completed_session_history", fail)
        panel.refresh()
    assert panel._baseline_id == pinned
    assert not panel._compare_button.isEnabled()
    assert not panel._pin_baseline_button.isEnabled()
    panel.refresh()
    panel._sessions_table.selectRow(1)
    assert panel._compare_button.isEnabled()


@pytest.mark.parametrize("baseline_net, comparison_net, duration, expected_net, expected_duration, expected_rate", [
    (None, 0, 1800, ["--", "$0", "--"], ["2h", "30m", "-1h 30m"], ["--", "$0/h", "--"]),
    (0, 0, 0, ["$0", "$0", "$0"], ["2h", "0s", "-2h"], ["$0/h", "--", "--"]),
    (-300, 50, -5400, ["-$300", "$50", "+$350"], ["2h", "-1h 30m", "-3h 30m"], ["-$150/h", "--", "--"]),
    (-300, 50, None, ["-$300", "$50", "+$350"], ["2h", "--", "--"], ["-$150/h", "--", "--"]),
])
def test_unknown_zero_and_signed_duration_stay_distinct(panel, records, qt, baseline_net, comparison_net,
                                                      duration, expected_net, expected_duration, expected_rate):
    repository, _, _, _, ids = records
    with repository._session_scope() as session:
        session.query(Session).filter_by(id=ids[-1]).update({"total_earnings": baseline_net})
        session.query(Session).filter_by(id=ids[-2]).update({
            "total_earnings": comparison_net,
            "started_at": None if duration is None else datetime(2026, 1, 27, 0, 30) - timedelta(seconds=duration),
        })
    dialog = open_comparison(panel, qt)
    rows = table_rows(dialog._metrics_table)
    assert rows[0][1:] == expected_net
    assert rows[1][1:] == expected_duration
    assert rows[2][1:] == expected_rate


def test_cancel_export_creates_nothing_and_reenables_action(panel, qt, tmp_path, monkeypatch):
    dialog = open_comparison(panel, qt)
    original = dialog._comparison
    before = sorted(path.name for path in tmp_path.iterdir())
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    dialog._export_button.click()
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert dialog._comparison is original
    assert dialog._export_button.isEnabled()


def test_export_exact_displayed_snapshot_despite_reentrant_selection_and_database_changes(panel, records, qt,
                                                                                        tmp_path, monkeypatch):
    repository, _, _, _, ids = records
    dialog = open_comparison(panel, qt)
    expected = dialog._comparison.to_report()
    destination = tmp_path / "snapshot.json"
    choices = []
    def choose(*args, **kwargs):
        choices.append(args[2])
        panel._sessions_table.selectRow(3)
        with repository._session_scope() as session:
            session.query(Session).filter_by(id=ids[-1]).update({"total_earnings": 999999})
        dialog._export_comparison()
        return str(destination), ""
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", choose)
    dialog._export_button.click()
    assert choices == [f"session_{ids[-1]}_vs_{ids[-2]}.json"]
    assert json.loads(destination.read_text()) == expected
    assert "Exported comparison" in dialog._status_label.text()
    assert dialog._export_button.isEnabled()


def test_export_failure_preserves_old_file_and_retries(panel, qt, tmp_path, monkeypatch):
    from src.utils import persistence
    dialog = open_comparison(panel, qt)
    destination = tmp_path / "existing.json"
    destination.write_text("previous complete file")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    with monkeypatch.context() as patch:
        def fail(source, target):
            raise OSError("synthetic replace failure")
        patch.setattr(persistence.os, "replace", fail)
        dialog._export_button.click()
    assert destination.read_text() == "previous complete file"
    assert "synthetic replace failure" in dialog._status_label.text()
    assert dialog._export_button.isEnabled()
    dialog._export_button.click()
    assert json.loads(destination.read_text()) == dialog._comparison.to_report()


def test_chooser_exception_is_visible_and_retry_remains_enabled(panel, qt, monkeypatch):
    dialog = open_comparison(panel, qt)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic chooser failure")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", fail)
    dialog._export_button.click()
    assert "synthetic chooser failure" in dialog._status_label.text()
    assert dialog._export_button.isEnabled()


def test_long_literal_names_and_null_blank_custom_types_remain_inspectable(panel, qt):
    from src.database.session_comparison import ActivityTypeComparison
    from src.ui.widgets.session_comparison_dialog import SessionComparisonDialog
    from src.utils.exporter import DataExporter
    opened = open_comparison(panel, qt)
    snapshot = opened._comparison
    opened.close()
    long_name = "<b>Literal Ω name</b> " * 25
    snapshot = replace(snapshot, baseline=replace(snapshot.baseline, character_name=long_name), activity_types=(
        ActivityTypeComparison(None, 1, 0, -1),
        ActivityTypeComparison("", 0, 2, 2),
        ActivityTypeComparison(long_name, 3, 1, -2),
    ))
    dialog = SessionComparisonDialog(snapshot, exporter=DataExporter(panel._get_repository()), parent=panel)
    try:
        dialog.resize(620, 430)
        dialog.show()
        qt.processEvents()
        assert long_name in dialog._baseline_label.text()
        assert dialog._baseline_label.textFormat() == Qt.TextFormat.PlainText
        assert dialog._baseline_label.wordWrap()
        assert table_rows(dialog._activity_types_table)[0][0] == "(unknown type)"
        assert table_rows(dialog._activity_types_table)[1][0] == '""'
        type_text = json.dumps(long_name, ensure_ascii=False)
        assert table_rows(dialog._activity_types_table)[2][0] == type_text
        assert dialog._activity_types_table.item(2, 0).toolTip() == f"<qt>{escape(type_text)}</qt>"
        assert dialog._scroll_area.widgetResizable()
        assert dialog._scroll_area.verticalScrollBar().maximum() > 0
        assert dialog._export_button.isVisible()
        assert not dialog.grab().isNull()
    finally:
        dialog.close()


def test_type_markers_cannot_collide_with_actual_custom_names(panel, qt):
    from src.database.session_comparison import ActivityTypeComparison
    from src.ui.widgets.session_comparison_dialog import SessionComparisonDialog
    from src.utils.exporter import DataExporter
    opened = open_comparison(panel, qt)
    snapshot = replace(opened._comparison, activity_types=tuple(
        ActivityTypeComparison(kind, 1, 0, -1)
        for kind in (None, "", "(unknown type)", "(blank type)", '"quoted"\\type')
    ))
    opened.close()
    dialog = SessionComparisonDialog(snapshot, exporter=DataExporter(panel._get_repository()), parent=panel)
    try:
        labels = [row[0] for row in table_rows(dialog._activity_types_table)]
        assert len(set(labels)) == 5, "Stored strings must be distinct from missing/blank type markers"
        assert labels == ["(unknown type)", '""', '"(unknown type)"', '"(blank type)"', '"\\"quoted\\"\\\\type"']
        assert "quoted" in dialog._notes_label.text().lower()
        assert dialog._comparison.activity_types[2].activity_type == "(unknown type)"
    finally:
        dialog.close()


def test_retired_dialog_completion_cannot_clear_new_comparison(panel, qt):
    first = open_comparison(panel, qt)
    first.reject()
    assert panel._comparison_dialog is None
    panel._compare_selected()
    second = panel._comparison_dialog
    assert second is not None and second is not first
    # A second completion can already be queued when the first dialog closes.
    first.finished.emit(0)
    assert panel._comparison_dialog is second
    assert second.isVisible()
    assert not panel._compare_button.isEnabled()
