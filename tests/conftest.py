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

Integration tests (--integration flag)
---------------------------------------
Pass --integration to run tests against a live PostgreSQL database.
These tests use the pg_engine / pg_client fixtures defined below.
Requires DATABASE_URL in the environment (or the default Docker Compose URL).
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# pytest CLI option — --integration
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests against a live PostgreSQL database",
    )


@pytest.fixture(scope="session")
def integration(request):
    """True when --integration is passed on the command line."""
    return request.config.getoption("--integration")

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


# ---------------------------------------------------------------------------
# PostgreSQL fixtures — only used by tests/test_integration.py
# ---------------------------------------------------------------------------

_PG_DEFAULT_URL = "postgresql+asyncpg://audit:audit_pass@localhost:5432/audit_log"


@pytest_asyncio.fixture
async def pg_engine(integration):
    """
    Async engine connected to a real PostgreSQL database.

    Drops and recreates the entire schema at the start of each test so
    every integration test starts from a clean slate.  Drops again on
    teardown.

    IMPORTANT: this destroys all data in the target database — use only
    against the Docker Compose test database, never production.

    Automatically skipped unless --integration is passed.

    NullPool fix for streaming-export teardown hang
    ------------------------------------------------
    Streaming export tests (StreamingResponse + async generator) leave an
    implicit read transaction open on the asyncpg connection.  With a
    connection pool, that connection is returned to the pool on
    session.close() and the ROLLBACK is sent lazily.  If the next step is
    drop_all (needs ACCESS EXCLUSIVE), it blocks on the ACCESS SHARE lock
    held by the pending transaction — infinite hang.

    NullPool eliminates this entirely: every connection is closed
    immediately when the session is released (no pool to return to), so
    the ROLLBACK is sent synchronously and the lock is gone before
    teardown even starts.
    """
    if not integration:
        pytest.skip("requires --integration flag")

    pg_url = os.environ.get("DATABASE_URL", _PG_DEFAULT_URL)
    engine = create_async_engine(
        pg_url,
        echo=False,
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_engine):
    """
    httpx.AsyncClient wired to the FastAPI app using the live PostgreSQL engine.

    Mirrors the SQLite `client` fixture — only the underlying engine differs.
    """
    AsyncPGSession = async_sessionmaker(
        bind=pg_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db():
        async with AsyncPGSession() as session:
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
