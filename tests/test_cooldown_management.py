"""Manual reminders, recoverable storage errors, and serialized tracker access."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event, current_thread
from uuid import UUID

import pytest

from src.tracking import cooldowns
from src.utils import persistence


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = NOW
        reads = 0

        @classmethod
        def now(cls, tz=None):
            cls.reads += 1
            return cls.value.astimezone(tz) if tz else cls.value.replace(tzinfo=None)

    monkeypatch.setattr(cooldowns, "datetime", Clock)
    return Clock


def payload(name="fixture", duration=300, started=NOW):
    return {
        "activity_name": name,
        "display_name": name.title(),
        "started_at": started.isoformat(),
        "duration_seconds": duration,
    }


@pytest.mark.parametrize("name, seconds", [("  Café <b>☃</b>  ", 1), ("🎮" * 200, 604800)],
                         ids=["literal-name", "200-code-points"])
def test_manual_validation_keeps_literal_unicode_name(name, seconds):
    assert cooldowns.validate_reminder_values(name, seconds) == (name.strip(), seconds)


@pytest.mark.parametrize("name", [None, 12, "", "   ", "x" * 201,
                                   "a\nb", "a\rb", "a\tb", "a\0b", "a\x7fb",
                                   "a\u2028b", "a\u2029b", "a\ud800b", "a\u202eb"])
def test_invalid_name_is_rejected_before_any_change(clock, tmp_path, name):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    original = tracker.start_cooldown("fixture", "Original", 300)
    stored = path.read_bytes()

    with pytest.raises(ValueError):
        tracker.set_timer("fixture", name, 120)
    with pytest.raises(ValueError):
        tracker.start_custom_timer(name, 120)

    assert tracker.get_cooldown("fixture") == original
    assert path.read_bytes() == stored


@pytest.mark.parametrize("seconds", [True, False, None, "30", 1.0, float("nan"),
                                      float("inf"), 0, -1, 604801])
def test_invalid_duration_creates_no_file(clock, tmp_path, seconds):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)

    with pytest.raises(ValueError):
        tracker.start_custom_timer("Fixture", seconds)

    assert tracker.get_active_cooldowns() == []
    assert not path.exists()


@pytest.mark.parametrize("key", [None, 42, "", "  ", "a" * 257, "a\nb", "a\ud800b"])
def test_invalid_key_cannot_replace_a_timer(clock, tmp_path, key):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    original = tracker.start_cooldown("fixture", "Original", 300)
    stored = path.read_bytes()

    with pytest.raises(ValueError):
        tracker.set_timer(key, "Replacement", 120)

    assert tracker.get_active_cooldowns() == [original]
    assert path.read_bytes() == stored


def test_custom_duplicate_names_have_opaque_distinct_persisted_keys(clock, tmp_path):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    first = tracker.start_custom_timer("  Headhunter  ", 60)
    second = tracker.start_custom_timer("Headhunter", 90)

    assert first.display_name == second.display_name == "Headhunter"
    assert first.activity_name != second.activity_name
    for timer in (first, second):
        prefix, identifier = timer.activity_name.split(":")
        assert prefix == "manual"
        assert str(UUID(identifier)) == identifier
    assert tracker.get_cooldown("headhunter") is None
    assert cooldowns.CooldownTracker(path).get_active_cooldowns() == [first, second]


def test_set_preserves_normalized_key_and_restarts_from_acceptance(clock, tmp_path):
    tracker = cooldowns.CooldownTracker(tmp_path / "cooldowns.json")
    original = tracker.start_cooldown("Headhunter")
    clock.value += timedelta(seconds=20)
    replacement = tracker.set_timer("HEADHUNTER", "Headhunter", 90)

    assert replacement.activity_name == original.activity_name == "headhunter"
    assert replacement.started_at == clock.value
    assert replacement.remaining_seconds == 90
    assert tracker.get_active_cooldowns() == [replacement]
    assert cooldowns.ACTIVITY_COOLDOWNS["headhunter"] == 300
    tracker.clear_cooldown("headhunter")
    recreated = tracker.set_timer(replacement.activity_name, "Headhunter", 60)
    assert tracker.get_cooldown("headhunter") == recreated
    assert tracker.set_timer("  Mixed_Key  ", "Space key", 60).activity_name == "  mixed_key  "


@pytest.mark.parametrize("read", ["start", "get", "list", "custom", "set"])
def test_exposed_records_cannot_mutate_tracker_or_saved_state(clock, tmp_path, read):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    record = tracker.start_cooldown("fixture", "Fixture", 300)
    if read == "get":
        record = tracker.get_cooldown("fixture")
    elif read == "list":
        record = tracker.get_active_cooldowns()[0]
    elif read == "custom":
        record = tracker.start_custom_timer("Fixture", 300)
    elif read == "set":
        record = tracker.set_timer("fixture", "Fixture", 300)
    key = record.activity_name
    before = tracker.get_cooldown(key).to_dict()
    saved = path.read_bytes()

    record.display_name = "Altered"
    record.activity_name = "other"
    record.duration_seconds = 0
    record.started_at = NOW - timedelta(days=1)

    assert tracker.get_cooldown(key).to_dict() == before
    assert path.read_bytes() == saved


def test_list_pruning_and_ties_use_one_clock_snapshot(clock, tmp_path):
    tracker = cooldowns.CooldownTracker(tmp_path / "cooldowns.json")
    tracker.start_cooldown("zulu", "Zulu", 30)
    tracker.start_cooldown("alpha", "Alpha", 30)
    tracker.start_cooldown("expired", "Expired", 1)
    clock.value += timedelta(seconds=1)
    clock.reads = 0

    assert [item.activity_name for item in tracker.get_active_cooldowns()] == ["alpha", "zulu"]
    assert clock.reads == 1
    assert set(json.loads(tracker._data_path.read_text())["cooldowns"]) == {"alpha", "zulu"}


@pytest.mark.parametrize("failure", ["serialization", "replacement"])
def test_save_failure_keeps_latest_memory_until_explicit_retry(clock, tmp_path, monkeypatch, failure):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    tracker.start_cooldown("original", "Original", 300)
    stored = path.read_bytes()
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(True)
        if failure == "serialization":
            args[1].write("partial")
        raise OSError("secret-private-path")

    with monkeypatch.context() as patch:
        patch.setattr(cooldowns.json if failure == "serialization" else persistence.os,
                      "dump" if failure == "serialization" else "replace", fail)
        added = tracker.start_custom_timer("Added", 120)
        tracker.clear_cooldown("original")
        assert tracker.storage_error == "save_failed"
        assert tracker.needs_save_retry is True
        assert path.read_bytes() == stored
        assert list(path.parent.iterdir()) == [path]
        for _ in range(3):
            assert tracker.get_active_cooldowns() == [added]
            assert tracker.get_cooldown("original") is None
            assert tracker.cleanup_expired() == 0
            tracker.get_ready_activities()
        assert len(attempts) == 2
        assert tracker.retry_save() is False
        assert len(attempts) == 3

    clock.value += timedelta(seconds=10)
    assert tracker.retry_save() is True
    assert tracker.storage_error is None
    assert tracker.needs_save_retry is False
    restored = cooldowns.CooldownTracker(path).get_active_cooldowns()
    assert restored == [added]
    assert restored[0].remaining_seconds == 110
    assert tracker.retry_save() is False


def test_failed_expiry_prune_is_not_retried_by_following_reads(clock, tmp_path, monkeypatch):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    tracker.start_cooldown("fixture", "Fixture", 1)
    stored = path.read_bytes()
    clock.value += timedelta(seconds=1)
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(True)
        raise PermissionError("denied")

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", fail)
        assert tracker.get_active_cooldowns() == []
        assert tracker.get_cooldown("fixture") is None
        assert tracker.cleanup_expired() == 0
        assert tracker.get_active_cooldowns() == []
        assert len(attempts) == 1
        assert tracker.needs_save_retry
        assert path.read_bytes() == stored

    assert tracker.retry_save()
    assert json.loads(path.read_text()) == {"cooldowns": {}}


@pytest.mark.parametrize("bad", [None, [], "bad", {},
                                  dict(payload("broken"), duration_seconds=float("nan")),
                                  dict(payload("broken"), duration_seconds=float("inf")),
                                  dict(payload("broken"), duration_seconds=True),
                                  dict(payload("broken"), duration_seconds="300"),
                                  dict(payload("broken"), started_at=42),
                                  dict(payload("broken"), display_name=123)])
def test_invalid_record_discards_entire_staged_load_without_overwriting(clock, tmp_path, bad):
    path = tmp_path / "cooldowns.json"
    path.write_text(json.dumps({"cooldowns": {"fixture": payload(), "broken": bad}}))
    stored = path.read_bytes()
    tracker = cooldowns.CooldownTracker(path)

    assert tracker.storage_error == "load_failed"
    assert tracker.get_active_cooldowns() == []
    assert tracker.get_cooldown("fixture") is None
    assert tracker.cleanup_expired() == 0
    assert tracker.needs_save_retry is False
    assert tracker.retry_save() is False
    assert path.read_bytes() == stored
    replacement = tracker.start_custom_timer("Replacement", 60)
    assert tracker.storage_error is None
    assert cooldowns.CooldownTracker(path).get_active_cooldowns() == [replacement]


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null", b'{"cooldowns": []}', b"\xff"])
def test_unreadable_schema_preserves_source_on_observation(clock, tmp_path, raw):
    path = tmp_path / "cooldowns.json"
    path.write_bytes(raw)
    tracker = cooldowns.CooldownTracker(path)
    assert tracker.storage_error == "load_failed"
    assert tracker.get_active_cooldowns() == []
    assert tracker.retry_save() is False
    assert path.read_bytes() == raw


def test_unreadable_file_is_distinct_from_missing_file(clock, tmp_path):
    path = tmp_path / "directory.json"
    path.mkdir()
    unreadable = cooldowns.CooldownTracker(path)
    assert unreadable.storage_error == "load_failed"
    assert not unreadable.needs_save_retry
    missing = cooldowns.CooldownTracker(tmp_path / "missing.json")
    assert missing.storage_error is None
    assert not missing.needs_save_retry
    assert missing.retry_save() is False


@pytest.mark.parametrize("key, record", [
    ("mismatch", payload("other")),
    ("UPPERCASE", payload("UPPERCASE")),
    ("broken", dict(payload("broken", duration=-1), started_at="not-an-iso-timestamp")),
])
def test_bad_record_after_expired_entry_invalidates_whole_load(clock, tmp_path, key, record):
    path = tmp_path / "cooldowns.json"
    path.write_text(json.dumps({"cooldowns": {
        "expired": payload("expired", duration=-1),
        "valid": payload("valid"),
        key: record,
    }}))
    stored = path.read_bytes()
    tracker = cooldowns.CooldownTracker(path)

    assert tracker.get_active_cooldowns() == []
    assert tracker.storage_error == "load_failed"
    tracker.clear_cooldown(key)
    assert not tracker.retry_save()
    assert path.read_bytes() == stored


def test_new_successful_mutation_clears_pending_save_error(clock, tmp_path, monkeypatch):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)

    def fail(*args, **kwargs):
        raise OSError("replacement denied")

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", fail)
        unsaved = tracker.start_custom_timer("Unsaved", 30)
        assert tracker.storage_error == "save_failed"
        assert tracker.needs_save_retry
        assert not path.exists()

    added = tracker.start_custom_timer("Saved", 60)
    assert tracker.storage_error is None
    assert not tracker.needs_save_retry
    assert cooldowns.CooldownTracker(path).get_active_cooldowns() == [unsaved, added]


def test_display_reads_do_not_create_persistence_work(clock, tmp_path, monkeypatch):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    original = tracker.start_cooldown("fixture", "Fixture", 300)
    stored = path.read_bytes()

    def unexpected_write(*args, **kwargs):
        pytest.fail("A non-expiring display read must not write state")

    monkeypatch.setattr(cooldowns, "atomic_text_writer", unexpected_write)
    assert tracker.get_active_cooldowns() == [original]
    assert tracker.get_cooldown("fixture") == original
    assert tracker.get_remaining("fixture") == 300
    assert tracker.is_on_cooldown("fixture")
    assert tracker.get_ready_activities() == list(cooldowns.ACTIVITY_COOLDOWNS)
    assert tracker.cleanup_expired() == 0
    tracker.clear_cooldown("unknown")
    assert not tracker.retry_save()
    assert path.read_bytes() == stored


def test_legacy_naive_offset_and_fractional_duration_stay_compatible(clock, tmp_path):
    path = tmp_path / "cooldowns.json"
    data = {"cooldowns": {
        "naive": payload("naive", 30.5, NOW.replace(tzinfo=None)),
        "offset": payload("offset", 40, NOW.astimezone(timezone(timedelta(hours=2)))),
        "expired": payload("expired", 0),
    }}
    path.write_text(json.dumps(data))
    stored = path.read_bytes()
    tracker = cooldowns.CooldownTracker(path)
    assert tracker.storage_error is None
    assert tracker.get_remaining("naive") == 30.5
    assert tracker.get_remaining("offset") == 40
    assert tracker.get_cooldown("naive").started_at.tzinfo == timezone.utc
    assert tracker.get_cooldown("expired") is None
    assert path.read_bytes() == stored


def test_memory_only_timer_never_has_a_save_retry(clock):
    tracker = cooldowns.CooldownTracker()
    tracker.start_custom_timer("Local", 60)
    assert tracker.storage_error is None
    assert tracker.needs_save_retry is False
    assert tracker.retry_save() is False


def test_save_serializes_concurrent_replacement_and_removal(clock, tmp_path, monkeypatch):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    tracker.start_cooldown("fixture", "Original", 300)
    serializing = Event()
    release = Event()
    remover_started = Event()
    original_dump = json.dump

    def paused_dump(data, stream, **kwargs):
        if current_thread().name.startswith("replace"):
            serializing.set()
            assert release.wait(5), "test did not release serialization"
        return original_dump(data, stream, **kwargs)

    def remove():
        remover_started.set()
        tracker.clear_cooldown("fixture")

    monkeypatch.setattr(cooldowns.json, "dump", paused_dump)
    with ThreadPoolExecutor(1, thread_name_prefix="replace") as replacing:
        with ThreadPoolExecutor(1, thread_name_prefix="remove") as removing:
            first = replacing.submit(tracker.set_timer, "fixture", "Replacement", 90)
            try:
                assert serializing.wait(5)
                second = removing.submit(remove)
                assert remover_started.wait(5)
                acquired = tracker._lock.acquire(blocking=False)
                if acquired:
                    tracker._lock.release()
                assert not acquired, "save must retain the mutation lock through replacement"
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)

    assert tracker.get_active_cooldowns() == []
    assert json.loads(path.read_text()) == {"cooldowns": {}}
    assert cooldowns.CooldownTracker(path).get_active_cooldowns() == []


def test_expiry_read_cannot_delete_a_concurrent_replacement(clock, tmp_path, monkeypatch):
    path = tmp_path / "cooldowns.json"
    tracker = cooldowns.CooldownTracker(path)
    tracker.start_cooldown("fixture", "Original", 1)
    clock.value += timedelta(seconds=1)
    reading = Event()
    release = Event()
    replacement_started = Event()
    original_now = clock.now

    def paused_now(tz=None):
        if current_thread().name.startswith("read"):
            reading.set()
            assert release.wait(5), "test did not release the expired read"
        return original_now(tz)

    def replace():
        replacement_started.set()
        return tracker.set_timer("fixture", "Replacement", 90)

    monkeypatch.setattr(clock, "now", paused_now)
    with ThreadPoolExecutor(1, thread_name_prefix="read") as readers:
        with ThreadPoolExecutor(1, thread_name_prefix="replace") as writers:
            first = readers.submit(tracker.get_cooldown, "fixture")
            try:
                assert reading.wait(5)
                second = writers.submit(replace)
                assert replacement_started.wait(5)
                acquired = tracker._lock.acquire(blocking=False)
                if acquired:
                    tracker._lock.release()
                assert not acquired, "expiry read must retain its lock through cleanup"
            finally:
                release.set()
            assert first.result(timeout=5) is None
            replacement = second.result(timeout=5)

    assert tracker.get_cooldown("fixture") == replacement
    assert cooldowns.CooldownTracker(path).get_cooldown("fixture") == replacement
