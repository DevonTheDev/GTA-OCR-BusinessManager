"""Real-file save regressions for the YAML/JSON state stores."""

import json

import pytest
import yaml

from src.config.settings import Settings
from src.game.weekly_bonuses import WeeklyBonusTracker
from src.tracking.cooldowns import CooldownTracker
from src.tracking.goals import GoalTracker
from src.tracking.history import SessionHistory
from src.tracking.nightclub import NightclubTracker
from src.tracking.passive_income import PassiveIncomeTracker


_STORES = [Settings, CooldownTracker, GoalTracker, SessionHistory,
           NightclubTracker, PassiveIncomeTracker, WeeklyBonusTracker]


@pytest.fixture(params=_STORES, ids=lambda cls: cls.__name__)
def store(request, tmp_path):
    cls = request.param
    path = tmp_path / "state" / ("settings.yaml" if cls is Settings else "state.json")
    instance = cls(path)
    instance._save()
    return instance, path


def save_with_expected_error_handling(instance):
    if isinstance(instance, Settings):
        with pytest.raises(OSError, match="interrupted serialization"):
            instance._save()
    else:
        # Trackers deliberately log save errors instead of raising to the UI.
        instance._save()


def test_interrupted_serialization_preserves_previous_state(store, monkeypatch):
    instance, path = store
    previous = path.read_bytes()
    serializer = yaml if isinstance(instance, Settings) else json

    def partial_write_then_fail(data, stream, **kwargs):
        stream.write('partially serialized state')
        raise OSError("interrupted serialization")

    with monkeypatch.context() as patch:
        patch.setattr(serializer, "dump", partial_write_then_fail)
        save_with_expected_error_handling(instance)

    assert path.read_bytes() == previous
    assert list(path.parent.iterdir()) == [path]
    # The original state remains loadable, and a subsequent save can recover.
    restored = type(instance)(path)
    restored._save()
    payload = yaml.safe_load(path.read_text()) if isinstance(instance, Settings) else json.loads(path.read_text())
    assert isinstance(payload, dict)


def test_replace_failure_preserves_state_and_removes_temporary_file(store, monkeypatch, caplog):
    from src.utils import persistence
    instance, path = store
    previous = path.read_bytes()
    seen = []

    def refuse_replace(source, destination):
        assert source.parent == path.parent
        assert destination == path
        assert source != path
        assert source.read_bytes() == previous
        # The writer handle has closed: moving the staged file succeeds on Windows too.
        moved = source.with_suffix(".moved")
        source.rename(moved)
        moved.rename(source)
        seen.append(source)
        raise PermissionError("replacement denied")

    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", refuse_replace)
        if isinstance(instance, Settings):
            with pytest.raises(PermissionError, match="replacement denied"):
                instance._save()
        else:
            instance._save()
            assert "replacement denied" in caplog.text

    assert len(seen) == 1
    assert path.read_bytes() == previous
    assert list(path.parent.iterdir()) == [path]
    instance._save()
    assert path.read_bytes() == previous


def test_destination_changes_only_after_complete_serialization(store, monkeypatch):
    instance, path = store
    previous = path.read_bytes()
    serializer = yaml if isinstance(instance, Settings) else json
    original_dump = serializer.dump

    def write_changed_payload(data, stream, **kwargs):
        payload = dict(data, persistence_marker="Café ☃")
        original_dump(payload, stream, **kwargs)
        stream.flush()
        assert path.read_bytes() == previous

    monkeypatch.setattr(serializer, "dump", write_changed_payload)
    instance._save()

    content = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(content) if isinstance(instance, Settings) else json.loads(content)
    assert payload["persistence_marker"] == "Café ☃"
    assert list(path.parent.iterdir()) == [path]


def test_first_save_failure_does_not_create_partial_destination(store, monkeypatch):
    instance, path = store
    path.unlink()  # Remove only this test's disposable fixture.
    serializer = yaml if isinstance(instance, Settings) else json

    def fail(data, stream, **kwargs):
        stream.write("partial")
        raise OSError("interrupted serialization")

    monkeypatch.setattr(serializer, "dump", fail)
    save_with_expected_error_handling(instance)

    assert not path.exists()
    assert list(path.parent.iterdir()) == []


def test_temporary_file_is_closed_before_replacement(tmp_path, monkeypatch):
    from src.utils import persistence
    destination = tmp_path / "new" / "state.json"
    handles = []
    original_factory = persistence.NamedTemporaryFile
    original_replace = persistence.os.replace

    def record_handle(**kwargs):
        handle = original_factory(**kwargs)
        handles.append(handle)
        return handle

    def replace_after_close(source, target):
        assert handles[0].closed
        return original_replace(source, target)

    monkeypatch.setattr(persistence, "NamedTemporaryFile", record_handle)
    monkeypatch.setattr(persistence.os, "replace", replace_after_close)
    with persistence.atomic_text_writer(destination) as stream:
        stream.write("complete")

    assert destination.read_text() == "complete"
    assert list(destination.parent.iterdir()) == [destination]


def test_close_failure_preserves_old_file(tmp_path, monkeypatch):
    from src.utils import persistence
    path = tmp_path / "state.json"
    path.write_text("previous")
    original_factory = persistence.NamedTemporaryFile
    handles = []

    class FailingClose:
        def __init__(self, **kwargs):
            self.handle = original_factory(**kwargs)
            handles.append(self.handle)

        def __enter__(self):
            return self.handle.__enter__()

        def __exit__(self, *args):
            self.handle.__exit__(*args)
            raise OSError("flush failed on close")

    monkeypatch.setattr(persistence, "NamedTemporaryFile", FailingClose)
    with pytest.raises(OSError, match="flush failed on close"):
        with persistence.atomic_text_writer(path) as stream:
            stream.write("replacement")

    assert handles[0].closed
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_creation_failure_leaves_old_file(tmp_path, monkeypatch):
    from src.utils import persistence
    path = tmp_path / "state.json"
    path.write_text("previous")

    def refuse_create(**kwargs):
        raise PermissionError("temporary file denied")

    monkeypatch.setattr(persistence, "NamedTemporaryFile", refuse_create)
    with pytest.raises(PermissionError, match="temporary file denied"):
        with persistence.atomic_text_writer(path):
            pytest.fail("must not yield without a temporary file")

    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_symlink_still_targets_the_original_state_file(tmp_path):
    from src.utils.persistence import atomic_text_writer
    target = tmp_path / "state.json"
    target.write_text("previous")
    link = tmp_path / "settings-link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with atomic_text_writer(link) as stream:
        stream.write("updated")

    assert link.is_symlink()
    assert target.read_text() == link.read_text() == "updated"
    assert set(tmp_path.iterdir()) == {link, target}


def test_failed_overlapping_writer_cannot_remove_completed_save(tmp_path):
    from src.utils.persistence import atomic_text_writer
    path = tmp_path / "state.json"
    path.write_text("previous")

    with pytest.raises(OSError, match="first writer failed"):
        with atomic_text_writer(path) as first:
            first.write("incomplete first")
            with atomic_text_writer(path) as second:
                second.write("completed second")
            assert path.read_text() == "completed second"
            raise OSError("first writer failed")

    assert path.read_text() == "completed second"
    assert list(tmp_path.iterdir()) == [path]


def test_cleanup_failure_keeps_original_error_visible(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    from src.utils.persistence import atomic_text_writer
    path = tmp_path / "state.json"
    path.write_text("previous")
    original_unlink = Path.unlink

    def refuse_temporary_cleanup(candidate, *args, **kwargs):
        if candidate.suffix == ".tmp":
            raise PermissionError("temporary cleanup denied")
        return original_unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refuse_temporary_cleanup)
        with pytest.raises(OSError, match="original write failure"):
            with atomic_text_writer(path) as stream:
                stream.write("partial")
                raise OSError("original write failure")

    assert path.read_text() == "previous"
    assert "Unable to remove temporary state file" in caplog.text
    temporary = list(tmp_path.glob("*.tmp"))
    assert len(temporary) == 1
    temporary[0].unlink()
