from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.db.session import Base, engine
from app.models import audit_entry as _models  # noqa: F401 — registers models with Base.metadata


# ---------------------------------------------------------------------------
# Lifespan — runs on startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (Alembic handles this in prod;
    # this is a convenience fallback for a clean dev environment)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Dispose engine connections cleanly on shutdown
    await engine.dispose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from app.api.routes.events import router as events_router
from app.api.routes.export import router as export_router
from app.api.routes.redaction import router as redaction_router
from app.api.routes.verify import router as verify_router

app.include_router(events_router,   prefix="/audit", tags=["events"])
app.include_router(verify_router,   prefix="/audit", tags=["verify"])
app.include_router(redaction_router, prefix="/audit", tags=["redaction"])
app.include_router(export_router,   prefix="/audit", tags=["export"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": settings.app_version}
