"""Batches router (Story 2.2): create or append a send batch.

``POST /api/batches`` is the ONLY endpoint — the WS snapshot is the read
path; this story deliberately ships no REST reads.

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body (architecture mandate). Any authenticated role may send
(the owner sends exactly like a client, AC 5 — their batches are flagged
``is_owner_priority`` for Story 2.4's scheduler).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.broadcaster import broadcaster
from app.core.telegram import gateway
from app.db.base import get_session
from app.db.models import User
from app.db.repos import batches as batches_repo
from app.db.repos import gates as gates_repo
from app.errors import empty_batch, gate_not_found, telegram_unauthorized
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

    # Resolve the gate from the catalog — active only (retired and unknown
    # look the same, 404). Out-of-int4 ids can't exist (2.1 review lesson).
    if not 0 < body.gate_id <= _PG_INT_MAX:
        raise gate_not_found()
    gate = await gates_repo.get_by_id(session, body.gate_id)
    if gate is None or gate.deleted_at is not None:
        raise gate_not_found()

    # FOR UPDATE: serialize the append against the worker's
    # complete_if_drained (which locks the same row) — without it, an append
    # racing the last line's drain commits lines onto a just-'completed'
    # batch and they never send (next_queued_line joins state='sending').
    live = await batches_repo.get_live_batch(
        session, user.tenant_id, for_update=True
    )

    if live is None:
        # --- New batch -----------------------------------------------------
        lines = batches_service.apply_gate(body.text, gate.value)
        if not lines:
            raise empty_batch()
        batch = await batches_repo.create_batch(
            session,
            tenant_id=user.tenant_id,
            gate_value=gate.value,
            gate_name=gate.name,
            is_owner_priority=user.role == "owner",
        )
        await batches_repo.add_lines(
            session, batch=batch, texts=lines, start_position=0
        )
        await session.commit()
        await broadcaster.emit(
            user.tenant_id, "batch.state", {"state": "sending"}
        )
        progress = await batches_service.progress_data(session, batch)
        await broadcaster.emit(user.tenant_id, "batch.progress", progress)
        return BatchOut(
            id=batch.id,
            gate_name=batch.gate_name,
            gate_value=batch.gate_value,
            state=batch.state,
            sent=0,
            queued=len(lines),
            total=len(lines),
            appended=False,
            added=len(lines),
        )

    # --- Append to the live batch (AC 10) ----------------------------------
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
    await broadcaster.emit(user.tenant_id, "batch.progress", progress)
    return BatchOut(
        id=live.id,
        gate_name=live.gate_name,
        gate_value=live.gate_value,
        state=live.state,
        sent=progress["sent"],
        queued=progress["queued"],
        total=progress["total"],
        appended=True,
        added=len(new_lines),
    )
