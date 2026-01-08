"""Data access repository for GTA Business Manager."""

from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import contextmanager

from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import SQLAlchemyError

from .models import Character, Session, Activity, BusinessSnapshot, Earnings, init_database, utc_now
from ..utils.logging import get_logger


logger = get_logger("database")


class DatabaseError(Exception):
    """Base exception for database operations."""
    pass


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
        if not self._initialized:
            self.initialize()

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
        if not self._initialized:
            self.initialize()

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

    def end_session(self, session_id: int, end_money: int = 0) -> bool:
        """End a play session.

        Args:
            session_id: Session ID to end
            end_money: Money at session end

        Returns:
            True if successful
        """
        try:
            with self._session_scope() as db_session:
                session = db_session.query(Session).filter_by(id=session_id).first()
                if session:
                    session.ended_at = utc_now()
                    session.end_money = end_money
                    session.total_earnings = end_money - session.start_money
                    logger.info(f"Ended session {session_id}, earnings: ${session.total_earnings:,}")
                    return True
                return False
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

        try:
            with self._session_scope() as db_session:
                cutoff = utc_now() - timedelta(days=days)

                # Get sessions for this character in the period
                session_ids = [
                    s.id for s in db_session.query(Session)
                    .filter(
                        Session.character_id == character_id,
                        Session.started_at >= cutoff,
                    )
                    .all()
                ]

                if not session_ids:
                    return default_stats

                activities = (
                    db_session.query(Activity)
                    .filter(
                        Activity.session_id.in_(session_ids),
                        Activity.activity_type == activity_type,
                    )
                    .all()
                )

                if not activities:
                    return default_stats

                total_earnings = sum(a.earnings or 0 for a in activities)
                total_duration = sum(a.duration_seconds or 0 for a in activities)
                count = len(activities)

                return {
                    "count": count,
                    "total_earnings": total_earnings,
                    "avg_earnings": total_earnings // count if count else 0,
                    "avg_duration": total_duration // count if count else 0,
                }
        except DatabaseError as e:
            logger.error(f"Failed to get activity stats: {e}")
            return default_stats

    def export_session_data(self, session_id: int) -> Optional[dict]:
        """Export all data for a session.

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
                        "started_at": session.started_at.isoformat(),
                        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                        "start_money": session.start_money,
                        "end_money": session.end_money,
                        "total_earnings": session.total_earnings,
                        "duration_seconds": session.duration_seconds,
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
                            "timestamp": e.timestamp.isoformat(),
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
