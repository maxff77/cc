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
from app.db.repos import send_log as send_log_repo

# Pause reasons for a cookie-mode batch (Phase 2). Both are an ORDINARY
# ``STATE_PAUSED`` batch discriminated by ``Batch.pause_reason`` — NO new
# state, so the live/admitted predicates and the partial unique index stay
# intact, and the paused batch keeps its admission slot (``ADMITTED_STATES``).
PAUSE_COOKIES_EXHAUSTED = "cookies_exhausted"
PAUSE_VERDICT_TIMEOUT = "verdict_timeout"

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
    gate_credit_cost: int = 0,
    state: str = STATE_SENDING,
    gate_id: int | None = None,
) -> Batch:
    """Insert and flush a fresh live batch (gate strings snapshotted verbatim).

    ``priority`` is the scheduler tier (0=client, 1=admin, 2=owner). ``state``
    defaults to 'sending'; the admission-controlled POST passes 'waiting' when
    the cap is full (Story 4.2). ``gate_display_value`` is the client-visible
    "Comando visible" snapshot (clients render it instead of ``gate_value``).
    ``gate_credit_cost`` is the gate's per-✅ credit cost snapshotted at start
    (credits feature) — the capture pipeline charges THIS, so re-pricing the
    gate never re-charges this batch.

    ``gate_id`` is the SNAPSHOT of the gate's catalog id (Phase 2 cookie-mode):
    the cookie-rotation layer keys the active-cookie pick on
    ``(tenant_id, gate_id)``. Snapshotted (no FK / no relationship) so history
    survives a gate edit, the same denormalize-on-purpose stance as the gate
    strings. ``None`` for a non-cookie-mode batch (the worker never reads it
    there).
    """
    batch = Batch(
        tenant_id=tenant_id,
        gate_value=gate_value,
        gate_name=gate_name,
        gate_display_value=gate_display_value,
        gate_credit_cost=gate_credit_cost,
        state=state,
        priority=priority,
        gate_id=gate_id,
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
    # Cookie-mode SERIALIZE GATE (Phase 2): a cookie-mode batch sends the
    # atomic ``.cookie``/``.amz`` pair then HOLDS the tenant until the bot's
    # verdict for that line arrives. While ``awaiting_verdict_until`` is set and
    # in the future, the tenant is simply not returned here (so the scheduler
    # never re-picks it). The skip is resolved against DB ``func.now()`` — NOT
    # the scheduler's ``time.monotonic`` clock (mixing the two is meaningless);
    # this also makes the gate survive a worker restart for free. NULL (every
    # non-cookie-mode batch) never gates.
    awaiting_clear = (Batch.awaiting_verdict_until.is_(None)) | (
        Batch.awaiting_verdict_until <= func.now()
    )
    stmt = (
        select(Batch.tenant_id, Batch.id, Batch.priority, antispam)
        .select_from(Batch)
        .outerjoin(
            User, (User.tenant_id == Batch.tenant_id) & (User.role == "client")
        )
        .outerjoin(Plan, Plan.id == User.plan_id)
        .where(Batch.state == STATE_SENDING, has_queued, awaiting_clear)
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


# --- Cookie-mode serialize gate / rotation (Phase 2) -------------------------
#
# All of these mutate the cookie-mode serialize gate on ``Batch``. They are
# flush-not-commit; the caller (the send worker / the resume handler) owns the
# transaction and holds the batch ``FOR UPDATE`` lock around the read-verify-
# mutate so a verdict signal is attempt-fenced against a concurrent
# rotation/timeout/pause.


async def set_awaiting_verdict(
    session: AsyncSession,
    batch: Batch,
    *,
    chat_id: int,
    message_id: int,
    timeout_seconds: int,
) -> None:
    """Arm the serialize gate after a cookie-mode ``.amz`` send.

    Stores the awaited ``.amz`` ``(chat_id, message_id)`` (the attempt-fence)
    and sets ``awaiting_verdict_until = func.now() + timeout_seconds`` (DB clock
    — the single time source). While this is set and in the future,
    ``active_senders`` excludes the tenant (the serialize hold). A resend
    (rotation/timeout) re-arms with the NEW ``message_id``, superseding the old
    fence.
    """
    batch.awaiting_chat_id = chat_id
    batch.awaiting_message_id = message_id
    batch.awaiting_verdict_until = func.now() + func.make_interval(
        0, 0, 0, 0, 0, 0, timeout_seconds
    )
    await session.flush()


async def clear_awaiting_verdict(session: AsyncSession, batch: Batch) -> None:
    """Release the serialize gate (verdict consumed the line, or resume).

    NULLs all three await fields so ``active_senders`` returns the tenant again
    on the next step. Pairs with ``set_awaiting_verdict`` — a consumed/rejected
    verdict, a line-failure, and the resume handler all call this.
    """
    batch.awaiting_chat_id = None
    batch.awaiting_message_id = None
    batch.awaiting_verdict_until = None
    await session.flush()


async def awaited_line_id(session: AsyncSession, batch: Batch) -> int | None:
    """The single in-flight line of a cookie-mode batch, via the ATTEMPT-FENCE.

    🔒 The awaited line is the ONE whose ``send_log.(chat_id, message_id)`` ==
    the batch's ``(awaiting_chat_id, awaiting_message_id)`` — NOT "the batch's
    ``LINE_SENT`` row". A consumed (approved/declined) line correctly STAYS
    ``LINE_SENT`` (same as a normal sent line), so a multi-line cookie batch can
    hold two ``LINE_SENT`` rows while only ONE is actually awaiting a verdict;
    keying off ``LINE_SENT`` re-sends already-consumed lines (duplicate
    ``.amz``/Completa/CC/charge). The fence is the single source of "which line
    is in-flight".

    Returns ``None`` when the await is already cleared (e.g. the
    ``cookies_exhausted`` case where the line was already re-queued before the
    pause) — the caller no-ops. The caller holds the batch ``FOR UPDATE`` so the
    fence is read consistently against a concurrent verdict/timeout/pause.
    """
    if batch.awaiting_message_id is None or batch.awaiting_chat_id is None:
        return None
    row = await send_log_repo.get_by_chat_and_message_id(
        session, batch.awaiting_chat_id, batch.awaiting_message_id
    )
    return row.line_id if row is not None else None


async def set_pause_reason(
    session: AsyncSession, batch: Batch, reason: str | None
) -> None:
    """Set/clear the cookie-mode pause discriminator (rides the WS frame).

    ``'cookies_exhausted'`` / ``'verdict_timeout'`` on pause; ``None`` on
    resume. The batch is an ordinary ``STATE_PAUSED`` (no new state) — only
    this column distinguishes a cookie-mode pause from a client pause.
    """
    batch.pause_reason = reason
    await session.flush()


async def requeue_line_with_intent_reset(
    session: AsyncSession, line: BatchLine
) -> None:
    """Reset a line back to 'queued' AND clear its write-ahead send intent.

    The rotation/timeout resend path: the SAME line is re-sent as a NEW ``.amz``
    message with a NEW ``message_id``, but ``send_log`` REUSES the line's one
    row (``uq_send_log_line_id``). The caller MUST already have persisted the
    dead attempt's terminal ``kind='full'`` row (so its later edits resolve via
    the OLD ``(chat_id, message_id)``) before this runs. Clearing the intent's
    ``message_id`` here lets the reused row carry the NEXT send's id — no
    orphaned, unattributable ``send_log.message_id`` remains, and Completa shows
    the line once via latest-revision-per-``message_id`` across attempts. Runs
    under the batch ``FOR UPDATE`` lock the caller holds.
    """
    line.state = LINE_QUEUED
    # Every fresh ``.amz`` attempt flows through here (cookie-dead rotation,
    # pause-resume, and the timeout-resend base) — reset the durable verdict-
    # timeout retry budget so the new cookie attempt owns its own one-retry.
    # ``_resend_cookie_line`` then calls ``mark_verdict_retried`` to bump it to 1.
    line.verdict_timeout_retries = 0
    await session.flush()
    await send_log_repo.clear_intent(session, line.id)


async def mark_verdict_retried(session: AsyncSession, line: BatchLine) -> None:
    """Burn the line's ONE durable verdict-timeout retry (Phase 2).

    Called by ``_resend_cookie_line`` in the SAME txn as the timeout resend,
    AFTER ``requeue_line_with_intent_reset`` zeroed the budget — net +1. The
    sweep reads ``verdict_timeout_retries >= 1`` and pauses ``verdict_timeout``
    on the second silent elapse instead of resending forever. Durable across a
    restart — the crash-loop fix the old process-memory ``_timeout_retried`` set
    could not provide (boot recovery re-arms the await; this counter survives).
    """
    line.verdict_timeout_retries += 1
    await session.flush()


async def requeue_failed_cookie_line(session: AsyncSession, batch: Batch) -> None:
    """Resume of a cookie-mode pause: re-queue the batch's AWAITED line.

    🔒 Resolves the awaited line via the ATTEMPT-FENCE (``awaited_line_id`` —
    ``send_log.(chat_id, message_id) == batch.(awaiting_chat_id,
    awaiting_message_id)``), NOT "every ``LINE_SENT`` row". A multi-line cookie
    batch can hold several ``LINE_SENT`` rows (consumed lines stay ``LINE_SENT``
    like any normal sent line) — re-queueing them all would re-send already-
    consumed lines. Only the ONE in-flight line is re-queued.

    A ``verdict_timeout`` pause (second silent elapse) leaves the awaited line
    in ``LINE_SENT`` (the ``.amz`` went out) — it never reappears in
    ``active_senders`` (which needs a 'queued' line), so resume MUST hand it
    back to the queue with its write-ahead intent reset (the resend is a NEW
    ``.amz`` ``message_id``; a stale verdict for the OLD one is attempt-fenced
    and dropped). A ``cookies_exhausted`` pause already CLEARED the await and
    re-queued the line before the pause, so ``awaited_line_id`` returns ``None``
    here and this is a clean no-op.

    Runs in the SAME transaction as the resume's ``state=sending`` flip + the
    await-field clear, under the batch ``FOR UPDATE`` the handler holds — never
    split across commits, else the just-resumed batch would be instantly skipped
    by the serialize gate (or its stale ``send_log`` pair would orphan). The
    caller MUST call this BEFORE ``clear_awaiting_verdict`` (the fence needs the
    await fields).
    """
    line_id = await awaited_line_id(session, batch)
    if line_id is None:
        return
    line = await session.get(BatchLine, line_id)
    if line is not None and line.state == LINE_SENT:
        await requeue_line_with_intent_reset(session, line)


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
