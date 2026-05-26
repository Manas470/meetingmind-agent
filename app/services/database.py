"""
Async SQLAlchemy engine + session factory.
SQLite in dev/test, PostgreSQL via asyncpg in production.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.models.db_models import Base

# Module-level singletons (set during lifespan startup)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


async def init_db() -> None:
    """Create engine + tables. Called once on app startup."""
    global _engine, _session_factory

    settings = get_settings()
    db_url = settings.get_db_url()

    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    _engine = create_async_engine(
        db_url,
        echo=(settings.app_env == "development"),
        connect_args=connect_args,
    )
    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session per request."""
    async with get_session() as session:
        yield session
