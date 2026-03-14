from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from rift.core.config import settings
import logging

log = logging.getLogger("rift.db")


class Base(DeclarativeBase):
    pass


_engine: Optional[AsyncEngine] = None
_factory: Optional[async_sessionmaker] = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=settings.DEBUG,
            connect_args={"server_settings": {"application_name": "rift", "timezone": "UTC"}},
        )
    return _engine


def session_factory() -> async_sessionmaker:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(
            bind=engine(), class_=AsyncSession,
            expire_on_commit=False, autocommit=False, autoflush=False,
        )
    return _factory


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with db_session() as session:
        yield session


async def ping() -> bool:
    try:
        async with engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.error(f"DB ping failed: {e}")
        return False


async def close():
    global _engine, _factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _factory = None