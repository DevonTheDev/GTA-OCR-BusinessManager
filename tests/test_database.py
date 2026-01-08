"""Unit tests for database repository."""

import pytest
import tempfile
import os
from pathlib import Path

from src.database.repository import Repository, DatabaseError
from src.database.models import Character, Session, Activity, Earnings


class TestRepository:
    """Tests for Repository class."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        # Cleanup
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.fixture
    def repository(self, temp_db):
        """Create a repository with temporary database."""
        repo = Repository(temp_db)
        repo.initialize()
        return repo

    # Initialization tests
    def test_initialize_creates_tables(self, temp_db):
        repo = Repository(temp_db)
        assert repo.initialize()
        assert Path(temp_db).exists()

    def test_double_initialize_is_safe(self, repository):
        # Second initialization should be safe
        assert repository.initialize()

    # Character tests
    def test_create_character(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        assert character is not None
        assert character.name == "TestPlayer"
        assert character.id is not None

    def test_get_existing_character(self, repository):
        # Create a character
        char1 = repository.get_or_create_character("TestPlayer")

        # Get the same character
        char2 = repository.get_or_create_character("TestPlayer")

        assert char1.id == char2.id

    def test_get_all_characters(self, repository):
        repository.get_or_create_character("Player1")
        repository.get_or_create_character("Player2")

        characters = repository.get_all_characters()
        assert len(characters) == 2
        names = [c.name for c in characters]
        assert "Player1" in names
        assert "Player2" in names

    def test_set_active_character(self, repository):
        char1 = repository.get_or_create_character("Player1")
        char2 = repository.get_or_create_character("Player2")

        # Set char2 as active
        assert repository.set_active_character(char2.id)

        # Verify
        active = repository.get_active_character()
        assert active is not None
        assert active.id == char2.id

    # Session tests
    def test_start_session(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        assert session is not None
        assert session.character_id == character.id
        assert session.start_money == 1000000
        assert session.id is not None

    def test_end_session(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        # End the session
        assert repository.end_session(session.id, end_money=1500000)

    def test_get_recent_sessions(self, repository):
        character = repository.get_or_create_character("TestPlayer")

        # Create multiple sessions
        for i in range(5):
            session = repository.start_session(character, start_money=i * 100000)
            repository.end_session(session.id, end_money=(i + 1) * 100000)

        sessions = repository.get_recent_sessions(character.id, limit=3)
        assert len(sessions) == 3

    # Activity tests
    def test_log_activity(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        activity = repository.log_activity(
            session_id=session.id,
            activity_type="CONTACT_MISSION",
            activity_name="Rooftop Rumble",
            earnings=25000,
            success=True,
            duration_seconds=300,
        )

        assert activity is not None
        assert activity.activity_type == "CONTACT_MISSION"
        assert activity.earnings == 25000
        assert activity.success is True

    def test_get_session_activities(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        # Log multiple activities
        for i in range(3):
            repository.log_activity(
                session_id=session.id,
                activity_type="CONTACT_MISSION",
                activity_name=f"Mission {i}",
                earnings=10000 * (i + 1),
                success=True,
                duration_seconds=300,
            )

        activities = repository.get_session_activities(session.id)
        assert len(activities) == 3

    # Earnings tests
    def test_log_earning(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        earning = repository.log_earning(
            session_id=session.id,
            amount=50000,
            source="Contact Mission",
            balance_after=1050000,
        )

        assert earning is not None
        assert earning.amount == 50000
        assert earning.source == "Contact Mission"

    # Statistics tests
    def test_get_total_earnings(self, repository):
        character = repository.get_or_create_character("TestPlayer")

        # Create session with earnings
        session = repository.start_session(character, start_money=1000000)
        repository.end_session(session.id, end_money=1500000)

        total = repository.get_total_earnings(character.id, days=30)
        assert total == 500000

    def test_get_activity_stats(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        # Log activities
        for i in range(5):
            repository.log_activity(
                session_id=session.id,
                activity_type="CONTACT_MISSION",
                activity_name=f"Mission {i}",
                earnings=20000,
                success=True,
                duration_seconds=300,
            )

        stats = repository.get_activity_stats(character.id, "CONTACT_MISSION", days=30)
        assert stats["count"] == 5
        assert stats["total_earnings"] == 100000
        assert stats["avg_earnings"] == 20000
        assert stats["avg_duration"] == 300

    # Export tests
    def test_export_session_data(self, repository):
        character = repository.get_or_create_character("TestPlayer")
        session = repository.start_session(character, start_money=1000000)

        # Add activity
        repository.log_activity(
            session_id=session.id,
            activity_type="CONTACT_MISSION",
            activity_name="Test Mission",
            earnings=25000,
            success=True,
            duration_seconds=300,
        )

        # Add earning
        repository.log_earning(
            session_id=session.id,
            amount=25000,
            source="Test Mission",
            balance_after=1025000,
        )

        data = repository.export_session_data(session.id)

        assert data is not None
        assert "session" in data
        assert "activities" in data
        assert "earnings" in data
        assert len(data["activities"]) == 1
        assert len(data["earnings"]) == 1

    # Error handling tests
    def test_end_nonexistent_session(self, repository):
        result = repository.end_session(99999, end_money=1000000)
        assert result is False

    def test_get_stats_empty_character(self, repository):
        character = repository.get_or_create_character("EmptyPlayer")
        stats = repository.get_activity_stats(character.id, "CONTACT_MISSION", days=30)

        assert stats["count"] == 0
        assert stats["total_earnings"] == 0

    def test_close_repository(self, repository):
        # Should not raise
        repository.close()
        repository.close()  # Double close should be safe


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
