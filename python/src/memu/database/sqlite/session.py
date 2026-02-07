"""SQLite session manager for database connections."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, create_engine

logger = logging.getLogger(__name__)


class SQLiteSessionManager:
    """Handle engine lifecycle and session creation for SQLite store."""

    def __init__(self, *, dsn: str, engine_kwargs: dict[str, Any] | None = None) -> None:
        """Initialize SQLite session manager.

        Args:
            dsn: SQLite connection string (e.g., "sqlite:///path/to/db.sqlite").
            engine_kwargs: Optional keyword arguments for create_engine.
        """
        # Ensure parent directory exists and the DB file is touchable.
        # This removes a common source of intermittent "unable to open database file" errors.
        try:
            url = make_url(dsn)
            db_path = url.database
            if db_path and db_path != ":memory:":
                path = Path(db_path)
                if path.parent:
                    path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(exist_ok=True)
        except Exception:
            logger.debug("Failed to preflight sqlite path for dsn=%s", dsn, exc_info=True)

        kw: dict[str, Any] = {
            # Allow multi-threaded access
            "connect_args": {
                "check_same_thread": False,
                # Wait a bit for locks instead of failing fast
                "timeout": 30,
            },
            # Avoid cross-thread/process connection reuse issues
            "poolclass": NullPool,
        }
        if engine_kwargs:
            kw.update(engine_kwargs)

        self._engine = create_engine(dsn, **kw)

        @event.listens_for(self._engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:  # noqa: ANN401
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.execute("PRAGMA busy_timeout=5000;")
                cursor.close()
            except Exception:
                # Best-effort; don't fail connect if a pragma isn't supported.
                logger.debug("Failed to set sqlite pragmas", exc_info=True)

    def session(self) -> Session:
        """Create a new database session."""
        return Session(self._engine, expire_on_commit=False)

    def close(self) -> None:
        """Close the database engine and release resources."""
        try:
            self._engine.dispose()
        except SQLAlchemyError:
            logger.exception("Failed to close SQLite engine")

    @property
    def engine(self) -> Any:
        """Return the underlying SQLAlchemy engine."""
        return self._engine


__all__ = ["SQLiteSessionManager"]
