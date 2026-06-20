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
from app.core.cookie_verdict import CookieVerdict
from app.core.cookie_verdict import signal as cookie_verdict_signal
from app.core.display_transform import display_transform
from app.core.redact import (
    COOKIE_CONFIRMATION_MARKER,
    VERDICT_APPROVED,
    VERDICT_COOKIE_DEAD,
    VERDICT_DECLINED,
    VERDICT_FORMAT_ERROR,
    normalize_cookie_cc,
    parse_amazon_verdict,
    parse_approveds,
    redact_reply_text,
    strip_special_stats,
)
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, CaptureSession
from app.db.repos import batches as batches_repo
from app.db.repos import responses as responses_repo
from app.services import batches as batches_service

# Fail code recorded on a cookie-mode line whose reply is the bot's ``Format :``
# help message (a malformed ``.amz`` line) — a LINE-level terminal failure, NOT
# a cookie-dead rotation. The frontend maps it to Spanish copy.
_AMAZON_FORMAT_ERROR = "amazon_format_error"

# Verdict kinds that consume/rotate a cookie-mode line and therefore hand a
# signal to the send worker (``none``/``confirmation`` never signal).
_SIGNALLING_VERDICTS = frozenset(
    {
        VERDICT_APPROVED,
        VERDICT_DECLINED,
        VERDICT_COOKIE_DEAD,
        VERDICT_FORMAT_ERROR,
    }
)

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
    # Marked peer id of the chat this message lives in. Load-bearing for
    # attribution: message ids are per-chat, not account-global, so the key is
    # the (chat_id, message_id) PAIR (and (chat_id, reply_to_msg_id) → send_log).
    # Production ALWAYS sets it (the bridge from event.chat_id, the reconciler
    # from history); the ``0`` default is the single-id-space sentinel tests
    # (and any pre-multi-target path) rely on.
    chat_id: int = 0
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


def reconcile_enqueue(reply: IncomingReply) -> None:
    """Reply-reconciler entry point. Like ``enqueue`` but does NOT feed the
    reply-rate watchdog: a reconciled reply is HISTORICAL (re-read from chat
    history to recover a dropped update), so counting it as a live "bot is
    alive" signal would falsify the watchdog — the exact reason
    ``send_worker._boot_recovery`` never calls ``watchdog.note_sent()`` for
    reconciled sends. The single consumer then attributes/persists/emits it
    identically, idempotently (an already-captured reply is a total no-op)."""
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

    # 🔒 Side-band ``.cookie`` confirmation drop (Amazon cookie-mode, Phase 2).
    # The ``.cookie`` half of the atomic pair is side-band — no ``send_log``/
    # ``batch_line`` row — so its ``…almacenó tu cookie correctamente. ✅``
    # confirmation attributes to NOTHING. A content-sniff (not the
    # attribution-miss path) drops it at the very top: every cookie-mode line
    # yields one confirmation, so routing it through attribution-miss would
    # steadily inflate ``_unmatched_total`` and trip the ban guardrail. No
    # attribution, no unmatched retry, no ``alerts.note_unmatched``, no
    # ``_unmatched_total`` bump, no verdict signal. ``watchdog.note_reply`` was
    # already fed at ``enqueue`` (liveness, unchanged) — that stays.
    if reply.text and COOKIE_CONFIRMATION_MARKER in reply.text:
        logger.info(
            "event=cookie_confirmation_dropped message_id=%s chat_id=%s",
            reply.message_id,
            reply.chat_id,
        )
        return

    async with async_session_factory() as session:
        attributed = await attribution.resolve(
            session,
            chat_id=reply.chat_id,
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

        # 🔒 Strip the operator "Checked By" line + the global Credits segment
        # BEFORE anything reads the text — dedup, storage, CC extraction and
        # emission must all see the redacted version so neither the operator's
        # name nor the owner's balance ever persists nor reaches a tenant.
        redacted = redact_reply_text(reply.text)

        # Special-mode sessions (special-mode feature): the gate category was
        # flagged, so this session snapshots special_mode=True. There the
        # checker's "Approveds! ✅: N" count drives the verdict — a bare ✅ glyph
        # in "Approveds! ✅: 0" is NOT an approval (it was a false positive) —
        # and the Approveds!/Deads! segments are scrubbed from the stored reply.
        # Read from the SESSION snapshot (always present; batch_id may be NULL).
        capture_session = await session.get(
            CaptureSession, attributed.capture_session_id
        )
        special = capture_session is not None and capture_session.special_mode
        # Cookie-mode sessions (Amazon cookie-vault, Phase 2): the session
        # snapshots cookie_mode=True. There the verdict is OWNED by the bot's
        # ``⌿ Status:`` token (Approved/Declined carry NO ✅/❌ glyph), a hard
        # short-circuit that REPLACES the legacy glyph chain — never falls
        # through to previous_status. Classification happens ONLY here (inside
        # the attributed branch); an unattributed ``⌿ Status:`` reply was
        # already bucketed unmatched above, exactly like today.
        cookie_mode = (
            capture_session is not None and capture_session.cookie_mode
        )
        # Parse the count from the redacted-but-not-yet-stripped text BEFORE the
        # strip removes it; clean_text is what gets stored/dedup'd/emitted.
        # ``strip_special_stats`` is NEVER applied to cookie-mode replies.
        approveds = parse_approveds(redacted) if special else None
        clean_text = strip_special_stats(redacted) if special else redacted

        previous = await responses_repo.last_full_revision(
            session, chat_id=reply.chat_id, message_id=reply.message_id
        )
        previous_status = previous.status if previous is not None else None
        status: str | None
        # Cookie-mode classification state (only meaningful when cookie_mode):
        # the verdict kind handed to the worker, the CC values extracted from
        # the Approved card, and whether this reply marks the line failed.
        cookie_verdict_kind: str | None = None
        cookie_cc_values: list[str] = []
        cookie_line_failed = False
        if cookie_mode:
            # 🔒 HARD short-circuit OWNING status — runs on the REDACTED text
            # (Checked By / Credits already scrubbed); NO special-mode strip.
            kind, _token = parse_amazon_verdict(redacted)
            cookie_verdict_kind = kind
            if kind == VERDICT_APPROVED:
                # Approved → ok + the bare card. Normalize the inline ``⌿`` /
                # leading ``☇`` separators to newlines BEFORE extract_cc so the
                # ``CC:`` line terminates before ``Status:`` (yields the bare
                # ``377481016137504|05|2033|3845``, no trailing ``⌿``).
                status = responses_repo.STATUS_OK
                cookie_cc_values = extract_cc(normalize_cookie_cc(clean_text))
            elif kind == VERDICT_DECLINED:
                # Declined → rejected: line consumed, cookie ALIVE, NOTHING to
                # Filtrada, but a full rejected revision IS persisted.
                status = responses_repo.STATUS_REJECTED
            elif kind == VERDICT_COOKIE_DEAD:
                # Cookie dead → a full dead-verdict revision (rejected status)
                # so the reconciler stops re-feeding; the worker rotates+resends
                # on the signal.
                status = responses_repo.STATUS_REJECTED
            elif kind == VERDICT_FORMAT_ERROR:
                # Malformed ``.amz`` → LINE-level terminal failure (no rotation,
                # cookie stays active). Persist a terminal marker revision +
                # mark the line failed.
                status = responses_repo.STATUS_REJECTED
                cookie_line_failed = True
            else:
                # confirmation (already content-sniffed away normally) / none:
                # a pure no-verdict edit — keep previous, write nothing, no
                # signal (mirrors the legacy ⏳ intermediate state).
                status = previous_status
        elif special:
            # Validity = "Approveds! ✅: N" with N≥1. No Approveds line yet ⇒
            # still processing — the legacy ⏳ intermediate state (keep previous).
            if approveds is None:
                status = previous_status
            elif approveds >= 1:
                status = responses_repo.STATUS_OK
            else:
                status = responses_repo.STATUS_REJECTED
        elif "✅" in clean_text:
            status = responses_repo.STATUS_OK
        elif "❌" in clean_text:
            status = responses_repo.STATUS_REJECTED
        else:
            # Intermediate edit (⏳) keeps the previous state (legacy parity).
            status = previous_status

        # No-op edit (legacy parity): the stored text AND the status are both
        # unchanged. Status is part of the discriminator — NOT just the text —
        # because special mode strips the Approveds!/Deads! count out of
        # clean_text: a rejected→ok flip ("Approveds! ✅: 0" → "✅: 2") whose
        # only other content is an unchanged "Time:" reduces to byte-identical
        # clean_text, and a text-only no-op would silently drop the very
        # approval this feature exists to catch (review 4 CRITICAL).
        if (
            previous is not None
            and previous.text == clean_text
            and previous.status == status
        ):
            return

        if status is None:
            # First ⏳ with no emoji: no row (legacy parity — recorded
            # decision). Its later ✅/❌ edit arrives with reply_to intact and
            # attributes the same. Commit anyway: resolve() may have
            # backfilled a pre-3.1 batch binding.
            await session.commit()
            return

        # Credits charge (credits feature): the FIRST time this message reaches
        # ✅, debit the batch's snapshotted gate_credit_cost from the tenant.
        # Read from the BATCH snapshot (an owner re-pricing the gate never
        # re-charges an old batch); skipped when the batch row is gone (SET NULL
        # after cleanup) or the gate is free. MUST run BEFORE add_full so the
        # first-✅ existence check doesn't see the row we're about to insert —
        # same transaction, so the debit commits atomically with the revision.
        # ONLY client batches are metered (``priority == 0``): owner/admin
        # "house" tenants are fully exempt from credits — never charged, never
        # blocked — same ``Batch.priority`` snapshot the create/append guard
        # uses (1=admin, 2=owner). No debit ⇒ no ``credits.updated`` emit below.
        charged_balance: int | None = None
        if status == responses_repo.STATUS_OK and attributed.batch_id is not None:
            batch_row = await session.get(Batch, attributed.batch_id)
            if (
                batch_row is not None
                and batch_row.gate_credit_cost > 0
                and batch_row.priority == 0
            ):
                charged_balance = await responses_repo.charge_if_first_ok(
                    session,
                    tenant_id=attributed.tenant_id,
                    chat_id=reply.chat_id,
                    message_id=reply.message_id,
                    cost=batch_row.gate_credit_cost,
                )

        await responses_repo.add_full(
            session,
            tenant_id=attributed.tenant_id,
            capture_session_id=attributed.capture_session_id,
            batch_id=attributed.batch_id,
            line_id=attributed.line_id,
            chat_id=reply.chat_id,
            message_id=reply.message_id,
            status=status,
            text=clean_text,
        )
        # Cookie-mode: a Format-error reply is a LINE-level terminal failure —
        # mark the line failed (fail_code) so the batch keeps draining (no
        # rotation). The worker clears the await on the signal. Guarded on
        # line_id (a SET-NULL'd batch leaves it None).
        if (
            cookie_mode
            and cookie_line_failed
            and attributed.line_id is not None
        ):
            line = await session.get(BatchLine, attributed.line_id)
            if line is not None:
                await batches_repo.mark_failed(
                    session, line, _AMAZON_FORMAT_ERROR
                )
        new_cc: list[str] = []
        if cookie_mode:
            # CC only on a true Approved verdict — the card already extracted
            # from the separator-normalized text above (never Declined/dead/
            # format noise into Filtrada).
            if cookie_verdict_kind == VERDICT_APPROVED and cookie_cc_values:
                new_cc = await responses_repo.add_new_cc(
                    session,
                    tenant_id=attributed.tenant_id,
                    capture_session_id=attributed.capture_session_id,
                    batch_id=attributed.batch_id,
                    line_id=attributed.line_id,
                    chat_id=reply.chat_id,
                    message_id=reply.message_id,
                    values=cookie_cc_values,
                )
        elif status == responses_repo.STATUS_OK:
            new_cc = await responses_repo.add_new_cc(
                session,
                tenant_id=attributed.tenant_id,
                capture_session_id=attributed.capture_session_id,
                batch_id=attributed.batch_id,
                line_id=attributed.line_id,
                chat_id=reply.chat_id,
                message_id=reply.message_id,
                values=extract_cc(clean_text),
            )
        # The live "Datos CC nuevas" badge is a DISPLAY count, so it MUST honor
        # the cockpit Limpiar cutoff (PR-1) — otherwise the first reply after a
        # Limpiar would snap the badge back to the full historical CC count
        # while the snapshot/session.active path (which threads the cutoff)
        # shows the post-clear slice. ws.ts assigns this verbatim (`ccNew`).
        cc_total = await responses_repo.cc_count(
            session,
            attributed.capture_session_id,
            cleared_response_id=(
                capture_session.cleared_response_id
                if capture_session is not None
                else None
            ),
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
        line_id = attributed.line_id
        await session.commit()

    # 🔒 Capture→worker verdict signal (Amazon cookie-mode, Phase 2). Emit AFTER
    # the commit so the worker, draining this verdict, sees the persisted full
    # row (and any line-failure) and resolves the attempt-fence against durable
    # state. ONLY when cookie_mode, the verdict consumes/rotates the line
    # (approved/declined/cookie_dead/format_error — never none/confirmation),
    # and the line is attributed. It carries ONLY ids + the kind — the cookie
    # VALUE is never part of it. A reconciler replay returns early at the no-op
    # guard above, so the signal fires AT MOST once per real verdict (no
    # spurious re-rotation). The worker attempt-fences it under the batch FOR
    # UPDATE and drops a stale/superseded signal.
    if (
        cookie_mode
        and cookie_verdict_kind in _SIGNALLING_VERDICTS
        and line_id is not None
        # 🔒 The fence key IS ``reply.reply_to_msg_id`` (the ``.amz`` message the
        # bot is replying to). A verdict with ``reply_to_msg_id=None`` is
        # un-fenceable — it could never match ``Batch.awaiting_message_id`` and
        # would only churn the worker — so never signal it (PATCH 5).
        and reply.reply_to_msg_id is not None
    ):
        cookie_verdict_signal(
            CookieVerdict(
                chat_id=reply.chat_id,
                # 🔒 ATTEMPT-FENCE KEY: the worker awaits the ``.amz`` send's own
                # message_id (``Batch.awaiting_message_id``). The bot verdict
                # REPLIES TO that ``.amz`` message, so the answered id is
                # ``reply.reply_to_msg_id`` — NOT this bot reply's own
                # ``reply.message_id`` (which would never match the fence, and a
                # reconciler edit-replay keeps the same ``reply_to_msg_id``).
                message_id=reply.reply_to_msg_id,
                line_id=line_id,
                verdict_kind=cookie_verdict_kind,
            )
        )

    # Credits balance update (credits feature): emitted ONLY on a real debit
    # (charged_balance is None for free gates and for an already-charged
    # message). Fired before the response.captured guard below so it never gets
    # skipped — a first-✅ is always a transition, but emitting here keeps the
    # two events independent. The cockpit reduces it into its live balance.
    if charged_balance is not None:
        await broadcaster.emit(
            tenant_id, "credits.updated", {"balance": charged_balance}
        )

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
            "text": display_transform(clean_text, cookie_mode),
            "new_cc": new_cc,
            "cc_total": cc_total,
            "awaiting_reply": awaiting_reply,
            "captured_at": datetime.now(UTC).isoformat(),
        },
    )
