"""SQLAlchemy database models for GTA Business Manager."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Float, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


Base = declarative_base()


class Character(Base):
    """Represents a GTA Online character."""

    __tablename__ = "characters"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)

    # Relationships
    sessions = relationship("Session", back_populates="character")
    business_snapshots = relationship("BusinessSnapshot", back_populates="character")

    def __repr__(self) -> str:
        return f"<Character(id={self.id}, name='{self.name}')>"


class Session(Base):
    """Represents a play session."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    started_at = Column(DateTime, default=utc_now)
    ended_at = Column(DateTime, nullable=True)
    start_money = Column(Integer, default=0)
    end_money = Column(Integer, nullable=True)
    total_earnings = Column(Integer, default=0)

    # Relationships
    character = relationship("Character", back_populates="sessions")
    activities = relationship("Activity", back_populates="session")
    earnings = relationship("Earnings", back_populates="session")

    @property
    def duration_seconds(self) -> float:
        """Get session duration in seconds."""
        end = self.ended_at or utc_now()
        start = self.started_at

        # Handle timezone-aware vs naive datetime comparison
        # (for backwards compatibility with existing data)
        if end.tzinfo is not None and start.tzinfo is None:
            end = end.replace(tzinfo=None)
        elif start.tzinfo is not None and end.tzinfo is None:
            start = start.replace(tzinfo=None)

        return (end - start).total_seconds()

    def __repr__(self) -> str:
        return f"<Session(id={self.id}, started_at={self.started_at})>"


class Activity(Base):
    """Represents a tracked activity (mission, sell, etc.)."""

    __tablename__ = "activities"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    activity_type = Column(String(50), nullable=False)
    activity_name = Column(String(200), nullable=True)
    started_at = Column(DateTime, default=utc_now)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    earnings = Column(Integer, default=0)
    success = Column(Boolean, nullable=True)
    business_type = Column(String(50), nullable=True)
    notes = Column(String(500), nullable=True)

    # Relationships
    session = relationship("Session", back_populates="activities")

    def __repr__(self) -> str:
        return f"<Activity(id={self.id}, type='{self.activity_type}', name='{self.activity_name}')>"


class BusinessSnapshot(Base):
    """Snapshot of business state at a point in time."""

    __tablename__ = "business_snapshots"

    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    business_type = Column(String(50), nullable=False)
    timestamp = Column(DateTime, default=utc_now)
    stock_level = Column(Integer, nullable=True)  # Percentage 0-100
    supply_level = Column(Integer, nullable=True)  # Percentage 0-100
    stock_value = Column(Integer, nullable=True)  # Dollar value

    # Relationships
    character = relationship("Character", back_populates="business_snapshots")

    def __repr__(self) -> str:
        return f"<BusinessSnapshot(business='{self.business_type}', stock={self.stock_level}%)>"


class Earnings(Base):
    """Individual earning events."""

    __tablename__ = "earnings"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    timestamp = Column(DateTime, default=utc_now)
    amount = Column(Integer, nullable=False)
    source = Column(String(100), nullable=True)  # Inferred source
    balance_after = Column(Integer, nullable=True)

    # Relationships
    session = relationship("Session", back_populates="earnings")

    def __repr__(self) -> str:
        return f"<Earnings(amount=${self.amount:,}, source='{self.source}')>"


def init_database(db_path: str = "gta_manager.db") -> sessionmaker:
    """Initialize the database and return a session factory.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Session factory for creating database sessions
    """
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
