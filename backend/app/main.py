"""FastAPI application factory.

Lifespan owns the long-lived pieces: the Telethon gateway (connected at
startup — Story 2.2; failures are non-fatal, the app boots without Telegram),
the single background send-worker task, and the capture consumer task (Story
3.1 — the bridge callback is installed BEFORE connect so no early reply is
lost). Shutdown cancels both tasks, disconnects Telegram and disposes the DB
engine.

Tests use httpx ``ASGITransport``, which does NOT run the lifespan — the app
object stays importable and testable with no Telegram/worker running (capture
tests call ``capture.process_incoming`` directly, the ``step()`` idiom).
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
from app.api.cookies import router as cookies_router
from app.api.gates import router as gates_router
from app.api.health import router as health_router
from app.api.history import router as history_router
from app.api.keys import admin_router as keys_admin_router
from app.api.keys import client_router as keys_client_router
from app.api.observability import router as observability_router
from app.api.public import router as public_router
from app.api.sessions import router as sessions_router
from app.api.targets import router as targets_router
from app.api.watchdog import router as watchdog_router
from app.api.ws import router as ws_router
from app.core import capture
from app.core.reconciler import run_reconciler
from app.core.send_worker import run_worker
from app.core.telegram import gateway
from app.core.watchdog import watchdog
from app.db.base import async_session_factory, engine
from app.errors import AppError
from app.services import pacing as pacing_service
from app.services import targets as targets_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect Telegram + start the send worker and the capture consumer;
    tear everything down on shutdown."""
    gateway.register_capture(capture.enqueue)  # BEFORE connect — no lost reply
    # Hold the capture consumer until the worker's boot recovery confirms the
    # message ids a crash left unconfirmed — catch_up replays buffer in the
    # capture queue meanwhile (review 3-1).
    capture.hold_until_boot()
    await gateway.connect()  # never raises — unauthorized just means 503s
    # Load the send destinations (multi-target sending): seed the first one from
    # the legacy TELEGRAM_TARGET env on a fresh DB, then resolve the enabled
    # list into the gateway. Non-fatal — an unauthorized gateway resolves none
    # and sending stays 503 until re-auth (same as before). MUST run before the
    # worker starts so the first send has a target.
    async with async_session_factory() as boot_db:
        await targets_service.ensure_seeded(boot_db)
        await targets_service.reload_gateway(boot_db)
        # Restore the owner-configured send interval into the scheduler floor
        # so a restart preserves the cadence (no-op when unset → env default).
        await pacing_service.apply_persisted(boot_db)
    # Restore a persisted watchdog latch BEFORE the worker can claim anything
    # (Story 4.1, AC 3: a deploy/restart never resumes sending on its own).
    await watchdog.load_persisted()
    worker_task = asyncio.create_task(run_worker())
    capture_task = asyncio.create_task(capture.run_capture())
    # Reply reconciler: a periodic safety net that recovers bot replies the
    # live Telethon update stream dropped (catch_up gaps, missed edits) by
    # re-reading chat history into the SAME idempotent capture path.
    reconciler_task = asyncio.create_task(run_reconciler())
    yield
    worker_task.cancel()
    capture_task.cancel()
    reconciler_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
    with contextlib.suppress(asyncio.CancelledError):
        await capture_task
    with contextlib.suppress(asyncio.CancelledError):
        await reconciler_task
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
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(keys_admin_router)
    app.include_router(keys_client_router)
    app.include_router(gates_router)
    app.include_router(batches_router)
    app.include_router(cookies_router)
    app.include_router(sessions_router)
    app.include_router(history_router)
    app.include_router(targets_router)
    app.include_router(watchdog_router)
    app.include_router(observability_router)
    app.include_router(ws_router)
    return app


app = create_app()
