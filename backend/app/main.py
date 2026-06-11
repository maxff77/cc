"""FastAPI application factory.

Lifespan only initializes/disposes the DB engine. Telethon arrives in Epic 2 —
do NOT add it here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.db.base import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Dispose the async engine on shutdown (engine connects lazily)."""
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(title="cc-backend", lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
