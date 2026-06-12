"""Background send worker (Story 2.2) — port of legacy ``Engine._worker``.

A single ``asyncio.Task`` created in the lifespan drains queued batch lines
at the system-controlled interval. Each step opens its OWN session via
``async_session_factory`` (NEVER the request-scoped one) and the Telegram
send happens with no session held (a FloodWait can sleep for minutes — it
must not pin a pool connection).

Retry policy (legacy semantics, kept deliberately):
- ``FloodWaitError`` → broadcast GLOBAL ``flood.wait`` (the UI notice is
  Story 2.3 — only the event ships now), cancelable-sleep the requested
  seconds, retry the SAME line in place.
- Any other send error → tenant-scoped ``error`` event, sleep 2s, retry the
  same line FOREVER.  # Story 2.5 replaces retry-forever with cap=3.

Sleeps are cancelable via a module wake event (legacy ``_sleep_cancelable``,
app.py:246). 2.2 itself only needs plain sleeps; Story 2.3's pause/stop will
``wake()`` to interrupt them instantly.
"""

import asyncio
import logging

from app.config import settings
from app.core.broadcaster import broadcaster
from app.core.telegram import FloodWaitError, gateway
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine
from app.db.repos import batches as batches_repo
from app.services import batches as batches_service

logger = logging.getLogger(__name__)

# How long to sleep when the queue is empty before polling again.
_IDLE_SLEEP_SECONDS = 1.0
# Delay before retrying a line after a non-FloodWait send error.
# Story 2.5 replaces retry-forever with cap=3 + a 'failed' line state.
_ERROR_RETRY_SECONDS = 2.0

# Wakes any in-flight sleep (Story 2.3's pause/stop consumes this).
_wake = asyncio.Event()


def wake() -> None:
    """Interrupt the worker's current sleep immediately."""
    _wake.set()


async def sleep_cancelable(seconds: float) -> None:
    """Sleep up to ``seconds``, returning early when ``wake()`` fires."""
    if seconds <= 0:
        return
    try:
        await asyncio.wait_for(_wake.wait(), timeout=seconds)
    except TimeoutError:
        pass
    finally:
        _wake.clear()


async def step() -> bool:
    """Process at most one line. Returns True iff a line was sent.

    Factored out of the infinite loop so tests can await single steps
    deterministically (no real Telegram, no background task).
    """
    # 1. Claim the next queued line (short transaction — commit releases it).
    async with async_session_factory() as session:
        line = await batches_repo.next_queued_line(session)
        if line is None:
            return False
        await batches_repo.mark_sending(session, line)
        await session.commit()
        line_id = line.id
        batch_id = line.batch_id
        tenant_id = line.tenant_id
        position = line.position
        text = line.text

    # 2. Send — in-place retry on the SAME line, no DB session held.
    await _send_with_retries(tenant_id, text)

    # 3. Record + emit.
    async with async_session_factory() as session:
        recorded = await session.get(BatchLine, line_id)
        if recorded is None:  # batch deleted mid-send (tenant removed)
            return True
        await batches_repo.mark_sent(session, recorded)
        batch = await session.get(Batch, batch_id)
        drained = False
        progress: dict | None = None
        if batch is not None:
            drained = await batches_repo.complete_if_drained(session, batch)
            progress = await batches_service.progress_data(session, batch)
        await session.commit()

    await broadcaster.emit(
        tenant_id,
        "batch.line_sent",
        {"batch_id": batch_id, "position": position, "text": text},
    )
    if progress is not None:
        await broadcaster.emit(tenant_id, "batch.progress", progress)
    if drained:
        await broadcaster.emit(tenant_id, "batch.state", {"state": "idle"})
    return True


async def _send_with_retries(tenant_id: int, text: str) -> None:
    """Deliver ``text``, retrying the same line until it goes out (AC 7)."""
    while True:
        try:
            await gateway.send(text)
            return
        except FloodWaitError as e:
            # Architecture: every FloodWait is explained to everyone (global).
            await broadcaster.emit_global("flood.wait", {"seconds": e.seconds})
            await sleep_cancelable(float(e.seconds))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await broadcaster.emit(
                tenant_id, "error", {"code": "send_error", "message": str(e)}
            )
            # Story 2.5 replaces retry-forever with cap=3 + 'failed' state.
            await sleep_cancelable(_ERROR_RETRY_SECONDS)


async def run_worker() -> None:
    """Infinite drain loop (created as a task in the lifespan)."""
    # Boot recovery (NFR6): re-queue lines a crash left in 'sending' and
    # resume draining any batch still in state 'sending'. A small double-send
    # window is accepted until Story 2.5's reconciliation.
    try:
        async with async_session_factory() as session:
            requeued = await batches_repo.requeue_stuck_sending(session)
            await session.commit()
        if requeued:
            logger.info("boot recovery: requeued %d stuck line(s)", requeued)
    except Exception:
        logger.exception("boot recovery failed — continuing")

    while True:
        try:
            sent = await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            # DB unreachable or any unexpected error: log and retry. The
            # fail-stop buffering design is Story 2.5 — a plain
            # log/sleep/retry is enough here.
            logger.exception("send worker step failed — retrying")
            await sleep_cancelable(_ERROR_RETRY_SECONDS)
            continue
        if sent:
            # System-controlled interval between sends (FR12).
            await sleep_cancelable(settings.send_interval_seconds)
        else:
            await sleep_cancelable(_IDLE_SLEEP_SECONDS)
