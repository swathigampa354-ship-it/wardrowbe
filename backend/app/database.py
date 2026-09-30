"""Async SQLAlchemy setup that runs on both SQLite and PostgreSQL.

The engine is created lazily (not at import time) on purpose: a module-level
engine would freeze ``STORAGE_DIR``/``DATABASE_URL`` at the first import of the
process, which makes the app untestable and breaks hosts that set the
environment after import.

The trial creates its schema with ``create_all`` on startup instead of Alembic:
there are no migrations to preserve once the family/learning/studio/schedule
tables are gone.
"""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings

logger = logging.getLogger(__name__)


def _settings():
    return get_settings()


def normalize_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgresql+psycopg2://"):
        raw = raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return raw


def resolve_url() -> str:
    settings = _settings()
    if settings.uses_sqlite:
        if settings.database_url:
            return normalize_url(settings.database_url)
        return f"sqlite+aiosqlite:///{Path(settings.storage_dir) / 'wardrowbe.db'}"
    return normalize_url(settings.database_url)


_engine: AsyncEngine | None = None
_engine_url: str | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """The engine, rebuilt if the configured URL changed under us."""
    global _engine, _engine_url, _session_maker
    url = resolve_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            # Deliberately not awaited: rebuilds only happen in tests and on
            # first-run configuration; the old engine is disposed at shutdown.
            logger.info("Rebuilding database engine for a changed DATABASE_URL")
        if url.startswith("sqlite"):
            # SQLite + several uvicorn workers: no pool, no cross-process
            # connection reuse, so each request opens what it needs.
            _engine = create_async_engine(url, poolclass=NullPool, echo=_settings().database_echo)
        else:
            _engine = create_async_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                echo=_settings().database_echo,
            )
        _engine_url = url
        _session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_maker is not None
    return _session_maker


@event.listens_for(Engine, "connect", named=True)
def _sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - driver hook
    """WAL + a busy timeout, and only for SQLite files.

    Bulk uploads are concurrent by design (AI_UPLOAD_CONCURRENCY) and
    store_and_analyze() keeps its write transaction open across the AI call, so
    N simultaneous uploads means N overlapping writers - which the default
    rollback journal turns into `sqlite3.OperationalError: database is locked`.
    WAL lets readers through during a write and busy_timeout makes competing
    writers wait instead of failing (api/items.py additionally serialises
    writes, since SQLite could not run them in parallel anyway). Render Free
    ships no disk for a WAL sidecar to live on between restarts, so this is a
    within-process guarantee, not a cross-process one.
    """
    try:
        cur = dbapi_connection.cursor()
    except Exception:  # pragma: no cover - non-DBAPI connection
        return
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
    except Exception:  # pragma: no cover - e.g. in-memory or non-sqlite driver
        pass
    finally:
        cur.close()


def reset_engine() -> None:
    """Drop the cached engine (tests, or config reload)."""
    global _engine, _engine_url, _session_maker
    _engine = None
    _engine_url = None
    _session_maker = None


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    from app import models  # noqa: F401  (register mappers before create_all)

    engine = get_engine()
    url = _engine_url or ""
    if url.startswith("sqlite"):
        Path(url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database ready (%s)", "sqlite" if url.startswith("sqlite") else "postgres")


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """A session per request, committed on success and rolled back on error.

    No writer lock lives here on purpose: a request's transaction would then be
    held across response serialization, which blocks the background analyses a
    handler may have just spawned for longer than the handler itself takes.
    Endpoints that write take app/deps.py:writer_lock() around the block that
    actually touches the database instead.
    """

    async with get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


DbSession = Annotated[AsyncSession, Depends(get_db)]
