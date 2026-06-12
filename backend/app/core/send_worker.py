"""Background send worker (Story 2.2; pause/stop 2.3; scheduled 2.4;
hardened 2.5; admission control 4.2).

A single ``asyncio.Task`` created in the lifespan drains queued batch lines.
Selection is NOT FIFO: ``core.scheduler`` rotates round-robin across active
tenants with bounded owner priority, and the inter-send interval is the
adaptive ``G = max(g_min, P(n)/n)`` — recomputed every turn, never constant
(Story 2.4). Each step opens its OWN session via ``async_session_factory``
(NEVER the request-scoped one) and the Telegram send happens with no session
held (a FloodWait can sleep for minutes — it must not pin a pool connection).

Admission control (Story 4.2): every step begins with ``_admit_waiting`` —
batches queued over the owner-configured cap (``state='waiting'``, durable
FIFO by id) are promoted into freed slots before selection runs. Waiting
batches never appear in ``active_senders`` nor weigh on ``n``: the queue
protects the active senders' cadence instead of degrading it.

Retry policy (Story 2.5 hardening over the legacy semantics):
- ``FloodWaitError`` → note it to the scheduler (governor + GLOBAL no-send
  window), broadcast GLOBAL ``flood.wait``, deadline-sleep the requested
  seconds, retry the SAME line. FloodWaits never count toward the cap —
  they are account pacing, not a bad line.
- Any other send error → tenant-scoped ``error`` event, sleep 2s, retry the
  same line up to ``_MAX_SEND_ATTEMPTS`` total attempts; at the cap the line
  is marked 'failed' (+ ``fail_code``) and THE QUEUE CONTINUES — one bad
  line never blocks other tenants (retry-forever died here).

Write-ahead + fail-stop (Story 2.5, AC 2/5): the send intent is recorded in
``send_log`` in the SAME transaction as the 'sending' claim — BEFORE Telegram
is called — and ``message_id`` is filled in the record phase after delivery.
Order of operations IS the fail-stop: DB down before the claim ⇒ step raises
before any send (run_worker logs and retries); DB down after the send ⇒ the
record block retries FOREVER and nothing else is sent until it commits ("no
attribution possible = no sends"). The in-memory buffer of incoming replies
while the DB is down lives in the capture pipeline (Story 3.1,
``core/capture.py``: blocked queue + single retry-forever consumer);
``catch_up=True`` (telegram.py) recovers messages that arrived while
disconnected.

Pause/stop (Story 2.3): the batch state is re-read after every interrupted
sleep — pause RELEASES the claimed line back to the queue (a single worker
serving every tenant must not stall on one paused batch) and stop ABORTS it
(the queue was already cleared by the handler).

Sleeps are cancelable via the module wake event, but with DEADLINES (2.4,
absorbing the 2.3 deferred finding): a ``wake()`` belonging to ANOTHER
tenant's control re-sleeps the remainder instead of retrying early on the
shared account (``_wait_respecting_state``), and the global pacing sleep is
immune to wake altogether (``sleep_paced`` — FR12 is never bypassed by a
control). The own tenant's pause/stop still land instantly (release/abort).
The global FloodWait window (scheduler.flood_remaining) gates the TOP of
``step()`` — after a FloodWait nobody sends until it elapses, not even the
window-owning tenant via pause→resume (2.5 recorded decision).

Watchdog (Story 4.1): step 0 of ``step()`` gates on ``watchdog.is_paused`` —
a latched global pause blocks every claim/send until the OWNER explicitly
resumes (never automatic). ``SessionLostError`` from the gateway releases the
claimed line intact (it never went out — not a bad line, no 'failed') and
latches that pause; every real delivery feeds ``watchdog.note_sent()`` so a
reply-rate collapse latches it too.
"""

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerts, capture
from app.core.broadcaster import broadcaster
from app.core.scheduler import scheduler
from app.core.telegram import FloodWaitError, SessionLostError, gateway
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine
from app.db.repos import batches as batches_repo
from app.db.repos import send_log as send_log_repo
from app.db.repos import users as users_repo
from app.services import admission as admission_service
from app.services import batches as batches_service

logger = logging.getLogger(__name__)

# How long to sleep when the queue is empty before polling again.
_IDLE_SLEEP_SECONDS = 1.0
# Delay before retrying a line after a non-FloodWait send error.
_ERROR_RETRY_SECONDS = 2.0
# Total generic-error attempts before a line is marked 'failed' (Story 2.5).
# A PRODUCT rule from architecture's Risk Deep-Dive, NOT configuration.
# Counted per claim (a release/re-claim resets it); FloodWaits don't count.
_MAX_SEND_ATTEMPTS = 3

# Per-tenant sent counter — process memory ON PURPOSE (mirror of the legacy
# "counters never reset"); structured-log + GET observability, never durable.
_sent_by_tenant: Counter[int] = Counter()


def sent_by_tenant() -> dict[int, int]:
    """Copy of the per-tenant sent counters (Story 4.3 observability slice)."""
    return dict(_sent_by_tenant)

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


async def sleep_paced(seconds: float) -> None:
    """Deadline sleep IMMUNE to ``wake()`` — the global pacing sleep (FR12).

    Re-sleeps the remainder unconditionally: a control's wake must never make
    the system skip part of the inter-send interval (2.3 deferred #1, part 2).
    Safe to be uninterruptible: the worker holds no claimed line during this
    sleep, so no pause/stop needs to cut it — a paused batch simply isn't
    picked on the next turn, and 'stopping' only exists with a line in flight.
    """
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await sleep_cancelable(remaining)


async def _wait_respecting_state(
    batch_id: int, seconds: float
) -> Literal["elapsed", "release", "abort"]:
    """Deadline sleep holding a claimed line — yields only to the OWN batch.

    After every (possibly wake-interrupted) sleep the batch state is re-read:
    - 'paused'        → ``"release"`` (own pause lands instantly);
    - not 'sending'   → ``"abort"`` (own stop lands instantly);
    - still 'sending' → the wake was another tenant's control: re-sleep the
      REMAINDER — never retry early on the shared account (2.3 deferred #1,
      part 1: an early retry inside a FloodWait window escalates the next
      FloodWait for everyone).
    Returns ``"elapsed"`` once the deadline passes with the batch 'sending'.
    """
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "elapsed"
        await sleep_cancelable(remaining)
        async with async_session_factory() as session:
            state = await batches_repo.get_batch_state(session, batch_id)
        if state == batches_repo.STATE_PAUSED:
            return "release"
        if state != batches_repo.STATE_SENDING:
            return "abort"


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


async def _admit_waiting() -> None:
    """Admission sweep (Story 4.2): promote waiting batches into freed slots.

    Runs at the top of every ``step()`` — self-healing by construction (a
    missed wake costs at most one loop turn). Under the cap row's FOR UPDATE
    lock (the same one POST /api/batches takes) the oldest waiting batches
    are promoted FIFO while slots are free; a DISABLED cap promotes them ALL
    (AC 4: the fallback to pure Epic 2 semantics also rescues batches that
    queued while the cap was on). The remaining waiters get re-numbered
    ``batch.state waiting`` events — emitted only when something was actually
    promoted, so an idle queue never spams positions.

    Lives HERE and not in ``core.scheduler`` (recorded decision): the queue
    is DURABLE (Postgres rows, NFR6 — not process memory like the rotation
    cursor), the payload builders (``services.batches``) import the scheduler
    at module level (an inverse import would be circular), and the worker is
    the scheduler's only consumer — its loop is the sweep's natural home.
    ``scheduler.py`` needs no change at all: 'waiting' never matches
    ``active_senders``/``count_active_senders``, so pick/pace already ignore
    queued batches (that exclusion IS the point of admission control).
    """
    promoted: list[tuple[int, dict, dict]] = []
    repositioned: list[tuple[int, dict]] = []
    async with async_session_factory() as session:
        cap = await admission_service.get_cap_locked(session)
        waiting = await batches_repo.waiting_batches(session, for_update=True)
        if not waiting:
            return  # common case — zero extra cost per step
        if cap == admission_service.CAP_DISABLED:
            to_promote = waiting
        else:
            admitted = await batches_repo.count_admitted(session)
            to_promote = waiting[: max(0, cap - admitted)]
        if not to_promote:
            return  # queue full and no slot free — positions unchanged
        for batch in to_promote:
            batch.state = batches_repo.STATE_SENDING
            # Payloads built INSIDE the session (MissingGreenlet lesson).
            promoted.append(
                (
                    batch.tenant_id,
                    batches_service.state_data(batch, "sending"),
                    await batches_service.progress_data(session, batch),
                )
            )
        for i, batch in enumerate(waiting[len(to_promote) :], start=1):
            repositioned.append(
                (
                    batch.tenant_id,
                    batches_service.state_data(
                        batch, "waiting", queue_position=i
                    ),
                )
            )
        await session.commit()

    for tenant_id, state_payload, progress in promoted:
        logger.info(
            "event=batch_admitted tenant=%s batch=%s",
            tenant_id,
            state_payload["batch_id"],
        )
        await broadcaster.emit(tenant_id, "batch.state", state_payload)
        await broadcaster.emit(tenant_id, "batch.progress", progress)
    for tenant_id, state_payload in repositioned:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def step() -> bool:
    """Process at most one line. Returns True iff a line was sent OR failed
    (a failed line burned 3 real attempts against the API — pacing the
    adaptive interval afterwards protects the account all the same).

    Factored out of the infinite loop so tests can await single steps
    deterministically (no real Telegram, no background task).
    """
    # 0a. Watchdog latch (Story 4.1): a watchdog-triggered GLOBAL pause blocks
    #     every claim/send until the owner explicitly resumes — never
    #     automatic. Memory gate (zero queries); run_worker idles and rotates.
    #     Checked BEFORE the admission sweep: while the account is protected,
    #     nothing changes state automatically — not even promotions.
    if watchdog.is_paused:
        return False

    # -1. Admission sweep (Story 4.2): freed slots pull waiting batches in
    #     FIFO BEFORE anything else — even during an open FloodWait window
    #     (promotion sends nothing; the client sees "Enviando" as soon as a
    #     slot exists, the actual send still respects the window below).
    await _admit_waiting()

    # 0. Global FloodWait window (Story 2.5, closing the 2.4 deferred bypass):
    #    after a FloodWait NOBODY claims/sends until the window elapses — not
    #    even the window-owning tenant via pause→resume. Wake-immune is
    #    correct here: no line is claimed, same justification as the post-send
    #    pacing sleep.
    remaining = scheduler.flood_remaining()
    if remaining > 0:
        await sleep_paced(remaining)

    # 1. Scheduler picks WHOSE line goes next (round-robin + owner priority),
    #    then claim that tenant's oldest queued line (short transaction —
    #    commit releases it). The write-ahead intent is recorded in the SAME
    #    transaction (AC 2): the claim commit IS "recorded BEFORE Telegram".
    expired = False
    async with async_session_factory() as session:
        active = await batches_repo.active_senders(session)
        pick = scheduler.pick_next(active)
        if pick is None:
            return False
        # Plan expiry is checked at claim time on purpose (AC 7): this is the
        # only point where the pipeline would SPEND a channel slot on the
        # tenant. A paused batch of an expired tenant is never picked, so it
        # needs no active cancellation (1.4's lockout already shuts it out).
        expired = await users_repo.tenant_plan_expired(session, pick.tenant_id)
        if not expired:
            line = await batches_repo.next_queued_line_for_tenant(
                session, pick.tenant_id
            )
            if line is None:
                # Race: a stop emptied the picked tenant's queue between the
                # listing and this claim — idle this step; the next loop
                # rotates (recorded decision: no same-step retry of another
                # tenant).
                return False
            await batches_repo.mark_sending(session, line)
            await send_log_repo.record_intent(session, line)
            await session.commit()
            line_id = line.id
            batch_id = line.batch_id
            tenant_id = line.tenant_id
            position = line.position
            text = line.text

    if expired:
        # Close the claim session WITHOUT claiming, cancel, and let the next
        # loop rotate (same pattern as the 2.4 selection↔stop race).
        await _cancel_expired_batch(pick.tenant_id, pick.batch_id)
        return False

    # 2. Send — in-place retry on the SAME line, no DB session held. The
    #    state re-check inside may yield to a pause (release) or stop (abort);
    #    the retry cap may give up ("failed").
    result = await _send_with_retries(tenant_id, batch_id, text)
    if isinstance(result, str):
        if result == "release":
            await _release_line(tenant_id, batch_id, line_id)
        else:  # "abort"
            await _abort_line(tenant_id, batch_id, line_id)
        return False
    if isinstance(result, tuple):
        kind, info = result
        if kind == "session_lost":
            # The Telegram session died (Story 4.1): the line NEVER went out —
            # hand it back intact (release, not 'failed': it is not a bad
            # line), then latch the global pause + owner alert. The batch
            # stays 'sending' in the DB and resumes where it was once the
            # owner explicitly resumes.
            await _release_line(tenant_id, batch_id, line_id)
            await watchdog.session_lost(info)
            return False
        # ("failed", code) — the cap was hit
        await _record_failed(tenant_id, batch_id, line_id, position, text, info)
        return True

    # 3. Record + emit ("sent") — retries forever until the DB takes it.
    await _record_sent(tenant_id, batch_id, line_id, position, text, result)
    return True


async def _record_sent(
    tenant_id: int,
    batch_id: int,
    line_id: int,
    position: int,
    text: str,
    message_id: int,
) -> None:
    """Post-send record phase: 'sent' + ``message_id`` on the intent (AC 2/5).

    Retries FOREVER until the transaction commits — this IS the fail-stop of
    AC 5: a sent-but-unrecorded line blocks any further send ("no attribution
    possible = no sends"). Safe to re-run after a partially lost commit: the
    line UPDATE and ``set_message_id`` are idempotent by construction.
    """
    state_payload: dict | None = None
    progress: dict | None = None
    while True:
        state_payload = None
        progress = None
        try:
            async with async_session_factory() as session:
                recorded = await session.get(BatchLine, line_id)
                if recorded is None:  # batch deleted mid-send (tenant removed)
                    return
                await batches_repo.mark_sent(session, recorded)
                await send_log_repo.set_message_id(session, line_id, message_id)
                batch = await _locked_batch(session, batch_id)
                if batch is not None:
                    if batch.state == batches_repo.STATE_STOPPING:
                        # The stop landed while gateway.send was in flight and
                        # the line DID go out: record it honestly ('sent') and
                        # finalize 'stopped' — NOT complete_if_drained, which
                        # would mark the batch 'completed' (drained ≠ detenido,
                        # Epic 3 history).
                        batch.state = batches_repo.STATE_STOPPED
                        state_payload = batches_service.state_data(batch, "idle")
                    else:
                        drained = await batches_repo.complete_if_drained(
                            session, batch
                        )
                        progress = await batches_service.progress_data(session, batch)
                        if drained:
                            state_payload = batches_service.state_data(batch, "idle")
                await session.commit()
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=record line=%s — retrying until "
                "the DB returns (fail-stop: nothing else sends meanwhile)",
                line_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)

    _sent_by_tenant[tenant_id] += 1
    # Feed the reply-rate watchdog (Story 4.1) — REAL deliveries only (boot
    # reconciliation confirms are old sends and never call this). May latch
    # the global pause right here when the window collapsed.
    await watchdog.note_sent()
    logger.info(
        "event=line_sent tenant=%s batch=%s line=%s message_id=%s tenant_total=%s",
        tenant_id,
        batch_id,
        line_id,
        message_id,
        _sent_by_tenant[tenant_id],
    )
    await broadcaster.emit(
        tenant_id,
        "batch.line_sent",
        {"batch_id": batch_id, "position": position, "text": text},
    )
    if progress is not None:
        await broadcaster.emit(tenant_id, "batch.progress", progress)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _record_failed(
    tenant_id: int,
    batch_id: int,
    line_id: int,
    position: int,
    text: str,
    code: str,
) -> None:
    """Record-phase mirror for a line the retry cap gave up on (AC 3/4).

    Same retry-forever fail-stop as ``_record_sent`` (the failure must be
    durable before anything else sends). A batch whose LAST line fails still
    completes — 'failed' is not pending, so ``complete_if_drained`` drains.
    """
    state_payload: dict | None = None
    progress: dict | None = None
    while True:
        state_payload = None
        progress = None
        try:
            async with async_session_factory() as session:
                recorded = await session.get(BatchLine, line_id)
                if recorded is None:  # batch deleted mid-send (tenant removed)
                    return
                await batches_repo.mark_failed(session, recorded, code)
                batch = await _locked_batch(session, batch_id)
                if batch is not None:
                    if batch.state == batches_repo.STATE_STOPPING:
                        batch.state = batches_repo.STATE_STOPPED
                        state_payload = batches_service.state_data(batch, "idle")
                    else:
                        drained = await batches_repo.complete_if_drained(
                            session, batch
                        )
                        progress = await batches_service.progress_data(session, batch)
                        if drained:
                            state_payload = batches_service.state_data(batch, "idle")
                await session.commit()
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=record line=%s — retrying until "
                "the DB returns (fail-stop: nothing else sends meanwhile)",
                line_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)

    logger.warning(
        "event=line_failed tenant=%s batch=%s line=%s code=%s",
        tenant_id,
        batch_id,
        line_id,
        code,
    )
    await broadcaster.emit(
        tenant_id,
        "batch.line_failed",
        {"batch_id": batch_id, "position": position, "text": text, "code": code},
    )
    if progress is not None:
        await broadcaster.emit(tenant_id, "batch.progress", progress)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _cancel_expired_batch(tenant_id: int, batch_id: int) -> None:
    """Plan expired mid-batch (AC 7): cancel what's queued, keep what's sent.

    Runs BEFORE any claim (single worker), so only 'queued' lines exist to
    cancel — they are MARKED 'cancelled' (honest history of what the system
    cut off; the stop's DELETE is the user's choice, this is not). The 'sent'
    lines and their ``send_log`` rows are untouched: Story 3.1 attributes
    their replies even on a cancelled batch. 'cancelled' is terminal and not
    live — controls 409 and a renewed plan starts a fresh batch on their own.

    The emitted ``batch.state`` idle is honest-and-harmless: an expired tenant
    cannot open new sockets (the /ws handshake rejects it) but an already-open
    one exists while 2-2 #4 stays deferred.
    """
    state_payload: dict | None = None
    cancelled = 0
    async with async_session_factory() as session:
        batch = await _locked_batch(session, batch_id)
        if batch is None:
            return
        # Re-check under the lock (2.5 deferred fix): a user stop can finalize
        # the batch 'stopped' between the expiry check and this transaction —
        # rewriting a terminal state as 'cancelled' would misrepresent the
        # user's stop as a system cancellation in Epic 3 history.
        if batch.state not in batches_repo.LIVE_STATES:
            return
        cancelled = await batches_repo.cancel_queued_lines(session, batch_id)
        batch.state = batches_repo.STATE_CANCELLED
        state_payload = batches_service.state_data(batch, "idle")
        await session.commit()
    logger.info(
        "event=batch_cancelled reason=plan_expired tenant=%s batch=%s cancelled=%s",
        tenant_id,
        batch_id,
        cancelled,
    )
    await broadcaster.emit(tenant_id, "batch.state", state_payload)


def _fail_code(exc: BaseException) -> str:
    """Machine-readable failure code: snake_case of the exception class name.

    Recorded decision — stable, machine-legible, no invented taxonomy
    (``RPCError`` → ``rpc_error``, ``ValueError`` → ``value_error``), truncated
    to the column's 40 chars; the Spanish copy lives in the frontend with a
    fallback.
    """
    name = type(exc).__name__
    snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    return snake.lower()[:40]


async def _send_with_retries(
    tenant_id: int, batch_id: int, text: str
) -> int | tuple[Literal["failed", "session_lost"], str] | Literal["release", "abort"]:
    """Deliver ``text`` (→ its Telegram message id) — or yield/give up.

    The batch state is re-read at the TOP of every iteration AND inside every
    retry wait (``_wait_respecting_state`` deadline loop):
    - 'sending' → attempt the send;
    - 'paused'  → "release" (give the line back — don't hold it);
    - 'stopping'/'stopped'/gone → "abort".

    Retry policy (Story 2.5): FloodWait waits + retries the SAME line and
    NEVER counts toward the cap (account pacing, not a bad line); any other
    error counts, and at ``_MAX_SEND_ATTEMPTS`` total attempts the line is
    given up as ``("failed", code)``. The counter is per CLAIM on purpose
    (recorded decision): a release/re-claim resets it — simple, and the case
    is rare. ``SessionLostError`` (Story 4.1) short-circuits as
    ``("session_lost", detail)`` — neither a retry nor a 'failed'.
    """
    attempts = 0
    while True:
        async with async_session_factory() as session:
            state = await batches_repo.get_batch_state(session, batch_id)
        if state == batches_repo.STATE_PAUSED:
            return "release"
        if state != batches_repo.STATE_SENDING:
            return "abort"
        try:
            return await gateway.send(text)
        except FloodWaitError as e:
            # Governor + GLOBAL no-send window: every FloodWait raises the
            # pacing floor AND opens the window the worker respects before
            # claiming ANY tenant's line (Task 5) …
            scheduler.note_flood_wait(float(e.seconds))
            logger.warning(
                "event=flood_wait seconds=%s g_min=%s flood_total=%s "
                "raises_total=%s tenant=%s batch=%s",
                e.seconds,
                scheduler.g_min,
                scheduler.flood_events_total,
                scheduler.governor_raises,
                tenant_id,
                batch_id,
            )
            # … and is explained to everyone (global event, architecture).
            await broadcaster.emit_global("flood.wait", {"seconds": e.seconds})
            # Repeated FloodWaits inside the alert window alert the OWNER
            # (Story 4.3, AC 1 — the leading ban indicator).
            await alerts.note_flood_wait()
            outcome = await _wait_respecting_state(batch_id, float(e.seconds))
            if outcome != "elapsed":
                return outcome
        except asyncio.CancelledError:
            raise
        except SessionLostError as e:
            # The session died (Story 4.1) — NOT a bad line: never counts
            # toward the cap, never marks 'failed'. The caller releases the
            # line and latches the watchdog's global pause.
            return ("session_lost", str(e))
        except Exception as e:
            attempts += 1
            # Per-attempt error event kept — never silently dropped.
            await broadcaster.emit(
                tenant_id, "error", {"code": "send_error", "message": str(e)}
            )
            if attempts >= _MAX_SEND_ATTEMPTS:
                return ("failed", _fail_code(e))
            outcome = await _wait_respecting_state(batch_id, _ERROR_RETRY_SECONDS)
            if outcome != "elapsed":
                return outcome


async def _release_line(tenant_id: int, batch_id: int, line_id: int) -> None:
    """Pause release: hand the claimed line back to the queue intact.

    Resuming re-claims it immediately — same net effect as the legacy
    "pause→resume may retry before the FloodWait window elapses".

    The batch row is locked FIRST so this serializes with a concurrent stop
    (which holds the same lock while clearing the queue): if the stop already
    committed 'stopping', re-queueing the line would strand the batch in
    'stopping' forever (its lines are invisible to the worker's selection) — in
    that case the line is abandoned and the batch finalized instead.
    """
    state_payload: dict | None = None
    # Retry forever (2.5 deferred fix): a transient DB failure here would
    # otherwise strand the claimed line in 'sending' while the loop claims
    # OTHER lines — both branches are idempotent, same rationale as the
    # record phases.
    while True:
        try:
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
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=release line=%s — retrying until "
                "the DB returns (a stuck claim would strand the batch)",
                line_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _abort_line(tenant_id: int, batch_id: int, line_id: int) -> None:
    """Stop abort: discard the never-sent line and finalize the batch.

    The queue was already cleared by the stop handler; the abandoned line is
    deleted (it never went out). 'stopping' → 'stopped' + terminal idle event.
    """
    state_payload: dict | None = None
    # Retry forever — same rationale and idempotence as ``_release_line``.
    while True:
        try:
            async with async_session_factory() as session:
                batch = await _locked_batch(session, batch_id)
                await batches_repo.delete_line(session, line_id)
                if batch is not None and batch.state == batches_repo.STATE_STOPPING:
                    batch.state = batches_repo.STATE_STOPPED
                    state_payload = batches_service.state_data(batch, "idle")
                await session.commit()
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=abort line=%s — retrying until "
                "the DB returns (a stuck claim would strand the batch)",
                line_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _boot_recovery() -> None:
    """Heal state a restart abandoned (NFR6 + 2.3 review + 2.5 AC 6). Never
    raises.

    IN THIS ORDER:
    1. (transaction) Finalize batches stranded in 'stopping': the only
       'stopping' → 'stopped' transitions are this worker's in-process paths,
       so a restart while a stop was in flight would otherwise leave the batch
       live-but-undrainable forever — its tenant permanently 409-blocked.
       Pending lines (the 'sending' one included) are discarded like the
       in-process abort; no events (clients reconcile via the connect
       snapshot, the 2.3 boot-recovery pattern).
    2. (transaction) List lines a crash left in 'sending'. Invariant: ≤1 —
       the worker is singular and claims one line at a time (and step 1 ran
       first); >1 is tolerated for robustness.
    3. (no transaction) RECONCILE each against recent outgoing chat messages
       instead of blindly re-queueing (2.2's accepted double-send window dies
       here): a free candidate (id not already attributed in ``send_log``)
       with identical text confirms the line ('sent' + real ``message_id``);
       no match → re-queue (the NULL-message_id intent row is reused on the
       re-claim). Gateway down / listing fails → re-queue with a warning
       (recorded fallback: availability over the rare double-send when
       Telegram itself is down — without the gateway nothing sends anyway).

    On exit — success, failure or cancellation — the capture consumer's boot
    gate is released (review 3-1): catch_up replays buffered in the capture
    queue may reference exactly the message ids this function confirms, so
    the lifespan holds the consumer until here.
    """
    try:
        async with async_session_factory() as session:
            finalized = await batches_repo.finalize_stuck_stopping(session)
            await session.commit()
        if finalized:
            logger.info(
                "boot recovery: finalized %d orphaned stopping batch(es)", finalized
            )

        async with async_session_factory() as session:
            stuck = await batches_repo.stuck_sending_lines(session)
            # Capture attributes while the session is open (MissingGreenlet).
            stuck_data = [
                (line.id, line.batch_id, line.tenant_id, line.text) for line in stuck
            ]
        if not stuck_data:
            return

        # The lifespan connects the gateway BEFORE run_worker (main.py), so
        # its readiness is known here.
        candidates: list[tuple[int, str]] = []
        verified = False
        if gateway.ready:
            try:
                candidates = await gateway.recent_outgoing()
                verified = True
            except Exception:
                logger.warning(
                    "event=reconcile_unverified reason=recent_outgoing_failed "
                    "— re-queueing without verification"
                )
        else:
            logger.warning(
                "event=reconcile_unverified reason=gateway_not_ready "
                "— re-queueing without verification"
            )

        used: set[int] = set()
        if verified and candidates:
            async with async_session_factory() as session:
                used = await send_log_repo.used_message_ids(
                    session, [message_id for message_id, _ in candidates]
                )

        for line_id, batch_id, tenant_id, line_text in stuck_data:
            match_id: int | None = None
            if verified:
                # iter_messages lists newest first — the newest match wins.
                for message_id, message_text in candidates:
                    if message_id not in used and message_text == line_text:
                        match_id = message_id
                        break
            async with async_session_factory() as session:
                line = await session.get(BatchLine, line_id)
                if line is None:
                    continue
                if match_id is not None:
                    await batches_repo.mark_sent(session, line)
                    # Idempotent get-or-create FIRST (deferred 2-5 :616): a
                    # line left 'sending' by a pre-2.5 crash has NO intent row
                    # and set_message_id is a bare UPDATE that would silently
                    # no-op — leaving the confirmed line unattributable for
                    # 3.1 and its message_id invisible to used_message_ids.
                    await send_log_repo.record_intent(session, line)
                    await send_log_repo.set_message_id(session, line_id, match_id)
                    used.add(match_id)
                    # Same batch finalization as the step's record phase.
                    batch = await _locked_batch(session, batch_id)
                    if batch is not None:
                        if batch.state == batches_repo.STATE_STOPPING:
                            batch.state = batches_repo.STATE_STOPPED
                        else:
                            await batches_repo.complete_if_drained(session, batch)
                    outcome = "confirmed"
                else:
                    await batches_repo.mark_queued(session, line)
                    outcome = "requeued"
                await session.commit()
            logger.info(
                "event=line_reconciled outcome=%s tenant=%s batch=%s line=%s",
                outcome,
                tenant_id,
                batch_id,
                line_id,
            )
    except Exception:
        logger.exception("boot recovery failed — continuing")
    finally:
        capture.boot_recovered()


async def run_worker() -> None:
    """Infinite drain loop (created as a task in the lifespan)."""
    await _boot_recovery()

    while True:
        try:
            sent = await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            # DB unreachable before the claim (or any unexpected error): log
            # and retry. Order of operations guarantees nothing was sent —
            # the claim raises BEFORE gateway.send (fail-stop, AC 5).
            logger.exception(
                "event=db_unreachable phase=claim — send worker step failed, retrying"
            )
            await sleep_cancelable(_ERROR_RETRY_SECONDS)
            continue
        if sent:
            # System-controlled ADAPTIVE interval between sends (FR12):
            # G = max(g_min, P(n)/n), recomputed every turn. sleep_paced is
            # wake-immune — a control never makes the system send faster.
            # The count gets its own try/except: a transient DB failure here
            # must NOT escape the loop and kill the singleton worker (the
            # step() except above exists precisely to survive DB blips).
            try:
                async with async_session_factory() as session:
                    n = await batches_repo.count_active_senders(session)
            except Exception:
                logger.exception(
                    "pacing count failed — falling back to n=1 interval"
                )
                n = 1
            await sleep_paced(scheduler.interval(max(1, n)))
        else:
            await sleep_cancelable(_IDLE_SLEEP_SECONDS)
