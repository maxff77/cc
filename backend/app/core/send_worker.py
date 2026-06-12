"""Background send worker (Story 2.2; pause/stop-aware since Story 2.3).

A single ``asyncio.Task`` created in the lifespan drains queued batch lines
at the system-controlled interval. Each step opens its OWN session via
``async_session_factory`` (NEVER the request-scoped one) and the Telegram
send happens with no session held (a FloodWait can sleep for minutes — it
must not pin a pool connection).

Retry policy (legacy semantics, kept deliberately):
- ``FloodWaitError`` → broadcast GLOBAL ``flood.wait``, cancelable-sleep the
  requested seconds, retry the SAME line in place.
- Any other send error → tenant-scoped ``error`` event, sleep 2s, retry the
  same line FOREVER.  # Story 2.5 replaces retry-forever with cap=3.

Pause/stop (Story 2.3): the batch state is re-read at the top of every retry
iteration — pause RELEASES the claimed line back to the queue (a single
worker serving every tenant must not stall on one paused batch) and stop
ABORTS it (the queue was already cleared by the handler). The release/abort
pair is the 2.3 design: Story 2.5 introduces ``cancelled`` + send_log and
Story 2.4 replaces the FIFO selection — none of that is built here.

Sleeps are cancelable via the module wake event: pause/resume/stop call
``wake()`` so a mid-interval or mid-FloodWait sleep is interrupted instantly.
"""

import asyncio
import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

# Wakes any in-flight sleep (pause/resume/stop interrupt instantly).
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


async def _locked_batch(session: AsyncSession, batch_id: int) -> Batch | None:
    """SELECT … FOR UPDATE on the batch row (fresh session ⇒ fresh attrs).

    Serializes the worker's finalization branches with the stop/pause
    handlers, which lock the same row for their whole transaction.
    """
    return (
        await session.execute(
            select(Batch).where(Batch.id == batch_id).with_for_update()
        )
    ).scalar_one_or_none()


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

    # 2. Send — in-place retry on the SAME line, no DB session held. The
    #    state re-check inside may yield to a pause (release) or stop (abort).
    result = await _send_with_retries(tenant_id, batch_id, text)
    if result == "release":
        await _release_line(tenant_id, batch_id, line_id)
        return False
    if result == "abort":
        await _abort_line(tenant_id, batch_id, line_id)
        return False

    # 3. Record + emit ("sent").
    state_payload: dict | None = None
    progress: dict | None = None
    async with async_session_factory() as session:
        recorded = await session.get(BatchLine, line_id)
        if recorded is None:  # batch deleted mid-send (tenant removed)
            return True
        await batches_repo.mark_sent(session, recorded)
        batch = await _locked_batch(session, batch_id)
        if batch is not None:
            if batch.state == batches_repo.STATE_STOPPING:
                # The stop landed while gateway.send was in flight and the
                # line DID go out: record it honestly ('sent') and finalize
                # 'stopped' — NOT complete_if_drained, which would mark the
                # batch 'completed' (drained ≠ detenido, Epic 3 history).
                batch.state = batches_repo.STATE_STOPPED
                state_payload = batches_service.state_data(batch, "idle")
            else:
                drained = await batches_repo.complete_if_drained(session, batch)
                progress = await batches_service.progress_data(session, batch)
                if drained:
                    state_payload = batches_service.state_data(batch, "idle")
        await session.commit()

    await broadcaster.emit(
        tenant_id,
        "batch.line_sent",
        {"batch_id": batch_id, "position": position, "text": text},
    )
    if progress is not None:
        await broadcaster.emit(tenant_id, "batch.progress", progress)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)
    return True


async def _send_with_retries(
    tenant_id: int, batch_id: int, text: str
) -> Literal["sent", "release", "abort"]:
    """Deliver ``text`` — or yield to a pause/stop that landed meanwhile.

    The batch state is re-read at the TOP of every iteration (including the
    first, and after every ``wake()``-interrupted sleep):
    - 'sending' → attempt the send (FloodWait / generic-error retries kept);
    - 'paused'  → "release" (give the line back — don't hold it);
    - 'stopping'/'stopped'/gone → "abort".
    """
    while True:
        async with async_session_factory() as session:
            state = await batches_repo.get_batch_state(session, batch_id)
        if state == batches_repo.STATE_PAUSED:
            return "release"
        if state != batches_repo.STATE_SENDING:
            return "abort"
        try:
            await gateway.send(text)
            return "sent"
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


async def _release_line(tenant_id: int, batch_id: int, line_id: int) -> None:
    """Pause release: hand the claimed line back to the queue intact.

    Resuming re-claims it immediately — same net effect as the legacy
    "pause→resume may retry before the FloodWait window elapses".

    The batch row is locked FIRST so this serializes with a concurrent stop
    (which holds the same lock while clearing the queue): if the stop already
    committed 'stopping', re-queueing the line would strand the batch in
    'stopping' forever (its lines are invisible to next_queued_line) — in
    that case the line is abandoned and the batch finalized instead.
    """
    state_payload: dict | None = None
    async with async_session_factory() as session:
        batch = await _locked_batch(session, batch_id)
        if batch is not None and batch.state == batches_repo.STATE_STOPPING:
            await batches_repo.delete_line(session, line_id)
            batch.state = batches_repo.STATE_STOPPED
            state_payload = batches_service.state_data(batch, "idle")
        else:
            line = await session.get(BatchLine, line_id)
            if line is not None:
                await batches_repo.mark_queued(session, line)
        await session.commit()
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _abort_line(tenant_id: int, batch_id: int, line_id: int) -> None:
    """Stop abort: discard the never-sent line and finalize the batch.

    The queue was already cleared by the stop handler; the abandoned line is
    deleted (it never went out). 'stopping' → 'stopped' + terminal idle event.
    """
    state_payload: dict | None = None
    async with async_session_factory() as session:
        batch = await _locked_batch(session, batch_id)
        await batches_repo.delete_line(session, line_id)
        if batch is not None and batch.state == batches_repo.STATE_STOPPING:
            batch.state = batches_repo.STATE_STOPPED
            state_payload = batches_service.state_data(batch, "idle")
        await session.commit()
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _boot_recovery() -> None:
    """Heal state a restart abandoned (NFR6 + 2.3 review). Never raises.

    IN THIS ORDER, one transaction:
    1. Finalize batches stranded in 'stopping': the only 'stopping' →
       'stopped' transitions are this worker's in-process paths, so a restart
       (or a step() crash after the claim commit, healed at the next restart)
       while a stop was in flight would otherwise leave the batch live-but-
       undrainable forever — its tenant permanently 409-blocked
       (``get_live_batch`` keeps returning it; the partial unique index
       blocks any new batch). Pending lines are discarded like the in-process
       abort; no events (clients reconcile via the connect snapshot).
    2. Re-queue lines a crash left in 'sending' so draining resumes. Running
       AFTER step 1 means a stopping batch's abandoned line is deleted, not
       re-queued. A small double-send window is accepted until Story 2.5's
       reconciliation.
    """
    try:
        async with async_session_factory() as session:
            finalized = await batches_repo.finalize_stuck_stopping(session)
            requeued = await batches_repo.requeue_stuck_sending(session)
            await session.commit()
        if finalized:
            logger.info(
                "boot recovery: finalized %d orphaned stopping batch(es)", finalized
            )
        if requeued:
            logger.info("boot recovery: requeued %d stuck line(s)", requeued)
    except Exception:
        logger.exception("boot recovery failed — continuing")


async def run_worker() -> None:
    """Infinite drain loop (created as a task in the lifespan)."""
    await _boot_recovery()

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
