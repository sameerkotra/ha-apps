"""Database connection and session management."""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("db")

# Database engine and session factory
_engine = None
_session_factory = None


def get_engine():
    """Get or create the database engine."""
    global _engine, _session_factory

    if _engine is None:
        settings = get_settings()

        # Ensure directory exists
        db_path = settings.database_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # Create engine with SQLite-specific settings
        # No pool_pre_ping: a SQLite file has no connection to go stale, and the ping cost a query per checkout.
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=settings.log_level == "DEBUG",
        )

        # Enable WAL mode and foreign keys
        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        _session_factory = sessionmaker(
            bind=_engine,
            autoflush=False,
            autocommit=False,
            class_=Session,
        )

        logger.info("Database initialized: %s", db_path)

    return _engine


def apply_log_level(level: str) -> None:
    """SQL statements are logged at the Debug log level; a change in App settings applies straight away."""
    if _engine is not None:
        _engine.echo = level == "DEBUG"


def dispose_engine() -> None:
    """Close all pooled connections and forget the engine (the next use opens a fresh one).

    Used when the database file is replaced by an import.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db_session():
    """Get the session factory."""
    get_engine()  # Ensure engine is created
    return _session_factory


def get_db():
    """
    Database session dependency for FastAPI (a plain generator: FastAPI opens and closes it on its thread pool, never
    on the event loop).

    Note: The caller is responsible for commit/rollback.

    Usage:
        @app.get("/endpoint")
        def endpoint(db: Session = Depends(get_db)):
            # do work
            db.commit()
            return result
    """
    session_factory = get_db_session()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


# Alias for session factory (used by __init__.py exports)

# session_local is an alias for the session factory
session_local = get_db_session
