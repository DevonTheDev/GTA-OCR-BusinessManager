"""Data access repository for GTA Business Manager."""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List
from contextlib import contextmanager

from sqlalchemy import case, func
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import SQLAlchemyError

from .models import Character, Session, Activity, BusinessSnapshot, Earnings, init_database, utc_now
from .session_comparison import (
    ActivityTypeComparison,
    SessionComparison,
    SessionComparisonUnavailable,
    SessionSummary,
    build_session_metrics,
    finite_number,
    subtract_metrics,
)
from ..utils.logging import get_logger


logger = get_logger("database")


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


@dataclass(frozen=True)
class SessionHistoryItem:
    """Detached completed-session values, preserving nullable legacy data.

    Timestamps retain their stored UTC values. ``net_change`` is the stored
    session total, not a recalculation from balances, activities or earnings.
    """

    id: int
    character_id: int
    character_name: str
    started_at: Optional[datetime]
    ended_at: datetime
    start_money: Optional[int]
    end_money: Optional[int]
    net_change: Optional[int]
    duration_seconds: Optional[float]
    activities_count: int


@dataclass(frozen=True)
class SessionHistoryPage:
    """A caller-owned session list and the matching total before pagination."""

    sessions: List[SessionHistoryItem]
    total: int


class Repository:
    """Data access layer for the database."""

    def __init__(self, db_path: str = "gta_manager.db"):
        """Initialize repository.

        Args:
            db_path: Path to SQLite database file
        """
        self._db_path = db_path
        self._session_factory = None
        self._db_session: Optional[DBSession] = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize the database connection.

        Returns:
            True if initialization successful
        """
        try:
            self._session_factory = init_database(self._db_path)
            self._initialized = True
            logger.info(f"Database initialized: {self._db_path}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Failed to initialize database: {e}")
            return False

    @contextmanager
    def _session_scope(self):
        """Provide a transactional scope around operations."""
        if not self._initialized and not self.initialize():
            raise DatabaseError("Database initialization failed")

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error: {e}")
            raise DatabaseError(f"Database operation failed: {e}") from e
        finally:
            session.close()

    def _get_session(self) -> DBSession:
        """Get or create a database session."""
        if not self._initialized and not self.initialize():
            raise DatabaseError("Database initialization failed")

        if self._db_session is None:
            self._db_session = self._session_factory()
        return self._db_session

    def close(self) -> None:
        """Close the database session."""
        if self._db_session:
            try:
                self._db_session.close()
            except Exception as e:
                logger.warning(f"Error closing database session: {e}")
            finally:
                self._db_session = None

    # Character operations

    def get_or_create_character(self, name: str) -> Optional[Character]:
        """Get existing character or create new one.

        Args:
            name: Character name

        Returns:
            Character instance or None on error
        """
        try:
            with self._session_scope() as session:
                character = session.query(Character).filter_by(name=name).first()

                if not character:
                    character = Character(name=name)
                    session.add(character)
                    session.flush()  # Get the ID
                    logger.info(f"Created new character: {name}")

                # Detach from session for safe return
                session.expunge(character)
                return character
        except DatabaseError as e:
            logger.error(f"Failed to get/create character: {e}")
            return None

    def get_active_character(self) -> Optional[Character]:
        """Get the currently active character."""
        try:
            with self._session_scope() as session:
                character = session.query(Character).filter_by(is_active=True).first()
                if character:
                    session.expunge(character)
                return character
        except DatabaseError as e:
            logger.error(f"Failed to get active character: {e}")
            return None

    def get_all_characters(self) -> List[Character]:
        """Get all characters."""
        try:
            with self._session_scope() as session:
                characters = session.query(Character).all()
                for char in characters:
                    session.expunge(char)
                return characters
        except DatabaseError as e:
            logger.error(f"Failed to get characters: {e}")
            return []

    def set_active_character(self, character_id: int) -> bool:
        """Set a character as active.

        Args:
            character_id: Character ID to activate

        Returns:
            True if successful
        """
        try:
            with self._session_scope() as session:
                # Deactivate all
                session.query(Character).update({Character.is_active: False})
                # Activate target
                result = session.query(Character).filter_by(id=character_id).update(
                    {Character.is_active: True}
                )
                return result > 0
        except DatabaseError as e:
            logger.error(f"Failed to set active character: {e}")
            return False

    # Session operations

    def start_session(self, character: Character, start_money: int = 0) -> Optional[Session]:
        """Start a new play session.

        Args:
            character: Character for this session
            start_money: Money at session start

        Returns:
            New Session instance or None on error
        """
        try:
            with self._session_scope() as db_session:
                session = Session(
                    character_id=character.id,
                    start_money=start_money,
                )
                db_session.add(session)
                db_session.flush()
                logger.info(f"Started session {session.id} for {character.name}")
                db_session.expunge(session)
                return session
        except DatabaseError as e:
            logger.error(f"Failed to start session: {e}")
            return None

    def set_session_start_money(self, session_id: int, start_money: int) -> bool:
        """Set the first observed balance of an open session.

        Sessions can be created before OCR supplies a balance. Closed session
        history must not be rebased by a delayed reading.
        """
        try:
            with self._session_scope() as db_session:
                session = db_session.query(Session).filter_by(
                    id=session_id, ended_at=None
                ).first()
                if session is None:
                    return False
                session.start_money = start_money
                return True
        except DatabaseError as e:
            logger.error(f"Failed to set session opening balance: {e}")
            return False

    def end_session(
        self, session_id: int, end_money: int = 0, *, start_money: Optional[int] = None
    ) -> bool:
        """Close an open session, optionally recovering its observed baseline.

        The opening balance, ending balance and net change commit together.
        ``None`` preserves the stored opening balance; zero is a valid override.
        Closed or missing sessions return False without rewriting history.
        """
        updates = {
            Session.ended_at: utc_now(),
            Session.end_money: end_money,
            Session.total_earnings: end_money - (
                start_money if start_money is not None else Session.start_money
            ),
        }
        if start_money is not None:
            updates[Session.start_money] = start_money
        try:
            with self._session_scope() as db_session:
                # The open-session condition belongs to the UPDATE itself so
                # two concurrent finalizers cannot both rewrite the same row.
                changed = db_session.query(Session).filter_by(
                    id=session_id, ended_at=None
                ).update(updates, synchronize_session=False)
                if not changed:
                    return False
                earnings = db_session.query(Session.total_earnings).filter_by(
                    id=session_id
                ).scalar()
            # Do not log success before the transaction has committed.
            logger.info("Ended session %s, net earnings: %s", session_id, earnings)
            return True
        except DatabaseError as e:
            logger.error(f"Failed to end session: {e}")
            return False

    def get_recent_sessions(self, character_id: int, limit: int = 10) -> List[Session]:
        """Get recent sessions for a character.

        Args:
            character_id: Character ID to query
            limit: Maximum sessions to return

        Returns:
            List of Session objects
        """
        try:
            with self._session_scope() as db_session:
                sessions = (
                    db_session.query(Session)
                    .filter_by(character_id=character_id)
                    .order_by(Session.started_at.desc())
                    .limit(limit)
                    .all()
                )
                for s in sessions:
                    db_session.expunge(s)
                return sessions
        except DatabaseError as e:
            logger.error(f"Failed to get recent sessions: {e}")
            return []

    def get_completed_session_history(
        self, character_id: Optional[int] = None, limit: int = 50, offset: int = 0
    ) -> SessionHistoryPage:
        """Read completed sessions in stable newest-start, newest-ID order.

        ``character_id=None`` includes all characters. ``total`` counts matching
        sessions before applying the page bounds. Missing legacy start times
        sort last and have no duration; stored money and timestamps are returned
        unchanged. Activity counts include every activity belonging to a session.

        Raises:
            ValueError: A character ID is not positive, limit is outside 1..500,
                or offset is negative. All arguments must be integers (not bool),
                except the optional character ID.
            DatabaseError: Initialization or querying failed. An unavailable
                database is deliberately distinct from an empty history page.
        """
        if character_id is not None and (
            isinstance(character_id, bool)
            or not isinstance(character_id, int)
            or character_id < 1
        ):
            raise ValueError("character_id must be None or a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")

        with self._session_scope() as db_session:
            query = (
                db_session.query(Session, Character.name)
                .join(Character, Session.character_id == Character.id)
                .filter(Session.ended_at.isnot(None))
            )
            if character_id is not None:
                query = query.filter(Session.character_id == character_id)
            total = query.count()

            # Aggregate activities independently of earnings so neither count
            # nor the stored net change can be multiplied by a join.
            activity_counts = (
                db_session.query(
                    Activity.session_id,
                    func.count(Activity.id).label("activities_count"),
                )
                .group_by(Activity.session_id)
                .subquery()
            )
            rows = (
                query.add_columns(func.coalesce(activity_counts.c.activities_count, 0))
                .outerjoin(activity_counts, activity_counts.c.session_id == Session.id)
                .order_by(Session.started_at.desc(), Session.id.desc())
                .limit(limit)
                .offset(offset)
                .all()
            )
            items = [
                SessionHistoryItem(
                    id=record.id,
                    character_id=record.character_id,
                    character_name=character_name,
                    started_at=record.started_at,
                    ended_at=record.ended_at,
                    start_money=record.start_money,
                    end_money=record.end_money,
                    net_change=record.total_earnings,
                    duration_seconds=(record.ended_at - record.started_at).total_seconds()
                    if record.started_at is not None else None,
                    activities_count=activities_count,
                )
                for record, character_name, activities_count in rows
            ]
            return SessionHistoryPage(sessions=items, total=total)

    def get_session_comparison(
        self, baseline_id: int, comparison_id: int
    ) -> SessionComparison:
        """Read an exact pair of completed sessions in one SELECT.

        All activities are grouped by session and actual type independently of
        earnings events. The returned values are immutable and detached from
        storage, so display and export can share this exact snapshot.

        Raises:
            ValueError: IDs are not distinct positive integers (bools excluded).
            SessionComparisonUnavailable: A selected row is missing, open, or
                has no available character; ``session_ids`` identifies it.
            DatabaseError: Database initialization or the SELECT failed.
        """
        for name, value in (("baseline_id", baseline_id), ("comparison_id", comparison_id)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if baseline_id == comparison_id:
            raise ValueError("baseline_id and comparison_id must be different")
        selected_ids = (baseline_id, comparison_id)
        # SQLite row IDs cannot exceed signed 64-bit integers. Larger positive
        # IDs are unavailable selections, not Python-to-SQLite binding faults.
        query_ids = tuple(session_id for session_id in selected_ids if session_id < 2**63)

        with self._session_scope() as db_session:
            activity_counts = (
                db_session.query(
                    Activity.session_id,
                    Activity.activity_type,
                    func.count().label("recorded_activities"),
                    func.sum(case((Activity.success.is_(True), 1), else_=0)).label("passed"),
                    func.sum(case((Activity.success.is_(False), 1), else_=0)).label("failed"),
                )
                .filter(Activity.session_id.in_(query_ids))
                .group_by(Activity.session_id, Activity.activity_type)
                .subquery()
            )
            rows = (
                db_session.query(
                    Session.id.label("session_id"),
                    Session.character_id,
                    Character.name.label("character_name"),
                    Session.started_at,
                    Session.ended_at,
                    Session.start_money,
                    Session.end_money,
                    Session.total_earnings,
                    activity_counts.c.session_id.label("activity_session_id"),
                    activity_counts.c.activity_type,
                    activity_counts.c.recorded_activities,
                    activity_counts.c.passed,
                    activity_counts.c.failed,
                )
                .join(Character, Character.id == Session.character_id)
                .outerjoin(activity_counts, activity_counts.c.session_id == Session.id)
                .filter(Session.id.in_(query_ids), Session.ended_at.isnot(None))
                .all()
            )

        records = {row.session_id: row for row in rows}
        unavailable = tuple(session_id for session_id in selected_ids if session_id not in records)
        if unavailable:
            raise SessionComparisonUnavailable(unavailable)

        counts = {session_id: [0, 0, 0] for session_id in selected_ids}
        types = {session_id: {} for session_id in selected_ids}
        for row in rows:
            # The outer-join placeholder is not an actual null activity type.
            if row.activity_session_id is None:
                continue
            totals = counts[row.session_id]
            totals[0] += row.recorded_activities
            totals[1] += row.passed
            totals[2] += row.failed
            types[row.session_id][row.activity_type] = row.recorded_activities

        summaries = {}
        for session_id in selected_ids:
            row = records[session_id]
            duration = (
                (row.ended_at - row.started_at).total_seconds()
                if row.started_at is not None else None
            )
            summaries[session_id] = SessionSummary(
                session_id=session_id,
                character_id=row.character_id,
                character_name=row.character_name,
                started_at=row.started_at,
                ended_at=row.ended_at,
                start_money=finite_number(row.start_money),
                end_money=finite_number(row.end_money),
                metrics=build_session_metrics(row.total_earnings, duration, *counts[session_id]),
            )

        baseline_types, comparison_types = types[baseline_id], types[comparison_id]
        ordered_types = sorted(
            baseline_types.keys() | comparison_types.keys(),
            key=lambda activity_type: (activity_type is not None, activity_type or ""),
        )
        return SessionComparison(
            baseline=summaries[baseline_id],
            comparison=summaries[comparison_id],
            differences=subtract_metrics(
                summaries[baseline_id].metrics, summaries[comparison_id].metrics
            ),
            activity_types=tuple(
                ActivityTypeComparison(
                    activity_type=activity_type,
                    baseline=baseline_types.get(activity_type, 0),
                    comparison=comparison_types.get(activity_type, 0),
                    difference=(
                        comparison_types.get(activity_type, 0)
                        - baseline_types.get(activity_type, 0)
                    ),
                )
                for activity_type in ordered_types
            ),
            generated_at=utc_now(),
        )

    # Activity operations

    def log_activity(
        self,
        session_id: int,
        activity_type: str,
        activity_name: str = "",
        earnings: int = 0,
        success: bool = True,
        duration_seconds: int = 0,
        business_type: str = "",
    ) -> Optional[Activity]:
        """Log a completed activity.

        Args:
            session_id: Current session ID
            activity_type: Type of activity
            activity_name: Name of specific activity
            earnings: Money earned
            success: Whether activity succeeded
            duration_seconds: How long it took
            business_type: Associated business (if any)

        Returns:
            New Activity instance or None on error
        """
        try:
            with self._session_scope() as db_session:
                activity = Activity(
                    session_id=session_id,
                    activity_type=activity_type,
                    activity_name=activity_name,
                    ended_at=utc_now(),
                    duration_seconds=duration_seconds,
                    earnings=earnings,
                    success=success,
                    business_type=business_type,
                )
                db_session.add(activity)
                db_session.flush()
                logger.debug(f"Logged activity: {activity_type} - {activity_name}, ${earnings:,}")
                db_session.expunge(activity)
                return activity
        except DatabaseError as e:
            logger.error(f"Failed to log activity: {e}")
            return None

    def get_session_activities(self, session_id: int) -> List[Activity]:
        """Get all activities for a session.

        Args:
            session_id: Session ID to query

        Returns:
            List of Activity objects
        """
        try:
            with self._session_scope() as db_session:
                activities = (
                    db_session.query(Activity)
                    .filter_by(session_id=session_id)
                    .order_by(Activity.ended_at.desc())
                    .all()
                )
                for a in activities:
                    db_session.expunge(a)
                return activities
        except DatabaseError as e:
            logger.error(f"Failed to get session activities: {e}")
            return []

    def get_character_activities(
        self, character_id: int, days: int = 30, activity_type: Optional[str] = None
    ) -> List[Activity]:
        """Read a UTC activity-time window, newest first, without a session cap.

        Completion time takes precedence; legacy/in-progress rows without it
        use their start time. Undated/future rows are outside the window.
        """
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise ValueError("days must be a nonnegative integer")
        now = utc_now()
        cutoff = now - timedelta(days=days)
        try:
            with self._session_scope() as db_session:
                activity_time = func.coalesce(Activity.ended_at, Activity.started_at)
                query = (
                    db_session.query(Activity)
                    .join(Session, Activity.session_id == Session.id)
                    .filter(
                        Session.character_id == character_id,
                        activity_time >= cutoff,
                        activity_time <= now,
                    )
                )
                if activity_type is not None:
                    query = query.filter(Activity.activity_type == activity_type)
                activities = query.order_by(activity_time.desc(), Activity.id.desc()).all()
                for activity in activities:
                    db_session.expunge(activity)
                return activities
        except DatabaseError as exc:
            logger.error(f"Failed to get character activities: {exc}")
            return []

    # Business snapshot operations

    def save_business_snapshot(
        self,
        character_id: int,
        business_type: str,
        stock_level: Optional[int] = None,
        supply_level: Optional[int] = None,
        stock_value: Optional[int] = None,
    ) -> Optional[BusinessSnapshot]:
        """Save a business state snapshot.

        Args:
            character_id: Character ID who owns the business
            business_type: Type of business
            stock_level: Stock percentage (0-100)
            supply_level: Supply percentage (0-100)
            stock_value: Dollar value of stock

        Returns:
            New BusinessSnapshot instance or None on error
        """
        try:
            with self._session_scope() as db_session:
                snapshot = BusinessSnapshot(
                    character_id=character_id,
                    business_type=business_type,
                    stock_level=stock_level,
                    supply_level=supply_level,
                    stock_value=stock_value,
                )
                db_session.add(snapshot)
                db_session.flush()
                db_session.expunge(snapshot)
                return snapshot
        except DatabaseError as e:
            logger.error(f"Failed to save business snapshot: {e}")
            return None

    def get_latest_business_snapshot(
        self, character_id: int, business_type: str
    ) -> Optional[BusinessSnapshot]:
        """Get the most recent snapshot for a business.

        Args:
            character_id: Character ID who owns the business
            business_type: Type of business

        Returns:
            Latest BusinessSnapshot or None
        """
        try:
            with self._session_scope() as db_session:
                snapshot = (
                    db_session.query(BusinessSnapshot)
                    .filter_by(character_id=character_id, business_type=business_type)
                    .order_by(BusinessSnapshot.timestamp.desc())
                    .first()
                )
                if snapshot:
                    db_session.expunge(snapshot)
                return snapshot
        except DatabaseError as e:
            logger.error(f"Failed to get business snapshot: {e}")
            return None

    # Earnings operations

    def log_earning(
        self, session_id: int, amount: int, source: str = "", balance_after: int = 0
    ) -> Optional[Earnings]:
        """Log an earning event.

        Args:
            session_id: Current session ID
            amount: Amount earned
            source: Source of earning (inferred)
            balance_after: Balance after earning

        Returns:
            New Earnings instance or None on error
        """
        try:
            with self._session_scope() as db_session:
                earning = Earnings(
                    session_id=session_id,
                    amount=amount,
                    source=source,
                    balance_after=balance_after,
                )
                db_session.add(earning)
                db_session.flush()
                db_session.expunge(earning)
                return earning
        except DatabaseError as e:
            logger.error(f"Failed to log earning: {e}")
            return None

    # Statistics

    def get_total_earnings(self, character_id: int, days: int = 30) -> int:
        """Get total earnings over a period.

        Args:
            character_id: Character ID to query
            days: Number of days to look back

        Returns:
            Total earnings in dollars
        """
        try:
            with self._session_scope() as db_session:
                cutoff = utc_now() - timedelta(days=days)

                sessions = (
                    db_session.query(Session)
                    .filter(
                        Session.character_id == character_id,
                        Session.started_at >= cutoff,
                    )
                    .all()
                )

                return sum(s.total_earnings or 0 for s in sessions)
        except DatabaseError as e:
            logger.error(f"Failed to get total earnings: {e}")
            return 0

    def get_activity_stats(
        self, character_id: int, activity_type: str, days: int = 30
    ) -> dict:
        """Get statistics for an activity type.

        Args:
            character_id: Character ID to query
            activity_type: Type of activity
            days: Number of days to look back

        Returns:
            Dict with count, total_earnings, avg_earnings, avg_duration
        """
        default_stats = {"count": 0, "total_earnings": 0, "avg_earnings": 0, "avg_duration": 0}

        activities = self.get_character_activities(character_id, days, activity_type)
        if not activities:
            return default_stats

        total_earnings = sum(activity.earnings or 0 for activity in activities)
        total_duration = sum(activity.duration_seconds or 0 for activity in activities)
        count = len(activities)
        return {
            "count": count,
            "total_earnings": total_earnings,
            "avg_earnings": total_earnings // count,
            "avg_duration": total_duration // count,
        }

    def export_session_data(self, session_id: int) -> Optional[dict]:
        """Export all data for a session, preserving missing legacy values.

        Missing timestamps are None, as is duration when no start is recorded.

        Args:
            session_id: Session ID to export

        Returns:
            Dictionary with session data or None on error
        """
        try:
            with self._session_scope() as db_session:
                session = db_session.query(Session).filter_by(id=session_id).first()
                if not session:
                    return None

                activities = (
                    db_session.query(Activity)
                    .filter_by(session_id=session_id)
                    .all()
                )

                earnings = (
                    db_session.query(Earnings)
                    .filter_by(session_id=session_id)
                    .all()
                )

                return {
                    "session": {
                        "id": session.id,
                        "started_at": session.started_at.isoformat()
                        if session.started_at is not None else None,
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "start_money": session.start_money,
                        "end_money": session.end_money,
                        "total_earnings": session.total_earnings,
                        "duration_seconds": session.duration_seconds
                        if session.started_at is not None else None,
                    },
                    "activities": [
                        {
                            "type": a.activity_type,
                            "name": a.activity_name,
                            "earnings": a.earnings,
                            "duration_seconds": a.duration_seconds,
                            "success": a.success,
                            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
                        }
                        for a in activities
                    ],
                    "earnings": [
                        {
                            "amount": e.amount,
                            "source": e.source,
                            "balance_after": e.balance_after,
                            "timestamp": e.timestamp.isoformat() if e.timestamp is not None else None,
                        }
                        for e in earnings
                    ],
                }
        except DatabaseError as e:
            logger.error(f"Failed to export session data: {e}")
            return None


# Global repository instance
_repository: Optional[Repository] = None


def get_repository(db_path: Optional[str] = None) -> Repository:
    """Get the global repository instance.

    Args:
        db_path: Optional database path (only used on first call)

    Returns:
        Repository instance
    """
    global _repository
    if _repository is None:
        from ..config.settings import get_settings
        if db_path is None:
            db_path = str(get_settings().data_dir / "gta_manager.db")
        _repository = Repository(db_path)
        _repository.initialize()
    return _repository
