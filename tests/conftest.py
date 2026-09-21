"""
Shared pytest fixtures for all tests.

Database strategy
-----------------
Tests use an in-memory SQLite database via aiosqlite.  Each test function
gets a fresh engine (and therefore a completely empty database), so tests
are fully isolated from each other.

SQLite vs PostgreSQL differences handled here:
  - FOR UPDATE is a no-op in SQLite's aiosqlite dialect (silently ignored)
  - All other queries are identical

The `client` fixture provides an httpx.AsyncClient wired to the FastAPI app
with the `get_db` dependency overridden to use the test engine.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_db
from app.main import app

# In-memory SQLite — a fresh database per test function
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    """Create a clean in-memory SQLite engine and schema for one test."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_engine):
    """
    httpx.AsyncClient bound to the FastAPI app, with the database
    dependency overridden to use the test's in-memory SQLite engine.

    Each HTTP request gets its own session so commit/rollback semantics
    inside the route handlers work exactly as in production.
    """
    AsyncTestSession = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db():
        async with AsyncTestSession() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
