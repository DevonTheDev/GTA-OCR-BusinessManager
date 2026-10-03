"""Opt-in MainWindow → insights → ledger/export over temporary captured records."""

import json
import os

import pytest

if os.environ.get("GTA_RUN_QT_TESTS") != "1":
    pytest.skip("set GTA_RUN_QT_TESTS=1 for native insights integration", allow_module_level=True)

QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import Qt
from src.app import AppState
from src.database.models import Activity, Session
from src.ui.widgets.activity_ledger_dialog import ActivityLedgerDialog
from tests.test_activity_ledger_app_qt import window as window, contents
from tests.test_history_panel_qt import qt as qt, records as records


def open_insights(window, qt):
    _manager, view, data = window
    history = view._history_panel
    history._character_combo.setCurrentIndex(history._character_combo.findData(data[1].id))
    assert hasattr(history, "_activity_insights_button"), "History needs the new insights workflow"
    history._activity_insights_button.click()
    qt.processEvents()
    assert history._activity_insights_dialog is not None
    return history, history._activity_insights_dialog


def test_main_history_filters_drills_and_exports_without_capture_or_record_mutation(window, qt, monkeypatch, tmp_path):
    manager, view, data = window
    repository, alpha, active, _sessions, _opened = data
    before = contents(repository)
    history, insights = open_insights(window, qt)
    assert insights._snapshot.filters.character_id == alpha.id
    assert insights._snapshot.overall.activities == 31
    assert insights._snapshot.overall.sessions == 31
    assert insights._snapshot.overall.recorded_amount_total == 3100
    # The separate session-note query does not silently filter activity insight.
    history._annotation_query_edit.setText("no saved note matches")
    assert insights._snapshot.overall.activities == 31
    insights._query_edit.setText("activity 1")
    assert insights._snapshot is None and not insights._export_button.isEnabled()
    insights._apply_button.click(); qt.processEvents()
    snapshot = insights._snapshot
    assert snapshot.overall.activities == 5
    assert snapshot.overall.recorded_amount_total == 500
    assert snapshot.overall.recorded_amount_per_hour == 6000
    assert snapshot.overall.paired_records == 5
    insights._types_table.selectRow(0)
    insights._drill_button.click(); qt.processEvents()
    ledgers = insights.findChildren(ActivityLedgerDialog)
    assert len(ledgers) == 1
    ledger = ledgers[0]
    assert ledger._page.filters.character_id == alpha.id
    assert ledger._page.filters.query == "activity 1"
    assert ledger._page.filters.activity_type == "CONTACT_MISSION"
    assert {row.name for row in ledger._page.rows} == {f"Activity {i}" for i in (10, 12, 14, 16, 18)}
    target = tmp_path / "accepted-insights.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    insights._export_button.click(); qt.processEvents()
    assert json.loads(target.read_text()) == snapshot.to_report()
    assert manager.state == AppState.STOPPED and manager._capture_thread is None
    assert manager.session_stats is None and manager.data.db_session_id is None
    assert repository.get_active_character().id == active.id
    assert contents(repository) == before
    assert view._tabs.currentWidget() is history
    insights.close(); qt.processEvents()
    assert history._activity_insights_dialog is None


def test_drill_is_a_new_database_observation_while_export_retains_summary(window, qt, monkeypatch, tmp_path):
    _manager, _view, data = window
    repository, alpha, *_rest = data
    _history, insights = open_insights(window, qt)
    snapshot = insights._snapshot
    expected = snapshot.to_report()
    with repository._session_scope() as db:
        row = (db.query(Activity).join(Session, Activity.session_id == Session.id)
               .filter(Session.character_id == alpha.id, Session.ended_at.isnot(None))
               .order_by(Activity.id.desc()).first())
        row.earnings = 99999
        row.activity_name = "<b>Later recorded amount</b>"
    insights._drill_button.click(); qt.processEvents()
    ledger = insights.findChildren(ActivityLedgerDialog)[0]
    assert ledger._page.rows[0].recorded_amount == 99999
    assert "<b>Later recorded amount</b>" in ledger._details_edit.toPlainText()
    assert insights._snapshot is snapshot and snapshot.to_report() == expected
    target = tmp_path / "prior-observation.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    insights._export_button.click(); qt.processEvents()
    assert json.loads(target.read_text()) == expected
    ledger.close(); qt.processEvents()
    insights._refresh_button.click(); qt.processEvents()
    assert insights._snapshot.overall.recorded_amount_total == 3100 - 100 + 99999


def test_real_window_and_literal_type_layout(window, qt):
    _manager, view, data = window
    repository, alpha, *_rest = data
    literal = "<b>Custom & 雪</b>"
    with repository._session_scope() as db:
        row = (db.query(Activity).join(Session, Activity.session_id == Session.id)
               .filter(Session.character_id == alpha.id).order_by(Activity.id.desc()).first())
        row.activity_type = literal
    history, insights = open_insights(window, qt)
    view.resize(1000, 700); insights.resize(1100, 800)
    view._update_ui(); qt.processEvents()
    assert view.width() == 1000
    index = next(index for index, group in enumerate(insights._snapshot.groups) if group.activity_type == literal)
    insights._types_table.selectRow(index); qt.processEvents()
    assert literal in insights._details_edit.toPlainText()
    assert insights._details_edit.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert history._activity_insights_button.isVisible()
    assert view.grab().save("/tmp/gta-activity-insights-main.png")
    assert insights.grab().save("/tmp/gta-activity-insights-dialog.png")
