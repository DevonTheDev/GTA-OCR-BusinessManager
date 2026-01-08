"""Data export utilities for GTA Business Manager."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from .logging import get_logger
from ..database.repository import Repository, get_repository

logger = get_logger("utils.exporter")


@dataclass
class ExportResult:
    """Result of an export operation."""

    success: bool
    file_path: Optional[Path] = None
    rows_exported: int = 0
    error_message: str = ""


class DataExporter:
    """Exports session and activity data to various formats."""

    def __init__(self, repository: Optional[Repository] = None):
        """Initialize exporter.

        Args:
            repository: Repository to use (uses global if None)
        """
        self._repo = repository or get_repository()

    def export_session_to_csv(
        self,
        session_id: int,
        output_path: Path,
        include_earnings: bool = True,
    ) -> ExportResult:
        """Export a single session to CSV files.

        Creates multiple CSV files:
        - session_info.csv - Session summary
        - activities.csv - Activity log
        - earnings.csv - Earnings log (if include_earnings)

        Args:
            session_id: Session ID to export
            output_path: Directory to save files
            include_earnings: Whether to include earnings log

        Returns:
            ExportResult with success status
        """
        try:
            data = self._repo.export_session_data(session_id)
            if not data:
                return ExportResult(
                    success=False,
                    error_message=f"Session {session_id} not found"
                )

            output_path.mkdir(parents=True, exist_ok=True)
            rows_exported = 0

            # Export session info
            session_file = output_path / f"session_{session_id}_info.csv"
            with open(session_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Property", "Value"])
                session_info = data["session"]
                writer.writerow(["Session ID", session_info["id"]])
                writer.writerow(["Started At", session_info["started_at"]])
                writer.writerow(["Ended At", session_info["ended_at"] or "In Progress"])
                writer.writerow(["Start Money", f"${session_info['start_money']:,}"])
                writer.writerow(["End Money", f"${session_info['end_money'] or 0:,}"])
                writer.writerow(["Total Earnings", f"${session_info['total_earnings'] or 0:,}"])

                duration = session_info.get("duration_seconds", 0) or 0
                hours = duration // 3600
                minutes = (duration % 3600) // 60
                writer.writerow(["Duration", f"{hours}h {minutes}m"])
                rows_exported += 7

            # Export activities
            activities_file = output_path / f"session_{session_id}_activities.csv"
            with open(activities_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Activity Type", "Activity Name", "Earnings", "Duration (min)",
                    "Success", "Completed At"
                ])

                for activity in data["activities"]:
                    duration_min = (activity.get("duration_seconds") or 0) / 60
                    writer.writerow([
                        activity["type"],
                        activity["name"],
                        f"${activity['earnings']:,}",
                        f"{duration_min:.1f}",
                        "Yes" if activity["success"] else "No",
                        activity["ended_at"] or "",
                    ])
                    rows_exported += 1

            # Export earnings log
            if include_earnings:
                earnings_file = output_path / f"session_{session_id}_earnings.csv"
                with open(earnings_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Timestamp", "Amount", "Source", "Balance After"])

                    for earning in data["earnings"]:
                        writer.writerow([
                            earning["timestamp"],
                            f"${earning['amount']:,}",
                            earning["source"],
                            f"${earning['balance_after']:,}",
                        ])
                        rows_exported += 1

            logger.info(f"Exported session {session_id} to {output_path}")
            return ExportResult(
                success=True,
                file_path=output_path,
                rows_exported=rows_exported,
            )

        except Exception as e:
            logger.error(f"Failed to export session {session_id}: {e}")
            return ExportResult(
                success=False,
                error_message=str(e)
            )

    def export_sessions_summary(
        self,
        character_id: int,
        output_file: Path,
        limit: int = 100,
    ) -> ExportResult:
        """Export summary of all sessions for a character.

        Args:
            character_id: Character ID to export
            output_file: Output CSV file path
            limit: Maximum sessions to export

        Returns:
            ExportResult with success status
        """
        try:
            sessions = self._repo.get_recent_sessions(character_id, limit)
            if not sessions:
                return ExportResult(
                    success=False,
                    error_message=f"No sessions found for character {character_id}"
                )

            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Session ID", "Date", "Duration (min)", "Start Money",
                    "End Money", "Total Earnings", "$/Hour"
                ])

                for session in sessions:
                    duration_min = (session.duration_seconds or 0) / 60
                    duration_hours = duration_min / 60
                    earnings = session.total_earnings or 0
                    per_hour = earnings / duration_hours if duration_hours > 0 else 0

                    writer.writerow([
                        session.id,
                        session.started_at.strftime("%Y-%m-%d %H:%M") if session.started_at else "",
                        f"{duration_min:.0f}",
                        f"${session.start_money or 0:,}",
                        f"${session.end_money or 0:,}",
                        f"${earnings:,}",
                        f"${per_hour:,.0f}",
                    ])

            logger.info(f"Exported {len(sessions)} sessions to {output_file}")
            return ExportResult(
                success=True,
                file_path=output_file,
                rows_exported=len(sessions),
            )

        except Exception as e:
            logger.error(f"Failed to export sessions summary: {e}")
            return ExportResult(
                success=False,
                error_message=str(e)
            )

    def export_activity_history(
        self,
        character_id: int,
        output_file: Path,
        days: int = 30,
    ) -> ExportResult:
        """Export activity history for a character.

        Args:
            character_id: Character ID to export
            output_file: Output CSV file path
            days: Number of days to look back

        Returns:
            ExportResult with success status
        """
        try:
            sessions = self._repo.get_recent_sessions(character_id, limit=1000)
            if not sessions:
                return ExportResult(
                    success=False,
                    error_message=f"No sessions found for character {character_id}"
                )

            output_file.parent.mkdir(parents=True, exist_ok=True)
            rows_exported = 0

            with open(output_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Session ID", "Date", "Activity Type", "Activity Name",
                    "Earnings", "Duration (min)", "Success"
                ])

                for session in sessions:
                    activities = self._repo.get_session_activities(session.id)
                    for activity in activities:
                        duration_min = (activity.duration_seconds or 0) / 60
                        writer.writerow([
                            session.id,
                            activity.ended_at.strftime("%Y-%m-%d %H:%M") if activity.ended_at else "",
                            activity.activity_type,
                            activity.activity_name,
                            f"${activity.earnings or 0:,}",
                            f"{duration_min:.1f}",
                            "Yes" if activity.success else "No",
                        ])
                        rows_exported += 1

            logger.info(f"Exported {rows_exported} activities to {output_file}")
            return ExportResult(
                success=True,
                file_path=output_file,
                rows_exported=rows_exported,
            )

        except Exception as e:
            logger.error(f"Failed to export activity history: {e}")
            return ExportResult(
                success=False,
                error_message=str(e)
            )

    def export_to_json(
        self,
        session_id: int,
        output_file: Path,
    ) -> ExportResult:
        """Export session data to JSON format.

        Args:
            session_id: Session ID to export
            output_file: Output JSON file path

        Returns:
            ExportResult with success status
        """
        try:
            data = self._repo.export_session_data(session_id)
            if not data:
                return ExportResult(
                    success=False,
                    error_message=f"Session {session_id} not found"
                )

            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            rows = len(data.get("activities", [])) + len(data.get("earnings", []))
            logger.info(f"Exported session {session_id} to JSON: {output_file}")

            return ExportResult(
                success=True,
                file_path=output_file,
                rows_exported=rows,
            )

        except Exception as e:
            logger.error(f"Failed to export to JSON: {e}")
            return ExportResult(
                success=False,
                error_message=str(e)
            )

    def export_earnings_breakdown(
        self,
        character_id: int,
        output_file: Path,
        days: int = 30,
    ) -> ExportResult:
        """Export earnings breakdown by activity type.

        Args:
            character_id: Character ID to export
            output_file: Output CSV file path
            days: Number of days to analyze

        Returns:
            ExportResult with success status
        """
        try:
            # Collect activity types
            activity_types = [
                "CONTACT_MISSION", "VIP_WORK", "SELL_MISSION", "HEIST_FINALE",
                "HEIST_PREP", "SECURITY_CONTRACT", "PAYPHONE_HIT", "MC_CONTRACT"
            ]

            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Activity Type", "Count", "Total Earnings", "Avg Earnings",
                    "Avg Duration (min)", "$/Hour"
                ])

                for activity_type in activity_types:
                    stats = self._repo.get_activity_stats(character_id, activity_type, days)

                    if stats["count"] > 0:
                        avg_duration_min = stats["avg_duration"] / 60
                        avg_duration_hrs = avg_duration_min / 60
                        per_hour = stats["avg_earnings"] / avg_duration_hrs if avg_duration_hrs > 0 else 0

                        writer.writerow([
                            activity_type,
                            stats["count"],
                            f"${stats['total_earnings']:,}",
                            f"${stats['avg_earnings']:,}",
                            f"{avg_duration_min:.1f}",
                            f"${per_hour:,.0f}",
                        ])

            logger.info(f"Exported earnings breakdown to {output_file}")
            return ExportResult(
                success=True,
                file_path=output_file,
                rows_exported=len(activity_types),
            )

        except Exception as e:
            logger.error(f"Failed to export earnings breakdown: {e}")
            return ExportResult(
                success=False,
                error_message=str(e)
            )


def get_default_export_path() -> Path:
    """Get the default export directory.

    Returns:
        Path to exports directory
    """
    from ..config.settings import get_settings
    return get_settings().data_dir / "exports"


def quick_export_session(session_id: int) -> ExportResult:
    """Quick export of a session to the default location.

    Args:
        session_id: Session ID to export

    Returns:
        ExportResult with success status
    """
    exporter = DataExporter()
    output_dir = get_default_export_path() / f"session_{session_id}"
    return exporter.export_session_to_csv(session_id, output_dir)
