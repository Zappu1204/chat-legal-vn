"""Async database engine and session factory.

A single ``AsyncEngine`` is created per process and shared. Request handlers
acquire short-lived sessions via the :func:`get_session` dependency. The
engine is created lazily so importing this module does not require a live
database (important for Alembic offline mode and unit tests that swap in
their own engine).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.settings import get_web_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_web_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


def reset_engine_for_tests(engine: AsyncEngine) -> None:
    """Swap the global engine/sessionmaker — used only by the test harness."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
