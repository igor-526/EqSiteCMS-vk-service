from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from settings import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_database() -> None:
    await engine.dispose()


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """Create DB resources owned by one synchronous worker task event loop."""
    task_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    task_session_factory = async_sessionmaker(task_engine, expire_on_commit=False, autoflush=False)
    try:
        async with task_session_factory() as session:
            yield session
    finally:
        await task_engine.dispose()
