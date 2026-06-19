"""Batches router (Stories 2.2 + 2.3 + 4.2): create/append a batch + controls.

``POST /api/batches`` creates or appends; ``POST /api/batches/{id}/pause|
resume|stop`` (2.3) are the non-CRUD verb-suffix actions (architecture:
POST + 204, no body). The WS snapshot remains the only read path.

Admission control (Story 4.2): when the owner-configured cap is full, a new
batch is created ``'waiting'`` (FIFO queue, durable in Postgres) instead of
``'sending'`` — the POST response and the ``batch.state`` event carry its
``queue_position``. The decision happens under the cap row's FOR UPDATE lock
so it serializes with the worker's promotion sweep.

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body (architecture mandate). Any authenticated role may send
(owner and admins send exactly like a client, AC 5 — their batches carry a
``priority`` tier — owner 2 > admin 1 > client 0 — for Story 2.4's scheduler).
The controls act on the caller's own batch only: another tenant's id 404s
(2.3 AC 1).
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
from app.db.repos import gate_categories as gate_categories_repo
from app.db.repos import gates as gates_repo
from app.db.repos import plans as plans_repo
from app.db.repos import tenants as tenants_repo
from app.errors import (
    batch_line_limit,
    batch_not_found,
    batch_not_live,
    batch_stopping,
    batch_waiting,
    empty_batch,
    gate_not_found,
    insufficient_credits,
    sending_paused,
    telegram_unauthorized,
)
from app.services import admission as admission_service
from app.services import batches as batches_service

router = APIRouter(prefix="/api/batches", tags=["batches"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg

# Scheduler priority tier by role (owner > admin > client). Unknown roles
# fall to client priority — never silently above a real client.
_PRIORITY_BY_ROLE = {"owner": 2, "admin": 1, "client": 0}


async def _plan_line_cap(session: AsyncSession, plan_id: int | None) -> int | None:
    """The client's plan ``max_lines_per_batch`` cap, or ``None`` for no cap.

    Resolved from ``plan_id`` (plan-catalog feature). ``plan_id IS NULL`` —
    including every owner/admin "house" tenant and any pre-catalog client —
    means NO cap (unchanged behavior). A dangling plan_id (RESTRICT FK makes
    this impossible in practice) also yields no cap rather than a 500.

    Takes a plain ``plan_id`` (NOT the ORM ``user``) on purpose: the append
    path runs AFTER a ``session.rollback()`` that expires the session-loaded
    ``user``, so reading ``user.plan_id`` there would lazy-refresh synchronously
    (MissingGreenlet). The caller captures ``plan_id`` once before any rollback.
    """
    if plan_id is None:
        return None
    plan = await plans_repo.get_by_id(session, plan_id)
    return plan.max_lines_per_batch if plan is not None else None


# --- Schemas (inline, codebase convention) ---------------------------------


class CreateBatchRequest(BaseModel):
    text: str
    gate_id: int


class BatchOut(BaseModel):
    # Shape consumed by the UI to flip into live mode without waiting for WS.
    # Client-facing: carries the visible "Comando visible", NEVER the real value.
    id: int
    gate_name: str
    gate_display_value: str
    state: str
    sent: int
    queued: int
    failed: int  # lines the retry cap gave up on (Story 2.5; 0 on a new batch)
    total: int
    appended: bool
    added: int
    # FIFO admission position (Story 4.2): the POST response IS the first
    # position report — None unless state == 'waiting'.
    queue_position: int | None


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
    # synchronously (MissingGreenlet). ``plan_id`` is captured here too because
    # the append path reads it AFTER the create-path rollback (the plan line
    # cap is resolved from this scalar, never from the expired ``user``).
    tenant_id = user.tenant_id
    priority = _PRIORITY_BY_ROLE.get(user.role, 0)
    plan_id = user.plan_id

    # Resolve the gate from the catalog — active only (retired and unknown
    # look the same, 404). Out-of-int4 ids can't exist (2.1 review lesson).
    if not 0 < body.gate_id <= _PG_INT_MAX:
        raise gate_not_found()
    gate = await gates_repo.get_by_id(session, body.gate_id)
    if gate is None or gate.deleted_at is not None:
        raise gate_not_found()
    # gate_value is the REAL command (engine prepends + sends it);
    # gate_display_value is the client-visible "Comando visible" snapshot.
    gate_value, gate_name = gate.value, gate.name
    gate_display_value = gate.display_value
    # Per-✅ credit cost of this gate (credits feature): snapshotted onto the
    # new batch below so re-pricing never re-charges it, and used by the
    # insufficient_credits guard. 0 ⇒ a free gate (no charge, no balance gate).
    gate_credit_cost = gate.credit_cost
    # Special-mode flag of the gate's category (special-mode feature):
    # snapshotted onto the capture session below so the capture pipeline parses
    # THIS batch's replies in special mode. Loaded explicitly — the gate's
    # ``category`` relation is not eager-loaded here (lazy access would raise
    # MissingGreenlet); default False if the category vanished in a race.
    gate_category = await gate_categories_repo.get_by_id(session, gate.category_id)
    special_mode = gate_category.special_mode if gate_category is not None else False
    # Cookie-mode flag of the gate's category (cookie-vault feature): snapshotted
    # onto the capture session below so Phase-2's per-account cookie sending knows
    # this session is a cookie-mode session. Same load/default-False stance as
    # ``special_mode`` (no reader yet — the snapshot WRITE path ships now).
    cookie_mode = gate_category.cookie_mode if gate_category is not None else False
    # Snapshot of the gate's catalog id for the cookie-rotation key (Phase 2):
    # the active cookie is picked by ``(tenant_id, gate_id)``. Snapshotted onto
    # the batch (no FK) so a retired+recreated value never mis-keys cookies
    # across gate generations — NEVER re-resolved from ``gate_value`` at send
    # time. NULL for non-cookie-mode batches (the worker never reads it there).
    batch_gate_id = gate.id if cookie_mode else None

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
        # Plan line cap (plan-catalog feature): a fresh batch starts at 0 lines,
        # so the cap applies to this paste directly. Over the cap → 400
        # batch_line_limit and NOTHING is queued (checked before create_batch).
        # plan_id NULL → no cap (unchanged behavior).
        cap = await _plan_line_cap(session, plan_id)
        if cap is not None and len(lines) > cap:
            raise batch_line_limit(cap=cap, attempted=len(lines))
        # Credit gate (credits feature): a costed gate (credit_cost > 0) needs a
        # positive balance to START a batch. Free gates (cost 0) are always
        # allowed — the day-plan is untouched. ONLY clients are metered
        # (``priority == 0`` — captured before any rollback): owner/admin "house"
        # tenants never receive a plan grant and would otherwise be locked out of
        # a costed gate they're testing. The capture charge still clamps their
        # balance at 0 harmlessly. Balance read fresh from the DB (never off the
        # session-loaded ``user``: it may be expired by a prior rollback).
        # Nothing is queued when blocked.
        if gate_credit_cost > 0 and priority == 0:
            balance = await tenants_repo.get_credit_balance(session, tenant_id)
            if balance <= 0:
                raise insufficient_credits(gate_name=gate_name)
        try:
            # Admission decision (Story 4.2, AC 1/4) under the cap row's FOR
            # UPDATE lock — serialized against concurrent POSTs and the
            # worker's promotion sweep, so the cap is never overshot. A
            # disabled cap (0/missing row) admits directly: pure Epic 2
            # adaptive-interval semantics.
            cap = await admission_service.get_cap_locked(session)
            admitted = await batches_repo.count_admitted(session)
            state = (
                batches_repo.STATE_SENDING
                if admission_service.has_capacity(cap, admitted)
                else batches_repo.STATE_WAITING
            )
            batch = await batches_repo.create_batch(
                session,
                tenant_id=tenant_id,
                gate_value=gate_value,
                gate_name=gate_name,
                gate_display_value=gate_display_value,
                gate_credit_cost=gate_credit_cost,
                priority=priority,
                state=state,
                gate_id=batch_gate_id,
            )
            created_lines = await batches_repo.add_lines(
                session, batch=batch, texts=lines, start_position=0
            )
            # Capture-session binding (Story 3.1, AC 3) in the SAME
            # transaction: reuse the tenant's active session when its gate
            # matches, otherwise activate a fresh one — the batch commit IS
            # the "bound automatically at batch start". A WAITING batch binds
            # at creation too (recorded decision): the panels flip right
            # away, and old replies attribute via send_log → line → batch,
            # never via the active session.
            capture_session = await capture_sessions_repo.resolve_for_batch(
                session, tenant_id, gate_value, gate_name, gate_display_value,
                special_mode, cookie_mode,
            )
            batch.capture_session_id = capture_session.id
            # Computed inside the transaction (post-flush id) so the POST
            # response and the event report the same position.
            position = (
                await batches_repo.queue_position(session, batch.id)
                if state == batches_repo.STATE_WAITING
                else None
            )
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
            # Surface state mirrors the admission outcome: 'waiting' carries
            # its queue position (AC 2), 'sending' keeps the 2.2 shape.
            await broadcaster.emit(
                tenant_id,
                "batch.state",
                batches_service.state_data(
                    batch, state, queue_position=position
                ),
            )
            progress = await batches_service.progress_data(session, batch)
            await broadcaster.emit(tenant_id, "batch.progress", progress)
            # Seed the cockpit's "Pendientes" list with this batch's lines —
            # it then drains via batch.line_sent/line_failed (Pendientes UX).
            await broadcaster.emit(
                tenant_id,
                "batch.lines_queued",
                batches_service.lines_queued_data(batch.id, created_lines),
            )
            return BatchOut(
                id=batch.id,
                gate_name=batch.gate_name,
                gate_display_value=batch.gate_display_value,
                state=batch.state,
                sent=0,
                queued=len(lines),
                failed=0,
                total=len(lines),
                appended=False,
                added=len(lines),
                queue_position=position,
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
    # Plan line cap (plan-catalog feature): enforced against the RESULTING batch
    # size so a client can't bypass the cap by appending in chunks. Already-sent
    # lines plus still-pending lines count toward it (failed lines are terminal
    # and don't); over the cap → 400 batch_line_limit and NOTHING is queued.
    # plan_id NULL → no cap (unchanged behavior). ``plan_id`` was captured
    # before the create-path rollback (never re-read off the expired ``user``).
    cap = await _plan_line_cap(session, plan_id)
    if cap is not None and new_lines:
        sent, queued, _failed = await batches_repo.counts(session, live.id)
        attempted = sent + queued + len(new_lines)
        if attempted > cap:
            raise batch_line_limit(cap=cap, attempted=attempted)
    # Credit gate on append (credits feature): a costed live batch needs a
    # positive balance to accept MORE lines — mirrors the create guard so a
    # client can't keep feeding a costed gate at balance 0. Clients only
    # (``priority == 0``, like create); owner/admin are never metered. Only when
    # there are NEW lines (a zero-new append is a harmless no-op). The live batch
    # carries the cost snapshot taken at its creation.
    if live.gate_credit_cost > 0 and new_lines and priority == 0:
        balance = await tenants_repo.get_credit_balance(session, tenant_id)
        if balance <= 0:
            raise insufficient_credits(gate_name=live.gate_name)
    appended_lines: list = []
    if new_lines:
        start = await batches_repo.next_position(session, live.id)
        appended_lines = await batches_repo.add_lines(
            session, batch=live, texts=new_lines, start_position=start
        )
    await session.commit()
    progress = await batches_service.progress_data(session, live)
    await broadcaster.emit(tenant_id, "batch.progress", progress)
    # Append grows the live "Pendientes" list (zero NEW lines after dedup
    # emits nothing — keeps the event honest).
    if appended_lines:
        await broadcaster.emit(
            tenant_id,
            "batch.lines_queued",
            batches_service.lines_queued_data(live.id, appended_lines),
        )
    # A WAITING batch accepts appends (the lines queue up and wait with it);
    # the response keeps reporting its admission position (Story 4.2).
    position = (
        await batches_repo.queue_position(session, live.id)
        if live.state == batches_repo.STATE_WAITING
        else None
    )
    return BatchOut(
        id=live.id,
        gate_name=live.gate_name,
        gate_display_value=live.gate_display_value,
        state=live.state,
        sent=progress["sent"],
        queued=progress["queued"],
        failed=progress["failed"],
        total=progress["total"],
        appended=True,
        added=len(new_lines),
        queue_position=position,
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
    """sending → paused. Idempotent on 'paused' (two tabs): 204, no event.

    A manual pause landing DURING a cookie-mode verdict-await (Phase 2) must
    tear the serialize gate down: with the await left armed, resume would not
    re-send (the line is ``LINE_SENT``) and the batch would silently stall until
    the 90s timeout swept it. So when the paused batch has a live await
    (``awaiting_message_id is not None``), clear the await AND re-queue the
    awaited line (resolved via the attempt-fence) in the SAME FOR UPDATE txn, so
    resume re-sends it fresh. Normal (non-cookie) pause is unchanged.
    """
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_PAUSED:
        return  # no-op — no duplicate event
    if batch.state == batches_repo.STATE_STOPPING:
        raise batch_stopping()
    if batch.state == batches_repo.STATE_WAITING:
        # Nothing to pause yet (Story 4.2) — batch_not_live would lie.
        raise batch_waiting()
    if batch.state != batches_repo.STATE_SENDING:
        raise batch_not_live()
    if batch.awaiting_message_id is not None:
        # Cookie-mode mid-await pause: re-queue the awaited line FIRST (the
        # fence needs the await fields), THEN clear the await — both in this txn
        # under the lock ``_controlled_batch`` holds, so resume re-sends fresh.
        await batches_repo.requeue_failed_cookie_line(session, batch)
        await batches_repo.clear_awaiting_verdict(session, batch)
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
    """paused → sending. Idempotent on 'sending': 204, no event.

    A cookie-mode pause (``pause_reason='cookies_exhausted'``/``'verdict_timeout'``,
    Phase 2) resumes through the SAME endpoint — but its resume is ONE FOR UPDATE
    transaction (the lock is already held by ``_controlled_batch``) that flips
    ``state=sending``, clears every await field + the ``pause_reason``, AND
    re-queues the failed line. It must NOT split across commits: a half-applied
    resume (state flipped but the stale ``awaiting_verdict_until`` still set, or
    the in-flight line still ``LINE_SENDING``) would have the just-resumed batch
    instantly skipped by the serialize gate until the stale timeout elapses.
    """
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_SENDING:
        return  # no-op — no duplicate event
    if batch.state == batches_repo.STATE_STOPPING:
        raise batch_stopping()
    if batch.state == batches_repo.STATE_WAITING:
        # Resuming a queued batch would bypass admission (Story 4.2).
        raise batch_waiting()
    if batch.state != batches_repo.STATE_PAUSED:
        raise batch_not_live()
    # Cookie-mode pause (Phase 2): tear down the serialize gate + re-queue the
    # failed line in the SAME txn as the state flip, so the previously-failed
    # line is the very next thing the worker sends (a stale future
    # ``awaiting_verdict_until`` must not skip it post-resume).
    cookie_pause = batch.pause_reason is not None
    if cookie_pause:
        # Re-queue the awaited line FIRST — ``requeue_failed_cookie_line``
        # resolves it via the attempt-fence (the await fields), so it must run
        # BEFORE ``clear_awaiting_verdict`` NULLs them. A no-op when the await is
        # already cleared (the ``cookies_exhausted`` path re-queued before pause).
        await batches_repo.requeue_failed_cookie_line(session, batch)
        await batches_repo.clear_awaiting_verdict(session, batch)
        await batches_repo.set_pause_reason(session, batch, None)
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
    """sending|paused|waiting → stopped (or stopping while a line is in flight).

    Detener acts instantly — no confirmation anywhere (AC 4). Inside ONE
    transaction and IN THIS ORDER: clear the queue first (a DELETE racing the
    worker's claim blocks on the disputed row and skips it if it landed in
    'sending'), THEN check for an in-flight line.

    A WAITING batch (Story 4.2) stops through the direct branch (it never has
    a line in flight) — the client leaves the admission queue, and everyone
    behind shifts one place forward: no slot was freed, no promotion happens,
    so the handler reports the new positions itself (the worker's sweep only
    emits when it promotes).
    """
    batch = await _controlled_batch(session, user.tenant_id, batch_id)
    if batch.state == batches_repo.STATE_STOPPING:
        return  # no-op: the worker is already finishing this stop
    if batch.state not in (
        batches_repo.STATE_SENDING,
        batches_repo.STATE_PAUSED,
        batches_repo.STATE_WAITING,
    ):
        raise batch_not_live()
    was_waiting = batch.state == batches_repo.STATE_WAITING
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
        repositioned: list[tuple[int, dict]] = []
        if was_waiting:
            # Only the waiters BEHIND the leaver shifted (smaller ids keep
            # their place). Payloads built inside the session (MissingGreenlet
            # lesson); the autoflushed UPDATE drops the leaver from the list.
            for i, waiter in enumerate(
                await batches_repo.waiting_batches(session), start=1
            ):
                if waiter.id > batch.id:
                    repositioned.append(
                        (
                            waiter.tenant_id,
                            batches_service.state_data(
                                waiter, "waiting", queue_position=i
                            ),
                        )
                    )
        await session.commit()
        await broadcaster.emit(
            user.tenant_id, "batch.state", batches_service.state_data(batch, "idle")
        )
        for waiter_tenant_id, payload in repositioned:
            await broadcaster.emit(waiter_tenant_id, "batch.state", payload)
        # A direct stop of an ADMITTED batch frees a slot right now — cut the
        # worker's idle sleep so the promotion sweep runs immediately (AC 3).
        # Harmless after a waiting-batch stop (the sweep finds nothing new).
        send_worker.wake()
