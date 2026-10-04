"""Data access repository for GTA Business Manager."""

from datetime import date, datetime, time, timedelta
from dataclasses import dataclass
from typing import Optional, List
from contextlib import contextmanager
import sqlite3

from sqlalchemy import LargeBinary, case, cast, func, or_, select, text, true
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import SQLAlchemyError

from .models import (
    Character, Session, Activity, BusinessSnapshot, Earnings, SessionAnnotationRecord,
    init_database, utc_now,
)
from .session_annotations import (
    InvalidSessionAnnotation,
    SessionAnnotation,
    SessionAnnotationConflict,
    SessionAnnotationUnavailable,
    annotation_from_storage,
    normalize_annotation,
    normalize_annotation_query,
    validate_annotation_id,
    validate_annotation_revision,
)
from .activity_ledger import (
    ActivityLedgerFilters,
    ActivityLedgerPage,
    ActivityLedgerRow,
    validate_ledger_request,
)
from .activity_insights import (
    ActivityInsights,
    MAX_ACTIVITY_TYPE_BYTES,
    MAX_INSIGHT_RECORDS,
    build_activity_insights,
)
from .session_comparison import (
    ActivityTypeComparison,
    SessionComparison,
    SessionComparisonUnavailable,
    SessionSummary,
    build_session_metrics,
    finite_number,
    subtract_metrics,
)
from .business_checkins import (
    BusinessCheckIn, BusinessCheckInBoard, BusinessCheckInHistoryFilters, BusinessCheckInPage,
    BusinessCheckInDataError, BusinessCheckInLimitError, BusinessCheckInUnavailable,
    BusinessCheckInValidationError,
    CheckInCharacter, MAX_CHECKIN_BUSINESSES, MAX_CHECKIN_CHARACTERS,
    MAX_CHECKIN_CHARACTER_NAME_BYTES, MAX_CHECKIN_NOTE_BYTES,
    checkin_character_from_storage, checkin_from_storage, normalize_business_checkin,
    checkin_history_match_from_storage, validate_business_checkin_history_filters,
    validate_checkin_business_id, validate_checkin_id, validate_checkin_page,
)
from .business_checkin_comparison import (
    BusinessCheckInComparison, BusinessCheckInComparisonUnavailable,
)
from .business_checkin_trend import (
    BusinessCheckInTrend, BusinessCheckInTrendLimitError, MAX_CHECKIN_TREND_ROWS,
)
from .business_checkin_batches import (
    BusinessCheckInBatchUncertain, BusinessCheckInDraft, normalize_business_checkin_drafts,
)
from .character_profiles import (
    CharacterProfileAmbiguous, CharacterProfileLimitError, CharacterProfileUnavailable,
    MAX_CHARACTER_NAME_BYTES, SavedCharacterResult,
    normalize_character_name, saved_character_from_storage,
)
from .business_pins import (
    BusinessPinDataError, BusinessPinLimitError, BusinessPinUnavailable, ManualBusinessPins,
    MAX_BUSINESS_PINS, MAX_PIN_BUSINESS_ID_BYTES, MAX_PIN_CHARACTER_NAME_BYTES,
    manual_business_pins_from_storage, validate_pin_business_id, validate_pin_character_id,
    validate_pin_desired_state,
)
from ..utils.logging import get_logger


logger = get_logger("database")


def _checkin_text_column(table, field, maximum, name=None):
    """Bound bytes before driver decoding, including malformed UTF-8 and BLOBs."""
    name = name or field
    # Some SQLite versions return NULL for substr(empty BLOB); retain the
    # original typeof separately so this never converts an actual NULL to text.
    return (f"coalesce(substr(CAST({table}.{field} AS BLOB), 1, {maximum + 1}), x'') AS {name}, "
            f"typeof({table}.{field}) AS {name}_type")


_CHECKIN_CHARACTER_COLUMNS = (
    "c.id AS context_character_id, "
    + _checkin_text_column("c", "name", MAX_CHECKIN_CHARACTER_NAME_BYTES, "character_name")
    + ", CASE WHEN typeof(c.is_active) = 'integer' THEN c.is_active END AS active_integer"
)
_CHECKIN_ROW_COLUMNS = ", ".join([
    "m.id IS NOT NULL AS row_present",
    *(f"CASE WHEN typeof(m.{field}) = 'integer' THEN m.{field} END AS {field}, "
      f"typeof(m.{field}) AS {field}_type"
      for field in ("id", "character_id", "stock_percent", "supply_percent", "stock_value")),
    _checkin_text_column("m", "business_id", 50),
    _checkin_text_column("m", "recorded_at", 64),
    _checkin_text_column("m", "note", MAX_CHECKIN_NOTE_BYTES),
])


def _checkin_history_filter_sql(filters):
    """Share exact bounded predicate/count clauses across history and trend reads."""
    # Only active predicate fields cross into Python, always as bounded bytes
    # plus SQLite's storage type. Other payload is validated only when selected.
    if filters is not None:
        note_args = (f"coalesce(substr(CAST(m.note AS BLOB), 1, {MAX_CHECKIN_NOTE_BYTES + 1}), x''), "
                     "typeof(m.note)" if filters.note_query is not None else "NULL, NULL")
        date_args = ("coalesce(substr(CAST(m.recorded_at AS BLOB), 1, 65), x''), typeof(m.recorded_at)"
                     if filters.recorded_from is not None or filters.recorded_until is not None
                     else "NULL, NULL")
        prefix = (
            "WITH evaluated AS (SELECT m.id, "
            f"checkin_history_match({note_args}, {date_args}) AS match_status "
            "FROM manual_business_checkins m "
            "WHERE m.character_id = :character_id AND m.business_id = :business_id), "
            "counted AS (SELECT COUNT(CASE WHEN match_status = 1 THEN 1 END) AS total, "
            "coalesce(MAX(CASE WHEN match_status = -1 THEN 1 ELSE 0 END), 0) AS invalid "
            "FROM evaluated), "
        )
        selection = "JOIN evaluated ON evaluated.id = m.id WHERE evaluated.match_status = 1 "
    else:
        prefix = (
            "WITH counted AS (SELECT COUNT(*) AS total, 0 AS invalid FROM manual_business_checkins "
            "WHERE character_id = :character_id AND business_id = :business_id), "
        )
        selection = "WHERE m.character_id = :character_id AND m.business_id = :business_id "
    return prefix, selection


_BUSINESS_PIN_QUERY = text(
    "WITH owners AS ("
    "SELECT CASE WHEN typeof(c.id) = 'integer' THEN c.id END AS context_character_id, "
    "typeof(c.id) AS context_character_id_type, "
    + _checkin_text_column("c", "name", MAX_PIN_CHARACTER_NAME_BYTES, "character_name")
    + " FROM characters c WHERE c.id = :character_id LIMIT 2), "
    "pins AS (SELECT 1 AS row_present, "
    "CASE WHEN typeof(p.character_id) = 'integer' THEN p.character_id END AS pin_character_id, "
    "typeof(p.character_id) AS pin_character_id_type, "
    + _checkin_text_column("p", "business_id", MAX_PIN_BUSINESS_ID_BYTES)
    + " FROM manual_business_pins p WHERE p.character_id = :character_id LIMIT :maximum) "
    "SELECT owners.*, (SELECT count(*) FROM owners) AS owner_count, pins.* "
    "FROM owners LEFT JOIN pins ON 1 = 1 LIMIT :maximum"
)


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
    annotation_label: Optional[str] = None
    annotation_tags: tuple[str, ...] = ()
    annotation_status: str = "missing"


@dataclass(frozen=True)
class SessionHistoryPage:
    """A caller-owned session list and the matching total before pagination."""

    sessions: List[SessionHistoryItem]
    total: int


def _completed_activity_selection(filters: ActivityLedgerFilters):
    """Shared completed-activity joins and exact ledger filter semantics."""
    activity_time = func.coalesce(Activity.ended_at, Activity.started_at)
    outcome = case(
        (Activity.success.is_(True), "passed"),
        (Activity.success.is_(False), "failed"),
        else_="unknown",
    )
    joined = (
        Activity.__table__
        .join(Session.__table__, Activity.session_id == Session.id)
        .join(Character.__table__, Session.character_id == Character.id)
    )
    predicates = [Session.ended_at.isnot(None)]
    if filters.character_id is not None:
        predicates.append(Session.character_id == filters.character_id)
    if filters.date_from is not None:
        predicates.append(activity_time >= datetime.combine(filters.date_from, time.min))
    if filters.date_until is not None:
        if filters.date_until == date.max:
            # There is no representable next day. This comparison includes
            # every instant of date.max while still excluding null times.
            predicates.append(activity_time <= datetime.max)
        else:
            end_exclusive = datetime.combine(filters.date_until + timedelta(days=1), time.min)
            predicates.append(activity_time < end_exclusive)
    if filters.activity_type is not None:
        predicates.append(Activity.activity_type == filters.activity_type)
    if filters.outcome is not None:
        predicates.append(outcome == filters.outcome)
    if filters.query is not None:
        predicates.append(or_(
            Activity.activity_name.icontains(filters.query, autoescape=True),
            Activity.notes.icontains(filters.query, autoescape=True),
        ))
    return joined, predicates, activity_time, outcome


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

    # Manual observations intentionally never call legacy business/accounting methods.

    @contextmanager
    def _business_checkin_scope(self):
        """Keep operational failures strict and safe for direct display in the editor."""
        try:
            with self._session_scope() as db_session:
                yield db_session
        except (DatabaseError, SQLAlchemyError, UnicodeError, OverflowError):
            raise BusinessCheckInUnavailable() from None

    def get_business_checkin_characters(self) -> tuple[CheckInCharacter, ...]:
        """Read existing choices only, with stable name/ID order and no hidden fallback."""
        statement = text(
            f"SELECT {_CHECKIN_CHARACTER_COLUMNS} FROM characters c "
            "ORDER BY c.name, c.id LIMIT :maximum"
        )
        with self._business_checkin_scope() as db_session:
            rows = db_session.execute(statement, {"maximum": MAX_CHECKIN_CHARACTERS + 1}).mappings().all()
            if len(rows) > MAX_CHECKIN_CHARACTERS:
                raise BusinessCheckInLimitError()
            characters = tuple(checkin_character_from_storage(row) for row in rows)
        return characters

    def save_business_checkin(
        self, character_id: int, business_id: str, stock_percent: int | None = None,
        supply_percent: int | None = None, stock_value: int | None = None, note: str = "",
    ) -> BusinessCheckIn:
        """Atomically require an existing owner and append; return only after commit."""
        validate_checkin_id(character_id)
        validate_checkin_business_id(business_id, for_write=True)
        stock_percent, supply_percent, stock_value, note = normalize_business_checkin(
            stock_percent, supply_percent, stock_value, note)
        # A single INSERT SELECT protects ownership even on legacy connections
        # without PRAGMA foreign_keys; a separate preflight read would race.
        with self._business_checkin_scope() as db_session:
            recorded_at = utc_now()
            result = db_session.execute(text(
                "INSERT INTO manual_business_checkins "
                "(character_id, business_id, recorded_at, stock_percent, supply_percent, stock_value, note) "
                "SELECT id, :business_id, :recorded_at, :stock_percent, :supply_percent, :stock_value, :note "
                "FROM characters WHERE id = :character_id"
            ), {"character_id": character_id, "business_id": business_id,
                "recorded_at": recorded_at.isoformat(sep=" "), "stock_percent": stock_percent,
                "supply_percent": supply_percent, "stock_value": stock_value, "note": note})
            if result.rowcount != 1:
                raise BusinessCheckInUnavailable()
            raw = db_session.execute(text(
                f"SELECT {_CHECKIN_ROW_COLUMNS} FROM manual_business_checkins m WHERE m.id = :id"
            ), {"id": result.lastrowid}).mappings().first()
            if raw is None:
                raise BusinessCheckInDataError()
            saved = checkin_from_storage(raw)
            if saved.character_id != character_id or saved.business_id != business_id:
                raise BusinessCheckInDataError()
        return saved

    def save_business_checkins(
        self, character_id: int,
        drafts: list[BusinessCheckInDraft] | tuple[BusinessCheckInDraft, ...],
    ) -> tuple[BusinessCheckIn, ...]:
        """Append a reviewed batch in one transaction, returning only after close.

        Validation precedes storage access. Each INSERT SELECT requires the
        existing owner even on legacy connections without foreign keys. Errors
        while leaving the completed transaction body have an uncertain outcome:
        the shared scope may raise after commit, so they never imply rollback.
        """
        validate_checkin_id(character_id)
        drafts = normalize_business_checkin_drafts(drafts)
        commit_phase = False
        try:
            with self._business_checkin_scope() as db_session:
                recorded_at = utc_now()
                inserted = []
                for draft in drafts:
                    result = db_session.execute(text(
                        "INSERT INTO manual_business_checkins "
                        "(character_id, business_id, recorded_at, stock_percent, supply_percent, stock_value, note) "
                        "SELECT id, :business_id, :recorded_at, :stock_percent, :supply_percent, :stock_value, :note "
                        "FROM characters WHERE id = :character_id"
                    ), {"character_id": character_id, "business_id": draft.business_id,
                        "recorded_at": recorded_at.isoformat(sep=" "), "stock_percent": draft.stock_percent,
                        "supply_percent": draft.supply_percent, "stock_value": draft.stock_value, "note": draft.note})
                    if result.rowcount != 1:
                        raise BusinessCheckInUnavailable()
                    row_id = result.lastrowid
                    try:
                        validate_checkin_id(row_id)
                    except BusinessCheckInValidationError:
                        raise BusinessCheckInDataError() from None
                    if row_id in inserted:
                        raise BusinessCheckInDataError()
                    inserted.append(row_id)
                rows = []
                for row_id, draft in zip(inserted, drafts):
                    raw = db_session.execute(text(
                        f"SELECT {_CHECKIN_ROW_COLUMNS} FROM manual_business_checkins m WHERE m.id = :id"
                    ), {"id": row_id}).mappings().first()
                    if raw is None:
                        raise BusinessCheckInDataError()
                    row = checkin_from_storage(raw)
                    if (row.id != row_id or row.character_id != character_id
                            or row.business_id != draft.business_id or row.recorded_at != recorded_at
                            or (row.stock_percent, row.supply_percent, row.stock_value, row.note)
                            != (draft.stock_percent, draft.supply_percent, draft.stock_value, draft.note)):
                        raise BusinessCheckInDataError()
                    rows.append(row)
                saved = tuple(rows)
                commit_phase = True
        except Exception as error:
            if commit_phase:
                raise BusinessCheckInBatchUncertain() from None
            if isinstance(error, (BusinessCheckInValidationError, BusinessCheckInDataError,
                                  BusinessCheckInUnavailable)):
                raise
            raise BusinessCheckInUnavailable() from None
        return saved

    def get_business_checkin_board(self, character_id: int) -> BusinessCheckInBoard:
        """Observe owner and latest insertion per business in one bounded SQL statement."""
        validate_checkin_id(character_id)
        statement = text(
            "WITH latest AS ("
            "SELECT MAX(id) AS id FROM manual_business_checkins "
            "WHERE character_id = :character_id GROUP BY business_id LIMIT :maximum"
            "), selected AS ("
            f"SELECT {_CHECKIN_ROW_COLUMNS} FROM manual_business_checkins m "
            "JOIN latest ON latest.id = m.id"
            ") "
            f"SELECT {_CHECKIN_CHARACTER_COLUMNS}, selected.* FROM characters c "
            "LEFT JOIN selected ON 1 = 1 WHERE c.id = :character_id "
            "ORDER BY selected.business_id, selected.id"
        )
        with self._business_checkin_scope() as db_session:
            captured_at = utc_now()
            raw = db_session.execute(statement, {"character_id": character_id,
                                                "maximum": MAX_CHECKIN_BUSINESSES + 1}).mappings().all()
            if not raw:
                raise BusinessCheckInUnavailable()
            if len(raw) > MAX_CHECKIN_BUSINESSES:
                raise BusinessCheckInLimitError()
            character = checkin_character_from_storage(raw[0])
            rows = tuple(checkin_from_storage(row) for row in raw if row["row_present"])
            if any(row.character_id != character.id for row in rows):
                raise BusinessCheckInDataError()
            board = BusinessCheckInBoard(character.id, character.name, captured_at, rows)
        return board

    def get_business_checkin_history(
        self, character_id: int, business_id: str, offset: int = 0, limit: int = 25,
        *, filters: BusinessCheckInHistoryFilters | None = None,
    ) -> BusinessCheckInPage:
        """One SQLite observation owns count, page payload and character context."""
        validate_checkin_id(character_id)
        validate_checkin_business_id(business_id)
        validate_checkin_page(offset, limit)
        filters = validate_business_checkin_history_filters(filters)
        prefix, selection = _checkin_history_filter_sql(filters)
        statement = text(
            prefix + "page AS (" +
            f"SELECT {_CHECKIN_ROW_COLUMNS} FROM manual_business_checkins m "
            + selection +
            "ORDER BY m.id DESC LIMIT :limit OFFSET :offset"
            ") "
            f"SELECT {_CHECKIN_CHARACTER_COLUMNS}, counted.total, counted.invalid, page.* FROM characters c "
            "CROSS JOIN counted LEFT JOIN page ON 1 = 1 WHERE c.id = :character_id "
            "ORDER BY page.id DESC"
        )
        with self._business_checkin_scope() as db_session:
            captured_at = utc_now()
            with self._business_checkin_history_predicate(db_session, filters):
                raw = db_session.execute(statement, {"character_id": character_id, "business_id": business_id,
                                                    "offset": offset, "limit": limit}).mappings().all()
            if not raw:
                raise BusinessCheckInUnavailable()
            if raw[0]["invalid"]:
                raise BusinessCheckInDataError()
            character = checkin_character_from_storage(raw[0])
            rows = tuple(checkin_from_storage(row) for row in raw if row["row_present"])
            if any(row.character_id != character.id or row.business_id != business_id for row in rows):
                raise BusinessCheckInDataError()
            page = BusinessCheckInPage(character.id, character.name, business_id, captured_at,
                                       rows, offset, limit, raw[0]["total"], filters)
        return page

    def get_business_checkin_trend(
        self, character_id: int, business_id: str,
        *, filters: BusinessCheckInHistoryFilters | None = None,
    ) -> BusinessCheckInTrend:
        """Capture owner, count and all qualifying rows together, up to a fixed cap."""
        validate_checkin_id(character_id)
        validate_checkin_business_id(business_id)
        filters = validate_business_checkin_history_filters(filters)
        prefix, selection = _checkin_history_filter_sql(filters)
        statement = text(
            prefix + "selected AS (" +
            f"SELECT {_CHECKIN_ROW_COLUMNS} FROM manual_business_checkins m "
            + selection +
            "ORDER BY m.id ASC LIMIT :maximum"
            ") "
            f"SELECT {_CHECKIN_CHARACTER_COLUMNS}, counted.total, counted.invalid, selected.* FROM characters c "
            "CROSS JOIN counted LEFT JOIN selected ON 1 = 1 WHERE c.id = :character_id "
            "ORDER BY selected.id ASC LIMIT :maximum"
        )
        with self._business_checkin_scope() as db_session:
            captured_at = utc_now()
            with self._business_checkin_history_predicate(db_session, filters):
                raw = db_session.execute(statement, {
                    "character_id": character_id, "business_id": business_id,
                    "maximum": MAX_CHECKIN_TREND_ROWS + 1,
                }).mappings().all()
            if not raw:
                raise BusinessCheckInUnavailable()
            if raw[0]["invalid"]:
                raise BusinessCheckInDataError()
            character = checkin_character_from_storage(raw[0])
            total = raw[0]["total"]
            if type(total) is not int or total < 0:
                raise BusinessCheckInDataError()
            if total > MAX_CHECKIN_TREND_ROWS:
                raise BusinessCheckInTrendLimitError()
            if (len(raw) != max(1, total)
                    or any(record["total"] != total or record["invalid"] for record in raw)):
                raise BusinessCheckInDataError()
            rows = tuple(checkin_from_storage(record) for record in raw if record["row_present"])
            if len(rows) != total:
                raise BusinessCheckInDataError()
            try:
                trend = BusinessCheckInTrend(character.id, character.name, business_id, captured_at, rows, filters)
            except BusinessCheckInValidationError:
                raise BusinessCheckInDataError() from None
        return trend

    def get_business_checkin_comparison(
        self, character_id: int, business_id: str, baseline_id: int, comparison_id: int,
    ) -> BusinessCheckInComparison:
        """Read just the selected same-owner/business pair and context in one bounded observation."""
        validate_checkin_id(character_id)
        validate_checkin_business_id(business_id)
        validate_checkin_id(baseline_id)
        validate_checkin_id(comparison_id)
        if baseline_id == comparison_id:
            raise BusinessCheckInValidationError("Choose two different manual check-ins to compare.")
        requested_ids = (baseline_id, comparison_id)
        statement = text(
            f"SELECT {_CHECKIN_CHARACTER_COLUMNS}, {_CHECKIN_ROW_COLUMNS} FROM characters c "
            "LEFT JOIN manual_business_checkins m ON m.character_id = c.id "
            "AND m.business_id = :business_id AND m.id IN (:baseline_id, :comparison_id) "
            "WHERE c.id = :character_id LIMIT 2"
        )
        with self._business_checkin_scope() as db_session:
            captured_at = utc_now()
            raw = db_session.execute(statement, {
                "character_id": character_id, "business_id": business_id,
                "baseline_id": baseline_id, "comparison_id": comparison_id,
            }).mappings().all()
            if not raw:
                raise BusinessCheckInComparisonUnavailable(requested_ids)
            character = checkin_character_from_storage(raw[0])
            rows = tuple(checkin_from_storage(row) for row in raw if row["row_present"])
            if any(row.character_id != character.id or row.business_id != business_id
                   or row.id not in requested_ids for row in rows):
                raise BusinessCheckInDataError()
            by_id = {row.id: row for row in rows}
            unavailable_ids = tuple(checkin_id for checkin_id in requested_ids if checkin_id not in by_id)
            if unavailable_ids:
                raise BusinessCheckInComparisonUnavailable(unavailable_ids)
            comparison = BusinessCheckInComparison(
                character.id, character.name, business_id,
                by_id[baseline_id], by_id[comparison_id], captured_at,
            )
        return comparison

    @staticmethod
    @contextmanager
    def _business_checkin_history_predicate(db_session, filters):
        """Install the exact UTC/literal predicate only for this checked-out connection."""
        if filters is None:
            yield
            return
        sql_connection = db_session.connection()
        connection = sql_connection.connection.driver_connection
        try:
            connection.create_function(
                "checkin_history_match", 4,
                lambda *values: checkin_history_match_from_storage(*values, filters),
            )
            try:
                yield
            finally:
                # Remove the callback (and its captured filters) before the
                # session returns its connection to the pool, even after errors.
                connection.create_function("checkin_history_match", 4, None)
        except sqlite3.Error:
            # A failed unregister must never leave a filter closure on a pooled
            # connection. Discard the physical connection on lifecycle errors.
            sql_connection.invalidate()
            raise BusinessCheckInUnavailable() from None

    @contextmanager
    def _business_pin_scope(self):
        """Use a fresh scoped session and expose only safe storage failures."""
        try:
            with self._session_scope() as db_session:
                yield db_session
        except (DatabaseError, SQLAlchemyError, UnicodeError, OverflowError, OSError):
            raise BusinessPinUnavailable() from None

    @staticmethod
    def _read_manual_business_pins(db_session, character_id: int) -> ManualBusinessPins:
        """One bounded statement captures both the owner and current pin set."""
        captured_at = utc_now()
        records = db_session.execute(_BUSINESS_PIN_QUERY, {
            "character_id": character_id, "maximum": MAX_BUSINESS_PINS + 1,
        }).mappings().all()
        return manual_business_pins_from_storage(records, character_id, captured_at)

    def get_manual_business_pins(self, character_id: int) -> ManualBusinessPins:
        """Read detached preferences without creating rows or changing the owner."""
        validate_pin_character_id(character_id)
        with self._business_pin_scope() as db_session:
            snapshot = self._read_manual_business_pins(db_session, character_id)
        return snapshot

    def set_manual_business_pin(
        self, character_id: int, business_id: str, pinned: bool,
    ) -> ManualBusinessPins:
        """Commit one desired state against the freshly locked set, never replace it.

        The writer lock precedes owner/set reads. Distinct concurrent changes
        merge; repeated desired states are no-ops; opposite changes follow
        SQLite's transaction order. Legacy connections need not enforce FKs.
        """
        validate_pin_character_id(character_id)
        validate_pin_desired_state(pinned)
        validate_pin_business_id(business_id, for_pin=pinned)
        with self._business_pin_scope() as db_session:
            db_session.execute(text("BEGIN IMMEDIATE"))
            snapshot = self._read_manual_business_pins(db_session, character_id)
            ids = set(snapshot.business_ids)
            if pinned != (business_id in ids):
                if pinned:
                    if len(ids) >= MAX_BUSINESS_PINS:
                        raise BusinessPinLimitError()
                    result = db_session.execute(text(
                        "INSERT INTO manual_business_pins (character_id,business_id) "
                        "VALUES (:character_id,:business_id)"
                    ), {"character_id": character_id, "business_id": business_id})
                    ids.add(business_id)
                else:
                    result = db_session.execute(text(
                        "DELETE FROM manual_business_pins "
                        "WHERE character_id = :character_id AND business_id = :business_id"
                    ), {"character_id": character_id, "business_id": business_id})
                    ids.remove(business_id)
                if result.rowcount != 1:
                    raise BusinessPinDataError()
                snapshot = self._read_manual_business_pins(db_session, character_id)
                if set(snapshot.business_ids) != ids:
                    raise BusinessPinDataError()
        return snapshot

    # Character operations

    def create_saved_character(self, name: str) -> SavedCharacterResult:
        """Commit one inactive saved character or reuse one exact existing name.

        Validation precedes initialization. The SQLite writer lock precedes all
        lookups, serializing cooperating manual and legacy creators. Existing
        rows retain every field; old duplicate names are explicitly ambiguous.
        This does not activate a character, create a session, or alter settings.
        Minimal legacy schemas may be read but are never migrated for insertion.
        """
        name = normalize_character_name(name)
        columns = (
            "CASE WHEN typeof(c.id) = 'integer' THEN c.id END AS id, "
            + _checkin_text_column("c", "name", MAX_CHARACTER_NAME_BYTES)
        )
        try:
            with self._session_scope() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                matches = session.execute(text(
                    f"SELECT {columns} FROM characters c "
                    "WHERE c.name = :name COLLATE BINARY LIMIT 2"
                ), {"name": name}).mappings().all()
                if len(matches) > 1:
                    raise CharacterProfileAmbiguous()
                if matches:
                    saved = saved_character_from_storage(matches[0], created=False)
                else:
                    count = session.execute(text(
                        "SELECT count(*) FROM (SELECT 1 FROM characters LIMIT :maximum)"
                    ), {"maximum": MAX_CHECKIN_CHARACTERS}).scalar_one()
                    if count >= MAX_CHECKIN_CHARACTERS:
                        raise CharacterProfileLimitError()
                    character = Character(name=name, is_active=False)
                    session.add(character)
                    session.flush()
                    row = session.execute(text(
                        f"SELECT {columns} FROM characters c WHERE c.id = :id"
                    ), {"id": character.id}).mappings().first()
                    if row is None:
                        raise CharacterProfileUnavailable()
                    saved = saved_character_from_storage(row, created=True)
                if saved.name != name:
                    raise CharacterProfileUnavailable()
        except (DatabaseError, SQLAlchemyError, UnicodeError, OverflowError, OSError):
            raise CharacterProfileUnavailable() from None
        return saved

    def get_or_create_character(self, name: str) -> Optional[Character]:
        """Get existing character or create new one.

        Args:
            name: Character name

        Returns:
            Character instance or None on error

        Existing-name lookups briefly acquire SQLite's writer lock so this
        legacy entry point cannot race manual saved-character creation. Its
        permissive names, first-match reuse and default-active insertion remain.
        """
        try:
            with self._session_scope() as session:
                session.execute(text("BEGIN IMMEDIATE"))
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

    @staticmethod
    def _annotation_rows(db_session, session_ids):
        """Read bounded raw annotation values without eager text/date decoding."""
        table = SessionAnnotationRecord.__table__
        # SQLite's dynamic typing permits TEXT (even malformed UTF-8) in an
        # INTEGER column. Never ask the driver to decode a corrupt revision.
        columns = [table.c.session_id, case(
            (func.typeof(table.c.revision) == "integer", table.c.revision),
            else_=None,
        ).label("revision")]
        for name in ("label", "tags_text", "note", "updated_at"):
            column = table.c[name]
            columns.extend([
                cast(column, LargeBinary).label(name),
                func.typeof(column).label(f"{name}_type"),
            ])
        return db_session.execute(
            select(*columns).where(table.c.session_id.in_(session_ids))
        ).mappings().all()

    @classmethod
    def _read_annotation(cls, db_session, session_id):
        rows = cls._annotation_rows(db_session, [session_id])
        return annotation_from_storage(rows[0]) if rows else None

    @staticmethod
    def _require_annotation_target(db_session, session_id):
        # Avoid a Python-to-SQLite binding overflow for an unavailable large ID.
        if session_id >= 2**63 or db_session.execute(
            select(Session.id).join(Character, Session.character_id == Character.id)
            .where(Session.id == session_id, Session.ended_at.isnot(None))
        ).first() is None:
            raise SessionAnnotationUnavailable()

    def get_session_annotation(self, session_id: int) -> SessionAnnotation | None:
        """Read saved context for a completed session, distinguishing corruption from absence."""
        validate_annotation_id(session_id)
        with self._session_scope() as db_session:
            self._require_annotation_target(db_session, session_id)
            return self._read_annotation(db_session, session_id)

    def save_session_annotation(
        self, session_id: int, label: str, tags: tuple[str, ...] | list[str],
        note: str, expected_revision: int,
    ) -> SessionAnnotation:
        """Commit an annotation only if its saved revision still matches the editor.

        Revision zero means genuinely absent. Clearing retains a row/revision so
        an older editor cannot recreate context over a newer clear. An immediate
        SQLite transaction protects target validation and serializes first saves;
        existing saves also use a revision-conditioned UPDATE. No retry overwrites
        another editor, and no value is returned before commit has succeeded.
        """
        validate_annotation_id(session_id)
        validate_annotation_revision(expected_revision)
        label, tags, note = normalize_annotation(label, tags, note)
        with self._session_scope() as db_session:
            db_session.execute(text("BEGIN IMMEDIATE"))
            self._require_annotation_target(db_session, session_id)
            current = self._read_annotation(db_session, session_id)
            if expected_revision != (current.revision if current is not None else 0):
                raise SessionAnnotationConflict(current)
            if current is not None and (label, tags, note) == (current.label, current.tags, current.note):
                saved = current
            else:
                if expected_revision >= 2**63 - 1:
                    raise DatabaseError("Saved session notes have reached the revision limit.")
                values = dict(label=label, tags_text=", ".join(tags), note=note,
                              revision=expected_revision + 1, updated_at=utc_now())
                table = SessionAnnotationRecord.__table__
                if current is None:
                    db_session.execute(table.insert().values(session_id=session_id, **values))
                else:
                    result = db_session.execute(table.update().where(
                        table.c.session_id == session_id, table.c.revision == expected_revision,
                    ).values(**values))
                    if result.rowcount != 1:
                        raise SessionAnnotationConflict(self._read_annotation(db_session, session_id))
                saved = self._read_annotation(db_session, session_id)
        return saved

    def get_completed_session_history(
        self, character_id: Optional[int] = None, limit: int = 50, offset: int = 0,
        *, annotation_query: Optional[str] = None,
    ) -> SessionHistoryPage:
        """Read completed sessions in stable newest-start, newest-ID order.

        ``character_id=None`` includes all characters. ``total`` counts matching
        sessions before applying the page bounds. Missing legacy start times
        sort last and have no duration; stored money and timestamps are returned
        unchanged. Activity counts include every activity belonging to a session.
        Annotation search is a literal substring across saved label, displayed
        tags, and note. ASCII letters match case-insensitively; other Unicode
        letters match exactly, following the activity-ledger SQLite convention.

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
        annotation_query = normalize_annotation_query(annotation_query)

        with self._session_scope() as db_session:
            query = (
                db_session.query(Session, Character.name)
                .join(Character, Session.character_id == Character.id)
                .filter(Session.ended_at.isnot(None))
            )
            if character_id is not None:
                query = query.filter(Session.character_id == character_id)
            if annotation_query is not None:
                query = query.filter(select(SessionAnnotationRecord.session_id).where(
                    SessionAnnotationRecord.session_id == Session.id,
                    or_(
                        SessionAnnotationRecord.label.icontains(annotation_query, autoescape=True),
                        SessionAnnotationRecord.tags_text.icontains(annotation_query, autoescape=True),
                        SessionAnnotationRecord.note.icontains(annotation_query, autoescape=True),
                    ),
                ).exists())
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
            annotation_summaries = {}
            for annotation_row in self._annotation_rows(db_session, [row[0].id for row in rows]):
                try:
                    annotation = annotation_from_storage(annotation_row)
                    summary = dict(annotation_label=annotation.label,
                                   annotation_tags=annotation.tags, annotation_status="saved")
                except InvalidSessionAnnotation:
                    summary = dict(annotation_status="unavailable")
                annotation_summaries[annotation_row["session_id"]] = summary
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
                    **annotation_summaries.get(record.id, {}),
                )
                for record, character_name, activities_count in rows
            ]
            return SessionHistoryPage(sessions=items, total=total)

    def get_completed_activity_ledger(
        self, filters: ActivityLedgerFilters | None = None, *, limit: int = 25, offset: int = 0
    ) -> ActivityLedgerPage:
        """Read a bounded page of completed-session activities in one SELECT.

        Count and page use the same predicates and statement observation. The
        count anchor survives an empty or out-of-range page. Only the page's
        payload is selected; no full matching payload is materialized in Python
        or a shared CTE. Counting/filtering may still scan the database.

        Stored timestamps follow the existing naive UTC convention. The primary
        time is completion, falling back to recorded start; missing times sort
        last. Separate calls do not retain a multi-page snapshot.

        Raises ValueError for invalid inputs before accessing storage, and
        DatabaseError on initialization or query failure instead of an empty page.
        """
        filters = validate_ledger_request(filters, limit=limit, offset=offset)
        joined, predicates, activity_time, outcome = _completed_activity_selection(filters)

        count = (
            select(func.count(Activity.id).label("total"))
            .select_from(joined).where(*predicates).subquery("ledger_count")
        )
        page = (
            select(
                Activity.id.label("id"), Activity.session_id.label("session_id"),
                Character.id.label("character_id"), Character.name.label("character_name"),
                Activity.activity_type.label("activity_type"), Activity.activity_name.label("name"),
                Activity.business_type.label("business_type"), Activity.notes.label("notes"),
                Activity.started_at.label("recorded_start"), Activity.ended_at.label("completed_at"),
                activity_time.label("activity_time"), Activity.earnings.label("recorded_amount"),
                Activity.duration_seconds.label("duration_seconds"), outcome.label("outcome"),
            )
            .select_from(joined).where(*predicates)
            .order_by(activity_time.desc(), Activity.id.desc())
            .limit(limit).offset(offset).subquery("ledger_page")
        )
        statement = (
            select(count.c.total, *page.c)
            .select_from(count.outerjoin(page, true()))
            .order_by(page.c.activity_time.desc(), page.c.id.desc())
        )
        with self._session_scope() as db_session:
            records = db_session.execute(statement).mappings().all()
            rows = []
            for record in records:
                if record["id"] is None:
                    continue
                values = {key: record[key] for key in page.c.keys()}
                values["recorded_amount"] = finite_number(values["recorded_amount"])
                values["duration_seconds"] = finite_number(values["duration_seconds"])
                rows.append(ActivityLedgerRow(**values))
            return ActivityLedgerPage(
                filters=filters, rows=tuple(rows), total=records[0]["total"],
                offset=offset, limit=limit, observed_at=utc_now(),
            )

    def get_completed_activity_insights(
        self, filters: ActivityLedgerFilters | None = None,
    ) -> ActivityInsights:
        """Summarize all matching completed activities within fixed safety limits.

        One narrow SELECT projects bounded type bytes, session ID, classified
        outcome and real numeric storage only. Its cursor remains open during
        exact aggregation; no full activity objects or earnings events are read.
        Requests beyond the source/type limits fail without a partial report.
        """
        filters = validate_ledger_request(filters)
        joined, predicates, _, outcome = _completed_activity_selection(filters)
        stored_type = case(
            (func.typeof(Activity.activity_type) == "null", None),
            (func.typeof(Activity.activity_type) == "text",
             func.coalesce(
                 func.substr(cast(Activity.activity_type, LargeBinary), 1,
                             MAX_ACTIVITY_TYPE_BYTES + 1), b"",
             )),
            else_=0,  # Nontext storage marker, distinct from null and text bytes.
        )

        def numeric(column):
            # Do not transfer malformed text/blobs or coerce them through SUM.
            return case((func.typeof(column).in_(("integer", "real")), column), else_=None)

        statement = (
            select(
                stored_type.label("activity_type"), Activity.session_id.label("session_id"),
                outcome.label("outcome"), numeric(Activity.earnings).label("recorded_amount"),
                numeric(Activity.duration_seconds).label("duration_seconds"),
            )
            .select_from(joined).where(*predicates).limit(MAX_INSIGHT_RECORDS + 1)
        )
        with self._session_scope() as db_session:
            # A Core cursor avoids ORM entity materialization and eager row buffering.
            records = db_session.connection().execute(statement).mappings()
            try:
                return build_activity_insights(records, filters)
            finally:
                records.close()

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

                payload = {
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
                try:
                    annotation = self._read_annotation(db_session, session_id)
                    if annotation is not None:
                        payload["annotation"] = annotation.to_dict()
                except InvalidSessionAnnotation:
                    payload["annotation"] = {"available": False, "error_code": "invalid_annotation"}
                return payload
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
