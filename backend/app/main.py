"""FastAPI application factory.

Lifespan owns the long-lived pieces: the Telethon gateway (connected at
startup — Story 2.2; failures are non-fatal, the app boots without Telegram)
and the single background send-worker task. Shutdown cancels the worker,
disconnects Telegram and disposes the DB engine.

Tests use httpx ``ASGITransport``, which does NOT run the lifespan — the app
object stays importable and testable with no Telegram/worker running.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.batches import router as batches_router
from app.api.gates import router as gates_router
from app.api.health import router as health_router
from app.api.ws import router as ws_router
from app.core.send_worker import run_worker
from app.core.telegram import gateway
from app.db.base import engine
from app.errors import AppError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Telegram + start the send worker; tear both down on shutdown."""
    await gateway.connect()  # never raises — unauthorized just means 503s
    worker_task = asyncio.create_task(run_worker())
    yield
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
    await gateway.disconnect()
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
    app.include_router(batches_router)
    app.include_router(ws_router)
    return app


app = create_app()
