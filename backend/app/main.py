"""FastAPI application factory.

Lifespan only initializes/disposes the DB engine. Telethon arrives in Epic 2 —
do NOT add it here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.gates import router as gates_router
from app.api.health import router as health_router
from app.db.base import engine
from app.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Dispose the async engine on shutdown (engine connects lazily)."""
    yield
    await engine.dispose()


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Render a domain error as ``{code, message}`` with its HTTP status.

    The single mapping every later story reuses (the project error contract).
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(title="cc-backend", lifespan=lifespan)
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(gates_router)
    return app


app = create_app()
