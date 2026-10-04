"""Reviewed batches commit together and expose ambiguous commit outcomes safely."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from importlib import import_module
from importlib.util import find_spec
import sqlite3
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from src.database.business_checkins import (
    BUSINESS_LABELS, BusinessCheckIn, BusinessCheckInDataError,
    BusinessCheckInUnavailable, BusinessCheckInValidationError,
)
from src.database.repository import Repository
from tests.test_business_checkins import journal as journal, source_tables


def domain():
    assert find_spec("src.database.business_checkin_batches") is not None
    return import_module("src.database.business_checkin_batches")


def draft(business_id="bunker", **values):
    values.setdefault("stock_percent", 0)
    return domain().BusinessCheckInDraft(business_id, **values)


def save(journal, drafts=None, character_id=None):
    method = getattr(journal.repo, "save_business_checkins", None)
    assert callable(method), "The repository must expose one transactional batch boundary"
    return method(journal.first if character_id is None else character_id,
                  [draft(), draft("nightclub", stock_percent=None, stock_value=0)]
                  if drafts is None else drafts)


def persisted(journal):
    with sqlite3.connect(journal.path) as db:
        return db.execute("SELECT * FROM manual_business_checkins ORDER BY id").fetchall()


def test_batch_contract_is_frozen_bounded_and_has_no_source_metadata():
    item = domain().BusinessCheckInDraft("bunker")
    assert [field.name for field in fields(item)] == [
        "business_id", "stock_percent", "supply_percent", "stock_value", "note",
    ]
    assert (item.stock_percent, item.supply_percent, item.stock_value, item.note) == (None, None, None, "")
    assert domain().MAX_CHECKIN_BATCH_ROWS == len(BUSINESS_LABELS)
    with pytest.raises(FrozenInstanceError):
        item.note = "changed"
    assert not isinstance(domain().BusinessCheckInBatchUncertain(), BusinessCheckInUnavailable)


def test_normalization_detaches_the_collection_and_each_frozen_draft():
    item = draft(stock_percent=None, supply_percent=0, note="  Café\n\t ")
    incoming = [item]
    normalized = domain().normalize_business_checkin_drafts(incoming)
    assert type(normalized) is tuple and normalized == (item,) and normalized[0] is not item
    incoming.clear()
    assert normalized[0].note == "  Café\n\t " and normalized[0].supply_percent == 0


@pytest.mark.parametrize("collection", [list, tuple])
def test_batch_keeps_order_values_unicode_one_utc_save_time_and_legacy_tables(journal, collection, monkeypatch):
    before = source_tables(journal.path)
    instant = datetime(2026, 10, 4, 13, 15, 16, 123456, tzinfo=timezone.utc)
    calls = []

    def now():
        calls.append(1)
        return instant

    monkeypatch.setattr("src.database.repository.utc_now", now)
    items = collection([
        draft("nightclub", stock_percent=100, stock_value=2**63 - 1, note="  Café 雪 🚀\n\t "),
        draft("bunker", stock_percent=None, supply_percent=0),
        draft("agency", stock_percent=None, note="🚀" * 2000),
    ])
    saved = save(journal, items)
    assert type(saved) is tuple and all(type(row) is BusinessCheckIn for row in saved)
    assert [row.business_id for row in saved] == [item.business_id for item in items]
    assert [row.id for row in saved] == sorted({row.id for row in saved})
    assert len(calls) == 1
    assert all(row.character_id == journal.first and row.recorded_at == instant
               and row.recorded_at.tzinfo is timezone.utc for row in saved)
    assert [(row.stock_percent, row.supply_percent, row.stock_value, row.note) for row in saved] == [
        (item.stock_percent, item.supply_percent, item.stock_value, item.note) for item in items
    ]
    assert source_tables(journal.path) == before
    assert len(persisted(journal)) == 3
    with pytest.raises(FrozenInstanceError):
        saved[0].note = "changed"
    if collection is list:
        items.clear()
    journal.repo.close()
    assert saved[0].stock_value == 2**63 - 1 and saved[1].stock_percent is None


def test_whole_catalog_fits_and_later_batches_append_without_carryforward(journal):
    first = save(journal, [draft(key, stock_percent=25) for key in BUSINESS_LABELS])
    second = save(journal, [draft(key, stock_percent=None, note="note only") for key in BUSINESS_LABELS])
    assert len(first) == len(second) == len(BUSINESS_LABELS)
    assert first[-1].id < second[0].id
    for row in second:
        assert row.stock_percent is row.supply_percent is row.stock_value is None
        assert journal.repo.get_business_checkin_history(journal.first, row.business_id).rows == (
            row, next(item for item in first if item.business_id == row.business_id),
        )


@pytest.mark.parametrize("invalid", [None, {}, "bunker", set(), iter(()), [], ()])
def test_invalid_batch_collection_is_rejected_before_storage(invalid, monkeypatch):
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "_business_checkin_scope", lambda: pytest.fail("accessed storage"))
    method = getattr(repo, "save_business_checkins", None)
    assert callable(method)
    with pytest.raises(BusinessCheckInValidationError):
        method(1, invalid)


@pytest.mark.parametrize("case", ["duplicate", "unknown", "oversize", "dict", "object", "draft_subclass",
                                  "list_subclass", "tuple_subclass"])
def test_invalid_batch_shape_or_business_is_rejected_before_storage(case, monkeypatch):
    module = domain()
    items = [draft()]
    if case == "duplicate":
        items.append(draft())
    elif case == "unknown":
        items.append(draft("future-business"))
    elif case == "oversize":
        items = [draft()] * (module.MAX_CHECKIN_BATCH_ROWS + 1)
    elif case == "dict":
        items.append({"business_id": "nightclub", "stock_percent": 0})
    elif case == "object":
        items.append(object())
    elif case == "draft_subclass":
        class SubDraft(module.BusinessCheckInDraft):
            pass
        items.append(SubDraft("nightclub", stock_percent=0))
    elif case == "list_subclass":
        items = type("SubList", (list,), {})(items)
    else:
        items = type("SubTuple", (tuple,), {})(items)
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "_business_checkin_scope", lambda: pytest.fail("accessed storage"))
    with pytest.raises(BusinessCheckInValidationError):
        repo.save_business_checkins(1, items)


@pytest.mark.parametrize("field,value", [
    ("stock_percent", True), ("stock_percent", 1.0), ("stock_percent", -1), ("stock_percent", 101),
    ("supply_percent", "0"), ("supply_percent", False), ("supply_percent", 101),
    ("stock_value", 2**63), ("stock_value", -1), ("stock_value", True), ("stock_value", 1.0),
    ("note", None), ("note", "x" * 2001), ("note", "\ud800"), ("note", "bad\0note"),
    ("note", "bad\rnote"), ("note", "bad\x7fnote"),
])
def test_invalid_last_draft_is_validated_before_storage(field, value, monkeypatch):
    items = [draft(), draft("nightclub", **{field: value})]
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "_business_checkin_scope", lambda: pytest.fail("accessed storage"))
    with pytest.raises(BusinessCheckInValidationError):
        repo.save_business_checkins(1, items)


@pytest.mark.parametrize("owner", [None, True, False, 0, -1, 2**63, 1.0, "1", []])
def test_invalid_owner_is_rejected_before_storage(owner, monkeypatch):
    items = [draft()]
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "_business_checkin_scope", lambda: pytest.fail("accessed storage"))
    with pytest.raises(BusinessCheckInValidationError):
        repo.save_business_checkins(owner, items)


def test_all_unknown_or_blank_only_last_draft_never_opens_storage(monkeypatch):
    items = [draft(), draft("nightclub", stock_percent=None, note=" \n\t ")]
    repo = Repository("must-not-create.db")
    monkeypatch.setattr(repo, "_business_checkin_scope", lambda: pytest.fail("accessed storage"))
    with pytest.raises(BusinessCheckInValidationError):
        repo.save_business_checkins(1, items)


def test_missing_owner_inserts_nothing_without_foreign_key_enforcement(journal):
    before = source_tables(journal.path)
    with pytest.raises(BusinessCheckInUnavailable):
        save(journal, character_id=99999)
    assert persisted(journal) == [] and source_tables(journal.path) == before


def test_second_insert_failure_rolls_back_first_and_explicit_retry_can_succeed(journal):
    attempts = []

    def fail_second(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO manual_business_checkins"):
            attempts.append(statement)
            if len(attempts) == 2:
                raise OperationalError("private SQL", {}, Exception("private path"))

    before = source_tables(journal.path)
    event.listen(journal.engine, "before_cursor_execute", fail_second)
    try:
        with pytest.raises(BusinessCheckInUnavailable) as error:
            save(journal)
        assert "private" not in str(error.value)
    finally:
        event.remove(journal.engine, "before_cursor_execute", fail_second)
    assert len(attempts) == 2 and persisted(journal) == []
    assert source_tables(journal.path) == before
    assert len(save(journal)) == len(persisted(journal)) == 2


def test_all_rows_are_revalidated_after_the_last_insert(journal):
    with sqlite3.connect(journal.path) as db:
        db.execute("CREATE TRIGGER corrupt_earlier AFTER INSERT ON manual_business_checkins "
                   "WHEN NEW.business_id = 'nightclub' BEGIN UPDATE manual_business_checkins "
                   "SET note = char(0) WHERE business_id = 'bunker'; END")
    with pytest.raises(BusinessCheckInDataError):
        save(journal)
    assert persisted(journal) == []


@pytest.mark.parametrize("change", ["stock_percent = 50", "supply_percent = 0", "stock_value = 1",
                                   "note = 'different valid note'",
                                   "recorded_at = '2020-01-01 00:00:00+00:00'"])
def test_valid_but_changed_earlier_row_rolls_back_the_entire_batch(journal, change):
    with sqlite3.connect(journal.path) as db:
        db.execute("CREATE TRIGGER change_earlier AFTER INSERT ON manual_business_checkins "
                   "WHEN NEW.business_id = 'nightclub' BEGIN UPDATE manual_business_checkins "
                   f"SET {change} WHERE business_id = 'bunker'; END")
    with pytest.raises(BusinessCheckInDataError):
        save(journal)
    assert persisted(journal) == []


@pytest.mark.parametrize("change", ["note = char(0)", "character_id = 2", "business_id = 'agency'", "id = id + 100"])
def test_corrupted_second_insert_validation_rolls_back_every_row(journal, change):
    with sqlite3.connect(journal.path) as db:
        db.execute("CREATE TRIGGER corrupt_batch AFTER INSERT ON manual_business_checkins "
                   "WHEN NEW.business_id = 'nightclub' BEGIN UPDATE manual_business_checkins "
                   f"SET {change} WHERE id = NEW.id; END")
    before = source_tables(journal.path)
    with pytest.raises(BusinessCheckInDataError):
        save(journal)
    assert persisted(journal) == [] and source_tables(journal.path) == before
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TRIGGER corrupt_batch")
    assert len(save(journal)) == 2


def test_inserted_id_is_checked_against_the_returned_detached_record(journal, monkeypatch):
    import src.database.repository as repository_module
    original = repository_module.checkin_from_storage

    def wrong_id(raw):
        row = original(raw)
        return replace(row, id=row.id + 100)

    monkeypatch.setattr(repository_module, "checkin_from_storage", wrong_id)
    with pytest.raises(BusinessCheckInDataError):
        save(journal)
    assert persisted(journal) == []


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
@pytest.mark.parametrize("error_type", [RuntimeError, OperationalError])
def test_commit_hook_errors_are_uncertain_even_when_specific_hook_proves_rollback(journal, phase, error_type):
    def fail_commit(session):
        if error_type is OperationalError:
            raise OperationalError("private SQL", {}, Exception("private path"))
        raise RuntimeError("private path")

    event.listen(journal.repo._session_factory, phase, fail_commit)
    try:
        with pytest.raises(domain().BusinessCheckInBatchUncertain) as error:
            save(journal)
        assert "private" not in str(error.value)
        assert "history" in str(error.value).lower()
    finally:
        event.remove(journal.repo._session_factory, phase, fail_commit)
    assert len(persisted(journal)) == (0 if phase == "before_commit" else 2)


def test_close_failure_after_actual_commit_is_uncertain_with_both_rows_persisted(journal, monkeypatch):
    session_class = journal.repo._session_factory.class_
    original = session_class.close

    def fail_close(session):
        original(session)
        raise OSError("private path")

    with monkeypatch.context() as patch:
        patch.setattr(session_class, "close", fail_close)
        with pytest.raises(domain().BusinessCheckInBatchUncertain) as error:
            save(journal)
        assert "private" not in str(error.value)
    assert len(persisted(journal)) == 2


def test_success_is_returned_only_after_one_commit_and_successful_close(journal, monkeypatch):
    phases = []
    session_class = journal.repo._session_factory.class_
    original_close = session_class.close

    def committed(session):
        assert len(persisted(journal)) == 2
        phases.append("commit")

    def closed(session):
        original_close(session)
        phases.append("close")

    event.listen(journal.repo._session_factory, "after_commit", committed)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(session_class, "close", closed)
            assert len(save(journal)) == 2
            phases.append("returned")
    finally:
        event.remove(journal.repo._session_factory, "after_commit", committed)
    assert phases == ["commit", "close", "returned"]


def test_non_sql_storage_failure_before_commit_is_safe_and_rolled_back(journal, monkeypatch):
    def fail_read(raw):
        raise RuntimeError("private path")

    monkeypatch.setattr("src.database.repository.checkin_from_storage", fail_read)
    with pytest.raises(BusinessCheckInUnavailable) as error:
        save(journal)
    assert "private" not in str(error.value) and persisted(journal) == []


def test_concurrent_normal_batches_remain_whole_and_keep_owner_scope(journal):
    ready = Barrier(3)

    def append(index):
        ready.wait()
        return save(journal, [draft("bunker", stock_percent=index), draft("nightclub", stock_percent=index)],
                    character_id=journal.first if index < 2 else journal.second)

    before = source_tables(journal.path)
    with ThreadPoolExecutor(max_workers=3) as workers:
        batches = tuple(workers.map(append, range(3)))
    assert len(persisted(journal)) == 6
    assert len({row.id for batch in batches for row in batch}) == 6
    for index, rows in enumerate(batches):
        assert rows[1].id == rows[0].id + 1
        assert rows[0].recorded_at == rows[1].recorded_at
        assert all(row.stock_percent == index for row in rows)
        assert all(row.character_id == (journal.first if index < 2 else journal.second) for row in rows)
    assert source_tables(journal.path) == before
