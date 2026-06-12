"""Batches router (Stories 2.2 + 2.3): create/append a batch + its controls.

``POST /api/batches`` creates or appends; ``POST /api/batches/{id}/pause|
resume|stop`` (2.3) are the non-CRUD verb-suffix actions (architecture:
POST + 204, no body). The WS snapshot remains the only read path.

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body (architecture mandate). Any authenticated role may send
(the owner sends exactly like a client, AC 5 — their batches are flagged
``is_owner_priority`` for Story 2.4's scheduler). The controls act on the
caller's own batch only: another tenant's id 404s (2.3 AC 1).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.core.telegram import gateway
from app.core.watchdog import watchdog
from app.db.base import get_session
from app.db.models import Batch, User
from app.db.repos import batches as batches_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import gates as gates_repo
from app.errors import (
    batch_not_found,
    batch_not_live,
    batch_stopping,
    empty_batch,
    gate_not_found,
    sending_paused,
    telegram_unauthorized,
)
from app.services import batches as batches_service

router = APIRouter(prefix="/api/batches", tags=["batches"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg


# --- Schemas (inline, codebase convention) ---------------------------------


class CreateBatchRequest(BaseModel):
    text: str
    gate_id: int


class BatchOut(BaseModel):
    # Shape consumed by the UI to flip into live mode without waiting for WS.
    id: int
    gate_name: str
    gate_value: str
    state: str
    sent: int
    queued: int
    failed: int  # lines the retry cap gave up on (Story 2.5; 0 on a new batch)
    total: int
    appended: bool
    added: int


@router.post("", response_model=BatchOut, status_code=201)
async def create_or_append_batch(
    body: CreateBatchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BatchOut:
    """Start a new batch, or APPEND to the tenant's live one (AC 3, 10).

    Append semantics (recorded decision): the submitted ``gate_id`` is
    validated for existence but its value IGNORED — new lines take the LIVE
    batch's gate (one lote = one gate; the UI locks the selector during a
    live lote). Dedup is against pending lines only: already-SENT texts may
    be re-queued (legacy ``/api/enviar`` semantics).
    """
    if not gateway.authorized or not gateway.target_ok:
        raise telegram_unauthorized()
    # Watchdog global pause (Story 4.1): create AND append are rejected while
    # the latch holds — queuing lines that will not send invites confusion;
    # the WS banner explains the state and only the owner can resume.
    if watchdog.is_paused:
        raise sending_paused()

    # Captured BEFORE any rollback: a rollback expires the session-loaded
    # ``user`` object and a later attribute access would lazy-refresh
    # synchronously (MissingGreenlet).
    tenant_id, is_owner = user.tenant_id, user.role == "owner"

    # Resolve the gate from the catalog — active only (retired and unknown
    # look the same, 404). Out-of-int4 ids can't exist (2.1 review lesson).
    if not 0 < body.gate_id <= _PG_INT_MAX:
        raise gate_not_found()
    gate = await gates_repo.get_by_id(session, body.gate_id)
    if gate is None or gate.deleted_at is not None:
        raise gate_not_found()
    gate_value, gate_name = gate.value, gate.name

    # FOR UPDATE: serialize the append against the worker's
    # complete_if_drained (which locks the same row) — without it, an append
    # racing the last line's drain commits lines onto a just-'completed'
    # batch and they never send (the worker's selection joins state='sending').
    live = await batches_repo.get_live_batch(session, tenant_id, for_update=True)

    if live is None:
        # --- New batch -----------------------------------------------------
        lines = batches_service.apply_gate(body.text, gate_value)
        if not lines:
            raise empty_batch()
        try:
            batch = await batches_repo.create_batch(
                session,
                tenant_id=tenant_id,
                gate_value=gate_value,
                gate_name=gate_name,
                is_owner_priority=is_owner,
            )
            await batches_repo.add_lines(
                session, batch=batch, texts=lines, start_position=0
            )
            # Capture-session binding (Story 3.1, AC 3) in the SAME
            # transaction: reuse the tenant's active session when its gate
            # matches, otherwise activate a fresh one — the batch commit IS
            # the "bound automatically at batch start".
            capture_session = await capture_sessions_repo.resolve_for_batch(
                session, tenant_id, gate_value, gate_name
            )
            batch.capture_session_id = capture_session.id
            await session.commit()
        except IntegrityError:
            # Two tabs raced past the live check (TOCTOU): the partial unique
            # index uq_batches_one_live_per_tenant rejected the second batch.
            # Re-read and fall through to the append path — never a 500.
            # (uq_capture_sessions_one_active_per_tenant can only collide in
            # this SAME race: this handler is the ONLY place that creates
            # ACTIVE sessions — the attribution backfill inserts INACTIVE
            # fallbacks (review 3-1) — so this rollback covers batch AND
            # session alike.)
            await session.rollback()
            live = await batches_repo.get_live_batch(
                session, tenant_id, for_update=True
            )
            if live is None:  # not the one-live-batch conflict — surface it
                raise
        else:
            await broadcaster.emit(
                tenant_id,
                "batch.state",
                batches_service.state_data(batch, "sending"),
            )
            progress = await batches_service.progress_data(session, batch)
            await broadcaster.emit(tenant_id, "batch.progress", progress)
            return BatchOut(
                id=batch.id,
                gate_name=batch.gate_name,
                gate_value=batch.gate_value,
                state=batch.state,
                sent=0,
                queued=len(lines),
                failed=0,
                total=len(lines),
                appended=False,
                added=len(lines),
            )

    # --- Append to the live batch (AC 10) ----------------------------------
    if live.state == batches_repo.STATE_STOPPING:
        # The queue was just cleared; appended lines would orphan on a batch
        # about to land 'stopped'. Append during 'paused' IS allowed (legacy
        # _lote_vivo semantics) — it emits progress and never touches state.
        raise batch_stopping()
    lines = batches_service.apply_gate(body.text, live.gate_value)
    if not lines:
        # Whitespace-only paste is an error even on append (AC 4) …
        raise empty_batch()
    pending = await batches_repo.pending_texts(session, live.id)
    new_lines = [line for line in lines if line not in pending]
    # … but zero NEW lines after dedup is NOT an error (added: 0).
    if new_lines:
        start = await batches_repo.next_position(session, live.id)
        await batches_repo.add_lines(
            session, batch=live, texts=new_lines, start_position=start
        )
    await session.commit()
    progress = await batches_service.progress_data(session, live)
    await broadcaster.emit(tenant_id, "batch.progress", progress)
    return BatchOut(
        id=live.id,
        gate_name=live.gate_name,
        gate_value=live.gate_value,
        state=live.state,
        sent=progress["sent"],
        queued=progress["queued"],
        failed=progress["failed"],
        total=progress["total"],
        appended=True,
        added=len(new_lines),
    )


# --- Batch controls (Story 2.3): pause | resume | stop ----------------------


async def _controlled_batch(
    session: AsyncSession, tenant_id: int, batch_id: int
) -> Batch:
    """Locked, tenant-scoped lookup shared by the three control endpoints.

    FOR UPDATE serializes the state transition against the worker's
    finalization paths and against the other control endpoints (two tabs).
    Unknown id, another tenant's id and out-of-int4 id all 404 alike.
    """
    if not 0 < batch_id <= _PG_INT_MAX:
        raise batch_not_found()
    batch = await batches_repo.get_batch(
        session, tenant_id, batch_id, for_update=True
    )
    if batch is None:
        raise batch_not_found()
    return batch


@router.post("/{batch_id}/pause", status_code=204)
async def pause_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """sending → paused. Idempotent on 'paused' (two tabs): 204, no event."""
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_PAUSED:
        return  # no-op — no duplicate event
    if batch.state == batches_repo.STATE_STOPPING:
        raise batch_stopping()
    if batch.state != batches_repo.STATE_SENDING:
        raise batch_not_live()
    batch.state = batches_repo.STATE_PAUSED
    await session.commit()
    await broadcaster.emit(
        user.tenant_id, "batch.state", batches_service.state_data(batch, "paused")
    )
    send_worker.wake()  # cut a mid-interval sleep instantly (AC 3)


@router.post("/{batch_id}/resume", status_code=204)
async def resume_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """paused → sending. Idempotent on 'sending': 204, no event."""
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_SENDING:
        return  # no-op — no duplicate event
    if batch.state == batches_repo.STATE_STOPPING:
        raise batch_stopping()
    if batch.state != batches_repo.STATE_PAUSED:
        raise batch_not_live()
    batch.state = batches_repo.STATE_SENDING
    await session.commit()
    await broadcaster.emit(
        user.tenant_id, "batch.state", batches_service.state_data(batch, "sending")
    )
    # Legacy semantics: pause→resume may retry before a FloodWait window
    # elapses — a waiting worker re-checks state right now.
    send_worker.wake()


@router.post("/{batch_id}/stop", status_code=204)
async def stop_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """sending|paused → stopped (or stopping while a line is in flight).

    Detener acts instantly — no confirmation anywhere (AC 4). Inside ONE
    transaction and IN THIS ORDER: clear the queue first (a DELETE racing the
    worker's claim blocks on the disputed row and skips it if it landed in
    'sending'), THEN check for an in-flight line.
    """
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_STOPPING:
        return  # no-op: the worker is already finishing this stop
    if batch.state not in (
        batches_repo.STATE_SENDING,
        batches_repo.STATE_PAUSED,
    ):
        raise batch_not_live()
    await batches_repo.delete_queued_lines(session, batch.id)
    if await batches_repo.has_sending_line(session, batch.id):
        batch.state = batches_repo.STATE_STOPPING
        await session.commit()
        await broadcaster.emit(
            user.tenant_id,
            "batch.state",
            batches_service.state_data(batch, "stopping"),
        )
        send_worker.wake()  # the worker abandons the line and finalizes
    else:
        batch.state = batches_repo.STATE_STOPPED
        await session.commit()
        await broadcaster.emit(
            user.tenant_id, "batch.state", batches_service.state_data(batch, "idle")
        )
