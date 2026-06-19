"""Background send worker (Story 2.2; pause/stop 2.3; scheduled 2.4;
hardened 2.5; admission control 4.2).

A single ``asyncio.Task`` created in the lifespan drains queued batch lines.
Selection is NOT FIFO: ``core.scheduler`` rotates round-robin across active
tenants with bounded owner priority, and the inter-send interval is the
constant ``G = g_min`` (owner decision 2026-06-13; the FloodWait governor
still tunes it upward). Each step opens its OWN session via ``async_session_factory``
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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerts, capture
from app.core.broadcaster import broadcaster
from app.core.cookie_verdict import CookieVerdict
from app.core.cookie_verdict import drain as drain_cookie_verdicts
from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_COOKIE_DEAD,
    VERDICT_DECLINED,
    VERDICT_FORMAT_ERROR,
)
from app.core.scheduler import scheduler
from app.core.telegram import FloodWaitError, SessionLostError, gateway
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, CaptureSession
from app.db.repos import batches as batches_repo
from app.db.repos import gate_cookies as gate_cookies_repo
from app.db.repos import send_log as send_log_repo
from app.db.repos import users as users_repo
from app.services import admission as admission_service
from app.services import batches as batches_service
from app.services import pacing as pacing_service

logger = logging.getLogger(__name__)

# How long to sleep when the queue is empty before polling again.
_IDLE_SLEEP_SECONDS = 1.0
# Delay before retrying a line after a non-FloodWait send error.
_ERROR_RETRY_SECONDS = 2.0
# Total generic-error attempts before a line is marked 'failed' (Story 2.5).
# A PRODUCT rule from architecture's Risk Deep-Dive, NOT configuration.
# Counted per claim (a release/re-claim resets it); FloodWaits don't count.
_MAX_SEND_ATTEMPTS = 3

# --- Amazon cookie-mode serialize gate (Phase 2) -----------------------------
#
# A cookie-mode batch sends the atomic ``.cookie <active_value>`` then
# ``.amz <line>`` pair in ONE worker turn (no ``scheduler.pick_next`` between
# them) and then HOLDS the tenant until the bot's ``⌿ Status:`` verdict for that
# ``.amz`` line arrives. ``awaiting_verdict_until`` (DB clock) is the durable
# serialize gate; ``core.cookie_verdict`` is the fast-path signal.
#
# Verdict-timeout window: hardcoded (pipeline internals are not configuration —
# 2.5 rule). On elapse the line is retried ONCE with a fresh cookie + a NEW
# awaited ``message_id``; a SECOND silent elapse pauses the batch
# ``verdict_timeout`` + owner alert.
_VERDICT_TIMEOUT_SECONDS = 90

# The verdict-timeout retry-once budget is now DURABLE on the line
# (``BatchLine.verdict_timeout_retries``), NOT process memory — so a crash loop
# around the 90s timeout can no longer grant a fresh retry (and a fresh
# ``.cookie``+``.amz`` resend on the shared account) per restart. Read/written in
# ``_resend_cookie_line`` / ``_sweep_verdict_timeouts`` via the repo helpers
# (``mark_verdict_retried`` / the requeue reset).

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


async def _is_cookie_mode_batch(session: AsyncSession, batch: Batch) -> bool:
    """Is this batch bound to a cookie-mode capture session (Phase 2)?

    Reads the ``cookie_mode`` snapshot off the bound ``CaptureSession`` (the
    same source ``step()``/capture read). NULL ``capture_session_id`` (pre-3.1)
    ⇒ never cookie-mode."""
    if not batch.capture_session_id:
        return False
    cs = await session.get(CaptureSession, batch.capture_session_id)
    return cs is not None and cs.cookie_mode


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
    constant interval afterwards protects the account all the same).

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
        # The per-tenant antispam cooldown rides on each ActiveSender, resolved
        # as coalesce(client plan.antispam_seconds, 0.0): a tenant with plan_id
        # NULL carries NO per-tenant cooldown (legacy behavior). The global
        # interval below remains the account-wide pacer (the pacing sleep at the
        # end of the loop); it is still passed for caller/stub compatibility but
        # no longer gates a no-plan tenant in the scheduler.
        global_interval = await pacing_service.get_interval(session)
        active = await batches_repo.active_senders(
            session, global_interval=global_interval
        )
        pick = scheduler.pick_next(active)
        if pick is None:
            return False
        # Plan expiry is checked at claim time on purpose (AC 7): this is the
        # only point where the pipeline would SPEND a channel slot on the
        # tenant. A paused batch of an expired tenant is never picked, so it
        # needs no active cancellation (1.4's lockout already shuts it out).
        # ONLY client batches (priority 0) are subject to expiry — owner/admin
        # carry no plan (mirror of services.plans.is_plan_expired, which the
        # auth gate uses). tenant_plan_expired is tenant-WIDE EXISTS, so
        # without this guard a staff batch in a SHARED "house" tenant that
        # also holds an expired client would be wrongly cancelled.
        expired = pick.priority == 0 and await users_repo.tenant_plan_expired(
            session, pick.tenant_id
        )
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
            # Cookie-mode resolution (Phase 2): a cookie-mode session snapshots
            # ``cookie_mode=True`` and the batch snapshots ``gate_id`` (the
            # active-cookie key). Read both off the just-claimed batch + its
            # bound capture session INSIDE this open session (MissingGreenlet).
            # Non-cookie-mode batches fall through to the unchanged Phase-1 path.
            cookie_mode = False
            gate_id = None
            claimed_batch = await session.get(Batch, batch_id)
            if claimed_batch is not None and claimed_batch.capture_session_id:
                cs = await session.get(
                    CaptureSession, claimed_batch.capture_session_id
                )
                cookie_mode = cs is not None and cs.cookie_mode
                gate_id = claimed_batch.gate_id

    if expired:
        # Close the claim session WITHOUT claiming, cancel, and let the next
        # loop rotate (same pattern as the 2.4 selection↔stop race).
        await _cancel_expired_batch(pick.tenant_id, pick.batch_id)
        return False

    # 1b. Amazon cookie-mode branch (Phase 2): the checker stores the cookie
    #     PER ACCOUNT and the account is shared, so each ``.amz <line>`` MUST be
    #     immediately preceded by its own ``.cookie <value>`` with NO other
    #     message between them. Send the atomic pair in THIS one turn (no
    #     ``scheduler.pick_next`` between ``.cookie`` and ``.amz``), then HOLD
    #     the tenant (``awaiting_verdict_until``) until the verdict for the
    #     ``.amz`` line arrives — the serialize gate in ``active_senders`` SQL.
    if cookie_mode:
        sent = await _send_cookie_pair(
            tenant_id, batch_id, line_id, position, text, gate_id
        )
        return sent

    # 2. Send — in-place retry on the SAME line, no DB session held. The
    #    state re-check inside may yield to a pause (release) or stop (abort);
    #    the retry cap may give up ("failed"). Success is the tagged tuple
    #    ("sent", chat_id, message_id) — chat_id namespaces the per-chat id.
    result = await _send_with_retries(tenant_id, batch_id, text)
    if result == "release":
        await _release_line(tenant_id, batch_id, line_id)
        return False
    if result == "abort":
        await _abort_line(tenant_id, batch_id, line_id)
        return False
    tag = result[0]
    if tag == "session_lost":
        # The Telegram session died (Story 4.1): the line NEVER went out —
        # hand it back intact (release, not 'failed': it is not a bad
        # line), then latch the global pause + owner alert. The batch
        # stays 'sending' in the DB and resumes where it was once the
        # owner explicitly resumes.
        await _release_line(tenant_id, batch_id, line_id)
        await watchdog.session_lost(result[1])
        return False
    if tag == "failed":  # the cap was hit
        await _record_failed(tenant_id, batch_id, line_id, position, text, result[1])
        return True

    # 3. Record + emit ("sent") — retries forever until the DB takes it.
    _, chat_id, message_id = result
    await _record_sent(
        tenant_id, batch_id, line_id, position, text, chat_id, message_id
    )
    return True


async def _record_sent(
    tenant_id: int,
    batch_id: int,
    line_id: int,
    position: int,
    text: str,
    chat_id: int,
    message_id: int,
    *,
    complete_on_drain: bool = True,
) -> None:
    """Post-send record phase: 'sent' + ``(chat_id, message_id)`` on the intent
    (AC 2/5).

    Retries FOREVER until the transaction commits — this IS the fail-stop of
    AC 5: a sent-but-unrecorded line blocks any further send ("no attribution
    possible = no sends"). Safe to re-run after a partially lost commit: the
    line UPDATE and ``set_message_id`` are idempotent by construction.

    ``complete_on_drain`` is ``True`` for the normal Phase-1 path (a drained
    batch lands 'completed' right here). The cookie-mode pair passes ``False``:
    its ``.amz`` line is 'sent' but the batch must STAY 'sending' to await the
    verdict — completion happens only when the verdict CONSUMES the last line
    (``_apply_verdict`` → ``complete_if_drained``). A stop landing mid-send
    still finalizes 'stopped' in both modes (the line did go out)."""
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
                await send_log_repo.set_message_id(
                    session, line_id, chat_id, message_id
                )
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
                    elif complete_on_drain:
                        drained = await batches_repo.complete_if_drained(
                            session, batch
                        )
                        progress = await batches_service.progress_data(session, batch)
                        if drained:
                            state_payload = batches_service.state_data(batch, "idle")
                    else:
                        # Cookie-mode: do NOT complete on drain — the batch
                        # holds 'sending' until the verdict consumes the line.
                        # Still refresh progress so the ring advances on send.
                        progress = await batches_service.progress_data(session, batch)
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
    # Start this tenant's antispam cooldown (plan-catalog feature): pick_next
    # skips it until its plan's antispam_seconds elapses. REAL deliveries only,
    # same boundary as the watchdog feed below (boot reconciliation confirms
    # are old sends and never call this). Memory-only, like the rest of the
    # scheduler state; the global g_min sleep below still paces the account.
    scheduler.note_sent(tenant_id)
    # Feed the reply-rate watchdog (Story 4.1) — REAL deliveries only (boot
    # reconciliation confirms are old sends and never call this). May latch
    # the global pause right here when the window collapsed.
    await watchdog.note_sent()
    logger.info(
        "event=line_sent tenant=%s batch=%s line=%s chat_id=%s message_id=%s "
        "tenant_total=%s",
        tenant_id,
        batch_id,
        line_id,
        chat_id,
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


# --- Amazon cookie-mode send + rotation (Phase 2) ---------------------------


async def _pause_cookie_batch(
    tenant_id: int, batch_id: int, reason: str
) -> None:
    """Pause a cookie-mode batch (``cookies_exhausted`` / ``verdict_timeout``).

    An ORDINARY ``STATE_PAUSED`` discriminated by ``pause_reason`` (no new
    state — the partial unique index and admission slot stay intact). Locks the
    batch FOR UPDATE, re-queues the awaited line via the attempt-fence and THEN
    clears the await fields, and emits ``batch.state`` carrying the reason so the
    cockpit renders the right prompt. Retry-forever fail-stop, same as the record
    phases. A batch already finalized (stopped/cancelled) is left untouched.
    """
    state_payload: dict | None = None
    while True:
        try:
            async with async_session_factory() as session:
                batch = await _locked_batch(session, batch_id)
                if batch is None or batch.state not in batches_repo.LIVE_STATES:
                    return
                batch.state = batches_repo.STATE_PAUSED
                await batches_repo.set_pause_reason(session, batch, reason)
                # Re-queue the awaited line via the attempt-fence BEFORE clearing
                # the await: clear_awaiting_verdict NULLs the fence, after which
                # resume's requeue_failed_cookie_line can no longer resolve the
                # LINE_SENT line and would strand it (the verdict_timeout path).
                # The cookies_exhausted path already re-queued the line, so this
                # is an idempotent no-op there.
                await batches_repo.requeue_failed_cookie_line(session, batch)
                await batches_repo.clear_awaiting_verdict(session, batch)
                # The ``pause_reason`` rides the ``batch.state`` frame so the
                # cockpit renders the right prompt the moment the worker pauses.
                state_payload = batches_service.state_data(
                    batch, "paused", pause_reason=reason
                )
                await session.commit()
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=cookie_pause batch=%s reason=%s — "
                "retrying until the DB returns",
                batch_id,
                reason,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)
    if state_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", state_payload)


async def _arm_await(
    batch_id: int,
    line_id: int,
    chat_id: int,
    message_id: int,
    cookie_id: int | None,
) -> None:
    """Arm the serialize gate after a cookie-mode ``.amz`` send (retry-forever).

    Stores the awaited ``.amz`` ``(chat_id, message_id)`` + ``func.now()+90s``
    under the batch FOR UPDATE so ``active_senders`` excludes the tenant until a
    matching verdict or the timeout. Idempotent (a bare UPDATE of the await
    columns) — safe to re-run after a partially lost commit, same fail-stop as
    ``_record_sent``.

    🔒 Also stamps ``BatchLine.failed_cookie_id = cookie_id`` (the cookie ACTUALLY
    sent for THIS attempt). A ``cookie_dead`` verdict reads it back to mark the
    exact cookie dead — never re-derives "oldest active" across unlocked sessions
    (which could burn a healthy cookie if the vault changed during the 90s await).
    """
    while True:
        try:
            async with async_session_factory() as session:
                batch = await _locked_batch(session, batch_id)
                if batch is None:
                    return
                await batches_repo.set_awaiting_verdict(
                    session,
                    batch,
                    chat_id=chat_id,
                    message_id=message_id,
                    timeout_seconds=_VERDICT_TIMEOUT_SECONDS,
                )
                line = await session.get(BatchLine, line_id)
                if line is not None:
                    line.failed_cookie_id = cookie_id
                    await session.flush()
                await session.commit()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=cookie_arm batch=%s — retrying "
                "until the DB returns (the serialize gate must be armed)",
                batch_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)


async def _send_cookie_pair(
    tenant_id: int,
    batch_id: int,
    line_id: int,
    position: int,
    text: str,
    gate_id: int | None,
) -> bool:
    """Send the atomic ``.cookie <value>`` then ``.amz <line>`` pair (Phase 2).

    🔒 The two sends happen in ONE worker turn with NO ``scheduler.pick_next``
    (and no other tenant/line) between them — the cookie-per-account invariant.
    Up-front the SAME account guards as a normal send (``watchdog.is_paused`` /
    ``scheduler.flood_remaining``); if blocked, NOTHING goes out — the line is
    released and re-queued. The ``.cookie`` half is SIDE-BAND: a bare guarded
    send with NO ``send_log``/``batch_line`` row and NO ``watchdog.note_sent``
    (it is not a tenant line); its confirmation reply is content-sniffed away in
    capture. The ``.amz`` half rides the EXISTING write-ahead path (intent
    already recorded at claim; ``message_id`` recorded after).

    Returns ``True`` iff the ``.amz`` line went out (the loop then paces the
    account); ``False`` on any release/re-queue (guard blocked, exhausted,
    SessionLost, FloodWait, or pair-abort).

    🔒 The cookie VALUE is NEVER logged/echoed — only ``(tenant_id, gate_id,
    cookie_id, MASKED)``. A ``.cookie`` send failure routes as release with a
    masked, value-free log and NEVER emits the per-attempt ``error`` event
    (which interpolates ``str(e)`` and could echo the outgoing text).
    """
    # Up-front account guards (BEFORE the ``.cookie``): a latched watchdog or an
    # open FloodWait window means nothing sends this turn. Release the line
    # (nothing went out) and re-queue — resume re-picks a FRESH cookie.
    if watchdog.is_paused or scheduler.flood_remaining() > 0:
        await _release_line(tenant_id, batch_id, line_id)
        return False

    # Resolve the active cookie FOR UPDATE SKIP LOCKED (FIFO by id). No active
    # cookie (or no gate_id snapshot) ⇒ pause ``cookies_exhausted`` + re-queue;
    # the client adds cookies and resumes from this line.
    async with async_session_factory() as session:
        cookie = (
            await gate_cookies_repo.get_active_for_rotation(
                session, tenant_id, gate_id
            )
            if gate_id is not None
            else None
        )
        cookie_value = cookie.value if cookie is not None else None
        cookie_id = cookie.id if cookie is not None else None
        # Hold the cookie row's lock only as long as needed to read the value;
        # the send happens with NO session held (a FloodWait may sleep minutes).
        await session.commit()
    if cookie_value is None:
        logger.warning(
            "event=cookies_exhausted tenant=%s gate=%s batch=%s line=%s",
            tenant_id,
            gate_id,
            batch_id,
            line_id,
        )
        await _release_line(tenant_id, batch_id, line_id)
        await _pause_cookie_batch(
            tenant_id, batch_id, batches_repo.PAUSE_COOKIES_EXHAUSTED
        )
        return False

    # (a) SIDE-BAND ``.cookie`` send — guarded, value never logged.
    try:
        await gateway.send(f".cookie {cookie_value}")
    except FloodWaitError as e:
        scheduler.note_flood_wait(float(e.seconds))
        logger.warning(
            "event=cookie_flood_wait seconds=%s tenant=%s gate=%s cookie=%s "
            "batch=%s — release + re-queue (no bare retry)",
            e.seconds,
            tenant_id,
            gate_id,
            cookie_id,
            batch_id,
        )
        await broadcaster.emit_global("flood.wait", {"seconds": e.seconds})
        await alerts.note_flood_wait()
        await _release_line(tenant_id, batch_id, line_id)
        return False
    except asyncio.CancelledError:
        raise
    except SessionLostError as e:
        logger.warning(
            "event=cookie_session_lost tenant=%s gate=%s cookie=%s batch=%s "
            "— release + latch watchdog",
            tenant_id,
            gate_id,
            cookie_id,
            batch_id,
        )
        await _release_line(tenant_id, batch_id, line_id)
        await watchdog.session_lost(str(e))
        return False
    except Exception:
        # MASKED, value-free log (NO ``error`` event — it interpolates str(e)
        # which could echo the ``.cookie`` text). The pair never completed:
        # release + re-queue; resume re-sends the FULL pair fresh.
        logger.exception(
            "event=cookie_send_error tenant=%s gate=%s cookie=%s batch=%s "
            "value=MASKED — release + re-queue",
            tenant_id,
            gate_id,
            cookie_id,
            batch_id,
        )
        await _release_line(tenant_id, batch_id, line_id)
        return False

    # (b) ``.amz`` send via the write-ahead path (intent already recorded). NO
    #     ``pick_next`` between the two. A FloodWait HERE is a PAIR-ABORT (the
    #     ``.cookie`` is already out and the global window is opening — another
    #     tenant's ``.cookie`` could clobber the account context): never a bare
    #     ``.amz`` retry — release + re-queue so resume re-sends the WHOLE pair.
    try:
        chat_id, message_id = await gateway.send(text)
    except FloodWaitError as e:
        scheduler.note_flood_wait(float(e.seconds))
        logger.warning(
            "event=flood_wait_pair_abort seconds=%s tenant=%s gate=%s "
            "cookie=%s batch=%s line=%s — pair-abort (no bare .amz retry)",
            e.seconds,
            tenant_id,
            gate_id,
            cookie_id,
            batch_id,
            line_id,
        )
        await broadcaster.emit_global("flood.wait", {"seconds": e.seconds})
        await alerts.note_flood_wait()
        await _release_line(tenant_id, batch_id, line_id)
        return False
    except asyncio.CancelledError:
        raise
    except SessionLostError as e:
        await _release_line(tenant_id, batch_id, line_id)
        await watchdog.session_lost(str(e))
        return False
    except Exception:
        # Generic ``.amz`` failure: the pair never completed, the cookie
        # context is burned — pair-abort (release + re-queue), never a bare
        # ``.amz`` retry. No value in ``text`` (it is the ``.amz`` line, not the
        # cookie) but no per-attempt error event either — keep cookie-mode
        # failures uniform and value-free.
        logger.exception(
            "event=amz_send_error tenant=%s batch=%s line=%s — pair-abort, "
            "release + re-queue",
            tenant_id,
            batch_id,
            line_id,
        )
        await _release_line(tenant_id, batch_id, line_id)
        return False

    # Record the ``.amz`` delivery (the write-ahead fail-stop) — ``.cookie`` is
    # NOT recorded. ``complete_on_drain=False``: the batch must STAY 'sending'
    # to await the verdict (the verdict, not the send, completes a drained
    # cookie-mode batch). Then ARM the serialize gate so the next ``step()``
    # skips this tenant until the verdict (or the 90s timeout).
    await _record_sent(
        tenant_id,
        batch_id,
        line_id,
        position,
        text,
        chat_id,
        message_id,
        complete_on_drain=False,
    )
    await _arm_await(batch_id, line_id, chat_id, message_id, cookie_id)
    logger.info(
        "event=cookie_pair_sent tenant=%s gate=%s cookie=%s batch=%s line=%s "
        "chat_id=%s message_id=%s",
        tenant_id,
        gate_id,
        cookie_id,
        batch_id,
        line_id,
        chat_id,
        message_id,
    )
    return True


async def _resend_cookie_line(
    tenant_id: int,
    batch_id: int,
    line_id: int,
) -> None:
    """Re-queue the SAME line for a TIMEOUT resend (per-attempt attribution).

    The verdict-timeout retry-once path: the bot went silent past the 90s
    window, so there is no dead cookie to mark — only RESET the line's write-
    ahead intent (clear ``message_id``) so the reused ``send_log`` row carries
    the NEW send's id, re-queue the line at its original position, and clear the
    await so the next ``step()`` re-sends the pair with a freshly-picked cookie.
    A late verdict for the superseded ``message_id`` is dropped by the attempt-
    fence in ``_apply_verdict``.

    The line is ``LINE_SENT`` (the ``.amz`` went out; the ``send_log`` row holds
    the OLD pair) — ``requeue_line_with_intent_reset`` flips it back to 'queued'
    and clears the pair. Burns the line's one timeout-retry DURABLY in the same
    txn (``mark_verdict_retried`` — the requeue just zeroed it) so the second
    elapse pauses even across a restart.

    Retry-forever fail-stop (the line must not strand mid-resend). The cookie-
    dead ROTATION path no longer uses this — it rotates atomically inside
    ``_apply_verdict`` under the held lock (FIX 2)."""
    while True:
        try:
            async with async_session_factory() as session:
                batch = await _locked_batch(session, batch_id)
                if batch is None:
                    return
                line = await session.get(BatchLine, line_id)
                if line is not None:
                    await batches_repo.requeue_line_with_intent_reset(
                        session, line
                    )
                    # Durable +1 in THIS txn (the requeue zeroed it): the second
                    # silent elapse pauses ``verdict_timeout`` even across a restart.
                    await batches_repo.mark_verdict_retried(session, line)
                await batches_repo.clear_awaiting_verdict(session, batch)
                await session.commit()
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=db_unreachable phase=cookie_resend batch=%s line=%s — "
                "retrying until the DB returns",
                batch_id,
                line_id,
            )
            await sleep_paced(_ERROR_RETRY_SECONDS)


async def _apply_verdict(verdict: CookieVerdict) -> None:
    """Drive one cookie-mode verdict signal (attempt-fenced, Phase 2).

    🔒 ATTEMPT-FENCE: accept the verdict ONLY if its ``message_id`` matches the
    batch's ``awaiting_message_id`` AND the await is still set — verified in-txn
    under the batch FOR UPDATE. A verdict for a superseded attempt (rotation/
    timeout resend) or an already-cleared await is logged and DROPPED (closes
    verdict-edit double-fire, timeout-then-late-verdict, pause-race signals).

    Routing (capture already persisted the durable ``kind='full'`` row + any CC
    / line-failure before signalling, so the reconciler is already idempotent):
    - ``approved``/``declined`` ⇒ clear the await; the tenant resumes to the
      next line (capture saved Filtrada+ok / the rejected full row).
    - ``format_error`` ⇒ the line is already ``failed`` by capture; clear the
      await; next line (cookie NOT rotated).
    - ``cookie_dead`` ⇒ ``mark_dead`` the current cookie + reset intent +
      re-queue the SAME line at its position; the next ``step()`` resends with
      the next-oldest active cookie. If none remain ⇒ pause ``cookies_exhausted``.
    """
    line_id = verdict.line_id
    # Resolve which batch this line belongs to + attempt-fence under its lock.
    async with async_session_factory() as session:
        line = await session.get(BatchLine, line_id)
        if line is None:
            logger.info(
                "event=cookie_verdict_dropped reason=line_gone message_id=%s "
                "line=%s kind=%s",
                verdict.message_id,
                line_id,
                verdict.verdict_kind,
            )
            return
        batch_id = line.batch_id
        tenant_id = line.tenant_id
        batch = await _locked_batch(session, batch_id)
        if (
            batch is None
            or batch.awaiting_message_id is None
            or batch.awaiting_message_id != verdict.message_id
            or batch.awaiting_chat_id != verdict.chat_id
        ):
            # Superseded attempt or already-cleared await: drop (no double-
            # advance, no double-save, no rotating a healthy cookie).
            logger.info(
                "event=cookie_verdict_dropped reason=attempt_fence "
                "message_id=%s awaited=%s batch=%s line=%s kind=%s",
                verdict.message_id,
                batch.awaiting_message_id if batch is not None else None,
                batch_id,
                line_id,
                verdict.verdict_kind,
            )
            await session.commit()
            return
        if batch.state != batches_repo.STATE_SENDING:
            # The batch is no longer sending (a manual pause / stop / cancel
            # landed). Drop the verdict — never mutate a non-sending batch behind
            # the user's back. The pause/resume + timeout-sweep path recovers the
            # line (a manual pause already re-queued the awaited line and cleared
            # the await; a stop/cancel discards it). The fence above may still
            # pass briefly if the pause raced this drain, so this is the second
            # guard.
            logger.info(
                "event=cookie_verdict_dropped reason=not_sending state=%s "
                "message_id=%s batch=%s line=%s kind=%s",
                batch.state,
                verdict.message_id,
                batch_id,
                line_id,
                verdict.verdict_kind,
            )
            await session.commit()
            return
        kind = verdict.verdict_kind
        if kind in (VERDICT_APPROVED, VERDICT_DECLINED, VERDICT_FORMAT_ERROR):
            # Consumed / line-failed: release the gate; the tenant resumes on
            # the next step (capture already persisted the terminal row + any
            # CC / line-failure). If this consumed the LAST pending line, the
            # batch drains 'completed' HERE (the send deliberately did NOT —
            # ``complete_on_drain=False`` — so a cookie-mode batch completes only
            # once its final line's verdict lands).
            await batches_repo.clear_awaiting_verdict(session, batch)
            drained = await batches_repo.complete_if_drained(session, batch)
            state_payload = (
                batches_service.state_data(batch, "idle") if drained else None
            )
            progress = await batches_service.progress_data(session, batch)
            await session.commit()
            logger.info(
                "event=cookie_verdict_consumed kind=%s tenant=%s batch=%s "
                "line=%s drained=%s",
                kind,
                tenant_id,
                batch_id,
                line_id,
                drained,
            )
            await broadcaster.emit(tenant_id, "batch.progress", progress)
            if state_payload is not None:
                await broadcaster.emit(tenant_id, "batch.state", state_payload)
            return
        if kind != VERDICT_COOKIE_DEAD:
            # Unknown kind (defensive): clear and move on.
            await batches_repo.clear_awaiting_verdict(session, batch)
            await session.commit()
            return

        # 🔒 cookie_dead — rotate ATOMICALLY in THIS one txn (still holding the
        # batch FOR UPDATE from the attempt-fence above). Mark the cookie that
        # was ACTUALLY sent for this attempt (``BatchLine.failed_cookie_id``,
        # stamped by ``_arm_await``) — NOT a re-derived "oldest active" across
        # unlocked sessions, which could burn a healthy cookie if the vault
        # changed during the 90s await. ``count_active_for`` in the SAME session
        # excludes the just-dead row (the ``status`` filter sees the flushed
        # ``mark_dead``), so the exhaustion decision is consistent.
        gate_id = batch.gate_id
        dead_cookie_id = line.failed_cookie_id
        if dead_cookie_id is not None:
            await gate_cookies_repo.mark_dead(session, dead_cookie_id, tenant_id)
        remaining = (
            await gate_cookies_repo.count_active_for(session, tenant_id, gate_id)
            if gate_id is not None
            else 0
        )
        # Re-queue the SAME line at its position with its write-ahead intent
        # reset (capture already persisted the dead attempt's terminal full row,
        # so its later edits resolve via the OLD pair). The dead cookie is
        # already flushed dead in this txn, so the next ``step()`` cannot
        # re-pick it.
        await batches_repo.requeue_line_with_intent_reset(session, line)
        if remaining == 0:
            # No active cookie left ⇒ pause ``cookies_exhausted`` (the re-queued
            # line waits for the client to add cookies + resume). Clears the
            # await so resume's fence-based re-queue is a clean no-op (the line
            # is already 'queued' here). All in this ONE committed txn.
            batch.state = batches_repo.STATE_PAUSED
            await batches_repo.set_pause_reason(
                session, batch, batches_repo.PAUSE_COOKIES_EXHAUSTED
            )
            await batches_repo.clear_awaiting_verdict(session, batch)
            exhausted_payload = batches_service.state_data(
                batch, "paused", pause_reason=batches_repo.PAUSE_COOKIES_EXHAUSTED
            )
        else:
            # Cookie remains: clear the await so the next ``step()`` picks the
            # next-oldest active cookie fresh and resends this line.
            await batches_repo.clear_awaiting_verdict(session, batch)
            exhausted_payload = None
        await session.commit()

    logger.info(
        "event=cookie_rotated tenant=%s gate=%s dead_cookie=%s batch=%s "
        "line=%s remaining_active=%s",
        tenant_id,
        gate_id,
        dead_cookie_id,
        batch_id,
        line_id,
        remaining,
    )
    if exhausted_payload is not None:
        await broadcaster.emit(tenant_id, "batch.state", exhausted_payload)


async def _sweep_verdict_timeouts() -> None:
    """Verdict-timeout sweep (Phase 2): a cookie-mode batch whose
    ``awaiting_verdict_until`` elapsed with no verdict.

    The line is ``LINE_SENT`` (the ``.amz`` went out) so it never reappears in
    ``active_senders`` (which needs a 'queued' line) once the gate clears — the
    timeout MUST be swept explicitly here (mirror of ``_admit_waiting``).

    🔒 The awaited line is resolved via the ATTEMPT-FENCE
    (``batches_repo.awaited_line_id`` — ``send_log.(chat_id, message_id) ==
    batch.(awaiting_chat_id, awaiting_message_id)`` under the batch ``FOR
    UPDATE``), NOT "the batch's single ``LINE_SENT`` row". A consumed
    (approved/declined) line STAYS ``LINE_SENT`` like any normal sent line, so a
    multi-line cookie batch can hold several ``LINE_SENT`` rows while only ONE is
    actually awaiting a verdict — keying off ``LINE_SENT`` would re-send already-
    consumed lines. If the fence resolves nothing (the await was cleared), the
    batch is skipped.

    First elapse ⇒ retry the line ONCE with a fresh cookie + a NEW awaited
    ``message_id`` (reset the intent + re-queue so ``step()`` re-sends the pair).
    Second elapse ⇒ pause ``verdict_timeout`` + owner alert. A late verdict for
    the superseded ``message_id`` is dropped by the attempt-fence in
    ``_apply_verdict``."""
    timed_out: list[tuple[int, int, int, int]] = []  # (tenant, batch, line, retries)
    async with async_session_factory() as session:
        stmt = (
            select(Batch.tenant_id, Batch.id)
            .where(
                Batch.state == batches_repo.STATE_SENDING,
                Batch.awaiting_verdict_until.is_not(None),
                Batch.awaiting_verdict_until <= func.now(),
            )
        )
        rows = (await session.execute(stmt)).all()
        for tenant_id, batch_id in rows:
            # Lock the batch + resolve the awaited line via the attempt-fence
            # (the single in-flight line, identified by send_log, not by
            # LINE_SENT state). The fence read is consistent under the lock.
            batch = await _locked_batch(session, batch_id)
            if batch is None:
                continue
            line_id = await batches_repo.awaited_line_id(session, batch)
            if line_id is not None:
                # Read the DURABLE retry budget under the same lock (replaces the
                # old process-memory ``_timeout_retried`` set — survives a restart,
                # so a crash loop can't grant a fresh retry per restart).
                line = await session.get(BatchLine, line_id)
                retries = line.verdict_timeout_retries if line is not None else 0
                timed_out.append((tenant_id, batch_id, line_id, retries))
        await session.commit()

    for tenant_id, batch_id, line_id, retries in timed_out:
        if retries >= 1:
            # Second silent elapse (the one durable retry already burned) ⇒ pause
            # + owner alert (structured WARNING, the codebase's alert shape). The
            # resume re-queue zeroes the budget for a fresh attempt.
            logger.warning(
                "event=verdict_timeout_pause tenant=%s batch=%s line=%s — "
                "bot silent past retry-once; pausing for owner",
                tenant_id,
                batch_id,
                line_id,
            )
            await _pause_cookie_batch(
                tenant_id, batch_id, batches_repo.PAUSE_VERDICT_TIMEOUT
            )
        else:
            # First elapse ⇒ retry once with a fresh cookie + new awaited id.
            logger.warning(
                "event=verdict_timeout_retry tenant=%s batch=%s line=%s — "
                "retrying once with a fresh cookie",
                tenant_id,
                batch_id,
                line_id,
            )
            await _resend_cookie_line(tenant_id, batch_id, line_id)


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
) -> (
    tuple[Literal["sent"], int, int]
    | tuple[Literal["failed", "session_lost"], str]
    | Literal["release", "abort"]
):
    """Deliver ``text`` (→ ``("sent", chat_id, message_id)``) — or yield/give up.

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
            chat_id, message_id = await gateway.send(text)
            return ("sent", chat_id, message_id)
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
        candidates: list[tuple[int, int, str]] = []  # (chat_id, message_id, text)
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

        used: set[tuple[int, int]] = set()
        if verified and candidates:
            async with async_session_factory() as session:
                used = await send_log_repo.used_message_pairs(
                    session, [(chat_id, mid) for chat_id, mid, _ in candidates]
                )

        for line_id, batch_id, tenant_id, line_text in stuck_data:
            match_pair: tuple[int, int] | None = None
            if verified:
                # iter_messages lists newest first — the newest match wins.
                # Match the (chat_id, message_id) PAIR: the id alone collides
                # across the per-chat destination sequences.
                for chat_id, message_id, message_text in candidates:
                    if (chat_id, message_id) not in used and (
                        message_text == line_text
                    ):
                        match_pair = (chat_id, message_id)
                        break
            async with async_session_factory() as session:
                line = await session.get(BatchLine, line_id)
                if line is None:
                    continue
                if match_pair is not None:
                    await batches_repo.mark_sent(session, line)
                    # Idempotent get-or-create FIRST (deferred 2-5 :616): a
                    # line left 'sending' by a pre-2.5 crash has NO intent row
                    # and set_message_id is a bare UPDATE that would silently
                    # no-op — leaving the confirmed line unattributable for
                    # 3.1 and its message_id invisible to used_message_pairs.
                    await send_log_repo.record_intent(session, line)
                    await send_log_repo.set_message_id(
                        session, line_id, match_pair[0], match_pair[1]
                    )
                    used.add(match_pair)
                    # Same batch finalization as the step's record phase.
                    batch = await _locked_batch(session, batch_id)
                    if batch is not None:
                        if batch.state == batches_repo.STATE_STOPPING:
                            batch.state = batches_repo.STATE_STOPPED
                        elif await _is_cookie_mode_batch(session, batch):
                            # Cookie-mode (Phase 2): a confirmed ``.amz`` is
                            # awaiting its verdict — RECONSTRUCT the serialize
                            # gate (a ``.cookie`` outgoing message never matches
                            # a line: its ``.cookie `` prefix differs from the
                            # ``.amz `` line text, so it is never reconciled
                            # here). Without this the post-boot ``step()`` would
                            # never re-send (the line is 'sent') but also never
                            # wait for the verdict — the line would silently
                            # stall, never consumed. Arming it lets the verdict/
                            # timeout drive it exactly as in-process.
                            await batches_repo.set_awaiting_verdict(
                                session,
                                batch,
                                chat_id=match_pair[0],
                                message_id=match_pair[1],
                                timeout_seconds=_VERDICT_TIMEOUT_SECONDS,
                            )
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


async def _drain_verdicts() -> None:
    """Apply every pending cookie-mode verdict signal (Phase 2).

    Drained at the top of each loop turn (the FAST path; the 90s timeout is the
    durable backstop). Each verdict is BOTH attempt-fenced AND state-gated under
    the batch FOR UPDATE inside ``_apply_verdict`` — a stale/superseded signal,
    OR a verdict for a non-sending (paused/stopping/stopped/cancelled) batch, is
    logged and dropped there (the pause/resume + timeout path recovers the line).
    A failure on one verdict must not wedge the loop: it is logged and the rest
    still drain (the durable ``awaiting_verdict_until`` gate + timeout recover
    a dropped one)."""
    for verdict in drain_cookie_verdicts():
        try:
            await _apply_verdict(verdict)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event=cookie_verdict_failed message_id=%s line=%s kind=%s — "
                "dropped (timeout gate recovers)",
                verdict.message_id,
                verdict.line_id,
                verdict.verdict_kind,
            )


async def run_worker() -> None:
    """Infinite drain loop (created as a task in the lifespan)."""
    await _boot_recovery()

    while True:
        try:
            # Cookie-mode (Phase 2): apply pending verdicts (rotate/consume/
            # fail) and sweep elapsed verdict timeouts BEFORE the send step, so
            # a rotated/timed-out line is re-queued in time for this turn's
            # claim. Both are no-ops with zero cookie-mode batches in flight.
            await _drain_verdicts()
            await _sweep_verdict_timeouts()
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
            # System-controlled CONSTANT interval between sends (FR12):
            # G = g_min regardless of n. sleep_paced is wake-immune — a control
            # never makes the system send faster. n no longer affects pacing,
            # so the per-send active-sender count (and its DB round-trip) is
            # gone; the FloodWait governor still tunes g_min upward.
            await sleep_paced(scheduler.interval(1))
        else:
            await sleep_cancelable(_IDLE_SLEEP_SECONDS)
