"""Reply-rate watchdog + session-loss latch (Story 4.1) — the ban guardrail.

Two silent-failure modes can burn the shared account while everything LOOKS
fine: the bot silently blocking us (sends keep succeeding, replies stop) and
a dead Telethon session surfacing mid-send. Either one latches a GLOBAL send
pause: the worker gates on ``watchdog.is_paused`` at the top of ``step()``
(nobody claims, nobody sends; batches keep their DB state and resume where
they were) and the owner is alerted via a GLOBAL broadcaster event (idiom
``flood.wait`` — the pause affects everyone, the owner's tab is one of them,
and the UI decides per role what to show).

Reply-rate collapse (recorded decision, no tunable ratio): at least
``_MIN_SENDS_IN_WINDOW`` real sends inside the ``_WINDOW_SECONDS`` sliding
window, zero replies in the same window, AND the silence spanning at least
``_MIN_SILENCE_SPAN_SECONDS`` of sending (the oldest in-window send is that
old). Zero-replies-under-sustained-sending is the one unambiguous "bot
silently blocking the account" signal — a ratio threshold would invent
tuning without data, and a false positive pauses EVERY tenant; the span
floor keeps a batch's first seconds (replies lag sends) from tripping it.
Sends are noted by the worker's record phase (real deliveries only —
boot-reconciliation confirms are old sends); replies are noted at ARRIVAL
(the telegram bridge's ``capture.enqueue``), attributed or not, ✅ or ⏳ —
any bot message proves life, independent of DB health (with the DB down the
2.5 fail-stop already halted sending; buffered replies must keep the
watchdog calm).

Resume is NEVER automatic (AC 3): ``note_reply`` with the latch set does not
unpause, boot RESTORES the latch, and the only ``resume()`` call site in the
codebase is the owner-only endpoint. The latch is DURABLE (``watchdog_state``
row): CI deploys on every push to main and a pause that evaporates on
restart would be exactly the automatic resume the AC forbids. Persistence is
best-effort on purpose — the in-memory latch is the guard; with the DB down
the fail-stop already blocks the pipeline, so the row only buys durability.

State is process memory + one row, NOT configuration: window/threshold are
module constants (2.5 rule — pipeline internals are never settings).
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.broadcaster import broadcaster
from app.db.base import async_session_factory
from app.db.repos import watchdog as watchdog_repo

logger = logging.getLogger(__name__)

# Sliding window over which the reply rate is judged.
_WINDOW_SECONDS = 300.0
# Minimum sends inside the window before zero-replies means anything — fewer
# sends is not "active sending", it's noise (a batch may be seconds old and
# the bot's first reply still in flight).
_MIN_SENDS_IN_WINDOW = 5
# The silence must also SPAN this much sending (oldest in-window send at
# least this old) before it counts as a collapse: replies lag sends, so a
# burst at batch start must not pause every tenant over a transient blip.
_MIN_SILENCE_SPAN_SECONDS = 60.0

REASON_REPLY_RATE = "reply_rate_collapse"
REASON_SESSION_LOST = "session_lost"
# Boot guard (services/account_guard): the Telegram account fingerprint changed
# while historical send_log/responses exist — latch fail-closed so a silent
# account swap can never mis-attribute replies across tenants.
REASON_ACCOUNT_CHANGED = "telegram_account_changed"


class Watchdog:
    """Sliding send/reply window + the global pause latch.

    The clock is injectable so the window arithmetic is deterministic under
    test (idiom ``Scheduler``); production uses ``time.monotonic``.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._sends: deque[float] = deque()
        self._replies: deque[float] = deque()
        self._paused = False
        self._reason: str | None = None
        self._detail: str | None = None
        self._paused_at: datetime | None = None
        self._resumed_at: datetime | None = None

    # --- state inspection ----------------------------------------------------

    @property
    def is_paused(self) -> bool:
        """The worker's per-step gate (memory — zero queries per step)."""
        return self._paused

    def status(self) -> dict:
        """Snapshot/GET slice — the shape every tab rebuilds its banner from."""
        return {
            "paused": self._paused,
            "reason": self._reason,
            "detail": self._detail,
            "paused_at": (
                self._paused_at.isoformat() if self._paused_at is not None else None
            ),
        }

    def reset(self) -> None:
        """Wipe process state (tests) — memory only, never the DB row."""
        self._sends.clear()
        self._replies.clear()
        self._paused = False
        self._reason = None
        self._detail = None
        self._paused_at = None
        self._resumed_at = None

    # --- window feeding (AC 1) -----------------------------------------------

    def note_reply(self) -> None:
        """A bot message ARRIVED (telegram bridge → capture.enqueue).

        Counts attributed and unmatched alike — any bot activity proves life.
        Never unpauses (AC 3: resume is the owner's explicit action only).
        """
        now = self._now()
        self._replies.append(now)
        self._prune(now)

    async def note_sent(self) -> None:
        """A line was REALLY delivered (worker record phase) — evaluate.

        Evaluation lives exactly here on purpose ("Given active sending"):
        without sends there is no signal and idle periods can never false-
        trigger. Once paused the worker stops sending, so this naturally
        stops firing too.
        """
        now = self._now()
        self._sends.append(now)
        self._prune(now)
        if self._paused:
            return
        if (
            len(self._sends) >= _MIN_SENDS_IN_WINDOW
            and not self._replies
            and now - self._sends[0] >= _MIN_SILENCE_SPAN_SECONDS
        ):
            await self.trigger(
                REASON_REPLY_RATE,
                detail=(
                    f"{len(self._sends)} sends in the last "
                    f"{int(_WINDOW_SECONDS)}s window with zero bot replies"
                ),
            )

    def _prune(self, now: float) -> None:
        """Drop timestamps that slid out of the window."""
        horizon = now - _WINDOW_SECONDS
        while self._sends and self._sends[0] < horizon:
            self._sends.popleft()
        while self._replies and self._replies[0] < horizon:
            self._replies.popleft()

    # --- latch (AC 1/2/3/4) ----------------------------------------------------

    async def session_lost(self, detail: str) -> None:
        """Telethon session died mid-send (AC 2) — latch immediately."""
        await self.trigger(REASON_SESSION_LOST, detail=detail)

    async def trigger(self, reason: str, detail: str) -> None:
        """Latch the global pause + alert the owner. Idempotent: an already-
        latched watchdog never re-logs or re-emits (no duplicate alerts)."""
        if self._paused:
            return
        # Memory FIRST — this is the actual guard the worker gates on.
        self._paused = True
        self._reason = reason
        self._detail = detail
        self._paused_at = datetime.now(UTC)
        self._resumed_at = None
        # Structured log (AC 4) — journald-greppable, Story 4.3's raw material.
        logger.warning(
            "event=watchdog_paused reason=%s detail=%s sends_in_window=%s "
            "replies_in_window=%s",
            reason,
            detail,
            len(self._sends),
            len(self._replies),
        )
        await self._persist()
        await broadcaster.emit_global(
            "watchdog.paused",
            {
                "reason": reason,
                "detail": detail,
                "paused_at": self._paused_at.isoformat(),
            },
        )

    async def resume(self) -> bool:
        """Clear the latch — called ONLY by the owner endpoint (AC 3).

        Returns False (idempotent no-op, no duplicate event) when nothing is
        paused. The window starts FRESH (recorded decision): the pre-pause
        timestamps would re-trigger the collapse on the very first send.
        """
        if not self._paused:
            return False
        self._paused = False
        reason = self._reason
        self._reason = None
        self._detail = None
        self._paused_at = None
        self._resumed_at = datetime.now(UTC)
        self._sends.clear()
        self._replies.clear()
        logger.info("event=watchdog_resumed reason_was=%s", reason)
        await self._persist()
        await broadcaster.emit_global(
            "watchdog.resumed", {"resumed_at": self._resumed_at.isoformat()}
        )
        return True

    # --- durability (AC 3 across restarts) -------------------------------------

    async def _persist(self) -> None:
        """Mirror the latch into ``watchdog_state`` — BEST-EFFORT on purpose.

        The in-memory latch is the guard; with the DB down the 2.5 fail-stop
        already blocks every send, so a failed write only costs durability
        across a restart that happens before the DB returns (edge of edge).
        """
        try:
            async with async_session_factory() as session:
                await watchdog_repo.save_state(
                    session,
                    paused=self._paused,
                    reason=self._reason,
                    detail=self._detail,
                    paused_at=self._paused_at,
                    resumed_at=self._resumed_at,
                )
                await session.commit()
        except Exception:
            logger.exception(
                "event=watchdog_persist_failed — the in-memory latch still "
                "holds; with the DB down the fail-stop blocks sending anyway"
            )

    async def load_persisted(self) -> None:
        """Boot (lifespan, BEFORE the worker task): restore a persisted latch.

        A missing row / unpaused row / unreadable DB boots unpaused — when
        the DB is down nothing sends anyway (fail-stop), and the watchdog
        re-arms from live traffic.
        """
        try:
            async with async_session_factory() as session:
                state = await watchdog_repo.get_state(session)
                if state is None or not state.paused:
                    return
                # Copy inside the block (the 2.3 MissingGreenlet lesson).
                reason = state.reason
                detail = state.detail
                paused_at = state.paused_at
        except Exception:
            logger.exception(
                "event=watchdog_load_failed — booting unpaused; the watchdog "
                "re-arms from live traffic"
            )
            return
        self._paused = True
        self._reason = reason
        self._detail = detail
        self._paused_at = paused_at
        logger.warning(
            "event=watchdog_restored reason=%s — global pause persisted "
            "across restart; owner resume required",
            reason,
        )


# Module-level singleton (same idiom as settings / gateway / scheduler).
watchdog = Watchdog()
