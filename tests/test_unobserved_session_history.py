"""Newly completed unknown balances through actual SQLite, readers and exports."""

import csv
from datetime import date
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.database.daily_session_overview import DailySessionFilters
from src.database.repository import Repository
from src.utils.exporter import DataExporter


DAY = date(2026, 1, 2)


@pytest.fixture
def history(tmp_path):
    repo = Repository(str(tmp_path / "unobserved.sqlite"))
    assert repo.initialize()
    character = repo.get_or_create_character("Observed and unavailable")
    yield SimpleNamespace(repo=repo, character=character, exporter=DataExporter(repo), root=tmp_path)
    repo.close()
    repo._session_factory.kw["bind"].dispose()


def complete(history, *, opening=1000, ending=1100, unobserved=False):
    session = history.repo.start_session(history.character, start_money=opening)
    assert session is not None
    assert history.repo.end_session(session.id, ending, unobserved=unobserved)
    # Use one hour on a fixed UTC day, independent of machine clock and speed.
    with sqlite3.connect(history.repo._db_path) as db:
        db.execute("UPDATE sessions SET started_at=?,ended_at=? WHERE id=?",
                   ("2026-01-02 11:00:00", "2026-01-02 12:00:00", session.id))
    return session.id


def rows(history):
    with sqlite3.connect(history.repo._db_path) as db:
        return db.execute("SELECT * FROM sessions ORDER BY id").fetchall()


def test_new_unobserved_session_survives_history_and_selected_json(history):
    session_id = complete(history, unobserved=True)
    before = rows(history)
    page = history.repo.get_completed_session_history()
    assert page.total == 1
    session = page.sessions[0]
    assert session.id == session_id
    assert session.started_at is not None and session.ended_at is not None
    assert session.duration_seconds == 3600
    assert session.start_money is session.end_money is session.net_change is None
    path = history.root / "selected.json"
    assert history.exporter.export_to_json(session_id, path).success
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["session"]["start_money"] is None
    assert report["session"]["end_money"] is None
    assert report["session"]["total_earnings"] is None
    assert report["session"]["duration_seconds"] == 3600
    assert rows(history) == before


@pytest.mark.parametrize("include_observed_zero", [False, True])
def test_daily_coverage_excludes_unknown_net_but_includes_observed_zero(history, include_observed_zero):
    complete(history)
    unknown_id = complete(history, unobserved=True)
    if include_observed_zero:
        complete(history, opening=0, ending=0)
    before = rows(history)
    overview = history.repo.get_daily_session_overview(DailySessionFilters(DAY, DAY))
    expected_known = 2 if include_observed_zero else 1
    assert overview.overall.sessions == expected_known + 1
    assert overview.overall.known_net == overview.overall.paired_sessions == expected_known
    assert overview.overall.net_change_total == 100
    assert overview.overall.duration_seconds_total == 3600 * (expected_known + 1)
    assert overview.overall.net_per_hour == 100 / expected_known
    unknown = next(row for row in overview.rows if row.session_id == unknown_id)
    assert unknown.net_change is None and unknown.duration_seconds == 3600
    output = history.root / "daily.json"
    assert history.exporter.export_daily_session_overview(overview, output).success
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == overview.to_report()
    assert next(row for row in report["rows"] if row["session_id"] == unknown_id)["net_change"] is None
    assert rows(history) == before


def test_all_unobserved_daily_totals_and_rates_remain_unavailable(history):
    complete(history, unobserved=True)
    complete(history, unobserved=True)
    overview = history.repo.get_daily_session_overview(DailySessionFilters(DAY, DAY))
    assert overview.overall.sessions == 2
    assert overview.overall.positive_durations == 2
    assert overview.overall.duration_seconds_total == 7200
    assert overview.overall.known_net == overview.overall.paired_sessions == 0
    assert overview.overall.net_change_total is overview.overall.net_per_hour is None


def test_comparison_and_its_json_preserve_unavailable_money_and_differences(history):
    known_id = complete(history)
    unknown_id = complete(history, unobserved=True)
    before = rows(history)
    comparison = history.repo.get_session_comparison(known_id, unknown_id)
    assert comparison.comparison.start_money is comparison.comparison.end_money is None
    assert comparison.comparison.metrics.net_change is comparison.comparison.metrics.net_per_hour is None
    assert comparison.differences.net_change is comparison.differences.net_per_hour is None
    assert comparison.comparison.metrics.duration_seconds == 3600
    output = history.root / "comparison.json"
    assert history.exporter.export_session_comparison(comparison, output).success
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == comparison.to_report()
    assert report["comparison"]["start_money"] is None
    assert report["comparison"]["metrics"]["net_change"] is None
    assert report["differences"]["net_per_hour"] is None
    assert rows(history) == before


@pytest.mark.parametrize("balances, expected", [
    ((None, None, None), ("", "", "", "")),
    ((0, 0, 0), ("$0", "$0", "$0", "$0")),
    ((1000, 1200, 200), ("$1,000", "$1,200", "$200", "$200")),
    ((1000, 800, -200), ("$1,000", "$800", "$-200", "$-200")),
    ((None, 0, None), ("", "$0", "", "")),
    ((0, None, 0), ("$0", "", "$0", "$0")),
], ids=["unknown", "zero", "gain", "loss", "partial-unknown", "known-zero-net"])
def test_both_public_session_csv_paths_preserve_unknown_and_numeric_money(history, balances, expected):
    session_id = complete(history, unobserved=balances == (None, None, None))
    # Partial legacy values stay independently nullable; exports must not infer them.
    with sqlite3.connect(history.repo._db_path) as db:
        db.execute("UPDATE sessions SET start_money=?,end_money=?,total_earnings=? WHERE id=?",
                   (*balances, session_id))
    assert history.repo.log_activity(session_id, "VIP_WORK", "Fixture", earnings=25,
                                     success=True, duration_seconds=60)
    assert history.repo.log_earning(session_id, 25, "Fixture", 1025)
    before = rows(history)
    output = history.root / "csv"
    result = history.exporter.export_session_to_csv(session_id, output)
    assert result.success and result.rows_exported == 9
    with (output / f"session_{session_id}_info.csv").open(newline="", encoding="utf-8") as stream:
        info = dict(csv.reader(stream))
    assert (info["Start Money"], info["End Money"], info["Total Earnings"]) == expected[:3]
    assert info["Duration"] == "1.0h 0.0m"
    with (output / f"session_{session_id}_activities.csv").open(newline="", encoding="utf-8") as stream:
        activity = list(csv.DictReader(stream))[0]
    assert activity["Earnings"] == "$25"
    assert activity["Duration (min)"] == "1.0" and activity["Success"] == "Yes"
    with (output / f"session_{session_id}_earnings.csv").open(newline="", encoding="utf-8") as stream:
        earning = list(csv.DictReader(stream))[0]
    assert earning["Amount"] == "$25" and earning["Balance After"] == "$1,025"
    summary = history.root / "summary.csv"
    result = history.exporter.export_sessions_summary(history.character.id, summary)
    assert result.success and result.rows_exported == 1
    with summary.open(newline="", encoding="utf-8") as stream:
        item = list(csv.DictReader(stream))[0]
    assert tuple(item[key] for key in ("Start Money", "End Money", "Total Earnings", "$/Hour")) == expected
    assert item["Date"] == "2026-01-02 11:00" and item["Duration (min)"] == "60"
    assert rows(history) == before


def test_new_finalization_does_not_reclassify_legacy_zero_or_null_rows(history):
    zero_id = complete(history, opening=0, ending=0)
    null_id = complete(history, unobserved=True)
    before = rows(history)
    complete(history, unobserved=True)
    assert rows(history)[:2] == before
    assert history.repo.export_session_data(zero_id)["session"]["total_earnings"] == 0
    assert history.repo.export_session_data(null_id)["session"]["total_earnings"] is None
