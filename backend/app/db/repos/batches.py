"""Data access for batches and batch lines (Story 2.2).

TENANT-SCOPED — this is NOT the gates/users global exception: every handler-
facing function takes ``tenant_id`` explicitly. The worker-facing queries at
the bottom run outside any request (the send worker drains ALL tenants'
queues) and are the single documented exception, marked as such.

Pure ORM, flush not commit — callers own the transaction.
"""

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch, BatchLine, Plan, User

# Lifecycle states (2.2 + 2.3 + 2.5 + 4.2) — plain strings, no DB enum (see
# the model docstring). 'cancelled' (2.5, plan expiry) is terminal and NOT
# live. 'waiting' (4.2, admission control) IS live: created over the cap,
# FIFO-queued until the worker's sweep promotes it to 'sending'.
STATE_SENDING = "sending"
STATE_COMPLETED = "completed"
STATE_PAUSED = "paused"
STATE_STOPPING = "stopping"
STATE_STOPPED = "stopped"
STATE_CANCELLED = "cancelled"
STATE_WAITING = "waiting"

# "Live" = the tenant's one in-flight batch (Story 2.3 state machine). This
# tuple IS the predicate of the partial unique index
# ``uq_batches_one_live_per_tenant`` and the append notion of POST /api/batches.
# 'cancelled' is deliberately absent: a renewed plan starts a fresh batch and
# the controls 409 ``batch_not_live`` on a cancelled one with zero extra code.
LIVE_STATES = (STATE_SENDING, STATE_PAUSED, STATE_STOPPING, STATE_WAITING)

# "Admitted" = live states that OCCUPY an admission slot (Story 4.2). A
# PAUSED batch keeps its slot on purpose (recorded decision): releasing it on
# pause would force resume through re-admission (resume → wait again) or
# overshoot the cap; the adaptive interval already excludes paused tenants
# from ``n`` (2.4), so a held slot degrades nobody — it only limits new
# admissions. Finishing, stopping or cancelling frees the slot (AC 3).
ADMITTED_STATES = (STATE_SENDING, STATE_PAUSED, STATE_STOPPING)

LINE_QUEUED = "queued"
LINE_SENDING = "sending"
LINE_SENT = "sent"
LINE_FAILED = "failed"
LINE_CANCELLED = "cancelled"

# Line states that count as "pending" (still in the queue). 'failed' and
# 'cancelled' are EXCLUDED on purpose (2.5): they never block
# ``complete_if_drained`` nor weigh on ``count_active_senders`` — a batch
# whose last line fails still completes ("the queue continues", AC 3).
_PENDING_STATES = (LINE_QUEUED, LINE_SENDING)


async def get_live_batch(
    session: AsyncSession, tenant_id: int, *, for_update: bool = False
) -> Batch | None:
    """Return the tenant's single live batch (state in LIVE_STATES), or ``None``.

    One live batch per tenant is the invariant (now DB-enforced by the partial
    unique index ``uq_batches_one_live_per_tenant``): ``POST /api/batches``
    appends to it instead of creating a second one. ``order_by(Batch.id)``
    keeps the pick deterministic if the invariant were ever broken (2.2 review).

    ``for_update=True`` locks the row until commit. The APPEND path must pass
    it so it serializes with the worker's ``complete_if_drained`` (which locks
    the same row): otherwise an append landing as the last pending line drains
    can commit its lines onto a batch that just committed 'completed' —
    ``next_queued_line_for_tenant`` joins on state='sending', so those lines
    would never send. Read-only callers (snapshot) keep the default.
    """
    stmt = (
        select(Batch)
        .where(Batch.tenant_id == tenant_id, Batch.state.in_(LIVE_STATES))
        .order_by(Batch.id)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def get_batch(
    session: AsyncSession, tenant_id: int, batch_id: int, *, for_update: bool = False
) -> Batch | None:
    """TENANT-SCOPED batch lookup for the pause/resume/stop controls.

    Another tenant's id returns ``None`` (the handler 404s — existence is
    never leaked; AC 1 "only that client's batch is affected").
    """
    stmt = select(Batch).where(Batch.id == batch_id, Batch.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def get_batch_state(session: AsyncSession, batch_id: int) -> str | None:
    """Short unlocked state read (the worker's per-iteration re-check)."""
    stmt = select(Batch.state).where(Batch.id == batch_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def delete_queued_lines(session: AsyncSession, batch_id: int) -> int:
    """Stop "clears the remaining queue": drop every still-'queued' line.

    Runs BEFORE ``has_sending_line`` inside the stop transaction (order
    matters): a DELETE racing the worker's claim blocks on the disputed row
    and skips it if it landed in 'sending' — the in-flight check that follows
    then sees it.
    """
    stmt = delete(BatchLine).where(
        BatchLine.batch_id == batch_id, BatchLine.state == LINE_QUEUED
    )
    result = await session.execute(stmt)
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount


async def has_sending_line(session: AsyncSession, batch_id: int) -> bool:
    """Is a line of this batch currently claimed by the worker ('sending')?"""
    stmt = (
        select(BatchLine.id)
        .where(BatchLine.batch_id == batch_id, BatchLine.state == LINE_SENDING)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def create_batch(
    session: AsyncSession,
    *,
    tenant_id: int,
    gate_value: str,
    gate_name: str,
    gate_display_value: str,
    priority: int,
    state: str = STATE_SENDING,
) -> Batch:
    """Insert and flush a fresh live batch (gate strings snapshotted verbatim).

    ``priority`` is the scheduler tier (0=client, 1=admin, 2=owner). ``state``
    defaults to 'sending'; the admission-controlled POST passes 'waiting' when
    the cap is full (Story 4.2). ``gate_display_value`` is the client-visible
    "Comando visible" snapshot (clients render it instead of ``gate_value``).
    """
    batch = Batch(
        tenant_id=tenant_id,
        gate_value=gate_value,
        gate_name=gate_name,
        gate_display_value=gate_display_value,
        state=state,
        priority=priority,
    )
    session.add(batch)
    await session.flush()
    return batch


async def add_lines(
    session: AsyncSession,
    *,
    batch: Batch,
    texts: list[str],
    start_position: int,
) -> list[BatchLine]:
    """Append ``texts`` as queued lines at positions ``start_position..``."""
    lines = [
        BatchLine(
            batch_id=batch.id,
            tenant_id=batch.tenant_id,
            position=start_position + i,
            text=text,
            state=LINE_QUEUED,
        )
        for i, text in enumerate(texts)
    ]
    session.add_all(lines)
    await session.flush()
    return lines


async def pending_texts(session: AsyncSession, batch_id: int) -> set[str]:
    """Texts of lines still pending (queued/sending) — the append-dedup set.

    SENT lines are deliberately absent so an already-sent text may be
    re-queued (legacy ``/api/enviar`` semantics).
    """
    stmt = select(BatchLine.text).where(
        BatchLine.batch_id == batch_id, BatchLine.state.in_(_PENDING_STATES)
    )
    return set((await session.execute(stmt)).scalars().all())


async def next_position(session: AsyncSession, batch_id: int) -> int:
    """The position the next appended line should take (max + 1, or 0)."""
    stmt = select(func.max(BatchLine.position)).where(BatchLine.batch_id == batch_id)
    max_pos = (await session.execute(stmt)).scalar_one_or_none()
    return 0 if max_pos is None else max_pos + 1


async def counts(session: AsyncSession, batch_id: int) -> tuple[int, int, int]:
    """Return ``(sent, queued, failed)`` for a batch (queued includes 'sending')."""
    stmt = (
        select(BatchLine.state, func.count())
        .where(BatchLine.batch_id == batch_id)
        .group_by(BatchLine.state)
    )
    rows = (await session.execute(stmt)).all()
    by_state: dict[str, int] = {state: count for state, count in rows}
    sent = by_state.get(LINE_SENT, 0)
    queued = by_state.get(LINE_QUEUED, 0) + by_state.get(LINE_SENDING, 0)
    failed = by_state.get(LINE_FAILED, 0)
    return sent, queued, failed


async def failed_lines(session: AsyncSession, batch_id: int) -> list[BatchLine]:
    """The batch's failed lines in position order — feeds the WS snapshot."""
    stmt = (
        select(BatchLine)
        .where(BatchLine.batch_id == batch_id, BatchLine.state == LINE_FAILED)
        .order_by(BatchLine.position)
    )
    return list((await session.execute(stmt)).scalars().all())


async def queued_lines(
    session: AsyncSession, batch_id: int, limit: int
) -> list[BatchLine]:
    """The batch's still-pending (queued/sending) lines in position order.

    Feeds the WS snapshot's "Pendientes" list — the precedent is ``failed_lines``
    (2.5), added so a reconnecting tab rebuilds a per-line panel from the
    snapshot alone. ``limit`` mirrors ``_SNAPSHOT_ROWS``: the list is capped,
    the ``queued`` count stays the authoritative total (badges never lie).
    """
    stmt = (
        select(BatchLine)
        .where(
            BatchLine.batch_id == batch_id, BatchLine.state.in_(_PENDING_STATES)
        )
        .order_by(BatchLine.position)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# --- Worker queries ----------------------------------------------------------
#
# Used ONLY by core.send_worker, never by request handlers. Deliberately
# unscoped: the single worker drains every tenant's queue. Story 2.4 replaced
# the naive global FIFO with the round-robin scheduler: the worker lists
# ``active_senders``, lets ``core.scheduler`` pick a tenant, then claims that
# tenant's oldest queued line via ``next_queued_line_for_tenant``.


class ActiveSender(NamedTuple):
    """One tenant's claim on the send rotation (Story 2.4 scheduler input).

    The partial unique index ``uq_batches_one_live_per_tenant`` guarantees
    at most one live batch per tenant, so tenant ≡ batch in the rotation.

    ``antispam_seconds`` is the tenant's per-tenant scheduler COOLDOWN
    (plan-catalog feature): the gap a tenant waits before being re-picked,
    resolved as ``coalesce(client plan.antispam_seconds, 0.0)`` — a plan_id of
    NULL means NO per-tenant cooldown (legacy behavior: the account-wide
    ``g_min`` sleep is the SOLE pacer). It rides on the struct so
    ``core.scheduler.pick_next`` stays DB-free: the cooldown only SLOWS a
    tenant on top of the global floor; it never speeds the account up.
    """

    tenant_id: int
    batch_id: int
    priority: int  # scheduler tier: 0=client, 1=admin, 2=owner
    # Per-tenant cooldown (plan or global fallback). Defaults to 0.0 ("no
    # cooldown" — always eligible, the pre-plan-catalog behavior) so callers
    # that don't resolve a plan (the scheduler's own unit tests) build the same
    # struct they always did; ``active_senders`` always sets a real value.
    antispam_seconds: float = 0.0


async def active_senders(
    session: AsyncSession, *, global_interval: float
) -> list[ActiveSender]:
    """Tenants with a 'sending' batch that has ≥1 servable ('queued') line.

    Ordered by ``tenant_id`` so the scheduler's cyclic cursor is stable.
    Paused batches fall out on their own (``state='paused'`` doesn't match) —
    the AC 2 paused-tenant exclusion needs no extra code, only the test.

    Each sender's ``antispam_seconds`` (the per-tenant cooldown) is resolved
    here as ``coalesce(plan.antispam_seconds, 0.0)`` via the tenant → client
    user → plan join — left joins so a tenant with no client row or a
    ``plan_id`` of NULL falls back to 0.0: NO per-tenant cooldown, the legacy
    behavior where the global ``g_min`` sleep alone paces the account. Only a
    tenant WITH a plan carries a positive cooldown (and the plan's antispam is
    itself floored at ≥1s on the way in). ``global_interval`` is accepted for
    caller/stub compatibility but no longer gates a no-plan tenant — the global
    floor lives in the worker's own pacing sleep, not in this per-tenant gate.
    """
    has_queued = (
        select(BatchLine.id)
        .where(BatchLine.batch_id == Batch.id, BatchLine.state == LINE_QUEUED)
        .exists()
    )
    # Resolve the cooldown via tenant → its client user → that user's plan.
    # Both joins are OUTER: owner/admin "house" tenants carry no client row,
    # and a client with plan_id NULL has no plan row — either way coalesce
    # lands on 0.0 (no per-tenant cooldown; the global g_min sleep paces them,
    # exactly as before the plan catalog). (One client per tenant — see
    # ``users_repo.get_user_by_tenant`` — so the join never multiplies rows;
    # the belt-and-braces ``role == 'client'`` predicate keeps a shared tenant
    # from joining a staff row.)
    antispam = func.coalesce(Plan.antispam_seconds, 0.0)
    stmt = (
        select(Batch.tenant_id, Batch.id, Batch.priority, antispam)
        .select_from(Batch)
        .outerjoin(
            User, (User.tenant_id == Batch.tenant_id) & (User.role == "client")
        )
        .outerjoin(Plan, Plan.id == User.plan_id)
        .where(Batch.state == STATE_SENDING, has_queued)
        .order_by(Batch.tenant_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ActiveSender(
            tenant_id=tenant_id,
            batch_id=batch_id,
            priority=priority,
            antispam_seconds=float(antispam_seconds),
        )
        for tenant_id, batch_id, priority, antispam_seconds in rows
    ]


async def next_queued_line_for_tenant(
    session: AsyncSession, tenant_id: int
) -> BatchLine | None:
    """Oldest queued line by ``(batch_id, position)`` of ONE tenant's live batch.

    ``None`` means the queue emptied between the scheduler's listing and this
    claim (a stop raced us) — the caller idles and the next loop rotates.
    """
    stmt = (
        select(BatchLine)
        .join(Batch, Batch.id == BatchLine.batch_id)
        .where(
            BatchLine.state == LINE_QUEUED,
            Batch.state == STATE_SENDING,
            Batch.tenant_id == tenant_id,
        )
        .order_by(BatchLine.batch_id, BatchLine.position)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def count_active_senders(session: AsyncSession) -> int:
    """The ``n`` of the adaptive formula: tenants actively occupying the channel.

    Deliberately broader than ``active_senders``: the selection requires a
    *servable* ('queued') line, while ``n`` counts pending ('queued' OR
    'sending') — a tenant whose only line is in flight still occupies the
    channel and must weigh on everyone's interval/ETA.
    """
    has_pending = (
        select(BatchLine.id)
        .where(
            BatchLine.batch_id == Batch.id, BatchLine.state.in_(_PENDING_STATES)
        )
        .exists()
    )
    stmt = select(func.count(func.distinct(Batch.tenant_id))).where(
        Batch.state == STATE_SENDING, has_pending
    )
    count: int = (await session.execute(stmt)).scalar_one()
    return count


# --- Admission control (Story 4.2) -------------------------------------------
#
# The FIFO waiting queue is DURABLE: rows with state='waiting' ordered by id
# (creation order — the id IS the arrival order). The partial unique index
# guarantees ≤1 live batch per tenant, so queue ≡ waiting tenants. Positions
# are COMPUTED, never stored — nothing to rebalance.


async def count_admitted(session: AsyncSession) -> int:
    """Batches currently occupying an admission slot (``ADMITTED_STATES``).

    ≤1 per tenant (partial unique index), so counting rows ≡ counting
    tenants. Callers hold the cap row's FOR UPDATE lock while deciding.
    """
    stmt = select(func.count()).where(Batch.state.in_(ADMITTED_STATES))
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def waiting_batches(
    session: AsyncSession, *, for_update: bool = False
) -> list[Batch]:
    """The FIFO waiting queue, oldest first (``ORDER BY id``).

    ``for_update=True`` (the worker's promotion sweep) locks the rows so a
    concurrent stop on a waiting batch serializes with the promotion — the
    re-evaluated predicate drops a just-stopped row after the lock waits.
    """
    stmt = select(Batch).where(Batch.state == STATE_WAITING).order_by(Batch.id)
    if for_update:
        stmt = stmt.with_for_update()
    return list((await session.execute(stmt)).scalars().all())


async def queue_position(session: AsyncSession, batch_id: int) -> int:
    """1-based FIFO position of a waiting batch (1 + older waiting rows)."""
    stmt = select(func.count()).where(
        Batch.state == STATE_WAITING, Batch.id < batch_id
    )
    ahead: int = (await session.execute(stmt)).scalar_one()
    return ahead + 1


async def count_waiting(session: AsyncSession) -> int:
    """Depth of the FIFO waiting queue (Story 4.3 observability slice)."""
    stmt = select(func.count()).where(Batch.state == STATE_WAITING)
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def mark_sending(session: AsyncSession, line: BatchLine) -> None:
    """Claim a line: state → 'sending' (flush; caller commits)."""
    line.state = LINE_SENDING
    await session.flush()


async def mark_sent(session: AsyncSession, line: BatchLine) -> None:
    """Record a delivered line: state → 'sent' + ``sent_at`` (flush)."""
    line.state = LINE_SENT
    line.sent_at = datetime.now(UTC)
    await session.flush()


async def mark_queued(session: AsyncSession, line: BatchLine) -> None:
    """Release a claimed line back to 'queued' (the pause release, 2.3)."""
    line.state = LINE_QUEUED
    await session.flush()


async def mark_failed(session: AsyncSession, line: BatchLine, code: str) -> None:
    """Record a line the retry cap gave up on: state → 'failed' + its code.

    'failed' is not pending, so the batch keeps draining (AC 3) — the line
    stays as honest history and the frontend maps ``fail_code`` to copy.
    """
    line.state = LINE_FAILED
    line.fail_code = code
    await session.flush()


async def cancel_queued_lines(session: AsyncSession, batch_id: int) -> int:
    """Plan expiry mid-batch: every still-'queued' line → 'cancelled' (2.5).

    Unlike the stop (which DELETES the queue — the user asked for it), the
    system's cancellation MARKS the rows: honest history of what was cut off
    (recorded decision; Epic 3's history can show it). Returns the count.
    """
    stmt = (
        update(BatchLine)
        .where(BatchLine.batch_id == batch_id, BatchLine.state == LINE_QUEUED)
        .values(state=LINE_CANCELLED)
    )
    result = await session.execute(stmt)
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount


async def delete_line(session: AsyncSession, line_id: int) -> None:
    """Discard an in-flight line abandoned by a stop (it never went out)."""
    await session.execute(delete(BatchLine).where(BatchLine.id == line_id))


async def complete_if_drained(session: AsyncSession, batch: Batch) -> bool:
    """If no pending lines remain, mark the batch completed. Returns drained.

    Locks the batch row (FOR UPDATE) BEFORE the pending check so it serializes
    with the handler's locked append (``get_live_batch(for_update=True)``):
    either the append committed first and its new lines are visible here (not
    drained), or this completion commits first and the appender's locked
    re-read sees state!='completed'… i.e. no live batch, and starts a new one.
    Without the lock, an append racing the last line's drain strands its lines
    on a 'completed' batch forever.
    """
    locked = (
        await session.execute(
            select(Batch).where(Batch.id == batch.id).with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:  # batch row deleted mid-send (tenant removed)
        return False
    stmt = (
        select(BatchLine.id)
        .where(
            BatchLine.batch_id == batch.id,
            BatchLine.state.in_(_PENDING_STATES),
        )
        .limit(1)
    )
    pending = (await session.execute(stmt)).scalar_one_or_none()
    if pending is not None:
        return False
    batch.state = STATE_COMPLETED
    await session.flush()
    return True


async def finalize_stuck_stopping(session: AsyncSession) -> int:
    """Boot recovery (2.3 review): finalize stops a restart left orphaned.

    Every 'stopping' → 'stopped' transition lives in the worker's in-process
    paths (step / _release_line / _abort_line), which require it to be holding
    the claimed line. After a restart nobody holds it: even a re-queued line
    is never served because ``next_queued_line_for_tenant`` joins
    ``Batch.state == 'sending'`` — and 'stopping' is in ``LIVE_STATES``, so
    the tenant would be 409-blocked forever. Discard the still-pending lines
    (running BEFORE the 2.5 reconciliation, so a stopping batch's abandoned
    'sending' line is deleted, never reconciled; the stop handler
    already cleared the queue; 'sent' lines are kept as history, exactly like
    the in-process abort) and land the batch 'stopped'. Returns the number of
    batches finalized. No events — clients reconcile via the connect snapshot.
    """
    stopping_ids = select(Batch.id).where(Batch.state == STATE_STOPPING)
    await session.execute(
        delete(BatchLine).where(
            BatchLine.batch_id.in_(stopping_ids),
            BatchLine.state.in_(_PENDING_STATES),
        )
    )
    result = await session.execute(
        update(Batch).where(Batch.state == STATE_STOPPING).values(state=STATE_STOPPED)
    )
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount


async def stuck_sending_lines(session: AsyncSession) -> list[BatchLine]:
    """Lines a crash left in 'sending' (boot reconciliation input, 2.5).

    Invariant: ≤1 row — the worker is singular and claims one line at a time
    (and ``finalize_stuck_stopping`` runs first, deleting the 'sending' lines
    of stopping batches). The list shape tolerates >1 for robustness. Replaces
    2.2's blind ``requeue_stuck_sending``: each line is now reconciled against
    recent outgoing messages — confirmed or re-queued, never double-sent.
    """
    stmt = (
        select(BatchLine)
        .where(BatchLine.state == LINE_SENDING)
        .order_by(BatchLine.id)
    )
    return list((await session.execute(stmt)).scalars().all())
