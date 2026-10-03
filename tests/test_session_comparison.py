"""Exact completed-session comparisons using real, disposable SQLite databases."""

from dataclasses import FrozenInstanceError, asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from src.database.models import Activity, Character, Earnings, Session
from src.database.repository import DatabaseError, Repository


@pytest.fixture
def repository(tmp_path):
    repo = Repository(str(tmp_path / "comparison.db"))
    assert repo.initialize()
    yield repo
    repo.close()
    repo._session_factory.kw["bind"].dispose()


@pytest.fixture
def pair(repository):
    start = datetime(2026, 1, 1, 12)
    with repository._session_scope() as db:
        owner = Character(name="François <A>", is_active=True)
        other = Character(name="東京 & B", is_active=False)
        db.add_all([owner, other])
        db.flush()
        baseline = Session(
            character_id=owner.id, started_at=start, ended_at=start + timedelta(hours=1),
            start_money=1000, end_money=1250, total_earnings=-300,
        )
        unrelated = Session(
            character_id=owner.id, started_at=start, ended_at=start + timedelta(minutes=5),
            total_earnings=999999,
        )
        comparison = Session(
            character_id=other.id, started_at=start, ended_at=start + timedelta(hours=2),
            start_money=20, end_money=10, total_earnings=600,
        )
        opened = Session(character_id=owner.id, started_at=start)
        db.add_all([baseline, unrelated, comparison, opened])
        db.flush()
        db.add_all([
            Activity(session_id=baseline.id, activity_type="VIP_WORK", success=True, earnings=123),
            Activity(session_id=baseline.id, activity_type="VIP_WORK", success=False, earnings=456),
            Activity(session_id=baseline.id, activity_type="", success=None),
            Activity(session_id=comparison.id, activity_type="VIP_WORK", success=False),
            Activity(session_id=comparison.id, activity_type="Custom 🛸", success=True),
            Activity(session_id=unrelated.id, activity_type="not selected", success=True),
            Activity(session_id=opened.id, activity_type="not completed", success=True),
        ])
        db.add_all([
            Earnings(session_id=baseline.id, amount=1000) for _ in range(4)
        ] + [Earnings(session_id=comparison.id, amount=-77) for _ in range(3)])
        result = SimpleNamespace(
            repo=repository, baseline=baseline.id, comparison=comparison.id,
            unrelated=unrelated.id, opened=opened.id, owner=owner.id, other=other.id,
            start=start,
        )
    return result


def test_exact_pair_preserves_stored_net_and_separates_activity_outcomes(pair):
    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert result.baseline.session_id == pair.baseline
    assert result.comparison.session_id == pair.comparison
    assert result.baseline.character_id == pair.owner
    assert result.comparison.character_id == pair.other
    assert result.baseline.character_name == "François <A>"
    assert result.comparison.character_name == "東京 & B"
    assert result.baseline.started_at == result.comparison.started_at == pair.start
    assert result.baseline.ended_at == pair.start + timedelta(hours=1)
    assert (result.baseline.start_money, result.baseline.end_money) == (1000, 1250)
    assert asdict(result.baseline.metrics) == {
        "net_change": -300, "duration_seconds": 3600.0, "net_per_hour": -300.0,
        "recorded_activities": 3, "passed": 1, "failed": 1, "unknown": 1,
    }
    assert asdict(result.comparison.metrics) == {
        "net_change": 600, "duration_seconds": 7200.0, "net_per_hour": 300.0,
        "recorded_activities": 2, "passed": 1, "failed": 1, "unknown": 0,
    }
    assert asdict(result.differences) == {
        "net_change": 900, "duration_seconds": 3600.0, "net_per_hour": 600.0,
        "recorded_activities": -1, "passed": 0, "failed": 0, "unknown": -1,
    }
    assert [asdict(item) for item in result.activity_types] == [
        {"activity_type": "", "baseline": 1, "comparison": 0, "difference": -1},
        {"activity_type": "Custom 🛸", "baseline": 0, "comparison": 1, "difference": 1},
        {"activity_type": "VIP_WORK", "baseline": 2, "comparison": 1, "difference": -1},
    ]


def test_requested_order_controls_every_difference_even_for_tied_dates(pair):
    result = pair.repo.get_session_comparison(pair.comparison, pair.baseline)

    assert result.baseline.session_id == pair.comparison
    assert result.comparison.session_id == pair.baseline
    assert result.differences.net_change == -900
    assert result.differences.duration_seconds == -3600
    assert result.differences.net_per_hour == -600
    assert result.differences.recorded_activities == 1
    assert result.activity_types[-1].difference == 1


def test_single_select_does_not_read_earnings_or_materialize_activities(pair):
    engine = pair.repo._session_factory.kw["bind"]
    statements = []
    loaded = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def capture_activity(target, context):
        loaded.append(target)

    event.listen(engine, "before_cursor_execute", capture)
    event.listen(Activity, "load", capture_activity)
    try:
        result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(Activity, "load", capture_activity)

    assert result.baseline.metrics.recorded_activities == 3
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
    assert "join earnings" not in statements[0].lower()
    assert "from earnings" not in statements[0].lower()
    assert "activities.earnings" not in statements[0].lower()
    assert "group by" in statements[0].lower()
    assert loaded == []


def test_counts_are_not_capped_at_history_detail_limit(pair):
    with pair.repo._session_scope() as db:
        db.add_all([
            Activity(session_id=pair.baseline, activity_type="Large batch", success=True)
            for _ in range(1205)
        ])

    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert result.baseline.metrics.recorded_activities == 1208
    assert result.baseline.metrics.passed == 1206
    batch = next(item for item in result.activity_types if item.activity_type == "Large batch")
    assert (batch.baseline, batch.comparison, batch.difference) == (1205, 0, -1205)


def test_empty_activity_sets_have_zero_totals_and_no_placeholder_type(pair):
    with pair.repo._session_scope() as db:
        db.query(Activity).delete()

    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert result.activity_types == ()
    for summary in (result.baseline, result.comparison):
        assert summary.metrics.recorded_activities == 0
        assert summary.metrics.passed == summary.metrics.failed == summary.metrics.unknown == 0


def test_legacy_null_types_and_non_boolean_outcomes_remain_distinct(pair):
    # Older/imported databases may permit null types. Rebuild only this disposable
    # fixture table so the production schema stays untouched.
    with sqlite3.connect(pair.repo._db_path) as db:
        db.execute("CREATE TABLE legacy_activities AS SELECT * FROM activities")
        db.execute("DROP TABLE activities")
        db.execute("ALTER TABLE legacy_activities RENAME TO activities")
        db.executemany(
            "INSERT INTO activities (id, session_id, activity_type, success) VALUES (?, ?, ?, ?)",
            [(100, pair.baseline, None, 2), (101, pair.comparison, None, -1),
             (102, pair.comparison, None, "unknown")],
        )

    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert [item.activity_type for item in result.activity_types] == [
        None, "", "Custom 🛸", "VIP_WORK",
    ]
    assert asdict(result.activity_types[0]) == {
        "activity_type": None, "baseline": 1, "comparison": 2, "difference": 1,
    }
    assert result.baseline.metrics.unknown == 2
    assert result.comparison.metrics.unknown == 2
    assert result.baseline.metrics.passed == result.comparison.metrics.passed == 1
    assert result.baseline.metrics.failed == result.comparison.metrics.failed == 1


@pytest.mark.parametrize("net,duration,expected_rate", [
    (-300, 1800, -600), (0, 1800, 0), (None, 1800, None),
    (300, 0, None), (300, -1800, None), (300, None, None),
    (float("inf"), 1800, None), (float("-inf"), 1800, None),
    (float("nan"), 1800, None), ("not numeric", 1800, None),
    (12.5, 1800, 25),
])
def test_nullable_money_duration_and_rate_semantics(pair, net, duration, expected_rate):
    ended = pair.start if duration is None else pair.start + timedelta(seconds=duration)
    with sqlite3.connect(pair.repo._db_path) as db:
        db.execute(
            "UPDATE sessions SET total_earnings=?, started_at=?, ended_at=?, "
            "start_money=NULL, end_money=NULL WHERE id=?",
            (net, None if duration is None else pair.start.isoformat(" "),
             ended.isoformat(" "), pair.baseline),
        )

    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    metrics = result.baseline.metrics
    known_net = net if isinstance(net, (int, float)) and abs(net) != float("inf") else None
    if isinstance(known_net, float) and known_net != known_net:
        known_net = None
    assert metrics.net_change == known_net
    assert metrics.duration_seconds == duration
    assert metrics.net_per_hour == expected_rate
    assert result.baseline.start_money is result.baseline.end_money is None
    assert result.differences.net_change == (None if known_net is None else 600 - known_net)
    assert result.differences.duration_seconds == (None if duration is None else 7200 - duration)
    assert result.differences.net_per_hour == (None if expected_rate is None else 300 - expected_rate)
    json.dumps(result.to_report(), allow_nan=False)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "not numeric"])
def test_invalid_legacy_balances_become_unavailable(pair, value):
    with sqlite3.connect(pair.repo._db_path) as db:
        db.execute("UPDATE sessions SET start_money=?, end_money=? WHERE id=?",
                   (value, value, pair.baseline))

    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert result.baseline.start_money is result.baseline.end_money is None
    assert result.baseline.metrics.net_change == -300
    json.dumps(result.to_report(), allow_nan=False)


def test_comparison_does_not_mutate_inventory_or_active_character(pair):
    def contents():
        with sqlite3.connect(pair.repo._db_path) as db:
            return list(db.iterdump())

    before = contents()
    pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert contents() == before
    assert pair.repo.get_active_character().id == pair.owner


def test_detached_immutable_snapshot_and_deterministic_json_report(pair):
    before = datetime.now(timezone.utc)
    result = pair.repo.get_session_comparison(pair.baseline, pair.comparison)
    after = datetime.now(timezone.utc)
    report = result.to_report()

    assert before <= result.generated_at <= after
    assert isinstance(result.activity_types, tuple)
    for item, field in [
        (result, "generated_at"), (result.baseline, "character_name"),
        (result.baseline.metrics, "net_change"), (result.activity_types[0], "baseline"),
    ]:
        assert is_dataclass(item)
        with pytest.raises(FrozenInstanceError):
            setattr(item, field, None)
    assert report["format_version"] == 1
    assert report["orientation"] == "comparison_minus_baseline"
    assert report["timezone"] == "UTC"
    assert report["generated_at"] == result.generated_at.isoformat()
    assert report["baseline"] == {
        "session_id": pair.baseline, "character_id": pair.owner,
        "character_name": "François <A>", "started_at": pair.start.isoformat(),
        "ended_at": (pair.start + timedelta(hours=1)).isoformat(),
        "start_money": 1000, "end_money": 1250, "metrics": asdict(result.baseline.metrics),
    }
    assert report["differences"] == asdict(result.differences)
    assert report["activity_types"] == [asdict(item) for item in result.activity_types]
    assert report["metric_notes"] and all(isinstance(v, str) for v in report["metric_notes"].values())
    assert json.loads(json.dumps(report, ensure_ascii=False, allow_nan=False)) == report
    with pair.repo._session_scope() as db:
        db.query(Session).filter_by(id=pair.baseline).update({Session.total_earnings: 999})
        db.query(Character).filter_by(id=pair.owner).update({Character.name: "Renamed"})
        db.query(Activity).filter_by(session_id=pair.baseline).delete()
    assert result.to_report() == report
    report["baseline"]["metrics"]["net_change"] = 12345
    report["activity_types"].clear()
    report["metric_notes"].clear()
    assert result.to_report()["baseline"]["metrics"]["net_change"] == -300
    assert len(result.to_report()["activity_types"]) == 3
    assert result.to_report()["metric_notes"]


@pytest.mark.parametrize("field", ["baseline_id", "comparison_id"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "1", None])
def test_invalid_ids_fail_before_database_access(tmp_path, field, value):
    repo = Repository(str(tmp_path / "missing-directory" / "comparison.db"))
    arguments = {"baseline_id": 1, "comparison_id": 2, field: value}

    with pytest.raises(ValueError, match=field):
        repo.get_session_comparison(**arguments)

    assert repo._initialized is False
    assert not (tmp_path / "missing-directory").exists()


def test_identical_ids_fail_before_database_access(tmp_path):
    repo = Repository(str(tmp_path / "missing-directory" / "comparison.db"))

    with pytest.raises(ValueError, match="different|distinct"):
        repo.get_session_comparison(1, 1)

    assert repo._initialized is False


@pytest.mark.parametrize("baseline,comparison,missing", [
    ("baseline", "opened", ("opened",)),
    ("opened", "comparison", ("opened",)),
    ("baseline", 999, (999,)),
    (999, "comparison", (999,)),
    (999, "opened", (999, "opened")),
    (999, 1000, (999, 1000)),
    (2**63, "comparison", (2**63,)),
    (2**63, 2**64, (2**63, 2**64)),
])
def test_missing_or_open_sources_raise_typed_unavailable_ids(pair, baseline, comparison, missing):
    from src.database.session_comparison import SessionComparisonUnavailable

    def resolve(value):
        return getattr(pair, value) if isinstance(value, str) else value

    with pytest.raises(SessionComparisonUnavailable) as error:
        pair.repo.get_session_comparison(resolve(baseline), resolve(comparison))

    assert isinstance(error.value, ValueError)
    assert error.value.session_ids == tuple(map(resolve, missing))


def test_orphan_character_is_an_unavailable_source(pair):
    from src.database.session_comparison import SessionComparisonUnavailable

    with sqlite3.connect(pair.repo._db_path) as db:
        db.execute("DELETE FROM characters WHERE id=?", (pair.owner,))

    with pytest.raises(SessionComparisonUnavailable) as error:
        pair.repo.get_session_comparison(pair.baseline, pair.comparison)

    assert error.value.session_ids == (pair.baseline,)


def test_database_initialization_failure_remains_database_error(tmp_path):
    repo = Repository(str(tmp_path / "missing-directory" / "comparison.db"))

    with pytest.raises(DatabaseError, match="initialization failed"):
        repo.get_session_comparison(1, 2)


def test_query_failure_remains_database_error(repository):
    with repository._session_factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("DROP TABLE sessions")

    with pytest.raises(DatabaseError, match="Database operation failed"):
        repository.get_session_comparison(1, 2)
