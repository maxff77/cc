"""Reply reconciler — recover bot replies the Telethon update stream dropped.

Live capture (``core/capture.py``) depends on Telethon PUSHING ``NewMessage``/
``MessageEdited`` events. Telegram's update channel can drop them — catch_up /
``differenceTooLong`` gaps after a reconnect, a missed ⏳→✅ edit — and the
gateway itself notes a dropped replay "would be lost forever". A confirmed
incident: 300 lines delivered, replies present in Telegram (each a reply-quote),
only 92 captured.

This background task is the reply-side mirror of ``send_worker._boot_recovery``
(which reconciles SENT lines against ``gateway.recent_outgoing``): every pass it
asks the DB which of our delivered sends still have NO captured reply, re-reads
the target chat's recent inbound messages, and re-feeds any reply addressed to
one of those sends through the EXISTING ``capture.process_incoming`` path. That
path is already idempotent (text-equality dedup + the ``uq_responses_session_cc``
unique index), so re-injecting an already-captured reply is a no-op — the
reconciler only ever fills gaps, never duplicates. The scan is TARGETED on the
``(chat_id, reply_to_msg_id)`` pair (message ids are per-chat — it must match an
awaiting send IN ITS OWN chat), so attribution always succeeds and the
unmatched bucket is never inflated.

Account safety mirrors the worker: the scan is SKIPPED while the watchdog is
paused, the gateway is not ready, or a FloodWait window is open (a history read
can FloodWait too); reads are bounded; every error is swallowed so the task
never dies and never latches the watchdog from a read. When nothing is
awaiting, a pass costs ONE indexed DB query and touches Telegram not at all.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core import capture
from app.core.capture import IncomingReply
from app.core.scheduler import scheduler
from app.core.telegram import FloodWaitError, gateway
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.repos import send_log as send_log_repo

logger = logging.getLogger(__name__)

# Module constants, NOT settings (2.5 rule: no new configuration for pipeline
# internals). The interval is generous — recovery is a safety net, not the hot
# path, and frequent history reads must not themselves pace toward a FloodWait.
_RECONCILE_INTERVAL_SECONDS = 45.0
# How far back the awaiting work-list reaches. ≥48h so a reply that lands long
# after its send (or a batch still missing replies a day later) is recovered,
# while sends older than this stop driving the scan (the per-pass history depth
# is bounded by the oldest awaiting message id, ``floor_id``).
_RECONCILE_WINDOW_HOURS = 72
# Hard per-target cap on the raw history scan (the ``floor_id`` break normally
# stops far sooner; this only guards a pathological backlog).
_MAX_SCAN_PER_TARGET = 3000


async def reconcile_once() -> int:
    """One reconciliation pass. Returns the number of replies re-fed.

    Cheap when idle: the awaiting-ids query runs FIRST and a pass that finds
    nothing returns before any Telegram call.
    """
    within = datetime.now(UTC) - timedelta(hours=_RECONCILE_WINDOW_HOURS)
    async with async_session_factory() as session:
        awaiting = await send_log_repo.awaiting_sent_keys(session, within=within)
        if not awaiting:
            return 0
        # Sends too old to drive the scan are NOT silently dropped (I/O matrix:
        # "no silent cap") — surfaced in the pass log below so a growing tail of
        # permanently-lost replies stays visible. Counted only on a working pass.
        beyond = await send_log_repo.count_awaiting_beyond_window(
            session, within=within
        )

    # Account safety: never read while protected / unready / throttled (a
    # history read can FloodWait too). Self-healing — the next pass retries.
    if watchdog.is_paused or not gateway.ready or scheduler.flood_remaining() > 0:
        logger.info(
            "event=reconcile_skipped awaiting=%s paused=%s ready=%s flood=%.1f",
            len(awaiting),
            watchdog.is_paused,
            gateway.ready,
            scheduler.flood_remaining(),
        )
        return 0

    # Per-chat floors: message ids are per-chat, so each destination is scanned
    # newest-first down to ITS OWN oldest awaiting id (a single global floor
    # would force scanning a busy chat down to a tiny id from another chat).
    floors: dict[int, int] = {}
    for chat_id, message_id in awaiting:
        current = floors.get(chat_id)
        if current is None or message_id < current:
            floors[chat_id] = message_id
    try:
        inbound = await gateway.recent_incoming(floors, _MAX_SCAN_PER_TARGET)
    except FloodWaitError as e:
        # A read can FloodWait too — feed the SAME governor + global no-send
        # window the worker uses (🔒 protect the shared account), then back off.
        scheduler.note_flood_wait(float(e.seconds))
        logger.warning(
            "event=reconcile_skipped reason=flood_wait seconds=%s", e.seconds
        )
        return 0
    except Exception as error:  # never crash the task; never latch from a read
        logger.warning(
            "event=reconcile_skipped reason=read_failed error=%s", error
        )
        return 0

    fed = 0
    for chat_id, message_id, reply_to_msg_id, text in inbound:
        # Targeted on the (chat_id, reply_to) PAIR — a reply addressed to one of
        # our awaiting sends IN ITS OWN chat (so attribution always succeeds and
        # the bare id never matches the wrong supergroup).
        if (chat_id, reply_to_msg_id) in awaiting:
            capture.reconcile_enqueue(
                IncomingReply(
                    message_id=message_id,
                    reply_to_msg_id=reply_to_msg_id,
                    text=text,
                    edited=False,
                    chat_id=chat_id,
                )
            )
            fed += 1
    logger.info(
        "event=reconcile_pass awaiting=%s fed=%s beyond_window=%s",
        len(awaiting),
        fed,
        beyond,
    )
    return fed


async def run_reconciler() -> None:
    """Infinite reconciliation loop (created as a task in the lifespan).

    Sleeps one interval FIRST so the worker's boot recovery has confirmed the
    message ids of lines a crash left 'sending' before the first scan. Then
    loops forever, guarded exactly like ``run_worker``: ``CancelledError``
    propagates (clean shutdown), any other error is logged and the loop
    continues — the safety net must never be the thing that dies.
    """
    while True:
        try:
            await asyncio.sleep(_RECONCILE_INTERVAL_SECONDS)
            await reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event=reconcile_failed — continuing")
