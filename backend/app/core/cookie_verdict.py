"""Capture→worker verdict hand-off (Amazon cookie-mode, Phase 2).

The send worker sends the atomic ``.cookie``/``.amz`` pair then HOLDS the
tenant until the bot's ``⌿ Status:`` verdict for that ``.amz`` line arrives
(serialize). The capture consumer classifies that reply
(``redact.parse_amazon_verdict``) and hands the verdict back to the single
send-worker task through THIS in-process async signal — NOT the broadcaster
(which is reserved for tenant-scoped client WS fan-out, e.g. the
``cookies_exhausted`` prompt). This is engine-internal control flow, the same
shape as the capture queue / scheduler cursor: PROCESS MEMORY, reset on
restart, drainable, with a ``reset()`` for tests.

The signal is the FAST path; the DURABLE, authoritative gate is
``Batch.awaiting_verdict_until`` + ``awaiting_message_id`` (persisted on the
batch). The worker accepts a verdict only if its ``(chat_id, message_id)``
matches the message_id it is currently awaiting AND the await is still set,
verified in-txn under the batch ``FOR UPDATE`` — a verdict for a superseded
attempt or an already-cleared await is logged and dropped. So a lost/duplicated
signal is never authoritative on its own: the 90s timeout (DB ``now()``) is the
backstop.

Keyed on the ``.amz`` ``(chat_id, message_id)`` PAIR (message ids are per-chat,
not account-global — see ``SendLog``) plus the resolved ``line_id`` and the
classified ``verdict_kind``.
"""

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CookieVerdict:
    """One classified cookie-mode verdict the capture consumer hands to the
    worker. ``chat_id``/``message_id`` are the ``.amz`` attempt-fence the worker
    matches against ``Batch.awaiting_message_id``; ``verdict_kind`` is one of
    ``approved|declined|cookie_dead|format_error`` (``redact`` constants)."""

    chat_id: int
    message_id: int
    line_id: int
    verdict_kind: str


# In-process pending-verdict buffer (mirror of ``capture._queue`` / the
# scheduler's process-memory cursor): unbounded on purpose — a verdict must
# never be dropped at hand-off. The worker drains it; an unmatched signal is
# attempt-fenced and dropped THERE (in-txn under the batch FOR UPDATE), never
# here. ``wait()`` lets the worker block until a verdict (or its own ``_wake``)
# is available instead of busy-polling.
_queue: "asyncio.Queue[CookieVerdict]" = asyncio.Queue()


def signal(verdict: CookieVerdict) -> None:
    """Capture entry point (called from ``process_incoming`` after the commit):
    hand a classified verdict to the send worker. Synchronous / non-blocking —
    the queue is unbounded so this never awaits.

    The cookie VALUE is NEVER part of a verdict (only ids + the kind), so this
    cannot echo a credential.
    """
    _queue.put_nowait(verdict)
    logger.info(
        "event=cookie_verdict_signal chat_id=%s message_id=%s line_id=%s kind=%s",
        verdict.chat_id,
        verdict.message_id,
        verdict.line_id,
        verdict.verdict_kind,
    )


def drain() -> list[CookieVerdict]:
    """Pop ALL currently-pending verdicts (the worker's batch read each turn).

    Non-blocking: returns ``[]`` when empty. The worker attempt-fences each one
    under the batch ``FOR UPDATE`` and drops any that no longer matches the
    awaited ``message_id``.
    """
    drained: list[CookieVerdict] = []
    while True:
        try:
            drained.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return drained


def pending() -> int:
    """How many verdicts are buffered (tests / observability)."""
    return _queue.qsize()


def reset() -> None:
    """Wipe the pending buffer (tests / restart). Process-memory only — there
    is nothing durable to clear (the authoritative gate lives on ``Batch``)."""
    while not _queue.empty():
        _queue.get_nowait()
