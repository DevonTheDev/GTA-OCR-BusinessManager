"""Stored history can be opened without starting a capture session."""

from src import app as module
from src.app import AppState, GTABusinessManager
from src.config.settings import Settings
from src.database.repository import Repository


def test_history_repository_is_available_without_capture_or_new_records(tmp_path, monkeypatch):
    repository = Repository(str(tmp_path / 'history.db'))
    assert repository.initialize()
    calls = []
    def existing_repository():
        calls.append(True)
        return repository
    monkeypatch.setattr(module, 'get_repository', existing_repository)
    manager = GTABusinessManager(Settings(tmp_path / 'settings.yaml'))
    assert manager.history_repository is repository
    assert manager.history_repository is repository
    assert calls == [True]
    assert manager.state == AppState.STOPPED
    assert manager._capture_thread is None
    assert manager.session_stats is None
    assert manager.data.db_session_id is None
    assert repository.get_all_characters() == []


def test_history_access_reuses_existing_capture_repository(tmp_path, monkeypatch):
    repository = Repository(str(tmp_path / 'history.db'))
    assert repository.initialize()
    manager = GTABusinessManager(Settings(tmp_path / 'settings.yaml'))
    manager._repository = repository
    monkeypatch.setattr(module, 'get_repository', lambda: (_ for _ in ()).throw(AssertionError('must reuse')))
    assert manager.history_repository is repository
