"""Bot-reply capture pipeline (Story 3.1): queue + single consumer + the
legacy ✅/❌/⏳ state machine.

Telethon NEVER crosses this boundary: the bridge in ``core/telegram.py``
converts events into the plain ``IncomingReply`` dataclass and calls
``enqueue``. A single consumer task (``run_capture``, created in the
lifespan next to the send worker) serializes processing — Telethon may
dispatch handlers concurrently; the queue restores the legacy ordering and
makes the CC dedup race-free without locks.

The blocked-queue + single-consumer pair IS the in-memory reply buffer the
2.5 review deferred to this story (deferred 2-5 ``send_worker.py:28``): while
the DB is down, ``process_incoming`` raises, the consumer retries the SAME
item forever (mirror of the worker's ``_record_sent`` fail-stop) and every
other incoming reply accumulates in ``_queue``, flushing IN ORDER when the DB
returns. Two bounded exceptions (review 3-1): a NON-transient failure (e.g.
a data/constraint error Postgres will reject identically forever) quarantines
the item after ``_POISON_ATTEMPTS`` — a poison reply must not wedge capture
for every tenant — and a reply that races the worker's record phase
(``send_log.message_id`` still NULL when the reply lands) re-enqueues up to
``_ATTRIBUTION_ATTEMPTS`` times before it is declared unmatched. The lifespan
additionally holds the consumer (``hold_until_boot``) until the worker's boot
recovery confirms the message ids of lines a crash left 'sending'.
Telegram-side disconnections were already covered by ``catch_up=True``
(telegram.py, since 2.2).

Port of legacy ``_manejar_bot`` (app.py) with the per-message_id state
derived from the DB (``responses.last_full_revision``) instead of an
in-memory dict — survives restarts and dedups ``catch_up`` replays. Recorded
deviation from the legacy DISK behavior: ``'rejected'`` revisions ARE
persisted (Postgres now backs Story 3.2's Completa view, which paints ❌
rows); EMISSION parity stays exact (ok transition / ok-edit with new CC /
rejected transition; ok→ok without new CC persists silently).
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import exc as sa_exc

from app.core import alerts, attribution
from app.core.broadcaster import broadcaster
from app.core.cc_extract import extract_cc
from app.core.redact import redact_reply_text
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.repos import responses as responses_repo
from app.services import batches as batches_service

logger = logging.getLogger(__name__)

# Delay before retrying the SAME item after a DB failure. A module constant,
# NOT a setting (2.5 rule: no new configuration for pipeline internals).
_RETRY_SECONDS = 2.0

# The send→record race (review 3-1 HIGH): the worker fills send_log's
# message_id AFTER the Telegram send (record phase, retried on a ~2s cadence
# while the DB is down). A reply attributed in that window finds message_id
# NULL and would be bucketed unmatched FOREVER — so attribution gets
# _ATTRIBUTION_ATTEMPTS tries, re-enqueued _ATTRIBUTION_RETRY_SECONDS apart
# (~10s total, generous against the 2s record cadence), and only the FINAL
# attempt logs/counts toward the unmatched bucket.
_ATTRIBUTION_ATTEMPTS = 3
_ATTRIBUTION_RETRY_SECONDS = 5.0

# Bounded retries for NON-transient failures (review 3-1 MEDIUM): a poison
# item (e.g. a data error Postgres rejects identically on every attempt) must
# not wedge the single global consumer — after this many attempts it is
# quarantined with a distinct structured log and the queue keeps flowing.
# Transient/connectivity failures still retry forever (the DB-down buffer).
_POISON_ATTEMPTS = 5


@dataclass(frozen=True)
class IncomingReply:
    """Plain-data view of one bot message — the only thing that crosses the
    telethon boundary (``edited=True`` for ``MessageEdited`` events)."""

    message_id: int
    reply_to_msg_id: int | None
    text: str
    edited: bool
    # Attribution attempts consumed so far (the send→record race retry) — NOT
    # part of the bridge contract: retries re-enqueue via dataclasses.replace.
    attempts: int = 0


# Unbounded on purpose: this queue is the DB-down buffer — replies must never
# be dropped at enqueue time (the consumer is the only backpressure).
_queue: asyncio.Queue[IncomingReply] = asyncio.Queue()

# Delayed re-enqueues of replies that raced the worker's record phase — kept
# so reset() can cancel them (and the event loop can't GC a sleeping task).
_pending_retries: set[asyncio.Task[None]] = set()

# Boot gate (review 3-1 HIGH, boot variant): catch_up replays may reference
# message ids the worker's boot recovery is still confirming — the lifespan
# holds the consumer (replies buffer in _queue meanwhile) until
# send_worker._boot_recovery releases it. SET by default so tests and
# non-lifespan callers run immediately.
_boot_gate = asyncio.Event()
_boot_gate.set()

# Unmatched-replies monitoring bucket (AC 7) — process-memory counter, mirror
# of send_worker._sent_by_tenant: observability only (ban-guardrail,
# architecture assumption A1), seed of Story 4.3's dashboards.
_unmatched_total = 0


def unmatched_total() -> int:
    """Current unmatched bucket size (Story 4.3 observability slice)."""
    return _unmatched_total


def enqueue(reply: IncomingReply) -> None:
    """Bridge entry point (synchronous — called from telegram.py handlers).

    Feeds the reply-rate watchdog at ARRIVAL time (Story 4.1, recorded
    decision): the signal is "the bot is alive", independent of DB health
    (with the DB down replies buffer here and the watchdog must stay calm —
    the fail-stop already halted sending) and of attribution (an unmatched
    reply proves life all the same).
    """
    watchdog.note_reply()
    _queue.put_nowait(reply)


def hold_until_boot() -> None:
    """Lifespan: pause consumption until the worker's boot recovery confirms
    the message ids a crash left unconfirmed (replies buffer meanwhile)."""
    _boot_gate.clear()


def boot_recovered() -> None:
    """Send worker: boot recovery finished — release the consumer."""
    _boot_gate.set()


def reset() -> None:
    """Wipe module state (tests): drain the queue, cancel pending attribution
    retries, zero the counter, release the boot gate."""
    global _unmatched_total
    for task in list(_pending_retries):
        task.cancel()
    _pending_retries.clear()
    while not _queue.empty():
        _queue.get_nowait()
    _unmatched_total = 0
    _boot_gate.set()


def _requeue_later(reply: IncomingReply) -> None:
    """Re-enqueue ``reply`` after the attribution retry delay WITHOUT blocking
    the consumer — other tenants' replies keep flowing in between."""

    async def _delayed() -> None:
        await asyncio.sleep(_ATTRIBUTION_RETRY_SECONDS)
        _queue.put_nowait(reply)

    task = asyncio.create_task(_delayed())
    _pending_retries.add(task)
    task.add_done_callback(_pending_retries.discard)


# Connectivity-shaped errors retry forever (the DB-down buffer semantics);
# anything else is bounded by _POISON_ATTEMPTS. OSError covers the raw
# socket-level failures asyncpg raises unwrapped on connect (refused/reset);
# TimeoutError is the builtin (== asyncio.TimeoutError since 3.11).
_TRANSIENT_ERRORS = (
    OSError,
    TimeoutError,
    sa_exc.InterfaceError,
    sa_exc.OperationalError,
    sa_exc.TimeoutError,
)


def _is_transient(error: BaseException) -> bool:
    """True for connectivity-shaped failures (retry forever); False for
    everything else (bounded retries, then quarantine).

    ``DBAPIError.connection_invalidated`` catches driver errors SQLAlchemy
    already diagnosed as a dead connection regardless of their class.
    """
    if isinstance(error, sa_exc.DBAPIError) and error.connection_invalidated:
        return True
    return isinstance(error, _TRANSIENT_ERRORS)


async def run_capture() -> None:
    """Infinite consumer (mirror of ``run_worker``): one item at a time.

    Transient DB failure → retry the SAME item forever (the DB-down buffer:
    nothing connectivity-shaped is ever discarded; new replies buffer in
    ``_queue``). Non-transient failure → bounded retries, then quarantine
    (review 3-1: a poison reply must not halt capture for every tenant)."""
    await _boot_gate.wait()
    while True:
        reply = await _queue.get()
        attempts = 0
        while True:
            try:
                await process_incoming(reply)
                break
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if _is_transient(error):
                    logger.exception(
                        "event=db_unreachable phase=capture message_id=%s — "
                        "retrying the same reply; new replies buffer in memory "
                        "until the DB returns",
                        reply.message_id,
                    )
                    await asyncio.sleep(_RETRY_SECONDS)
                    continue
                attempts += 1
                if attempts >= _POISON_ATTEMPTS:
                    logger.exception(
                        "event=capture_quarantined message_id=%s attempts=%s "
                        "— non-transient failure; dropping the item so the "
                        "queue keeps flowing",
                        reply.message_id,
                        attempts,
                    )
                    break
                logger.exception(
                    "event=capture_retry message_id=%s attempt=%s — "
                    "non-transient failure, bounded retry",
                    reply.message_id,
                    attempts,
                )
                await asyncio.sleep(_RETRY_SECONDS)


async def process_incoming(reply: IncomingReply) -> None:
    """Attribute + persist + emit for ONE bot message (port of ``_manejar_bot``).

    Opens its own session (``async_session_factory`` — never request-scoped),
    emits AFTER the commit, and captures every emitted attribute before the
    session closes (the 2.3 MissingGreenlet lesson).
    """
    global _unmatched_total

    async with async_session_factory() as session:
        attributed = await attribution.resolve(
            session,
            message_id=reply.message_id,
            reply_to_msg_id=reply.reply_to_msg_id,
        )
        if attributed is None:
            if reply.attempts + 1 < _ATTRIBUTION_ATTEMPTS:
                # Likely the send→record race (the worker commits message_id
                # AFTER delivery) or boot recovery still confirming a crashed
                # 'sending' line: NON-terminal — re-enqueue with a delay and
                # only bucket on the final attempt (review 3-1 HIGH).
                _requeue_later(replace(reply, attempts=reply.attempts + 1))
                logger.info(
                    "event=unmatched_retry message_id=%s reply_to=%s attempt=%s",
                    reply.message_id,
                    reply.reply_to_msg_id,
                    reply.attempts + 1,
                )
                return
            # Unmatched bucket (AC 7), final attempt only: log + count, save
            # NOTHING. The legacy saved every bot message in the chat;
            # multi-tenant forbids saving without attribution.
            _unmatched_total += 1
            logger.warning(
                "event=unmatched_reply message_id=%s reply_to=%s total=%s",
                reply.message_id,
                reply.reply_to_msg_id,
                _unmatched_total,
            )
            # Abnormal growth alerts the owner (Story 4.3, AC 3 — attribution
            # health is part of the ban guardrail). Final attempts only:
            # retries of the send→record race never feed the window.
            await alerts.note_unmatched()
            return

        # 🔒 Strip the operator "Checked By" line BEFORE anything reads the
        # text — dedup, storage, CC extraction and emission must all see the
        # redacted version so the name never persists nor reaches a tenant.
        clean_text = redact_reply_text(reply.text)

        previous = await responses_repo.last_full_revision(
            session, reply.message_id
        )
        if previous is not None and previous.text == clean_text:
            return  # edition with no real change (legacy parity) — total no-op

        previous_status = previous.status if previous is not None else None
        status: str | None
        if "✅" in clean_text:
            status = responses_repo.STATUS_OK
        elif "❌" in clean_text:
            status = responses_repo.STATUS_REJECTED
        else:
            # Intermediate edit (⏳) keeps the previous state (legacy parity).
            status = previous_status
        if status is None:
            # First ⏳ with no emoji: no row (legacy parity — recorded
            # decision). Its later ✅/❌ edit arrives with reply_to intact and
            # attributes the same. Commit anyway: resolve() may have
            # backfilled a pre-3.1 batch binding.
            await session.commit()
            return

        await responses_repo.add_full(
            session,
            tenant_id=attributed.tenant_id,
            capture_session_id=attributed.capture_session_id,
            batch_id=attributed.batch_id,
            line_id=attributed.line_id,
            message_id=reply.message_id,
            status=status,
            text=clean_text,
        )
        new_cc: list[str] = []
        if status == responses_repo.STATUS_OK:
            new_cc = await responses_repo.add_new_cc(
                session,
                tenant_id=attributed.tenant_id,
                capture_session_id=attributed.capture_session_id,
                batch_id=attributed.batch_id,
                line_id=attributed.line_id,
                message_id=reply.message_id,
                values=extract_cc(clean_text),
            )
        cc_total = await responses_repo.cc_count(
            session, attributed.capture_session_id
        )
        # "Esperando respuesta" recomputed AFTER add_full's flush (so this
        # reply is already counted as answered): a message's FIRST ✅/❌ drops
        # the count by one, a later revision of the same message leaves it
        # unchanged (DISTINCT message_id). Authoritative — the frontend assigns
        # it, same contract as cc_total.
        awaiting_reply = await batches_service.awaiting_reply_count(
            session, attributed.capture_session_id
        )
        # Capture everything the emission needs BEFORE closing the session.
        tenant_id = attributed.tenant_id
        capture_session_id = attributed.capture_session_id
        batch_id = attributed.batch_id
        await session.commit()

    # Emission parity with the legacy (exact): transition to ok / ok-edit
    # with new CC / transition to rejected. ok→ok with new text but no new CC
    # persists WITHOUT emitting (legacy parity, recorded decision). ❌→✅
    # "moves the counters" by itself: the latest revision per message_id IS
    # the current state — counts are derived, never columns.
    is_transition = status != previous_status
    is_ok_edit = (
        status == responses_repo.STATUS_OK and not is_transition and bool(new_cc)
    )
    if not is_transition and not is_ok_edit:
        return
    await broadcaster.emit(
        tenant_id,
        "response.captured",
        {
            "session_id": capture_session_id,
            "batch_id": batch_id,
            "message_id": reply.message_id,
            "status": status,
            "previous_status": previous_status,
            "edited": reply.edited,
            "text": clean_text,
            "new_cc": new_cc,
            "cc_total": cc_total,
            "awaiting_reply": awaiting_reply,
            "captured_at": datetime.now(UTC).isoformat(),
        },
    )
