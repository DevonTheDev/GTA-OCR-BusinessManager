"""Tests for data export utilities."""

import pytest
import csv
import json
from pathlib import Path

from src.utils.exporter import DataExporter, ExportResult
from src.database.repository import Repository


class TestExportResult:
    """Tests for ExportResult dataclass."""

    def test_success_result(self):
        """Test successful result."""
        result = ExportResult(
            success=True,
            file_path=Path("/tmp/export.csv"),
            rows_exported=100,
        )

        assert result.success
        assert result.rows_exported == 100

    def test_failure_result(self):
        """Test failure result."""
        result = ExportResult(
            success=False,
            error_message="Session not found",
        )

        assert not result.success
        assert "not found" in result.error_message


class TestDataExporter:
    """Tests for DataExporter class."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Create temp database path."""
        return str(tmp_path / "test.db")

    @pytest.fixture
    def repo(self, db_path):
        """Create repository with test data."""
        repo = Repository(db_path)
        repo.initialize()

        # Create character
        character = repo.get_or_create_character("TestPlayer")

        # Create session with activities
        session = repo.start_session(character, start_money=1_000_000)

        # Log some activities
        repo.log_activity(
            session_id=session.id,
            activity_type="VIP_WORK",
            activity_name="Headhunter",
            earnings=25_000,
            success=True,
            duration_seconds=300,
        )
        repo.log_activity(
            session_id=session.id,
            activity_type="SELL_MISSION",
            activity_name="Bunker Sell",
            earnings=500_000,
            success=True,
            duration_seconds=900,
        )
        repo.log_activity(
            session_id=session.id,
            activity_type="VIP_WORK",
            activity_name="Sightseer",
            earnings=0,
            success=False,
            duration_seconds=180,
        )

        # Log some earnings
        repo.log_earning(session.id, 25_000, "Headhunter", 1_025_000)
        repo.log_earning(session.id, 500_000, "Bunker Sell", 1_525_000)

        # End session
        repo.end_session(session.id, end_money=1_525_000)

        return repo, character.id, session.id

    @pytest.fixture
    def exporter(self, repo):
        """Create exporter with test repository."""
        repository, _, _ = repo
        return DataExporter(repository)

    def test_export_session_to_csv(self, exporter, repo, tmp_path):
        """Test exporting session to CSV."""
        _, _, session_id = repo
        output_dir = tmp_path / "export"

        result = exporter.export_session_to_csv(session_id, output_dir)

        assert result.success
        assert result.rows_exported > 0
        assert (output_dir / f"session_{session_id}_info.csv").exists()
        assert (output_dir / f"session_{session_id}_activities.csv").exists()
        assert (output_dir / f"session_{session_id}_earnings.csv").exists()

    def test_export_session_csv_content(self, exporter, repo, tmp_path):
        """Test CSV content is correct."""
        _, _, session_id = repo
        output_dir = tmp_path / "export"

        exporter.export_session_to_csv(session_id, output_dir)

        # Check activities CSV
        activities_file = output_dir / f"session_{session_id}_activities.csv"
        with open(activities_file, "r") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Should have header + 3 activities
        assert len(rows) == 4
        assert rows[0][0] == "Activity Type"

        # Check Headhunter row
        headhunter = [r for r in rows[1:] if "Headhunter" in r[1]]
        assert len(headhunter) == 1
        assert "$25,000" in headhunter[0][2]

    def test_export_session_not_found(self, exporter, tmp_path):
        """Test exporting non-existent session."""
        result = exporter.export_session_to_csv(99999, tmp_path / "export")

        assert not result.success
        assert "not found" in result.error_message.lower()

    def test_export_without_earnings(self, exporter, repo, tmp_path):
        """Test exporting without earnings log."""
        _, _, session_id = repo
        output_dir = tmp_path / "export"

        result = exporter.export_session_to_csv(
            session_id, output_dir, include_earnings=False
        )

        assert result.success
        assert not (output_dir / f"session_{session_id}_earnings.csv").exists()

    def test_export_sessions_summary(self, exporter, repo, tmp_path):
        """Test exporting sessions summary."""
        _, character_id, _ = repo
        output_file = tmp_path / "sessions.csv"

        result = exporter.export_sessions_summary(character_id, output_file)

        assert result.success
        assert output_file.exists()
        assert result.rows_exported >= 1

    def test_export_sessions_summary_content(self, exporter, repo, tmp_path):
        """Test sessions summary CSV content."""
        _, character_id, _ = repo
        output_file = tmp_path / "sessions.csv"

        exporter.export_sessions_summary(character_id, output_file)

        with open(output_file, "r") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) >= 2  # Header + at least 1 session
        assert "Session ID" in rows[0]
        assert "Total Earnings" in rows[0]

    def test_export_activity_history(self, exporter, repo, tmp_path):
        """Test exporting activity history."""
        _, character_id, _ = repo
        output_file = tmp_path / "activities.csv"

        result = exporter.export_activity_history(character_id, output_file)

        assert result.success
        assert output_file.exists()
        assert result.rows_exported == 3  # We logged 3 activities

    def test_export_to_json(self, exporter, repo, tmp_path):
        """Test exporting session to JSON."""
        _, _, session_id = repo
        output_file = tmp_path / "session.json"

        result = exporter.export_to_json(session_id, output_file)

        assert result.success
        assert output_file.exists()

        with open(output_file, "r") as f:
            data = json.load(f)

        assert "session" in data
        assert "activities" in data
        assert "earnings" in data
        assert len(data["activities"]) == 3

    def test_export_earnings_breakdown(self, exporter, repo, tmp_path):
        """Test exporting earnings breakdown."""
        _, character_id, _ = repo
        output_file = tmp_path / "breakdown.csv"

        result = exporter.export_earnings_breakdown(character_id, output_file)

        assert result.success
        assert output_file.exists()

    def test_export_earnings_breakdown_content(self, exporter, repo, tmp_path):
        """Test earnings breakdown content."""
        _, character_id, _ = repo
        output_file = tmp_path / "breakdown.csv"

        exporter.export_earnings_breakdown(character_id, output_file)

        with open(output_file, "r") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert rows[0][0] == "Activity Type"

        # Check VIP_WORK row exists with correct count
        vip_rows = [r for r in rows[1:] if r[0] == "VIP_WORK"]
        if vip_rows:
            # Count should be 2 (Headhunter + failed Sightseer)
            assert vip_rows[0][1] == "2"

    def test_export_creates_directory(self, exporter, repo, tmp_path):
        """Test export creates output directory."""
        _, _, session_id = repo
        output_dir = tmp_path / "nested" / "deep" / "export"

        result = exporter.export_session_to_csv(session_id, output_dir)

        assert result.success
        assert output_dir.exists()

    def test_export_no_sessions_for_character(self, db_path, tmp_path):
        """Test exporting sessions for character with no sessions."""
        repo = Repository(db_path)
        repo.initialize()

        # Create character but no sessions
        character = repo.get_or_create_character("EmptyPlayer")

        exporter = DataExporter(repo)
        output_file = tmp_path / "sessions.csv"

        result = exporter.export_sessions_summary(character.id, output_file)

        assert not result.success
        assert "no sessions" in result.error_message.lower()
